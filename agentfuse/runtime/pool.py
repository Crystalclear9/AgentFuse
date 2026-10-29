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

    def terminate(self) -> None:
        """Kill the worker (and its children) and reap it."""

        from .cancellation import terminate_worker

        try:
            stdin = self.process.stdin
            if stdin is not None:
                stdin.close()
        except Exception:  # pragma: no cover - defensive
            pass
        terminate_worker(self.process, getattr(self.process, "pid", None))
        self.reusable = False


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
    return WorkerHandle(process=process)


def spawn_pool(size: int, on_ready: Callable[[WorkerHandle], None] | None = None) -> list[WorkerHandle]:
    """Start ``size`` workers in the background."""

    workers: list[WorkerHandle] = []
    for _ in range(max(0, int(size))):
        handle = start_worker()
        workers.append(handle)
        if on_ready is not None:
            on_ready(handle)
    return workers
