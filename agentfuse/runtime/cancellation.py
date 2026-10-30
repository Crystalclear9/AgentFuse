"""Cancellation and termination of worker processes.

Every cancellation path of AgentFuse ends here:

* a logical call is cancelled (follower, leader or all participants),
* an execution exceeds its timeout,
* the runtime shuts down.

Terminating a worker means killing the **whole process tree** - the worker
itself and every child it may have spawned - and then reaping it, so that no
zombie process is left behind.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..core.state import ExecutionStatus

try:  # pragma: no cover - psutil is a hard dependency of the runtime
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


class CancellationReason:
    """The reasons why an execution or a call can be cancelled."""

    FOLLOWER_CANCELLED = "follower cancelled"
    LEADER_CANCELLED = "leader cancelled"
    ALL_CANCELLED = "all participants cancelled"
    TIMEOUT = "execution timed out"
    SHUTDOWN = "runtime shutdown"
    WORKER_TERMINATED = "worker terminated"


def _wait_until_gone(pid: int | None, timeout: float = 2.0) -> bool:
    """Wait until a process is really gone.

    Right after ``TerminateProcess`` the process can still report itself as
    running for a short moment, therefore the check is retried instead of
    producing a false alarm.
    """

    if psutil is None or not pid:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not psutil.Process(int(pid)).is_running():
                return True
        except psutil.NoSuchProcess:
            return True
        except Exception:  # pragma: no cover - defensive
            return True
        time.sleep(0.05)
    return False


def terminate_worker(
    process: Any,
    pid: int | None = None,
    worker_pid: int | None = None,
    child_pids: list[int] | None = None,
) -> bool:
    """Kill a worker process (and its children) and reap it.

    ``worker_pid`` is the pid of the process that really executes the tool and
    ``child_pids`` are the processes the launcher started.  They differ from
    ``pid`` when the interpreter is started through a launcher stub (a virtual
    environment on Windows does that): killing the launcher closes the pipes,
    but a worker that is busy computing never reads them and would survive as
    an orphan.

    Returns ``True`` when every known process is really gone.  A failed
    termination is **not** swallowed silently: the caller can retry it.
    """

    if process is None:
        return True
    errors: list[BaseException] = []

    def kill_pid(target: int) -> None:
        if psutil is None or not target:
            return
        try:
            psutil.Process(int(target)).kill()
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)

    # 1. the process that really runs the tool
    if worker_pid and worker_pid != pid:
        kill_pid(worker_pid)
    # 2. the children of the launcher (they hold the tool process as well)
    for child in child_pids or ():
        if child != worker_pid and child != pid:
            kill_pid(child)
    if psutil is not None and pid:
        try:
            parent = psutil.Process(int(pid))
            for child in parent.children(recursive=True):
                kill_pid(child.pid)
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(exc)
    # 3. the launched process itself
    try:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except Exception as exc:  # pragma: no cover - defensive
                errors.append(exc)
    except Exception as exc:  # pragma: no cover - defensive
        errors.append(exc)

    gone = True
    for target in (worker_pid, pid, *(child_pids or ())):
        if not target:
            continue
        if not _wait_until_gone(target):
            gone = False
    try:
        if process.poll() is None:
            gone = False
    except Exception:  # pragma: no cover - defensive
        gone = False

    if not gone:
        import logging

        logging.getLogger("agentfuse.runtime").warning(
            "worker process %s (tool pid %s) could not be terminated: %s",
            pid,
            worker_pid,
            errors or "unknown",
        )
    return gone


async def terminate_execution(execution: Any, reason: str) -> None:
    """Terminate one execution and record the cancellation."""

    execution.terminated = True
    handle = getattr(execution, "worker_handle", None)
    if handle is not None:
        handle.reusable = False
    terminate_worker(
        execution.process,
        execution.pid,
        worker_pid=getattr(handle, "worker_pid", None),
        child_pids=list(getattr(handle, "child_pids", ()) or ()),
    )
    if execution.status is ExecutionStatus.RUNNING:
        execution.status = ExecutionStatus.CANCELLED
    if execution.timeout_task is not None:
        execution.timeout_task.cancel()
        execution.timeout_task = None
    if not execution.outcome.done():
        execution.outcome.set_result(
            _timeout_outcome(reason)
        )


def _timeout_outcome(reason: str) -> Any:
    from .execution import ExecutionOutcome
    from .protocol import ERROR_TIMEOUT

    return ExecutionOutcome(
        ok=False, kind=ERROR_TIMEOUT, message=reason, exit_status="timeout"
    )


async def cancel_call(call: Any, reason: str) -> None:
    """Cancel one logical call and resolve its future with a cancellation."""

    if call.future.done():
        return
    call.future.set_exception(asyncio.CancelledError(reason))
