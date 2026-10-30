"""Controlled adapter interface.

This is the *only* boundary an external multi-agent framework has to use in
order to participate in AgentFuse.  The adapter translates observed call
boundaries into the unified internal event protocol.

The contract is deliberately minimal:

* the external framework does **not** need a stable ``agent_id`` (the runtime
  creates the internal identity when the agent is first observed),
* the external framework does **not** need a fixed request payload,
* the adapter never guesses relations: every edge in the execution graph comes
  from an explicit call that was actually observed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..core.agent_context import AgentHandle, InvocationHandle
from ..core.events import EventBus
from ..core.state import EventType, InvocationStatus


class AgentAdapter(ABC):
    """Base class of all AgentFuse adapters."""

    def __init__(self, workflow: Any) -> None:
        self.workflow = workflow

    # ------------------------------------------------------------------
    @property
    def event_bus(self) -> EventBus:
        return self.workflow.event_bus

    @property
    def coordinator(self) -> Any:
        return self.workflow.coordinator

    # ------------------------------------------------------------------
    @abstractmethod
    async def bind_agent(
        self,
        external_identity: str | None = None,
        agent_description: str | None = None,
        registration_source: str | None = None,
        metadata: dict[str, Any] | None = None,
        identity_confirmed: bool = True,
    ) -> AgentHandle:
        """Bind an internal agent identity at an observed registration boundary.

        ``identity_confirmed=False`` says that the adapter could not confirm
        the identity of the agent.  The runtime then keeps the identity in the
        ``UNDETERMINED`` state and never merges the agent with another one
        (requirement 5.2).
        """

    @abstractmethod
    async def begin_invocation(
        self,
        agent_id: str,
        parent_invocation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InvocationHandle:
        """Start an agent invocation (an agent may be invoked many times)."""

    @abstractmethod
    async def submit_tool_call(
        self,
        invocation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        snapshot_id: str | None = None,
    ) -> Any:
        """Submit a logical tool call and await its result."""

    @abstractmethod
    async def deliver_result(self, invocation_id: str, logical_call_id: str, result: Any) -> None:
        """Record that the framework received a tool result.

        The runtime publishes the authoritative ``TOOL_RESULT_DELIVERED``
        event when the result is bound to a logical call.  An adapter must not
        publish that event again - implementations report the framework side
        boundary with their own event type instead.
        """

    @abstractmethod
    async def end_invocation(
        self, invocation_id: str, status: str = str(InvocationStatus.COMPLETED)
    ) -> None:
        """Close an agent invocation."""

    @abstractmethod
    async def record_event(
        self, event_type: str | EventType, **fields: Any
    ) -> Any:
        """Record an observed fact as a runtime event."""
