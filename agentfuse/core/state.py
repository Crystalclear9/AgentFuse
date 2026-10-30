"""Status vocabularies used across the AgentFuse runtime.

Every state transition is driven from real observed events; the values below
are the only legal vocabulary used by the coordinator, the storage layer and
the web UI.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    """`str` backed enum so values serialise directly to JSON/SQL."""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return str(self.value)


class WorkflowStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class AgentIdentityStatus(StrEnum):
    """How sure is the runtime about the identity of an agent?

    ``CONFIRMED``
        the identity was established at an observed registration boundary -
        the adapter could name the agent.
    ``UNDETERMINED``
        the adapter could **not** confirm that two events belong to the same
        agent.  Such an agent is never merged with another one (requirement
        5.2).
    """

    CONFIRMED = "CONFIRMED"
    UNDETERMINED = "UNDETERMINED"


class InvocationStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class LogicalCallStatus(StrEnum):
    PENDING = "PENDING"
    WAITING = "WAITING"
    SHARED_WAIT = "SHARED_WAIT"
    RUNNING_INDEPENDENTLY = "RUNNING_INDEPENDENTLY"
    DETACHED = "DETACHED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ExecutionStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    CRASHED = "CRASHED"
    INTERRUPTED = "INTERRUPTED"


class ShareGroupStatus(StrEnum):
    CREATED = "CREATED"
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


class ParticipantStatus(StrEnum):
    WAITING = "WAITING"
    SHARED_WAIT = "SHARED_WAIT"
    DETACHED = "DETACHED"
    RUNNING_INDEPENDENTLY = "RUNNING_INDEPENDENTLY"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ValidationStatus(StrEnum):
    PENDING = "PENDING"
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


class DependencyType(StrEnum):
    FILE_READ = "FILE_READ"
    DIRECTORY_LIST = "DIRECTORY_LIST"
    PATH_EXISTS = "PATH_EXISTS"
    PATH_NOT_EXISTS = "PATH_NOT_EXISTS"
    FILE_STAT = "FILE_STAT"


class RuntimeMode(StrEnum):
    """Execution mode of a workflow / benchmark run."""

    BASELINE = "baseline"
    ONLINE = "online"
    DEFERRED = "deferred"


class EventType(StrEnum):
    """The unified internal event protocol."""

    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_FINISHED = "WORKFLOW_FINISHED"
    AGENT_BOUND = "AGENT_BOUND"
    AGENT_INVOKED = "AGENT_INVOKED"
    AGENT_RETURNED = "AGENT_RETURNED"
    TOOL_CALL_REQUESTED = "TOOL_CALL_REQUESTED"
    TOOL_CALL_WAITING = "TOOL_CALL_WAITING"
    TOOL_CALL_RETRYING = "TOOL_CALL_RETRYING"
    TOOL_EXECUTION_STARTED = "TOOL_EXECUTION_STARTED"
    TOOL_READ_OBSERVED = "TOOL_READ_OBSERVED"
    DEPENDENCY_BYPASS = "DEPENDENCY_BYPASS"
    SHARE_CANDIDATE_FOUND = "SHARE_CANDIDATE_FOUND"
    SHARE_VALIDATION_PASSED = "SHARE_VALIDATION_PASSED"
    SHARE_VALIDATION_FAILED = "SHARE_VALIDATION_FAILED"
    SHARE_PARTICIPANT_DETACHED = "SHARE_PARTICIPANT_DETACHED"
    TOOL_EXECUTION_FINISHED = "TOOL_EXECUTION_FINISHED"
    TOOL_RESULT_DELIVERED = "TOOL_RESULT_DELIVERED"
    FRAMEWORK_RESULT_RECEIVED = "FRAMEWORK_RESULT_RECEIVED"
    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    TOOL_CALL_CANCELLED = "TOOL_CALL_CANCELLED"
    RESOURCE_LIMIT_WARNING = "RESOURCE_LIMIT_WARNING"
    RESOURCE_LEAK_WARNING = "RESOURCE_LEAK_WARNING"
