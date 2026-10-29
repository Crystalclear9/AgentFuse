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


def terminate_worker(process: Any, pid: int | None = None) -> None:
    """Kill a worker process (and its children) and reap it."""

    if process is None:
        return
    if psutil is not None and pid:
        try:
            parent = psutil.Process(int(pid))
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    continue
            parent.kill()
        except Exception:
            pass
    try:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except Exception:  # pragma: no cover - defensive
                pass
    except Exception:  # pragma: no cover - defensive
        pass


async def terminate_execution(execution: Any, reason: str) -> None:
    """Terminate one execution and record the cancellation."""

    execution.terminated = True
    handle = getattr(execution, "worker_handle", None)
    if handle is not None:
        handle.reusable = False
    terminate_worker(execution.process, execution.pid)
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
