"""Agent and invocation handles.

The internal identifiers are created by the runtime at the *observed* call
boundary.  External identities are optional: an external framework that does
not expose a stable agent id still gets a stable internal reference for the
whole workflow run.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentHandle:
    agent_id: str
    workflow_id: str
    external_identity: str | None = None
    agent_description: str | None = None
    registration_source: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    bound_at: float = field(default_factory=time.time)
    #: ``CONFIRMED`` or ``UNDETERMINED`` - an agent whose identity could not
    #: be established is never merged with another one (requirement 5.2)
    identity_status: str = "CONFIRMED"

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "workflow_id": self.workflow_id,
            "external_identity": self.external_identity,
            "agent_description": self.agent_description,
            "registration_source": self.registration_source,
            "metadata": self.metadata,
            "bound_at": self.bound_at,
            "identity_status": self.identity_status,
        }


@dataclass
class InvocationHandle:
    invocation_id: str
    agent_id: str
    workflow_id: str
    parent_invocation_id: str | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    status: str = "RUNNING"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invocation_id": self.invocation_id,
            "agent_id": self.agent_id,
            "workflow_id": self.workflow_id,
            "parent_invocation_id": self.parent_invocation_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "metadata": self.metadata,
        }
