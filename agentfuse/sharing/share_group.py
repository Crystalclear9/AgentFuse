"""Share group and participant state machine.

Legal transitions (enforced by :meth:`ShareGroup.transition` and
:meth:`Participant.transition`)::

    group:     CREATED -> EXECUTING -> VALIDATING -> COMPLETED | FAILED -> CLOSED
    participant: WAITING -> SHARED_WAIT -> COMPLETED | FAILED | CANCELLED
                        -> DETACHED -> RUNNING_INDEPENDENTLY -> COMPLETED | FAILED
                        -> RUNNING_INDEPENDENTLY (leader owns its own execution)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..core.state import ParticipantStatus, ShareGroupStatus, ValidationStatus


class IllegalTransition(RuntimeError):
    """Raised when a share group or participant would change state illegally."""


_GROUP_TRANSITIONS: dict[ShareGroupStatus, set[ShareGroupStatus]] = {
    ShareGroupStatus.CREATED: {ShareGroupStatus.EXECUTING, ShareGroupStatus.FAILED, ShareGroupStatus.CLOSED},
    ShareGroupStatus.EXECUTING: {
        ShareGroupStatus.VALIDATING,
        ShareGroupStatus.COMPLETED,
        ShareGroupStatus.FAILED,
        ShareGroupStatus.CLOSED,
    },
    ShareGroupStatus.VALIDATING: {
        ShareGroupStatus.COMPLETED,
        ShareGroupStatus.FAILED,
        ShareGroupStatus.CLOSED,
    },
    ShareGroupStatus.COMPLETED: {ShareGroupStatus.CLOSED},
    ShareGroupStatus.FAILED: {ShareGroupStatus.CLOSED},
    ShareGroupStatus.CLOSED: set(),
}

_PARTICIPANT_TRANSITIONS: dict[ParticipantStatus, set[ParticipantStatus]] = {
    ParticipantStatus.WAITING: {
        ParticipantStatus.SHARED_WAIT,
        ParticipantStatus.RUNNING_INDEPENDENTLY,
        ParticipantStatus.DETACHED,
        ParticipantStatus.CANCELLED,
        ParticipantStatus.FAILED,
    },
    ParticipantStatus.SHARED_WAIT: {
        ParticipantStatus.DETACHED,
        ParticipantStatus.COMPLETED,
        ParticipantStatus.FAILED,
        ParticipantStatus.CANCELLED,
        ParticipantStatus.RUNNING_INDEPENDENTLY,
    },
    ParticipantStatus.DETACHED: {
        ParticipantStatus.RUNNING_INDEPENDENTLY,
        ParticipantStatus.COMPLETED,
        ParticipantStatus.FAILED,
        ParticipantStatus.CANCELLED,
    },
    ParticipantStatus.RUNNING_INDEPENDENTLY: {
        ParticipantStatus.COMPLETED,
        ParticipantStatus.FAILED,
        ParticipantStatus.CANCELLED,
        ParticipantStatus.DETACHED,
    },
    ParticipantStatus.COMPLETED: set(),
    ParticipantStatus.FAILED: set(),
    ParticipantStatus.CANCELLED: set(),
}


@dataclass
class Participant:
    """One logical tool call inside a share group."""

    logical_call_id: str
    agent_id: str | None = None
    invocation_id: str | None = None
    snapshot_id: str | None = None
    status: ParticipantStatus = ParticipantStatus.WAITING
    validated_prefix_length: int = 0
    validation_status: ValidationStatus = ValidationStatus.PENDING
    attached_time: float = field(default_factory=time.time)
    detached_time: float | None = None
    delivered_time: float | None = None
    detach_reason: str | None = None
    is_leader: bool = False

    def transition(self, target: ParticipantStatus) -> None:
        if target == self.status:
            return
        allowed = _PARTICIPANT_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise IllegalTransition(
                f"illegal participant transition {self.status} -> {target}"
            )
        self.status = target

    @property
    def wait_duration(self) -> float | None:
        end = self.delivered_time or self.detached_time
        if end is None:
            return None
        return max(0.0, end - self.attached_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_call_id": self.logical_call_id,
            "agent_id": self.agent_id,
            "invocation_id": self.invocation_id,
            "snapshot_id": self.snapshot_id,
            "status": str(self.status),
            "validated_prefix_length": self.validated_prefix_length,
            "validation_status": str(self.validation_status),
            "attached_time": self.attached_time,
            "detached_time": self.detached_time,
            "delivered_time": self.delivered_time,
            "wait_duration": self.wait_duration,
            "detach_reason": self.detach_reason,
            "is_leader": self.is_leader,
        }


@dataclass
class ShareGroup:
    """A set of logical calls that may share one physical execution."""

    share_group_id: str
    workflow_id: str | None
    candidate_key: str
    tool_name: str
    tool_version: str
    leader_execution_id: str
    leader_logical_call_id: str
    creation_time: float = field(default_factory=time.time)
    closed_time: float | None = None
    status: ShareGroupStatus = ShareGroupStatus.CREATED
    participants: dict[str, Participant] = field(default_factory=dict)
    mode: str = "online"

    def transition(self, target: ShareGroupStatus) -> None:
        if target == self.status:
            return
        allowed = _GROUP_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise IllegalTransition(f"illegal share group transition {self.status} -> {target}")
        self.status = target

    # ------------------------------------------------------------------
    def add_participant(self, participant: Participant) -> None:
        if participant.logical_call_id in self.participants:
            raise IllegalTransition(
                f"{participant.logical_call_id} is already a participant of {self.share_group_id}"
            )
        self.participants[participant.logical_call_id] = participant

    def active_participants(self) -> list[Participant]:
        return [
            participant
            for participant in self.participants.values()
            if participant.status
            in (
                ParticipantStatus.WAITING,
                ParticipantStatus.SHARED_WAIT,
                ParticipantStatus.RUNNING_INDEPENDENTLY,
            )
        ]

    def has_live_participants(self) -> bool:
        return bool(self.active_participants())

    def to_dict(self) -> dict[str, Any]:
        return {
            "share_group_id": self.share_group_id,
            "workflow_id": self.workflow_id,
            "candidate_key": self.candidate_key,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "leader_execution_id": self.leader_execution_id,
            "leader_logical_call_id": self.leader_logical_call_id,
            "creation_time": self.creation_time,
            "closed_time": self.closed_time,
            "status": str(self.status),
            "mode": self.mode,
            "participant_count": len(self.participants),
            "participants": [p.to_dict() for p in self.participants.values()],
        }

