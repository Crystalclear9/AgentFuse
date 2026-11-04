"""Opt-in Docker isolation for third-party commands. Fail closed without Docker.

An arbitrary program's dependencies cannot be proved complete. These commands
are never eligible for cross-workspace sharing. The complete input tree is
fingerprinted instead of pretending a Python audit hook observes native I/O.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import uuid


class SandboxUnavailable(RuntimeError):
    pass


def input_manifest(root: Path):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("sandbox input cannot contain symbolic links")
        name = path.relative_to(root).as_posix()
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "DIRECTORY"
    return result


def run_isolated(snapshot, command, *, image, timeout=60, memory_mb=256, cpus=1.0):
    """Run a command in a pinned local Linux image, without network or host writes.

    No shell interpolation. No Docker socket, host PID namespace or devices.
    The caller chooses the image; only an immutable sha256 digest is accepted.
    """
    root = Path(snapshot).resolve(strict=True)
    if not root.is_dir() or not command or any(not isinstance(x, str) for x in command):
        raise ValueError("a snapshot directory and argv list are required")
    import re
    if not re.fullmatch(r"sha256:[0-9a-f]{64}|[^\s]+@sha256:[0-9a-f]{64}", image):
        raise ValueError("sandbox image must be pinned by sha256 digest")
    if not 0 < timeout <= 300 or not 16 <= memory_mb <= 4096 or not 0 < cpus <= 64:
        raise ValueError("sandbox resource limit outside supported bounds")
    try:
        check = subprocess.run(["docker", "info", "--format", "{{.OSType}}"], capture_output=True, timeout=15, text=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SandboxUnavailable("Docker Linux engine is unavailable") from exc
    if check.returncode or check.stdout.strip() != "linux":
        raise SandboxUnavailable("a running Docker Linux engine is required")
    # Copy the input before execution. Never expose a user directory directly.
    from .remote import pack_tree, unpack_tree
    with tempfile.TemporaryDirectory(prefix="agentfuse-sandbox-") as temporary:
        staged = Path(temporary)
        unpack_tree(pack_tree(str(root)), staged)
        before = input_manifest(staged)
        name = "agentfuse-" + uuid.uuid4().hex
        argv = ["docker", "run", "--rm", "--pull=never", "--name", name,
                "--network=none", "--read-only", "--cap-drop=ALL",
                "--security-opt=no-new-privileges", "--user", "65534:65534",
                "--pids-limit", "64", "--memory", f"{memory_mb}m",
                "--memory-swap", f"{memory_mb}m", "--cpus", str(cpus),
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                "--mount", f"type=bind,source={staged},target=/workspace,readonly",
                "--workdir", "/workspace", "--entrypoint", command[0], image, *command[1:]]
        try:
            import threading
            import time
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            output = [bytearray(), bytearray()]
            overflow = threading.Event()
            def drain(pipe, index):
                try:
                    while chunk := pipe.read(4096):
                        remaining = 1024 * 1024 - len(output[index])
                        output[index].extend(chunk[:max(0, remaining)])
                        if len(chunk) > remaining:
                            overflow.set()
                finally:
                    pipe.close()
            threads = [threading.Thread(target=drain, args=(pipe, index), daemon=True)
                       for index, pipe in enumerate((process.stdout, process.stderr))]
            for thread in threads:
                thread.start()
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if overflow.is_set():
                        raise RuntimeError("sandbox output exceeded 1 MiB per stream")
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(argv, timeout)
                    overflow.wait(0.02)
            finally:
                if process.poll() is None:
                    subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15)
                    process.kill()
                process.wait(timeout=15)
                for thread in threads:
                    thread.join(timeout=5)
            if overflow.is_set():
                raise RuntimeError("sandbox output exceeded 1 MiB per stream")
            out, err = bytes(output[0]), bytes(output[1])
            after = input_manifest(staged)
            if before != after:
                raise RuntimeError("sandbox input changed during execution")
            return dict(exit_code=process.returncode, stdout=out.decode(errors="replace"),
                        stderr=err.decode(errors="replace"), input_manifest=before,
                        input_hash=hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
                        dependency_complete=False, shareable=False, isolation="docker-linux")
        finally:
            subprocess.run(["docker", "rm", "--force", name], capture_output=True, timeout=15)
