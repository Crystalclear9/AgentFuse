"""Physical execution model.

A :class:`PhysicalExecution` is one *real* run of a tool: one operating system
process, one dependency ledger, one result.  Several logical tool calls may be
bound to the same physical execution - that relation is the core of the shared
execution protocol.

The dataclass lives in its own module so that the execution model can be
imported (and reasoned about) without pulling in the coordinator.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.state import ExecutionStatus
from ..sharing.dependency import DependencyLedger


@dataclass
class ExecutionOutcome:
    """The terminal result of a physical execution."""

    ok: bool
    kind: str | None = None
    message: str = ""
    payload: Any = None
    result_hash: str | None = None
    cpu_time: float = 0.0
    exit_status: str = "0"
    traceback: str | None = None
    #: False when the tool read the workspace without the controlled reader -
    #: the dependency ledger is incomplete, the result must not be shared.
    dependency_complete: bool = True
    dependency_bypass: list[str] = field(default_factory=list)


@dataclass
class PhysicalExecution:
    """One real execution of a tool inside a worker process."""

    execution_id: str
    workflow_id: str | None
    tool_name: str
    tool_version: str
    arguments: dict[str, Any]
    arguments_hash: str
    candidate_key: str
    snapshot_id: str
    share_group_id: str | None = None
    leader_call_id: str | None = None
    status: ExecutionStatus = ExecutionStatus.CREATED
    pid: int | None = None
    start_time: float | None = None
    finish_time: float | None = None
    cpu_time: float = 0.0
    peak_rss: int = 0
    exit_status: str | None = None
    result: Any = None
    result_hash: str | None = None
    #: the path of the stored result payload
    result_reference: str | None = None
    #: the event that started the execution - the causal parent of its events
    start_event_id: str | None = None
    error: str | None = None
    timeout_seconds: float = 60.0
    attempt: int = 1
    ledger: DependencyLedger = field(default_factory=lambda: DependencyLedger(""))
    outcome: asyncio.Future = field(default_factory=asyncio.Future, repr=False)
    process: Any = field(default=None, repr=False)
    worker_handle: Any = field(default=None, repr=False)
    timeout_task: asyncio.Task | None = field(default=None, repr=False)
    terminated: bool = False
    admission_reason: str | None = None
    queued_time: float | None = None
    #: the pid of the process that really executes the tool (it can differ
    #: from ``pid`` when the interpreter runs behind a launcher stub)
    worker_root_pid: int | None = None
    worker_command: str | None = None
    resource_warning_emitted: bool = False
    created_at: float = field(default_factory=time.time)
    stderr_output: str = ""

    @property
    def duration(self) -> float:
        if self.start_time is None:
            return 0.0
        end = self.finish_time if self.finish_time is not None else time.time()
        return max(0.0, end - self.start_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "arguments_hash": self.arguments_hash,
            "candidate_key": self.candidate_key,
            "snapshot_id": self.snapshot_id,
            "share_group_id": self.share_group_id,
            "status": str(self.status),
            "pid": self.pid,
            "start_time": self.start_time,
            "finish_time": self.finish_time,
            "duration": self.duration,
            "cpu_time": self.cpu_time,
            "peak_rss": self.peak_rss,
            "exit_status": self.exit_status,
            "result_hash": self.result_hash,
            "error": self.error,
            "queued_time": self.queued_time,
            "admission_reason": self.admission_reason,
            "worker_root_pid": self.worker_root_pid,
            "worker_command": self.worker_command,
            "dependency_count": len(self.ledger),
        }
