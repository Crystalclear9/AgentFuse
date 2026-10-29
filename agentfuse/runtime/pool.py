"""Worker handles and the warm worker pool.

A *worker handle* owns one operating system process plus the threads that read
its protocol stream and sample its resource usage.  The handle can serve
several executions in a row - the process stays alive and waits for the next
payload on stdin.

Two modes are supported:

``fresh``
    One process per execution.  The process is started when the execution is
    scheduled and terminated when it finished.  This is the default.

``pool``
    The runtime keeps a small pool of pre-started workers.  The interpreter
    start up and the imports are paid **once per worker** instead of once per
    execution, which removes ~90 ms from the critical path of every call.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

try:  # pragma: no cover - psutil is a hard dependency
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


@dataclass(eq=False)
class WorkerHandle:
    """One worker process together with its protocol threads."""

    process: Any
    execution_id: str | None = None
    reusable: bool = True
    created_at: float = field(default_factory=time.time)
    jobs_served: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)
    stderr_output: str = ""
    threads_started: bool = False
    terminated: bool = False
    confirmed_gone: bool = False
    #: the pid of the process that really executes the tool.  It can differ
    #: from ``process.pid``: a virtual environment on Windows starts the
    #: interpreter through a launcher stub.
    worker_pid: int | None = None
    #: the pids of the processes the launcher started (the real worker)
    child_pids: list[int] = field(default_factory=list)

    # ------------------------------------------------------------------
    def assign(self, execution_id: str) -> None:
        with self.lock:
            self.execution_id = execution_id
            self.jobs_served += 1

    def release(self) -> None:
        with self.lock:
            self.execution_id = None

    @property
    def current_execution_id(self) -> str | None:
        with self.lock:
            return self.execution_id

    def send(self, payload: dict[str, Any]) -> None:
        """Write one payload line to the worker."""

        stdin = self.process.stdin
        if stdin is None:  # pragma: no cover - defensive
            raise RuntimeError("the worker has no stdin pipe")
        stdin.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        stdin.flush()

    def is_alive(self) -> bool:
        try:
            return self.process.poll() is None
        except Exception:  # pragma: no cover - defensive
            return False

    def terminate(self) -> bool:
        """Kill the worker (and its children) and reap it.

        The call is idempotent **only when the process is really gone**: if the
        termination failed, a later call retries it.  Returns ``True`` when the
        worker process is gone.
        """

        from .cancellation import terminate_worker

        if self.process is None:
            self.terminated = True
            self.confirmed_gone = True
            return True
        if self.confirmed_gone:
            return True
        gone = terminate_worker(
            self.process,
            getattr(self.process, "pid", None),
            worker_pid=self.worker_pid,
            child_pids=list(self.child_pids),
        )
        self.reusable = False
        if gone:
            self.terminated = True
            self.confirmed_gone = True
        return gone


def build_command() -> list[str]:
    """The command that starts a tool worker."""

    return [sys.executable, "-u", "-m", "agentfuse.runtime.worker_child"]


def build_environment() -> dict[str, str]:
    """The environment of a worker (including the parent's import path)."""

    environment = dict(os.environ)
    python_path = [entry for entry in sys.path if entry]
    if python_path:
        existing = environment.get("PYTHONPATH")
        environment["PYTHONPATH"] = (
            os.pathsep.join(python_path + [existing])
            if existing
            else os.pathsep.join(python_path)
        )
    return environment


def discover_children(pid: int | None, timeout: float = 1.0) -> list[int]:
    """Find the processes that the started process launched.

    A virtual environment on Windows starts the real interpreter through a
    launcher stub.  The launcher waits for its child, so killing the launcher
    does **not** stop a worker that is busy computing - it never reads stdin
    again.  The child pids are therefore collected right after the start.
    """

    if psutil is None or not pid:
        return []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            parent = psutil.Process(int(pid))
            children = [child.pid for child in parent.children(recursive=True)]
        except psutil.NoSuchProcess:
            return []
        except Exception:  # pragma: no cover - defensive
            children = []
        if children:
            return children
        time.sleep(0.01)
    return []


def start_worker() -> WorkerHandle:
    """Start one worker process."""

    process = subprocess.Popen(  # noqa: S603 - fixed command, no shell
        build_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=build_environment(),
        cwd=os.getcwd(),
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    handle = WorkerHandle(process=process)
    handle.child_pids = discover_children(process.pid)
    return handle


def spawn_pool(size: int, on_ready: Callable[[WorkerHandle], None] | None = None) -> list[WorkerHandle]:
    """Start ``size`` workers in the background."""

    workers: list[WorkerHandle] = []
    for _ in range(max(0, int(size))):
        handle = start_worker()
        workers.append(handle)
        if on_ready is not None:
            on_ready(handle)
    return workers
