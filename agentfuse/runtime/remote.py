"""Authenticated streaming worker transport; one immutable snapshot per request.

Use TLS on non-loopback interfaces. Workers execute registered tools only.
Disconnecting a client cancels its process; remote PIDs never reach local kill APIs.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import platform
import socket
import ssl
import sys
import tempfile

MAX_FRAME = 96 * 1024 * 1024
MAX_BYTES = 64 * 1024 * 1024
MAX_FILES = 5000


def pack_tree(root: str) -> dict:
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("snapshot root must be a directory")
    files, directories, size = {}, [], 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("remote snapshots cannot contain symlinks")
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            directories.append(name)
            if len(directories) > MAX_FILES:
                raise ValueError("remote snapshot exceeds directory limit")
        elif path.is_file():
            content = path.read_bytes()
            size += len(content)
            if size > MAX_BYTES or len(files) >= MAX_FILES:
                raise ValueError("remote snapshot exceeds limits")
            files[name] = base64.b64encode(content).decode("ascii")
    return dict(files=files, directories=directories)


def unpack_tree(tree: dict, root: Path) -> None:
    files, directories = tree["files"], tree["directories"]
    if not isinstance(files, dict) or not isinstance(directories, list):
        raise ValueError("invalid snapshot entry collections")
    if len(files) > MAX_FILES or len(directories) > MAX_FILES:
        raise ValueError("too many snapshot entries")
    root = root.resolve()
    def target(name):
        if not isinstance(name, str) or any(part in ("", ".", "..") for part in name.split("/")):
            raise ValueError("unsafe snapshot path")
        path = PurePosixPath(name)
        if path.is_absolute() or "\\" in name or ":" in name or "\x00" in name:
            raise ValueError("unsafe snapshot path")
        if os.name == "nt":
            devices = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
            devices.update(prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³")
            if any(part.endswith((".", " ")) or part.split(".")[0].upper() in devices
                   or any(ord(char) < 32 or char in '<>"|?*' for char in part)
                   for part in path.parts):
                raise ValueError("unsafe Windows snapshot path")
        result = root.joinpath(*path.parts)
        result.resolve().relative_to(root)
        return result
    # Validate the whole tree before touching the destination. Path equality
    # also catches case aliases on Windows, where remote workers are supported.
    directory_paths = [target(name) for name in directories]
    file_paths = [target(name) for name in files]
    all_paths = directory_paths + file_paths
    if len(set(all_paths)) != len(all_paths):
        raise ValueError("duplicate snapshot path")
    file_set = set(file_paths)
    if any(parent in file_set for path in all_paths for parent in path.parents):
        raise ValueError("snapshot file conflicts with a directory")
    size = 0
    decoded = []
    for encoded in files.values():
        content = base64.b64decode(encoded, validate=True)
        size += len(content)
        if size > MAX_BYTES:
            raise ValueError("snapshot too large")
        decoded.append(content)
    for path in directory_paths:
        path.mkdir(parents=True, exist_ok=True)
    for path, content in zip(file_paths, decoded):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def environment() -> dict:
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for directory in ("tools", "workspace"):
        for path in sorted((root / directory).glob("*.py")):
            digest.update(path.name.encode())
            digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return dict(python=list(sys.version_info[:2]), implementation=platform.python_implementation(),
                platform=sys.platform, tool_digest=digest.hexdigest())


def forward(payload: dict, endpoint: dict, emit) -> None:
    token = os.environ.get(endpoint.get("token_env", "AGENTFUSE_WORKER_TOKEN"), "")
    if not token:
        raise ValueError("worker token environment variable is empty")
    host, port = endpoint["host"], int(endpoint["port"])
    if not endpoint.get("tls") and host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("TLS is required for non-loopback workers")
    request = dict(payload=payload, tree=pack_tree(payload["snapshot_root"]), timeout=endpoint.get("timeout", 60))
    request["payload"] = {k: v for k, v in payload.items() if k != "snapshot_root"}
    data = json.dumps(request).encode()
    if len(data) > MAX_FRAME:
        raise ValueError("worker request too large")
    with socket.create_connection((host, port), timeout=10) as raw:
        connection = ssl.create_default_context(cafile=endpoint.get("ca_file")).wrap_socket(raw, server_hostname=host) if endpoint.get("tls") else raw
        with connection:
            connection.settimeout(float(endpoint.get("timeout", 60)) + 15)
            with connection.makefile("rb") as stream:
                header = dict(token=token, environment=environment(), size=len(data))
                connection.sendall(json.dumps(header).encode() + b"\n")
                response = json.loads(stream.readline(4097))
                if response.get("t") != "ready":
                    emit(response)
                    return
                connection.sendall(data)
                while True:
                    line = stream.readline(MAX_FRAME + 1)
                    if not line or len(line) > MAX_FRAME:
                        raise RuntimeError("remote worker disconnected or exceeded frame limit")
                    message = json.loads(line)
                    if message.get("t") == "hello":
                        continue
                    emit(message)
                    if message.get("t") in ("result", "error"):
                        return


async def kill_tree(process):
    import psutil
    try:
        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        parent.kill()
    except psutil.NoSuchProcess:
        pass
    # Drain and close all asyncio pipe transports before their loop can close.
    await process.communicate()


async def start_server(host="127.0.0.1", port=8876, *, token: str, workers=2, tls=None):
    if len(token) < 32 or workers < 1:
        raise ValueError("token must have at least 32 characters; workers must be positive")
    if tls is None and host not in ("127.0.0.1", "::1", "localhost"):
        raise ValueError("TLS required for public listener")
    semaphore = asyncio.Semaphore(workers)
    handlers = {}

    async def handle(reader, writer):
        current = asyncio.current_task()
        handlers[current] = writer
        process = None
        temporary = None
        tasks = []
        peak_rss = 0
        async def emit(message):
            writer.write(json.dumps(message).encode() + b"\n")
            await writer.drain()
        try:
            if len(handlers) > workers * 4:
                raise ValueError("remote worker connection limit reached")
            line = await asyncio.wait_for(reader.readline(), 15)
            if len(line) > 4096:
                raise ValueError("worker authentication header too large")
            header = json.loads(line)
            if not hmac.compare_digest(str(header.get("token", "")), token):
                raise ValueError("worker authentication failed")
            if header.get("environment") != environment():
                raise ValueError("worker environment differs from coordinator")
            size = header.get("size")
            if type(size) is not int or not 0 < size <= MAX_FRAME:
                raise ValueError("invalid worker request size")
            async with asyncio.timeout(315):
                async with semaphore:
                    await emit(dict(t="ready"))
                    request = json.loads(await asyncio.wait_for(reader.readexactly(size), 15))
                    timeout = min(300.0, max(0.1, float(request.get("timeout", 60))))
                    temporary = tempfile.TemporaryDirectory(prefix="agentfuse-worker-")
                    root = Path(temporary.name)
                    unpack_tree(request["tree"], root)
                    payload = request["payload"]
                    from ..tools.registry import build_default_registry
                    build_default_registry().get(payload["tool_name"], payload.get("tool_version"))
                    payload["snapshot_root"] = str(root)
                    from .pool import build_command, build_environment
                    env = build_environment()
                    env.pop("AGENTFUSE_REMOTE_ENDPOINT", None)
                    process = await asyncio.create_subprocess_exec(
                        *build_command(),
                        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL, env=env, limit=MAX_FRAME)
                    process.stdin.write(json.dumps(payload).encode() + b"\n")
                    await process.stdin.drain()
                    async def monitor():
                        nonlocal peak_rss
                        import psutil
                        while process.returncode is None:
                            try:
                                parent = psutil.Process(process.pid)
                                rss = parent.memory_info().rss
                                for child in parent.children(recursive=True):
                                    try:
                                        rss += child.memory_info().rss
                                    except psutil.NoSuchProcess:
                                        pass
                                peak_rss = max(peak_rss, rss)
                                await emit(dict(t="resource", rss=rss, worker_pid=process.pid))
                            except psutil.NoSuchProcess:
                                return
                            await asyncio.sleep(0.05)
                    async def stream():
                        while line := await process.stdout.readline():
                            message = json.loads(line)
                            if message.get("t") == "result":
                                message["remote_peak_rss"] = max(peak_rss, int(message.get("rss_end", 0)))
                            if message.get("t") != "hello":
                                await emit(message)
                            if message.get("t") in ("result", "error"):
                                return
                        raise RuntimeError("worker exited without a result")
                    tasks = [asyncio.create_task(stream()), asyncio.create_task(reader.read(1)),
                             asyncio.create_task(monitor())]
                    done, _ = await asyncio.wait(tasks[:2], timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                    if tasks[0] in done:
                        await tasks[0]
                    elif not done:
                        raise TimeoutError("remote execution timed out")
                    else:
                        raise ConnectionError("client disconnected")
        except Exception as exc:
            try:
                await emit(dict(t="error", kind="internal_error", message=str(exc), cpu_time=0))
            except (ConnectionError, OSError):
                pass
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if process:
                await kill_tree(process)
            if temporary:
                temporary.cleanup()
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            handlers.pop(current, None)

    server = await asyncio.start_server(handle, host, port, ssl=tls, limit=4096)
    class ManagedServer:
        sockets = server.sockets
        def close(self):
            server.close()
            # Closing the connections drives normal disconnect cleanup. A second
            # task cancellation could interrupt process/pipe cleanup on Windows.
            for writer in tuple(handlers.values()):
                writer.close()
        async def wait_closed(self):
            await server.wait_closed()
            if handlers:
                await asyncio.gather(*tuple(handlers), return_exceptions=True)
        async def serve_forever(self):
            await server.serve_forever()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            self.close()
            await self.wait_closed()
    return ManagedServer()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8876)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    args = parser.parse_args()
    tls = None
    if args.cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(args.cert, args.key)
    async def run():
        server = await start_server(args.host, args.port, token=os.environ.get("AGENTFUSE_WORKER_TOKEN", ""), workers=args.workers, tls=tls)
        async with server:
            await server.serve_forever()
    asyncio.run(run())


if __name__ == "__main__":
    main()
