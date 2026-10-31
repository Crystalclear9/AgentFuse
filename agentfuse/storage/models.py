"""Domain records persisted by AgentFuse.

The dataclasses mirror the SQLite tables one to one; the repository converts
between them so that the rest of the runtime never has to deal with raw rows.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any



@dataclass
class WorkflowRecord:
    workflow_id: str
    scenario: str
    start_time: float
    status: str
    end_time: float | None = None
    configuration: dict[str, Any] = field(default_factory=dict)
    runtime_mode: str = "online"
    random_seed: int | None = None
    label: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRecord:
    agent_id: str
    workflow_id: str
    external_identity: str | None = None
    agent_description: str | None = None
    registration_source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    #: ``CONFIRMED`` or ``UNDETERMINED`` (requirement 5.2)
    identity_status: str = "CONFIRMED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentInvocationRecord:
    invocation_id: str
    workflow_id: str
    agent_id: str
    start_time: float
    end_time: float | None = None
    parent_invocation_id: str | None = None
    status: str = "RUNNING"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LogicalToolCallRecord:
    logical_call_id: str
    workflow_id: str
    tool_name: str
    tool_version: str
    normalized_arguments: str
    request_time: float
    invocation_id: str | None = None
    agent_id: str | None = None
    workspace_snapshot_id: str | None = None
    status: str = "PENDING"
    physical_execution_id: str | None = None
    result_reference: str | None = None
    result_hash: str | None = None
    candidate_key: str | None = None
    share_group_id: str | None = None
    error: str | None = None
    #: the failure kind (tool_error / worker_crash / timeout / cancelled /
    #: internal_error) - requirement 10.1
    error_kind: str | None = None
    completed_time: float | None = None
    retry_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PhysicalExecutionRecord:
    execution_id: str
    tool_name: str
    tool_version: str
    arguments_hash: str
    execution_status: str
    workflow_id: str | None = None
    candidate_key: str | None = None
    source_snapshot_id: str | None = None
    worker_pid: int | None = None
    start_time: float | None = None
    finish_time: float | None = None
    cpu_time: float | None = None
    peak_rss: int | None = None
    exit_status: str | None = None
    result_hash: str | None = None
    result_reference: str | None = None
    error: str | None = None
    #: the failure kind (tool_error / worker_crash / timeout / cancelled /
    #: internal_error) - requirement 10.1
    error_kind: str | None = None
    timeout_seconds: float | None = None
    share_group_id: str | None = None
    attempt: int = 1
    admission_reason: str | None = None
    queued_time: float | None = None
    #: the pid of the process that really executes the tool.  It differs from
    #: ``worker_pid`` when the interpreter runs behind a launcher stub.
    worker_root_pid: int | None = None
    worker_command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShareGroupRecord:
    share_group_id: str
    candidate_key: str
    creation_time: float
    status: str
    workflow_id: str | None = None
    leader_execution_id: str | None = None
    leader_logical_call_id: str | None = None
    closed_time: float | None = None
    participant_count: int = 0
    participant_calls: list[str] = field(default_factory=list)
    mode: str | None = None
    tool_name: str | None = None
    tool_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ShareParticipantRecord:
    share_group_id: str
    logical_call_id: str
    status: str
    workflow_id: str | None = None
    agent_id: str | None = None
    invocation_id: str | None = None
    snapshot_id: str | None = None
    validated_prefix_length: int = 0
    validation_status: str | None = None
    attached_time: float | None = None
    detached_time: float | None = None
    delivered_time: float | None = None
    wait_duration: float | None = None
    wait_reason: str | None = None
    detach_reason: str | None = None
    detach_kind: str | None = None
    is_leader: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class WorkspaceSnapshotRecord:
    snapshot_id: str
    parent_snapshot_id: str | None
    workspace_id: str
    snapshot_path: str
    creation_time: float
    manifest_hash: str
    source_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DependencyRecord:
    execution_id: str
    sequence_number: int
    dependency_type: str
    relative_path: str
    fingerprint: str | None
    observed_time: float
    snapshot_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationRecord:
    validation_id: str
    share_group_id: str
    logical_call_id: str
    validation_status: str
    validation_time: float
    dependency_sequence: int | None = None
    execution_id: str | None = None
    mismatch_reason: str | None = None
    elapsed_ms: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResourceSample:
    workflow_id: str | None
    execution_id: str | None
    timestamp: float
    rss_bytes: int | None = None
    cpu_seconds: float | None = None
    system_cpu_percent: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BenchmarkRunRecord:
    run_id: str
    scenario: str
    mode: str
    repeat_index: int
    started_at: float | None = None
    finished_at: float | None = None
    workflow_id: str | None = None
    makespan: float | None = None
    physical_executions: int | None = None
    logical_calls: int | None = None
    saved_executions: int | None = None
    eligible_calls: int | None = None
    shared_calls: int | None = None
    share_success_rate: float | None = None
    total_cpu_time: float | None = None
    peak_rss: int | None = None
    avg_share_wait: float | None = None
    total_share_wait: float | None = None
    validation_count: int | None = None
    validation_failures: int | None = None
    validation_ms: float | None = None
    detach_count: int | None = None
    first_conflict_at: float | None = None
    follower_independent_at: float | None = None
    correct: int | None = None
    mismatched: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
