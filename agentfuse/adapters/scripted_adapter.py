"""Scripted agent adapter and the offline multi-agent demo agents.

A *Scripted Agent* is a deterministic local program that reacts to the real
result of a tool call.  It is **not** an LLM agent and does not claim to be
one.  What matters for AgentFuse is that scripted agents enter the runtime
through exactly the same adapter interface an external framework would use -
there is no demo specific shortcut in the runtime.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence

from ..core.agent_context import AgentHandle, InvocationHandle
from ..core.state import EventType, InvocationStatus
from .base import AgentAdapter


class ScriptedAdapter(AgentAdapter):
    """Adapter used by the offline scripted agents."""

    async def bind_agent(
        self,
        external_identity: str | None = None,
        agent_description: str | None = None,
        registration_source: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentHandle:
        return self.workflow.bind_agent(
            external_identity=external_identity,
            agent_description=agent_description,
            registration_source=registration_source,
            metadata=metadata,
        )

    async def begin_invocation(
        self,
        agent_id: str,
        parent_invocation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InvocationHandle:
        return self.workflow.begin_invocation(
            agent_id, parent_invocation_id=parent_invocation_id, metadata=metadata
        )

    async def submit_tool_call(
        self,
        invocation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        snapshot_id: str | None = None,
    ) -> Any:
        invocation = self.workflow.invocations.get(invocation_id)
        agent_id = invocation.agent_id if invocation else None
        workflow = self.workflow
        resolved_snapshot = snapshot_id or workflow.default_snapshot_id
        if resolved_snapshot is None:
            raise ValueError("no workspace snapshot available for the tool call")
        return await self.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            invocation_id=invocation_id,
            agent_id=agent_id,
            tool_name=tool_name,
            arguments=arguments,
            snapshot_id=resolved_snapshot,
        )

    async def deliver_result(self, invocation_id: str, logical_call_id: str, result: Any) -> None:
        """Record that the *framework* received the result of a tool call.

        The authoritative delivery event (``TOOL_RESULT_DELIVERED``) is
        published by the runtime when the result is bound to the logical call.
        This method only records the framework side boundary, therefore it
        uses its own event type and can never duplicate the runtime event.
        """

        invocation = self.workflow.invocations.get(invocation_id)
        self.workflow.event_bus.publish(
            EventType.FRAMEWORK_RESULT_RECEIVED,
            workflow_id=self.workflow.workflow_id,
            agent_id=invocation.agent_id if invocation else None,
            invocation_id=invocation_id,
            logical_call_id=logical_call_id,
            metadata={"delivered_by": "adapter"},
        )

    async def end_invocation(
        self, invocation_id: str, status: str = str(InvocationStatus.COMPLETED)
    ) -> None:
        self.workflow.end_invocation(invocation_id, status=status)

    async def record_event(self, event_type: str | EventType, **fields: Any) -> Any:
        workflow = self.workflow
        return workflow.event_bus.publish(
            event_type,
            workflow_id=fields.pop("workflow_id", workflow.workflow_id),
            agent_id=fields.pop("agent_id", None),
            invocation_id=fields.pop("invocation_id", None),
            logical_call_id=fields.pop("logical_call_id", None),
            physical_execution_id=fields.pop("physical_execution_id", None),
            parent_event_id=fields.pop("parent_event_id", None),
            metadata=fields.pop("metadata", None) or fields,
        )


# ----------------------------------------------------------------------
# Scripted agents
# ----------------------------------------------------------------------
@dataclass
class ToolStep:
    """One logical tool request."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    snapshot_id: str | None = None
    label: str | None = None


@dataclass
class CallableStep:
    """A custom step; the callable receives the agent context."""

    func: Callable[["AgentContext"], Awaitable[Any]]
    label: str | None = None


Step = ToolStep | CallableStep


class AgentContext:
    """The runtime handle that a scripted agent uses to interact with AgentFuse."""

    def __init__(
        self,
        workflow: Any,
        adapter: ScriptedAdapter,
        agent: AgentHandle,
        invocation: InvocationHandle,
        snapshot_id: str | None = None,
    ) -> None:
        self.workflow = workflow
        self.adapter = adapter
        self.agent = agent
        self.invocation = invocation
        self.snapshot_id = snapshot_id
        self.results: list[Any] = []

    # ------------------------------------------------------------------
    @property
    def agent_id(self) -> str:
        return self.agent.agent_id

    @property
    def invocation_id(self) -> str:
        return self.invocation.invocation_id

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        snapshot_id: str | None = None,
    ) -> Any:
        result = await self.adapter.submit_tool_call(
            self.invocation_id,
            tool_name,
            arguments,
            snapshot_id=snapshot_id or self.snapshot_id,
        )
        self.results.append(result)
        return result

    async def invoke_agent(self, agent: "ScriptedAgent", snapshot_id: str | None = None) -> Any:
        """Run another agent as a child invocation of the current one."""

        return await agent.run_in(self.workflow, self.adapter, parent_invocation_id=self.invocation_id, snapshot_id=snapshot_id)

    def emit(self, event_type: str | EventType, **fields: Any) -> Any:
        fields.setdefault("agent_id", self.agent_id)
        fields.setdefault("invocation_id", self.invocation_id)
        return self.workflow.event_bus.publish(
            event_type,
            workflow_id=fields.pop("workflow_id", self.workflow.workflow_id),
            agent_id=fields.pop("agent_id", None),
            invocation_id=fields.pop("invocation_id", None),
            logical_call_id=fields.pop("logical_call_id", None),
            physical_execution_id=fields.pop("physical_execution_id", None),
            metadata=fields.pop("metadata", None) or fields,
        )

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class ScriptedAgent:
    """A deterministic local agent driven by an explicit list of steps."""

    def __init__(
        self,
        name: str,
        steps: Sequence[Step] | None = None,
        snapshot_id: str | None = None,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.steps = list(steps or [])
        self.snapshot_id = snapshot_id
        self.description = description or f"scripted agent {name}"
        self.metadata = dict(metadata or {})
        self.handle: AgentHandle | None = None

    # ------------------------------------------------------------------
    async def run_in(
        self,
        workflow: Any,
        adapter: ScriptedAdapter,
        parent_invocation_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> Any:
        if self.handle is None or self.handle.workflow_id != workflow.workflow_id:
            self.handle = await adapter.bind_agent(
                external_identity=self.name,
                agent_description=self.description,
                registration_source="scripted-agent",
                metadata=self.metadata,
            )
        invocation = await adapter.begin_invocation(
            self.handle.agent_id, parent_invocation_id=parent_invocation_id
        )
        context = AgentContext(
            workflow=workflow,
            adapter=adapter,
            agent=self.handle,
            invocation=invocation,
            snapshot_id=snapshot_id or self.snapshot_id or workflow.default_snapshot_id,
        )
        outcome: Any = None
        try:
            for step in self.steps:
                if isinstance(step, ToolStep):
                    outcome = await context.call_tool(
                        step.tool_name, step.arguments, snapshot_id=step.snapshot_id
                    )
                elif isinstance(step, CallableStep):
                    outcome = await step.func(context)
                else:  # pragma: no cover - defensive
                    raise TypeError(f"unsupported step: {step!r}")
        except asyncio.CancelledError:
            await adapter.end_invocation(invocation.invocation_id, status=str(InvocationStatus.CANCELLED))
            raise
        except Exception:
            await adapter.end_invocation(invocation.invocation_id, status=str(InvocationStatus.FAILED))
            raise
        await adapter.end_invocation(invocation.invocation_id, status=str(InvocationStatus.COMPLETED))
        return outcome

    async def run(self, workflow: Any, adapter: ScriptedAdapter, snapshot_id: str | None = None) -> Any:
        return await self.run_in(workflow, adapter, parent_invocation_id=None, snapshot_id=snapshot_id)
