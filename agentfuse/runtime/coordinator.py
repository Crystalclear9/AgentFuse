"""Runtime coordinator.

The coordinator owns every piece of mutable runtime state:

* logical tool calls (``LogicalToolCall``),
* physical executions (``PhysicalExecution``),
* share groups and their participants,
* the worker processes, their monitoring and their cancellation.

All share state transitions happen under a single :class:`asyncio.Lock`
(``self._lock``).  This is the mechanism that keeps the concurrent scenarios
consistent: joining, detaching, delivering, cancelling and releasing resources
can never interleave in a half finished state.

Nothing in this module *guesses* a relation.  A logical call is bound to a
physical execution because the runtime executed it, and a participant is
attached to a share group because the candidate key matched **and** every
observed dependency could be validated in the participant's own snapshot.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..core.events import EventBus
from ..core.ids import new_id
from ..core.state import (
    EventType,
    ExecutionStatus,
    LogicalCallStatus,
    ParticipantStatus,
    RuntimeMode,
    ShareGroupStatus,
    ValidationStatus,
)
from ..sharing.candidate import compute_candidate_key
from ..sharing.dependency import DependencyLedger, DependencyRecord
from ..sharing.result_cache import ResultCache
from ..sharing.result_delivery import ResultStore
from ..sharing.share_group import Participant, ShareGroup
from ..monitoring.resource_monitor import ResourceMonitor
from ..sharing.validator import DependencyValidator
from ..tools.registry import ToolRegistry
from ..workspace.fingerprint import canonical_json, hash_arguments
from ..workspace.snapshots import SnapshotStore
from .protocol import (
    ERROR_CANCELLED,
    ERROR_CRASH,
    ERROR_TIMEOUT,
    ERROR_TOOL,
    MSG_DEPENDENCY,
    MSG_ERROR,
    MSG_EOF,
    MSG_HELLO,
    MSG_RESULT,
)
from .cancellation import terminate_execution
from .pool import WorkerHandle, start_worker
from .execution import ExecutionOutcome, PhysicalExecution
from .scheduler import LocalResourceController

try:  # pragma: no cover
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


class ToolExecutionError(RuntimeError):
    """Raised when a logical tool call could not be completed."""

    def __init__(self, message: str, kind: str = "error", execution_id: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.execution_id = execution_id


@dataclass
class ToolCallResult:
    """The outcome of one logical tool call."""

    logical_call_id: str
    physical_execution_id: str
    result: Any
    result_hash: str
    shared: bool
    request_time: float
    completed_time: float
    share_group_id: str | None = None

    @property
    def wait_duration(self) -> float:
        return max(0.0, self.completed_time - self.request_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_call_id": self.logical_call_id,
            "physical_execution_id": self.physical_execution_id,
            "result_hash": self.result_hash,
            "shared": self.shared,
            "request_time": self.request_time,
            "completed_time": self.completed_time,
            "wait_duration": self.wait_duration,
            "share_group_id": self.share_group_id,
        }


@dataclass
class LogicalCall:
    logical_call_id: str
    workflow_id: str
    tool_name: str
    tool_version: str
    arguments: dict[str, Any]
    arguments_hash: str
    candidate_key: str
    snapshot_id: str
    invocation_id: str | None = None
    agent_id: str | None = None
    request_time: float = field(default_factory=time.time)
    status: LogicalCallStatus = LogicalCallStatus.PENDING
    timeout_seconds: float | None = None
    share_group_id: str | None = None
    physical_execution_id: str | None = None
    result: Any = None
    result_hash: str | None = None
    #: the path of the stored result payload (the digest lives in tool_results)
    result_reference: str | None = None
    #: the event that requested the call - the causal parent of its events
    request_event_id: str | None = None
    error: str | None = None
    completed_time: float | None = None
    retry_count: int = 0
    future: asyncio.Future = field(default_factory=asyncio.Future, repr=False)
    detached_reason: str | None = None
    detached_at: float | None = None
    shared: bool = False


def _worker_command(handle: Any) -> str | None:
    """The command line of the process that really executes the tool."""

    try:
        process = getattr(handle, "process", None)
        if process is None:
            return None
        return " ".join(process.args[:4])
    except Exception:  # pragma: no cover - defensive
        return None


class RuntimeCoordinator:
    """Executes tool calls, coordinates shared execution and owns the resources."""

    def __init__(
        self,
        *,
        repository: Any,
        registry: ToolRegistry,
        snapshots: SnapshotStore,
        event_bus: EventBus,
        result_store: ResultStore,
        scheduler: LocalResourceController,
        mode: RuntimeMode = RuntimeMode.ONLINE,
        default_timeout_seconds: int = 60,
        result_cache_entries: int = 64,
        worker_pool_size: int = 0,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.snapshots = snapshots
        self.event_bus = event_bus
        self.result_store = result_store
        self.scheduler = scheduler
        self.mode = mode
        self.default_timeout_seconds = default_timeout_seconds

        self.validator = DependencyValidator(snapshots)
        self.result_cache = ResultCache(max_entries=result_cache_entries)
        self.worker_pool_size = max(0, int(worker_pool_size))
        self._pool_lock = threading.Lock()
        self._idle_workers: list[WorkerHandle] = []
        self._worker_handles: set[WorkerHandle] = set()
        #: set once the pool warm up finished (or failed)
        self._pool_warmup_done = threading.Event()
        #: set when the runtime shuts down - stops the warm up thread
        self._pool_stopped = threading.Event()
        #: incremented on every shutdown.  A worker that was created under an
        #: older epoch and registers itself afterwards is terminated at once -
        #: otherwise it would survive the shutdown of the runtime.
        self._pool_epoch = 0
        self._pool_warmup_thread: threading.Thread | None = None
        self._shutdown_done = False
        self._resource_monitor = ResourceMonitor(repository=repository, interval=0.05)

        self._lock = asyncio.Lock()
        self._calls: dict[str, LogicalCall] = {}
        self._executions: dict[str, PhysicalExecution] = {}
        self._groups: dict[str, ShareGroup] = {}
        self._groups_by_key: dict[str, list[str]] = {}
        self._workflow_modes: dict[str, RuntimeMode] = {}
        self._tasks: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closing = False

    # ------------------------------------------------------------------
    # workflow modes
    # ------------------------------------------------------------------
    def register_workflow_mode(self, workflow_id: str, mode: RuntimeMode) -> None:
        """Bind an execution mode to a workflow (baseline / online / deferred)."""

        self._workflow_modes[workflow_id] = mode

    def mode_for(self, workflow_id: str | None) -> RuntimeMode:
        if workflow_id is not None:
            mode = self._workflow_modes.get(workflow_id)
            if mode is not None:
                return mode
        return self.mode

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    async def shutdown(self) -> None:
        """Terminate every running worker and release the resources."""

        if self._shutdown_done:
            return
        self._shutdown_done = True
        self._closing = True
        # Stop the background warm up first and invalidate the workers that are
        # created at this moment: a worker that registers itself after the
        # sweep would survive the shutdown.
        with self._pool_lock:
            self._pool_epoch += 1
            self._pool_stopped.set()
        thread = self._pool_warmup_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)
        self._pool_warmup_thread = None

        for execution in list(self._executions.values()):
            if execution.status is ExecutionStatus.RUNNING:
                await self._terminate_execution(execution, "runtime shutdown")

        # Two sweeps: the second one catches the workers that were registered
        # while the first sweep was running.
        for _ in range(2):
            with self._pool_lock:
                handles = list(self._worker_handles)
                self._idle_workers.clear()
                self._worker_handles.clear()
            for handle in handles:
                handle.terminate()

        survivors = [pid for pid in self.worker_pids() if pid]
        if survivors:
            self.event_bus.publish(
                EventType.RESOURCE_LEAK_WARNING,
                metadata={
                    "surviving_workers": survivors,
                    "scope": "worker processes that survived the runtime shutdown",
                },
            )

        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def worker_pids(self) -> list[int]:
        """The pids of the workers the runtime currently owns."""

        with self._pool_lock:
            return [
                handle.process.pid
                for handle in self._worker_handles
                if handle.process is not None and handle.process.pid
            ]

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def submit_tool_call(
        self,
        *,
        workflow_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        snapshot_id: str,
        invocation_id: str | None = None,
        agent_id: str | None = None,
        tool_version: str | None = None,
        timeout_seconds: float | None = None,
    ) -> ToolCallResult:
        """Execute (or share) one logical tool call."""

        spec = self.registry.get(tool_name, tool_version)
        normalized = self._normalize_arguments(spec, arguments)
        arguments_hash = hash_arguments(normalized)
        candidate_key = compute_candidate_key(spec, normalized)

        call = LogicalCall(
            logical_call_id=new_id("call"),
            workflow_id=workflow_id,
            tool_name=spec.tool_name,
            tool_version=spec.tool_version,
            arguments=normalized,
            arguments_hash=arguments_hash,
            candidate_key=candidate_key,
            snapshot_id=snapshot_id,
            invocation_id=invocation_id,
            agent_id=agent_id,
            timeout_seconds=timeout_seconds,
        )
        self._calls[call.logical_call_id] = call
        self._persist_call(call)
        requested_event = self.event_bus.publish(
            EventType.TOOL_CALL_REQUESTED,
            workflow_id=workflow_id,
            agent_id=agent_id,
            invocation_id=invocation_id,
            logical_call_id=call.logical_call_id,
            metadata={
                "tool_name": spec.tool_name,
                "tool_version": spec.tool_version,
                "candidate_key": candidate_key,
                "snapshot_id": snapshot_id,
                "shareable": spec.shareable,
                "mode": str(self.mode),
            },
        )
        # everything that happens for this call is a child of the request
        call.request_event_id = requested_event.event_id

        mode = self.mode_for(workflow_id)
        sharing_enabled = mode is not RuntimeMode.BASELINE and spec.shareable
        if sharing_enabled:
            served = await self._serve_from_cache(call)
            if served:
                return await call.future
            await self._enter_share_flow(call)
        else:
            async with self._lock:
                self._start_independent_locked(call)

        return await call.future

    async def cancel_logical_call(self, logical_call_id: str, reason: str = "cancelled") -> bool:
        """Cancel a logical call (follower, leader or independent)."""

        terminate: list[PhysicalExecution] = []
        async with self._lock:
            call = self._calls.get(logical_call_id)
            if call is None or call.future.done():
                return False
            call.status = LogicalCallStatus.CANCELLED
            call.error = reason
            call.completed_time = time.time()
            group = self._groups.get(call.share_group_id) if call.share_group_id else None
            if group is not None:
                participant = group.participants.get(logical_call_id)
                if participant is not None and participant.status not in (
                    ParticipantStatus.COMPLETED,
                    ParticipantStatus.FAILED,
                    ParticipantStatus.CANCELLED,
                ):
                    participant.transition(ParticipantStatus.CANCELLED)
                    participant.detached_time = time.time()
                    participant.detach_reason = reason
                    self._persist_participant(group, participant)
                if not group.has_live_participants():
                    execution = self._executions.get(group.leader_execution_id)
                    if execution is not None and execution.status is ExecutionStatus.RUNNING:
                        terminate.append(execution)
            self._persist_call(call)
            self.event_bus.publish(
                EventType.TOOL_CALL_CANCELLED,
                workflow_id=call.workflow_id,
                agent_id=call.agent_id,
                invocation_id=call.invocation_id,
                logical_call_id=call.logical_call_id,
                physical_execution_id=call.physical_execution_id,
                parent_event_id=call.request_event_id,
                metadata={"reason": reason},
            )
            if not call.future.done():
                call.future.set_exception(asyncio.CancelledError(reason))
        for execution in terminate:
            await self._terminate_execution(execution, "all participants cancelled")
        return True

    # ------------------------------------------------------------------
    # inspection
    # ------------------------------------------------------------------
    def get_call(self, logical_call_id: str) -> LogicalCall | None:
        return self._calls.get(logical_call_id)

    def get_execution(self, execution_id: str) -> PhysicalExecution | None:
        return self._executions.get(execution_id)

    def get_group(self, share_group_id: str) -> ShareGroup | None:
        return self._groups.get(share_group_id)

    def group_of_execution(self, execution_id: str) -> ShareGroup | None:
        execution = self._executions.get(execution_id)
        if execution is None or not execution.share_group_id:
            return None
        return self._groups.get(execution.share_group_id)

    def active_calls(self, workflow_id: str | None = None) -> list[LogicalCall]:
        return [
            call
            for call in self._calls.values()
            if workflow_id is None or call.workflow_id == workflow_id
        ]

    def calls_for_invocation(self, invocation_id: str) -> list[LogicalCall]:
        """Return every logical call that was issued by one invocation."""

        return [
            call for call in self._calls.values() if call.invocation_id == invocation_id
        ]

    def active_executions(self, workflow_id: str | None = None) -> list[PhysicalExecution]:
        return [
            execution
            for execution in self._executions.values()
            if workflow_id is None or execution.workflow_id == workflow_id
        ]

    def active_groups(self, workflow_id: str | None = None) -> list[ShareGroup]:
        return [
            group
            for group in self._groups.values()
            if workflow_id is None or group.workflow_id == workflow_id
        ]

    def live_worker_count(self) -> int:
        """Return the number of worker processes that are currently alive.

        The count covers the workers of the warm pool and the workers that
        belong to a running execution.  The benchmark uses it to prove that
        the repeated runs do not leak processes.
        """

        with self._pool_lock:
            idle = [handle for handle in self._idle_workers if handle.is_alive()]
        running = {
            handle.worker_pid or getattr(handle.process, "pid", None)
            for handle in self._live_worker_handles()
            if handle.is_alive()
        }
        return len(running) + sum(
            1
            for handle in idle
            if (handle.worker_pid or getattr(handle.process, "pid", None)) not in running
        )

    def _live_worker_handles(self) -> list[Any]:
        """Return the worker handles of the running executions."""

        handles = []
        for execution in self._executions.values():
            handle = getattr(execution, "worker_handle", None)
            if handle is not None:
                handles.append(handle)
        return handles

    def live_snapshot(self, workflow_id: str | None = None) -> dict[str, Any]:
        groups = self.active_groups(workflow_id)
        return {
            "mode": str(self.mode),
            "logical_calls": len(self.active_calls(workflow_id)),
            "physical_executions": len(self.active_executions(workflow_id)),
            "share_groups": len(groups),
            "shared_waiting": sum(
                1
                for group in groups
                for participant in group.participants.values()
                if participant.status is ParticipantStatus.SHARED_WAIT
            ),
            "detached": sum(
                1
                for group in groups
                for participant in group.participants.values()
                if participant.status is ParticipantStatus.DETACHED
            ),
            "resources": self.scheduler.snapshot(),
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _ledger_of(self, group: ShareGroup) -> list[DependencyRecord]:
        """Return the dependency ledger of the group's leader execution."""

        execution = self._executions.get(group.leader_execution_id)
        if execution is None:
            return []
        return execution.ledger.records()

    def _snapshot_is_trustworthy(self, snapshot_id: str, force: bool = False) -> bool:
        """Check that a snapshot still matches its manifest."""

        try:
            return self.snapshots.check_integrity(snapshot_id, force=force)
        except Exception:  # pragma: no cover - defensive
            return False

    def _normalize_arguments(self, spec: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        normalizer = getattr(spec, "argument_normalizer", None)
        if callable(normalizer):
            arguments = normalizer(arguments)
        return json.loads(canonical_json(arguments))

    # ------------------------------------------------------------------
    # share flow
    # ------------------------------------------------------------------
    def _snapshot_content_hash(self, snapshot_id: str) -> str | None:
        """The content hash of a snapshot (identical content -> identical hash)."""

        try:
            return self.snapshots.get(snapshot_id).manifest_hash
        except Exception:
            return None

    async def _serve_from_cache(self, call: LogicalCall) -> bool:
        """Serve a finished result from the session cache (fully validated)."""

        snapshot_hash = self._snapshot_content_hash(call.snapshot_id)
        if snapshot_hash is None:
            return False
        async with self._lock:
            entry = self.result_cache.get(call.candidate_key, snapshot_hash)
            if entry is None:
                return False
            # The cached result is only served when every dependency of the
            # original execution still matches the caller's own snapshot.
            status, reason, _matched = self.validator.validate_prefix(
                call.snapshot_id, entry.dependencies
            )
            if status is not ValidationStatus.MATCH:
                self.result_cache.invalidate(call.candidate_key)
                return False
            delivered = self._deliver_locked(
                call,
                entry.payload,
                entry.result_hash,
                entry.execution_id,
                cached=True,
            )
            return delivered

    async def _enter_share_flow(self, call: LogicalCall) -> None:
        """Try to attach the call to a running share group, otherwise lead a new one."""

        mode = self.mode_for(call.workflow_id)
        async with self._lock:
            if not self._snapshot_is_trustworthy(call.snapshot_id):
                # A snapshot that was modified outside of AgentFuse must not
                # take part in a shared execution - the dependency fingerprints
                # would no longer describe the state the tool actually read.
                self._start_independent_locked(call)
                return
            group_ids = list(self._groups_by_key.get(call.candidate_key, ()))
            for group_id in reversed(group_ids):
                group = self._groups.get(group_id)
                if group is None or group.status not in (
                    ShareGroupStatus.CREATED,
                    ShareGroupStatus.EXECUTING,
                ):
                    continue
                if mode is RuntimeMode.DEFERRED:
                    self._publish_candidate_found(call, group, joinable=True, matched=0)
                    self._attach_participant(group, call, validated_prefix=0)
                    break
                status, reason, matched = self.validator.validate_prefix(
                    call.snapshot_id, self._ledger_of(group)
                )
                self._publish_candidate_found(
                    call,
                    group,
                    joinable=status is ValidationStatus.MATCH,
                    matched=matched,
                    validation_status=status,
                )
                if status is ValidationStatus.MATCH:
                    self._attach_participant(group, call, validated_prefix=matched)
                    break
                participant = Participant(
                    logical_call_id=call.logical_call_id,
                    agent_id=call.agent_id,
                    invocation_id=call.invocation_id,
                    snapshot_id=call.snapshot_id,
                    status=ParticipantStatus.WAITING,
                )
                self._record_validation(
                    group,
                    participant,
                    status,
                    reason,
                    dependency_sequence=matched,
                    execution_id=group.leader_execution_id,
                )
                self.event_bus.publish(
                    EventType.SHARE_VALIDATION_FAILED,
                    workflow_id=call.workflow_id,
                    agent_id=call.agent_id,
                    invocation_id=call.invocation_id,
                    logical_call_id=call.logical_call_id,
                    physical_execution_id=group.leader_execution_id,
                    parent_event_id=call.request_event_id,
                    metadata={
                        "share_group_id": group.share_group_id,
                        "reason": reason,
                        "status": str(status),
                    },
                )
                # The candidate was rejected.  The rejection is recorded as a
                # detached participant - otherwise the database would not show
                # that this call was offered to this group and why it was
                # rejected.
                self._record_rejected_participant(group, participant, status, reason)
            else:
                self._create_group_locked(call)

    def _record_rejected_participant(
        self,
        group: ShareGroup,
        participant: Participant,
        status: ValidationStatus,
        reason: str | None,
    ) -> None:
        """Record a candidate that was rejected by the dependency validation.

        The call never became a participant of the group - it runs on its own.
        The rejection is nevertheless part of the audit trail: the group has to
        show that the call was offered to it and that the validation refused
        the join.
        """

        participant.transition(ParticipantStatus.DETACHED)
        participant.detached_time = time.time()
        participant.detach_reason = reason or "mismatch"
        # the candidate never joined - the validation refused it right away
        participant.detach_kind = "rejected_at_join"
        participant.validation_status = status
        group.add_participant(participant)
        self._persist_participant(group, participant)
        self._persist_group(group)
        call = self._calls.get(participant.logical_call_id)
        self.event_bus.publish(
            EventType.SHARE_PARTICIPANT_DETACHED,
            workflow_id=group.workflow_id,
            agent_id=participant.agent_id,
            invocation_id=participant.invocation_id,
            logical_call_id=participant.logical_call_id,
            physical_execution_id=group.leader_execution_id,
            parent_event_id=(call.request_event_id if call else None),
            metadata={
                "share_group_id": group.share_group_id,
                "reason": participant.detach_reason,
                "validated_prefix_length": participant.validated_prefix_length,
                "rejected_at_join": True,
            },
        )

    def _publish_candidate_found(
        self,
        call: LogicalCall,
        group: ShareGroup,
        joinable: bool,
        matched: int,
        validation_status: ValidationStatus | None = None,
    ) -> None:
        """Publish the fact that a potential sharing candidate was identified.

        The event states that a logical call *could* share an execution with an
        already running group.  It does **not** claim that the results are
        equivalent - that is decided by the dependency validation.
        """

        self.event_bus.publish(
            EventType.SHARE_CANDIDATE_FOUND,
            workflow_id=call.workflow_id,
            agent_id=call.agent_id,
            invocation_id=call.invocation_id,
            logical_call_id=call.logical_call_id,
            physical_execution_id=group.leader_execution_id or None,
            metadata={
                "candidate_key": call.candidate_key,
                "share_group_id": group.share_group_id,
                "tool_name": call.tool_name,
                "joinable": joinable,
                "matched_prefix_length": matched,
                "validation_status": str(validation_status) if validation_status else None,
                "mode": str(self.mode_for(call.workflow_id)),
            },
        )

    def _create_group_locked(self, call: LogicalCall) -> None:
        mode = self.mode_for(call.workflow_id)
        group = ShareGroup(
            share_group_id=new_id("sg"),
            workflow_id=call.workflow_id,
            candidate_key=call.candidate_key,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            leader_execution_id=None,
            leader_logical_call_id=call.logical_call_id,
            status=ShareGroupStatus.CREATED,
            mode=str(mode),
        )
        leader = Participant(
            logical_call_id=call.logical_call_id,
            agent_id=call.agent_id,
            invocation_id=call.invocation_id,
            snapshot_id=call.snapshot_id,
            status=ParticipantStatus.RUNNING_INDEPENDENTLY,
            validation_status=ValidationStatus.MATCH,
            is_leader=True,
        )
        group.add_participant(leader)
        self._groups[group.share_group_id] = group
        self._groups_by_key.setdefault(call.candidate_key, []).append(group.share_group_id)
        call.share_group_id = group.share_group_id
        call.status = LogicalCallStatus.RUNNING_INDEPENDENTLY
        self._persist_call(call)
        self._persist_group(group)
        self._persist_participant(group, leader)

        execution = self._create_execution_locked(call, share_group_id=group.share_group_id)
        group.leader_execution_id = execution.execution_id
        self._persist_group(group)
        self._schedule_execution(execution)
        self._publish_candidate_found(call, group, joinable=True, matched=0)

    def _wait_reason(self, group: ShareGroup, validated_prefix: int) -> str:
        """Explain why the participant has to wait.

        The reason is shown in the dashboard - a waiting call must be
        explainable without reading the event log.
        """

        if group.status is ShareGroupStatus.CREATED:
            return "等待 Leader 启动物理执行"
        if group.status is ShareGroupStatus.EXECUTING:
            return "等待 Leader 的物理执行完成"
        if group.status is ShareGroupStatus.VALIDATING:
            return "等待 Leader 结果的依赖校验完成"
        if group.status is ShareGroupStatus.COMPLETED:
            return "等待结果下发"
        if validated_prefix >= 0:
            return f"已确认前 {validated_prefix} 项依赖，等待 Leader 完成"
        return "等待 Leader 完成"

    def _attach_participant(
        self, group: ShareGroup, call: LogicalCall, validated_prefix: int
    ) -> Participant:
        wait_reason = self._wait_reason(group, validated_prefix)
        participant = Participant(
            logical_call_id=call.logical_call_id,
            agent_id=call.agent_id,
            invocation_id=call.invocation_id,
            snapshot_id=call.snapshot_id,
            status=ParticipantStatus.SHARED_WAIT,
            validated_prefix_length=validated_prefix,
            validation_status=(
                ValidationStatus.MATCH if validated_prefix >= 0 else ValidationStatus.PENDING
            ),
            wait_reason=wait_reason,
        )
        group.add_participant(participant)
        call.share_group_id = group.share_group_id
        call.status = LogicalCallStatus.SHARED_WAIT
        call.shared = True
        self._persist_call(call)
        self._persist_participant(group, participant)
        self._persist_group(group)
        self.event_bus.publish(
            EventType.TOOL_CALL_WAITING,
            workflow_id=call.workflow_id,
            agent_id=call.agent_id,
            invocation_id=call.invocation_id,
            logical_call_id=call.logical_call_id,
            physical_execution_id=group.leader_execution_id,
            parent_event_id=call.request_event_id,
            metadata={
                "share_group_id": group.share_group_id,
                "candidate_key": call.candidate_key,
                "validated_prefix_length": validated_prefix,
                "wait_reason": wait_reason,
                "mode": str(self.mode),
            },
        )
        return participant

    # ------------------------------------------------------------------
    # execution creation / scheduling
    # ------------------------------------------------------------------
    def _create_execution_locked(
        self, call: LogicalCall, share_group_id: str | None = None
    ) -> PhysicalExecution:
        execution = PhysicalExecution(
            execution_id=new_id("exec"),
            workflow_id=call.workflow_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            arguments=call.arguments,
            arguments_hash=call.arguments_hash,
            candidate_key=call.candidate_key,
            snapshot_id=call.snapshot_id,
            share_group_id=share_group_id,
            leader_call_id=call.logical_call_id,
            timeout_seconds=float(self.default_timeout_seconds),
            attempt=call.retry_count + 1,
        )
        if call.timeout_seconds is not None:
            execution.timeout_seconds = float(call.timeout_seconds)
        execution.ledger = DependencyLedger(execution.execution_id, call.snapshot_id)
        self._executions[execution.execution_id] = execution
        call.physical_execution_id = execution.execution_id
        self._persist_call_with_execution(call, execution)
        return execution

    def _start_independent_locked(self, call: LogicalCall) -> None:
        call.status = LogicalCallStatus.RUNNING_INDEPENDENTLY
        execution = self._create_execution_locked(call, share_group_id=None)
        self._persist_call(call)
        self._schedule_execution(execution)

    def _schedule_execution(self, execution: PhysicalExecution) -> None:
        task = asyncio.create_task(self._run_execution(execution))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_execution(self, execution: PhysicalExecution) -> None:
        spec = self.registry.get(execution.tool_name, execution.tool_version)
        try:
            reason = await self.scheduler.acquire(
                execution.execution_id,
                spec.estimated_memory_mb,
                spec.estimated_cpu_seconds,
            )
            execution.admission_reason = reason
            execution.queued_time = round(max(0.0, time.time() - execution.created_at), 6)
            self._persist_execution(execution)
            if execution.terminated:
                return
            await self._spawn_worker(execution)
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception as exc:  # pragma: no cover - defensive
            if not execution.outcome.done():
                execution.outcome.set_result(
                    ExecutionOutcome(ok=False, kind=ERROR_CRASH, message=str(exc))
                )
        try:
            outcome = await execution.outcome
        except asyncio.CancelledError:  # pragma: no cover
            raise
        await self._finalize_execution(execution, outcome)

    async def _spawn_worker(self, execution: PhysicalExecution) -> None:
        try:
            tree = self.snapshots.get(execution.snapshot_id).tree_path
        except Exception as exc:
            if not execution.outcome.done():
                execution.outcome.set_result(
                    ExecutionOutcome(ok=False, kind=ERROR_CRASH, message=str(exc))
                )
            return

        payload = {
            "tool_name": execution.tool_name,
            "tool_version": execution.tool_version,
            "arguments": execution.arguments,
            "snapshot_root": str(tree),
            "snapshot_id": execution.snapshot_id,
        }

        self._loop = self._loop or asyncio.get_running_loop()
        handle = self._acquire_worker()
        if handle is None:
            # The runtime is shutting down - no computation must be started.
            if not execution.outcome.done():
                execution.outcome.set_result(
                    ExecutionOutcome(
                        ok=False,
                        kind=ERROR_CANCELLED,
                        message="the runtime is shutting down",
                    )
                )
            return
        execution.worker_handle = handle
        execution.process = handle.process
        execution.pid = handle.process.pid
        # The launcher and the process that really executes the tool can have
        # different pids - the real one is reported by the worker itself.
        execution.worker_root_pid = handle.worker_pid or handle.process.pid
        execution.worker_command = _worker_command(handle)
        handle.assign(execution.execution_id)
        try:
            handle.send(payload)
        except Exception as exc:
            handle.terminate()
            if not execution.outcome.done():
                execution.outcome.set_result(
                    ExecutionOutcome(
                        ok=False, kind=ERROR_CRASH, message=f"cannot start worker: {exc}"
                    )
                )
            return

        execution.status = ExecutionStatus.RUNNING
        execution.start_time = time.time()

        group = self._groups.get(execution.share_group_id) if execution.share_group_id else None
        if group is not None and group.status is ShareGroupStatus.CREATED:
            group.transition(ShareGroupStatus.EXECUTING)

        leader_call = (
            self._calls.get(execution.leader_call_id) if execution.leader_call_id else None
        )
        started_event = self.event_bus.publish(
            EventType.TOOL_EXECUTION_STARTED,
            workflow_id=execution.workflow_id,
            physical_execution_id=execution.execution_id,
            logical_call_id=execution.leader_call_id,
            parent_event_id=leader_call.request_event_id if leader_call else None,
            metadata={
                "tool_name": execution.tool_name,
                "tool_version": execution.tool_version,
                "pid": execution.pid,
                "snapshot_id": execution.snapshot_id,
                "share_group_id": execution.share_group_id,
                "timeout_seconds": execution.timeout_seconds,
                "pooled": handle.jobs_served > 1,
            },
        )
        execution.start_event_id = started_event.event_id
        self._persist_execution(execution)
        execution.timeout_task = asyncio.create_task(self._timeout_watch(execution))

    # ------------------------------------------------------------------
    # worker pool
    # ------------------------------------------------------------------
    def _acquire_worker(self) -> WorkerHandle | None:
        """Take an idle worker from the pool or start a new one.

        The pool warm up runs in the background.  A worker is only started
        outside of the pool when the warm up did not deliver one yet - waiting
        for the warm up keeps the reuse of the workers deterministic.

        Returns ``None`` when the runtime is shutting down: the caller must not
        start a computation then.
        """

        self._wait_for_pool_warmup()
        self._trim_idle_pool()
        with self._pool_lock:
            if self._pool_stopped.is_set():
                return None
            epoch = self._pool_epoch
            while self._idle_workers:
                handle = self._idle_workers.pop()
                if handle.is_alive():
                    return handle
                handle.terminate()
                self._worker_handles.discard(handle)

        handle = start_worker()
        with self._pool_lock:
            # The worker was created while the runtime could have been shut
            # down in the meantime.  The epoch check makes sure that such a
            # worker is terminated instead of surviving the shutdown.
            stale = self._pool_stopped.is_set() or self._pool_epoch != epoch
            if not stale:
                self._worker_handles.add(handle)
        if stale:
            handle.terminate()
            return None
        self._start_worker_threads(handle)
        return handle

    def _trim_idle_pool(self) -> None:
        """Terminate the idle workers that exceed the current pool size.

        The pool size can be changed at runtime (for example by the
        benchmark API).  Workers that were started for a larger pool must not
        survive that change.
        """

        evicted: list[WorkerHandle] = []
        with self._pool_lock:
            limit = max(0, self.worker_pool_size)
            while len(self._idle_workers) > limit:
                handle = self._idle_workers.pop(0)
                self._worker_handles.discard(handle)
                evicted.append(handle)
        for handle in evicted:
            handle.terminate()

    def _wait_for_pool_warmup(self, timeout: float = 30.0) -> None:
        """Wait until the background warm up finished (bounded)."""

        if not self.worker_pool_size:
            return
        if self._pool_warmup_done.is_set():
            return
        # The wait happens outside of the pool lock on purpose: the warm up
        # thread needs that lock to publish its workers.
        self._pool_warmup_done.wait(timeout)

    def _release_worker(self, handle: WorkerHandle | None, reusable: bool = True) -> None:
        """Return a worker to the pool or terminate it."""

        if handle is None:
            return
        handle.release()
        if not reusable or not handle.is_alive():
            handle.terminate()
            with self._pool_lock:
                self._worker_handles.discard(handle)
            return
        evicted: WorkerHandle | None = None
        with self._pool_lock:
            if self.worker_pool_size:
                self._idle_workers.append(handle)
                if len(self._idle_workers) > self.worker_pool_size:
                    # The pool is full: the *oldest* idle worker is evicted so
                    # that the worker which just finished a job stays warm.
                    evicted = self._idle_workers.pop(0)
            else:
                evicted = handle
        if evicted is not None:
            evicted.terminate()
            with self._pool_lock:
                self._worker_handles.discard(evicted)

    def prewarm_workers(self) -> None:
        """Start the pool workers in the background.

        The interpreter start up of a worker is paid while the runtime is idle
        instead of blocking the first tool call.
        """

        if not self.worker_pool_size:
            self._pool_warmup_done.set()
            return

        def warm() -> None:
            try:
                for _ in range(self.worker_pool_size):
                    with self._pool_lock:
                        if self._pool_stopped.is_set():
                            return
                        epoch = self._pool_epoch
                    handle = None
                    try:
                        handle = start_worker()
                        with self._pool_lock:
                            stale = (
                                self._pool_stopped.is_set() or self._pool_epoch != epoch
                            )
                            if not stale:
                                self._worker_handles.add(handle)
                        if stale:
                            # the runtime is shutting down - do not leak it
                            handle.terminate()
                            return
                        self._start_worker_threads(handle)
                        with self._pool_lock:
                            # the pool size can be changed while the warm up
                            # runs - the idle list must respect the limit
                            if (
                                handle.is_alive()
                                and not self._pool_stopped.is_set()
                                and len(self._idle_workers) < self.worker_pool_size
                            ):
                                self._idle_workers.append(handle)
                            else:
                                handle.terminate()
                                self._worker_handles.discard(handle)
                    except Exception:  # pragma: no cover - defensive
                        if handle is not None:
                            handle.terminate()
                        return
            finally:
                self._pool_warmup_done.set()

        thread = threading.Thread(target=warm, name="agentfuse-pool-warmup", daemon=True)
        self._pool_warmup_thread = thread
        thread.start()

    def _start_worker_threads(self, handle: WorkerHandle) -> None:
        """Start the protocol threads of a worker (once per process)."""

        if handle.threads_started:
            return
        handle.threads_started = True
        for target, name in (
            (self._reader_loop, "agentfuse-reader"),
            (self._stderr_loop, "agentfuse-stderr"),
            (self._monitor_loop, "agentfuse-monitor"),
        ):
            threading.Thread(
                target=target,
                args=(handle,),
                name=f"{name}-{handle.process.pid}",
                daemon=True,
            ).start()

    # ------------------------------------------------------------------
    # worker plumbing
    # ------------------------------------------------------------------
    def _reader_loop(self, handle: WorkerHandle) -> None:
        """Read the protocol messages of one worker process."""

        process = handle.process
        stdout = getattr(process, "stdout", None)
        saw_result = False
        try:
            if stdout is not None:
                while True:
                    line = stdout.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        message = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(message, dict):
                        continue
                    if message.get("t") == MSG_HELLO:
                        # The worker tells us which process really executes
                        # the tool - it can differ from the launcher pid.
                        real_pid = int(message.get("pid") or 0) or None
                        handle.worker_pid = real_pid
                        execution_id = handle.current_execution_id
                        execution = self._executions.get(execution_id) if execution_id else None
                        if execution is not None and real_pid:
                            execution.worker_root_pid = real_pid
                            self._persist_execution(execution)
                        continue
                    if message.get("t") in (MSG_RESULT, MSG_ERROR):
                        saw_result = True
                    execution_id = handle.current_execution_id
                    if execution_id:
                        self._dispatch(execution_id, message)
        except Exception:  # pragma: no cover - defensive
            pass
        finally:
            handle.reusable = False
            # Close the pipe explicitly: a leaked file handle per execution
            # would exhaust the process handle limit on a long running runtime.
            try:
                if stdout is not None:
                    stdout.close()
            except Exception:  # pragma: no cover - defensive
                pass
            execution_id = handle.current_execution_id
            if execution_id:
                self._dispatch(execution_id, {"t": MSG_EOF, "saw_result": saw_result})

    def _stderr_loop(self, handle: WorkerHandle) -> None:
        """Collect the stderr output of a worker (diagnostics only)."""

        stderr = getattr(handle.process, "stderr", None)
        if stderr is None:
            return
        chunks: list[str] = []
        try:
            for line in stderr:
                chunks.append(line)
                if len(chunks) > 200:
                    del chunks[0]
        except Exception:  # pragma: no cover - defensive
            pass
        finally:
            try:
                stderr.close()
            except Exception:  # pragma: no cover - defensive
                pass
            handle.stderr_output = "".join(chunks)[-8000:]

    def _monitor_loop(self, handle: WorkerHandle) -> None:
        """Sample the memory of a worker while it serves executions."""

        if psutil is None or handle.process.pid is None:
            return
        while not self._closing:
            rss = self._resource_monitor.sample(self._worker_pid(handle))
            if rss is None:
                break
            execution_id = handle.current_execution_id
            execution = self._executions.get(execution_id) if execution_id else None
            if execution is not None:
                if rss > execution.peak_rss:
                    execution.peak_rss = rss
                self.scheduler.record_rss(execution.execution_id, rss)
                self._resource_monitor.record(
                    execution.workflow_id, execution.execution_id, rss
                )
                self._warn_about_resource_limit(execution, rss)
            if execution is None and not handle.is_alive():
                break
            time.sleep(self._resource_monitor.interval)

    def _sample_final_rss(self, execution: PhysicalExecution) -> None:
        """Read the memory of the worker once more when the execution ends.

        Short executions can finish between two samples of the monitor thread.
        The worker is still alive at this point, therefore the final reading
        gives the real peak of the tool process.
        """

        handle = execution.worker_handle
        if handle is None or not handle.is_alive():
            return
        candidates = [
            self._worker_pid(handle),
            getattr(handle.process, "pid", None),
            *list(getattr(handle, "child_pids", ()) or ()),
        ]
        rss = 0
        for pid in candidates:
            if not pid:
                continue
            rss = self._resource_monitor.sample(pid)
            if rss:
                break
        if not rss:
            return
        if rss > execution.peak_rss:
            execution.peak_rss = rss
        self.scheduler.record_rss(execution.execution_id, rss)
        self._resource_monitor.record(execution.workflow_id, execution.execution_id, rss)

    @staticmethod
    def _worker_pid(handle: Any) -> int | None:
        """The pid of the process that really executes the tool."""

        if handle is None:
            return None
        return getattr(handle, "worker_pid", None) or getattr(
            handle.process, "pid", None
        )

    def _warn_about_resource_limit(self, execution: PhysicalExecution, rss: int) -> None:
        """Raise a warning when the observed memory exceeds the soft limit."""

        limit = self.scheduler.effective_rss_limit_mb
        if not limit:
            return
        rss_mb = rss / (1024 * 1024)
        if rss_mb <= limit or execution.resource_warning_emitted:
            return
        execution.resource_warning_emitted = True
        self.event_bus.publish(
            EventType.RESOURCE_LIMIT_WARNING,
            workflow_id=execution.workflow_id,
            physical_execution_id=execution.execution_id,
            logical_call_id=execution.leader_call_id,
            metadata={
                "observed_rss_mb": round(rss_mb, 2),
                "limit_mb": limit,
                "scope": "observed RSS (not an OS hard limit)",
            },
        )

    def _dispatch(self, execution_id: str, message: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._on_worker_message, execution_id, message)
        except RuntimeError:  # pragma: no cover - loop already closed
            pass

    def _on_worker_message(self, execution_id: str, message: dict[str, Any]) -> None:
        kind = message.get("t")
        if kind == MSG_DEPENDENCY:
            self._spawn(self._handle_dependency(execution_id, message))
        elif kind == MSG_RESULT:
            self._spawn(self._handle_result(execution_id, message))
        elif kind == MSG_ERROR:
            self._spawn(self._handle_error(execution_id, message))
        elif kind == MSG_EOF:
            self._spawn(self._handle_eof(execution_id, message))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------------
    # worker event handling
    # ------------------------------------------------------------------
    async def _handle_dependency(self, execution_id: str, message: dict[str, Any]) -> None:
        async with self._lock:
            execution = self._executions.get(execution_id)
            if execution is None or execution.status is not ExecutionStatus.RUNNING:
                return
            record = DependencyRecord(
                execution_id=execution.execution_id,
                sequence_number=len(execution.ledger),
                dependency_type=str(message.get("dependency_type")),
                relative_path=str(message.get("relative_path")),
                fingerprint=message.get("fingerprint"),
                observed_time=time.time(),
                snapshot_id=execution.snapshot_id,
            )
            execution.ledger.append_record(record)
            self.repository.save_dependency(record)
            self.event_bus.publish(
                EventType.TOOL_READ_OBSERVED,
                workflow_id=execution.workflow_id,
                physical_execution_id=execution.execution_id,
                logical_call_id=execution.leader_call_id,
                parent_event_id=execution.start_event_id,
                metadata={
                    "dependency_type": record.dependency_type,
                    "relative_path": record.relative_path,
                    "fingerprint": record.fingerprint,
                    "sequence_number": record.sequence_number,
                },
            )
            group = self._groups.get(execution.share_group_id) if execution.share_group_id else None
            if group is None or group.status not in (
                ShareGroupStatus.CREATED,
                ShareGroupStatus.EXECUTING,
            ):
                return
            if self.mode_for(execution.workflow_id) is RuntimeMode.DEFERRED:
                # Deferred mode intentionally postpones the comparison until the
                # leader finished - used to measure the value of online checks.
                return
            for participant in list(group.participants.values()):
                if participant.is_leader:
                    continue
                if participant.status is not ParticipantStatus.SHARED_WAIT:
                    continue
                status, reason = self.validator.compare(participant.snapshot_id, record)
                participant.validation_status = status
                self._record_validation(
                    group,
                    participant,
                    status,
                    reason,
                    dependency_sequence=record.sequence_number,
                    execution_id=execution.execution_id,
                )
                if status is ValidationStatus.MATCH:
                    participant.validated_prefix_length += 1
                    self.event_bus.publish(
                        EventType.SHARE_VALIDATION_PASSED,
                        workflow_id=execution.workflow_id,
                        agent_id=participant.agent_id,
                        invocation_id=participant.invocation_id,
                        logical_call_id=participant.logical_call_id,
                        physical_execution_id=execution.execution_id,
                        metadata={
                            "share_group_id": group.share_group_id,
                            "dependency_sequence": record.sequence_number,
                            "relative_path": record.relative_path,
                        },
                    )
                else:
                    self.event_bus.publish(
                        EventType.SHARE_VALIDATION_FAILED,
                        workflow_id=execution.workflow_id,
                        agent_id=participant.agent_id,
                        invocation_id=participant.invocation_id,
                        logical_call_id=participant.logical_call_id,
                        physical_execution_id=execution.execution_id,
                        metadata={
                            "share_group_id": group.share_group_id,
                            "dependency_sequence": record.sequence_number,
                            "relative_path": record.relative_path,
                            "reason": reason,
                            "status": str(status),
                        },
                    )
                    self._detach_participant_locked(group, participant, reason or "mismatch")

    async def _handle_result(self, execution_id: str, message: dict[str, Any]) -> None:
        execution = self._executions.get(execution_id)
        if execution is None or execution.outcome.done():
            return
        bypass = list(message.get("dependency_bypass") or [])
        if bypass:
            self.event_bus.publish(
                EventType.DEPENDENCY_BYPASS,
                workflow_id=execution.workflow_id,
                physical_execution_id=execution.execution_id,
                logical_call_id=execution.leader_call_id,
                metadata={
                    "paths": bypass[:10],
                    "tool_name": execution.tool_name,
                    "scope": "the tool read the workspace without the controlled reader",
                },
            )
        execution.outcome.set_result(
            ExecutionOutcome(
                ok=True,
                payload=message.get("payload"),
                result_hash=message.get("result_hash"),
                cpu_time=float(message.get("cpu_time") or 0.0),
                exit_status="0",
                dependency_complete=not bypass and bool(
                    message.get("dependency_complete", True)
                ),
                dependency_bypass=bypass,
            )
        )

    async def _handle_error(self, execution_id: str, message: dict[str, Any]) -> None:
        execution = self._executions.get(execution_id)
        if execution is None or execution.outcome.done():
            return
        kind = str(message.get("kind") or ERROR_CRASH)
        execution.outcome.set_result(
            ExecutionOutcome(
                ok=False,
                kind=kind,
                message=str(message.get("message") or "tool failed"),
                cpu_time=float(message.get("cpu_time") or 0.0),
                exit_status="1",
                traceback=message.get("traceback"),
            )
        )

    async def _handle_eof(self, execution_id: str, message: dict[str, Any]) -> None:
        execution = self._executions.get(execution_id)
        if execution is None or execution.outcome.done():
            return
        exit_code = "unknown"
        process = execution.process
        if process is not None:
            try:
                exit_code = str(process.returncode)
            except Exception:  # pragma: no cover
                exit_code = "unknown"
        execution.outcome.set_result(
            ExecutionOutcome(
                ok=False,
                kind=ERROR_CRASH,
                message=f"worker exited unexpectedly (exit code {exit_code})",
                exit_status=exit_code,
            )
        )

    async def _timeout_watch(self, execution: PhysicalExecution) -> None:
        try:
            await asyncio.sleep(execution.timeout_seconds)
        except asyncio.CancelledError:
            return
        if execution.outcome.done():
            return
        await self._terminate_execution(
            execution, f"execution timed out after {execution.timeout_seconds:.0f}s"
        )

    async def _terminate_execution(self, execution: PhysicalExecution, reason: str) -> None:
        """Terminate a running worker (used by timeout, cancellation and shutdown)."""

        await terminate_execution(execution, reason)

    # ------------------------------------------------------------------
    # finalisation
    # ------------------------------------------------------------------
    async def _finalize_execution(
        self, execution: PhysicalExecution, outcome: ExecutionOutcome
    ) -> None:
        async with self._lock:
            execution.finish_time = time.time()
            execution.cpu_time = outcome.cpu_time or execution.cpu_time
            execution.exit_status = outcome.exit_status
            if execution.timeout_task is not None:
                execution.timeout_task.cancel()
                execution.timeout_task = None

            group = self._groups.get(execution.share_group_id) if execution.share_group_id else None
            if outcome.ok:
                execution.status = ExecutionStatus.COMPLETED
                execution.result = outcome.payload
                execution.result_hash = outcome.result_hash
            elif outcome.kind == ERROR_TIMEOUT:
                execution.status = ExecutionStatus.TIMEOUT
                execution.error = outcome.message
            elif outcome.kind == ERROR_TOOL:
                execution.status = ExecutionStatus.FAILED
                execution.error = outcome.message
            else:
                execution.status = ExecutionStatus.CRASHED
                execution.error = outcome.message

            # The final memory sample has to be taken *before* the row is
            # written - otherwise a short execution would persist a peak of 0.
            self._sample_final_rss(execution)
            self._persist_execution(execution)
            self._release_worker(
                execution.worker_handle, reusable=execution.worker_handle is not None
                and getattr(execution.worker_handle, "reusable", False)
            )
            self.event_bus.publish(
                EventType.TOOL_EXECUTION_FINISHED,
                workflow_id=execution.workflow_id,
                physical_execution_id=execution.execution_id,
                logical_call_id=execution.leader_call_id,
                parent_event_id=execution.start_event_id,
                metadata={
                    "status": str(execution.status),
                    "cpu_time": execution.cpu_time,
                    "peak_rss": execution.peak_rss,
                    "exit_status": execution.exit_status,
                    "error": execution.error,
                    "dependency_count": len(execution.ledger),
                },
            )
            self.scheduler.release(execution.execution_id)

            if outcome.ok:
                self._commit_success_locked(execution, group, outcome)
            else:
                self._commit_failure_locked(execution, group, outcome)

    def _commit_success_locked(
        self,
        execution: PhysicalExecution,
        group: ShareGroup | None,
        outcome: ExecutionOutcome,
    ) -> None:
        if group is None:
            call = self._calls.get(execution.leader_call_id) if execution.leader_call_id else None
            if call is not None:
                self._deliver_locked(call, outcome.payload, outcome.result_hash, execution.execution_id)
            return

        if group.status in (ShareGroupStatus.CREATED, ShareGroupStatus.EXECUTING):
            group.transition(ShareGroupStatus.VALIDATING)

        ledger = self._ledger_of(group)
        # A tool that read the workspace without the controlled reader produced
        # an incomplete dependency ledger.  Its result must not be shared with
        # anybody - the participants run independently instead.
        incomplete_ledger = not outcome.dependency_complete
        for participant in list(group.participants.values()):
            if participant.status in (
                ParticipantStatus.COMPLETED,
                ParticipantStatus.FAILED,
                ParticipantStatus.CANCELLED,
                ParticipantStatus.DETACHED,
            ):
                continue
            if participant.is_leader:
                call = self._calls.get(participant.logical_call_id)
                if call is not None:
                    self._deliver_locked(
                        call, outcome.payload, outcome.result_hash, execution.execution_id
                    )
                self._complete_participant(group, participant)
                continue
            if incomplete_ledger:
                self._detach_participant_locked(
                    group,
                    participant,
                    "the tool read the workspace without the controlled reader",
                )
                continue
            if self.mode_for(group.workflow_id) is RuntimeMode.DEFERRED:
                status, reason, matched = self.validator.validate_prefix(
                    participant.snapshot_id, ledger
                )
                participant.validated_prefix_length = matched
                participant.validation_status = status
                self._record_validation(
                    group,
                    participant,
                    status,
                    reason,
                    dependency_sequence=matched,
                    execution_id=execution.execution_id,
                )
                if status is not ValidationStatus.MATCH:
                    self.event_bus.publish(
                        EventType.SHARE_VALIDATION_FAILED,
                        workflow_id=group.workflow_id,
                        logical_call_id=participant.logical_call_id,
                        physical_execution_id=execution.execution_id,
                        metadata={
                            "share_group_id": group.share_group_id,
                            "reason": reason,
                            "status": str(status),
                            "deferred": True,
                        },
                    )
                    self._detach_participant_locked(group, participant, reason or "mismatch")
                    continue
            else:
                if (
                    participant.validated_prefix_length != len(ledger)
                    or participant.validation_status is not ValidationStatus.MATCH
                ):
                    self._detach_participant_locked(
                        group,
                        participant,
                        "dependency validation incomplete at commit time",
                    )
                    continue
                if not self._snapshot_is_trustworthy(participant.snapshot_id or "", force=True):
                    self._detach_participant_locked(
                        group,
                        participant,
                        "snapshot integrity check failed before the result was shared",
                    )
                    continue
            call = self._calls.get(participant.logical_call_id)
            if call is not None:
                self._deliver_locked(
                    call, outcome.payload, outcome.result_hash, execution.execution_id
                )
            self._complete_participant(group, participant)

        group.transition(ShareGroupStatus.COMPLETED)
        group.closed_time = time.time()
        self._persist_group(group)
        self._cache_result(group, execution, outcome)

    def _cache_result(
        self, group: ShareGroup, execution: PhysicalExecution, outcome: ExecutionOutcome
    ) -> None:
        """Remember a finished result for a later, identical request."""

        snapshot_hash = self._snapshot_content_hash(execution.snapshot_id)
        if snapshot_hash is None:
            return
        self.result_cache.put(
            candidate_key=execution.candidate_key,
            snapshot_hash=snapshot_hash,
            payload=outcome.payload,
            result_hash=outcome.result_hash or "",
            execution_id=execution.execution_id,
            dependencies=execution.ledger.records(),
            created_at=time.time(),
        )

    def _commit_failure_locked(
        self,
        execution: PhysicalExecution,
        group: ShareGroup | None,
        outcome: ExecutionOutcome,
    ) -> None:
        affected: list[str] = []
        if group is not None:
            for participant in group.participants.values():
                if participant.status in (
                    ParticipantStatus.COMPLETED,
                    ParticipantStatus.FAILED,
                    ParticipantStatus.CANCELLED,
                ):
                    continue
                affected.append(participant.logical_call_id)
            if group.status in (ShareGroupStatus.CREATED, ShareGroupStatus.EXECUTING):
                group.transition(ShareGroupStatus.FAILED)
            group.closed_time = time.time()
            self._persist_group(group)
        elif execution.leader_call_id:
            affected.append(execution.leader_call_id)

        retryable = outcome.kind in (ERROR_CRASH, ERROR_TIMEOUT)
        for call_id in affected:
            call = self._calls.get(call_id)
            if call is None or call.future.done():
                continue
            if retryable and call.retry_count == 0:
                call.retry_count += 1
                call.status = LogicalCallStatus.PENDING
                call.physical_execution_id = None
                call.error = None
                self._persist_call(call)
                self.event_bus.publish(
                    EventType.TOOL_CALL_RETRYING,
                    workflow_id=call.workflow_id,
                    logical_call_id=call.logical_call_id,
                    metadata={"retry": call.retry_count, "reason": outcome.message},
                )
                self._start_independent_locked(call)
                continue
            self._fail_call_locked(call, outcome.message, outcome.kind or "error")

    # ------------------------------------------------------------------
    # participant / call state helpers (lock must be held)
    # ------------------------------------------------------------------
    def _complete_participant(self, group: ShareGroup, participant: Participant) -> None:
        """Mark a participant as successfully served by the shared execution."""

        if participant.status not in (
            ParticipantStatus.COMPLETED,
            ParticipantStatus.FAILED,
            ParticipantStatus.CANCELLED,
        ):
            participant.transition(ParticipantStatus.COMPLETED)
        participant.delivered_time = time.time()
        self._persist_participant(group, participant)

    def _detach_participant_locked(
        self, group: ShareGroup, participant: Participant, reason: str
    ) -> None:
        if participant.status in (
            ParticipantStatus.DETACHED,
            ParticipantStatus.COMPLETED,
            ParticipantStatus.FAILED,
            ParticipantStatus.CANCELLED,
        ):
            return
        participant.transition(ParticipantStatus.DETACHED)
        participant.detached_time = time.time()
        participant.detach_reason = reason
        # the call was a participant of the group and is separated now
        participant.detach_kind = "after_join"
        participant.validation_status = (
            ValidationStatus.MISMATCH
            if "differs" in reason
            else participant.validation_status
        )
        self._persist_participant(group, participant)
        self._persist_group(group)
        participant_call = self._calls.get(participant.logical_call_id)
        self.event_bus.publish(
            EventType.SHARE_PARTICIPANT_DETACHED,
            workflow_id=group.workflow_id,
            agent_id=participant.agent_id,
            invocation_id=participant.invocation_id,
            logical_call_id=participant.logical_call_id,
            physical_execution_id=group.leader_execution_id,
            parent_event_id=(
                participant_call.request_event_id if participant_call else None
            ),
            metadata={
                "share_group_id": group.share_group_id,
                "reason": reason,
                "validated_prefix_length": participant.validated_prefix_length,
            },
        )
        call = self._calls.get(participant.logical_call_id)
        if call is None or call.future.done():
            return
        call.status = LogicalCallStatus.DETACHED
        call.detached_reason = reason
        call.detached_at = time.time()
        call.physical_execution_id = None
        call.share_group_id = None
        self._persist_call(call)
        self._reassign_after_detach_locked(call, group)

    def _reassign_after_detach_locked(self, call: LogicalCall, left_group: ShareGroup) -> None:
        """Try to join another compatible group before running on its own.

        A detachment usually affects several participants of the same branch at
        once.  Re-using an already running execution of the same candidate key
        avoids duplicated work.
        """

        mode = self.mode_for(call.workflow_id)
        if mode is not RuntimeMode.BASELINE:
            for group_id in reversed(self._groups_by_key.get(call.candidate_key, ())):
                group = self._groups.get(group_id)
                if group is None or group is left_group:
                    continue
                if group.status not in (
                    ShareGroupStatus.CREATED,
                    ShareGroupStatus.EXECUTING,
                ):
                    continue
                if mode is RuntimeMode.DEFERRED:
                    self._attach_participant(group, call, validated_prefix=0)
                    return
                status, reason, matched = self.validator.validate_prefix(
                    call.snapshot_id, self._ledger_of(group)
                )
                if status is ValidationStatus.MATCH:
                    self._attach_participant(group, call, validated_prefix=matched)
                    return
        # nobody compatible is running -> lead a new group so that further
        # detached participants of the same branch can join it
        self._create_group_locked(call)

    def _deliver_locked(
        self,
        call: LogicalCall,
        payload: Any,
        result_hash: str | None,
        execution_id: str,
        cached: bool = False,
    ) -> bool:
        if call.status in (
            LogicalCallStatus.COMPLETED,
            LogicalCallStatus.FAILED,
            LogicalCallStatus.CANCELLED,
        ) or call.future.done():
            # Idempotency guard: a call is never delivered twice.
            return False
        stored = self.result_store.store(
            payload,
            workflow_id=call.workflow_id,
            execution_id=execution_id,
            logical_call_id=call.logical_call_id,
        )
        call.status = LogicalCallStatus.COMPLETED
        call.result = payload
        call.result_hash = result_hash or stored.result_hash
        call.result_reference = stored.payload_path
        execution = self._executions.get(execution_id)
        if execution is not None:
            execution.result_reference = stored.payload_path
            # the execution row was written before the result existed
            self._persist_execution(execution)
        call.completed_time = time.time()
        call.error = None
        call.physical_execution_id = execution_id
        self._persist_call(call)
        self.event_bus.publish(
            EventType.TOOL_RESULT_DELIVERED,
            workflow_id=call.workflow_id,
            agent_id=call.agent_id,
            invocation_id=call.invocation_id,
            logical_call_id=call.logical_call_id,
            physical_execution_id=execution_id,
            parent_event_id=execution.start_event_id if execution else call.request_event_id,
            metadata={
                "result_hash": call.result_hash,
                "result_reference": stored.payload_path,
                "shared": call.shared,
                "cached": cached,
                "tool_name": call.tool_name,
                "wait_duration": call.completed_time - call.request_time,
            },
        )
        if not call.future.done():
            call.future.set_result(
                ToolCallResult(
                    logical_call_id=call.logical_call_id,
                    physical_execution_id=execution_id,
                    result=payload,
                    result_hash=call.result_hash or stored.result_hash,
                    shared=call.shared,
                    request_time=call.request_time,
                    completed_time=call.completed_time,
                    share_group_id=call.share_group_id,
                )
            )
        return True

    def _fail_call_locked(self, call: LogicalCall, message: str, kind: str) -> None:
        if call.future.done():
            return
        call.status = LogicalCallStatus.FAILED
        call.error = message
        call.completed_time = time.time()
        self._persist_call(call)
        self.event_bus.publish(
            EventType.TOOL_CALL_FAILED,
            workflow_id=call.workflow_id,
            agent_id=call.agent_id,
            invocation_id=call.invocation_id,
            logical_call_id=call.logical_call_id,
            physical_execution_id=call.physical_execution_id,
            parent_event_id=call.request_event_id,
            metadata={"reason": message, "kind": kind},
        )
        call.future.set_exception(
            ToolExecutionError(message, kind=kind, execution_id=call.physical_execution_id)
        )

    # ------------------------------------------------------------------
    # persistence helpers
    # ------------------------------------------------------------------
    def _persist_call(self, call: LogicalCall) -> None:
        self.repository.save_logical_call(self._logical_call_record(call))

    @staticmethod
    def _logical_call_record(call: LogicalCall):
        from ..storage.models import LogicalToolCallRecord

        return LogicalToolCallRecord(
            logical_call_id=call.logical_call_id,
            workflow_id=call.workflow_id,
            invocation_id=call.invocation_id,
            agent_id=call.agent_id,
            tool_name=call.tool_name,
            tool_version=call.tool_version,
            normalized_arguments=json.dumps(call.arguments, ensure_ascii=False, sort_keys=True),
            workspace_snapshot_id=call.snapshot_id,
            request_time=call.request_time,
            status=str(call.status),
            physical_execution_id=call.physical_execution_id,
            result_reference=call.result_reference,
            result_hash=call.result_hash,
            candidate_key=call.candidate_key,
            share_group_id=call.share_group_id,
            error=call.error,
            completed_time=call.completed_time,
            retry_count=call.retry_count,
        )

    def _persist_execution(self, execution: PhysicalExecution) -> None:
        self.repository.save_execution(self._execution_record(execution))

    def _persist_call_with_execution(
        self, call: LogicalCall, execution: PhysicalExecution
    ) -> None:
        """Persist the call and its execution in one transaction.

        The logical call references the physical execution - a crash between
        the two writes would leave the database in an inconsistent state.
        """

        self.repository.save_call_with_execution(
            self._logical_call_record(call), self._execution_record(execution)
        )

    @staticmethod
    def _execution_record(execution: PhysicalExecution):
        from ..storage.models import PhysicalExecutionRecord

        return PhysicalExecutionRecord(
            execution_id=execution.execution_id,
            workflow_id=execution.workflow_id,
            tool_name=execution.tool_name,
            tool_version=execution.tool_version,
            arguments_hash=execution.arguments_hash,
            candidate_key=execution.candidate_key,
            source_snapshot_id=execution.snapshot_id,
            worker_pid=execution.pid,
            start_time=execution.start_time,
            finish_time=execution.finish_time,
            execution_status=str(execution.status),
            cpu_time=execution.cpu_time,
            peak_rss=execution.peak_rss,
            exit_status=execution.exit_status,
            result_hash=execution.result_hash,
            result_reference=execution.result_reference,
            error=execution.error,
            timeout_seconds=execution.timeout_seconds,
            share_group_id=execution.share_group_id,
            attempt=execution.attempt,
            admission_reason=execution.admission_reason,
            queued_time=execution.queued_time,
            worker_root_pid=execution.worker_root_pid,
            worker_command=execution.worker_command,
        )

    def _persist_group(self, group: ShareGroup) -> None:
        from ..storage.models import ShareGroupRecord

        self.repository.save_share_group(
            ShareGroupRecord(
                share_group_id=group.share_group_id,
                workflow_id=group.workflow_id,
                candidate_key=group.candidate_key,
                leader_execution_id=group.leader_execution_id,
                leader_logical_call_id=group.leader_logical_call_id,
                creation_time=group.creation_time,
                closed_time=group.closed_time,
                status=str(group.status),
                participant_count=len(group.participants),
                participant_calls=list(group.participant_calls),
                mode=group.mode,
                tool_name=group.tool_name,
                tool_version=group.tool_version,
            )
        )

    def _persist_participant(self, group: ShareGroup, participant: Participant) -> None:
        from ..storage.models import ShareParticipantRecord

        self.repository.save_participant(
            ShareParticipantRecord(
                share_group_id=group.share_group_id,
                logical_call_id=participant.logical_call_id,
                workflow_id=group.workflow_id,
                agent_id=participant.agent_id,
                invocation_id=participant.invocation_id,
                snapshot_id=participant.snapshot_id,
                status=str(participant.status),
                validated_prefix_length=participant.validated_prefix_length,
                validation_status=str(participant.validation_status),
                attached_time=participant.attached_time,
                detached_time=participant.detached_time,
                delivered_time=participant.delivered_time,
                wait_duration=participant.wait_duration,
                wait_reason=participant.wait_reason,
                detach_reason=participant.detach_reason,
                detach_kind=participant.detach_kind,
                is_leader=participant.is_leader,
            )
        )

    def _record_validation(
        self,
        group: ShareGroup,
        participant: Participant,
        status: ValidationStatus,
        reason: str | None,
        dependency_sequence: int | None = None,
        execution_id: str | None = None,
    ) -> None:
        from ..core.ids import new_id as _new_id
        from ..storage.models import ValidationRecord

        started = time.perf_counter()
        self.repository.save_validation(
            ValidationRecord(
                validation_id=_new_id("val"),
                share_group_id=group.share_group_id,
                logical_call_id=participant.logical_call_id,
                validation_status=str(status),
                validation_time=time.time(),
                dependency_sequence=dependency_sequence,
                execution_id=execution_id or group.leader_execution_id,
                mismatch_reason=reason,
                elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
        )

