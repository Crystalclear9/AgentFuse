"""Workflow runtime.

A workflow is one complete multi-agent execution instance.  It owns the
agent identities, the invocations and the online execution graph.  The
execution graph is fed by the event bus, therefore it can only contain
relations that were really observed.
"""

from __future__ import annotations

import random
import time
from typing import Any

from ..core.agent_context import AgentHandle, InvocationHandle
from ..core.events import EventBus
from ..core.execution_graph import ExecutionGraph
from ..core.ids import new_id
from ..core.state import (
    AgentIdentityStatus,
    EventType,
    InvocationStatus,
    RuntimeMode,
    WorkflowStatus,
)
from ..storage.models import AgentInvocationRecord, AgentRecord, WorkflowRecord


class WorkflowRuntime:
    """Owns the state of one workflow run."""

    def __init__(
        self,
        *,
        coordinator: Any,
        repository: Any,
        event_bus: EventBus,
        scenario: str,
        mode: RuntimeMode = RuntimeMode.ONLINE,
        workflow_id: str | None = None,
        random_seed: int | None = None,
        label: str | None = None,
        configuration: dict[str, Any] | None = None,
    ) -> None:
        self.workflow_id = workflow_id or new_id("wf")
        self.scenario = scenario
        self.mode = mode
        self.coordinator = coordinator
        self.repository = repository
        self.event_bus = event_bus
        self.random_seed = random_seed
        self.label = label
        self.configuration = dict(configuration or {})

        self.start_time: float | None = None
        self.end_time: float | None = None
        self.status = WorkflowStatus.RUNNING
        self.error: str | None = None

        self.agents: dict[str, AgentHandle] = {}
        self.invocations: dict[str, InvocationHandle] = {}
        self.graph = ExecutionGraph(workflow_id=self.workflow_id)
        #: default workspace snapshot used by adapters that do not specify one
        self.default_snapshot_id: str | None = None
        #: the seeded random source of this workflow.  Every stochastic
        #: decision of the run has to use it, otherwise the seed would not
        #: have any effect and the run would not be reproducible.  The source
        #: is **not** installed globally: seeding ``random`` would change the
        #: behaviour of the whole process, including the code that embeds
        #: AgentFuse.
        self.rng = random.Random(random_seed)

        self._unsubscribe = event_bus.subscribe(self._on_event)

    # ------------------------------------------------------------------
    def _on_event(self, event: Any) -> None:
        if event.workflow_id in (None, self.workflow_id):
            self.graph.apply_event(event)

    def close(self) -> None:
        try:
            self._unsubscribe()
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        self.start_time = time.time()
        self.status = WorkflowStatus.RUNNING
        self._persist_workflow()
        self.event_bus.publish(
            EventType.WORKFLOW_STARTED,
            workflow_id=self.workflow_id,
            metadata={
                "scenario": self.scenario,
                "mode": str(self.mode),
                "random_seed": self.random_seed,
                "label": self.label,
                "configuration": self.configuration,
            },
        )

    def finish(
        self, status: WorkflowStatus = WorkflowStatus.COMPLETED, error: str | None = None
    ) -> None:
        self.end_time = time.time()
        self.status = status
        self.error = error
        self._persist_workflow()
        self.event_bus.publish(
            EventType.WORKFLOW_FINISHED,
            workflow_id=self.workflow_id,
            metadata={"status": str(status), "error": error},
        )

    @property
    def makespan(self) -> float:
        if self.start_time is None:
            return 0.0
        end = self.end_time if self.end_time is not None else time.time()
        return max(0.0, end - self.start_time)

    # ------------------------------------------------------------------
    # agents and invocations
    # ------------------------------------------------------------------
    def bind_agent(
        self,
        external_identity: str | None = None,
        agent_description: str | None = None,
        registration_source: str | None = None,
        metadata: dict[str, Any] | None = None,
        identity_confirmed: bool = True,
    ) -> AgentHandle:
        """Bind an internal agent identity at an observed boundary.

        ``identity_confirmed=False`` says that the adapter could **not**
        establish the identity of the agent.  Such an agent is recorded with
        the status ``UNDETERMINED`` and is never merged with another one
        (requirement 5.2).
        """

        handle = AgentHandle(
            agent_id=new_id("agent"),
            workflow_id=self.workflow_id,
            external_identity=external_identity,
            agent_description=agent_description,
            registration_source=registration_source,
            metadata=dict(metadata or {}),
            identity_status=(
                str(AgentIdentityStatus.CONFIRMED)
                if identity_confirmed
                else str(AgentIdentityStatus.UNDETERMINED)
            ),
        )
        self.agents[handle.agent_id] = handle
        self.repository.save_agent(
            AgentRecord(
                agent_id=handle.agent_id,
                workflow_id=self.workflow_id,
                external_identity=handle.external_identity,
                agent_description=handle.agent_description,
                registration_source=handle.registration_source,
                metadata=handle.metadata,
                created_at=handle.bound_at,
                identity_status=handle.identity_status,
            )
        )
        self.event_bus.publish(
            EventType.AGENT_BOUND,
            workflow_id=self.workflow_id,
            agent_id=handle.agent_id,
            metadata={
                "external_identity": handle.external_identity,
                "agent_description": handle.agent_description,
                "registration_source": handle.registration_source,
                "identity_status": handle.identity_status,
            },
        )
        return handle

    def begin_invocation(
        self,
        agent_id: str,
        parent_invocation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InvocationHandle:
        handle = InvocationHandle(
            invocation_id=new_id("inv"),
            agent_id=agent_id,
            workflow_id=self.workflow_id,
            parent_invocation_id=parent_invocation_id,
            metadata=dict(metadata or {}),
        )
        self.invocations[handle.invocation_id] = handle
        self.repository.save_invocation(
            AgentInvocationRecord(
                invocation_id=handle.invocation_id,
                workflow_id=self.workflow_id,
                agent_id=agent_id,
                start_time=handle.started_at,
                end_time=None,
                parent_invocation_id=parent_invocation_id,
                status=str(InvocationStatus.RUNNING),
                metadata=handle.metadata,
            )
        )
        self.event_bus.publish(
            EventType.AGENT_INVOKED,
            workflow_id=self.workflow_id,
            agent_id=agent_id,
            invocation_id=handle.invocation_id,
            metadata={
                "parent_invocation_id": parent_invocation_id,
                "agent_label": self.agents[agent_id].external_identity
                if agent_id in self.agents
                else agent_id,
            },
        )
        return handle

    def end_invocation(
        self, invocation_id: str, status: str = str(InvocationStatus.COMPLETED)
    ) -> None:
        handle = self.invocations.get(invocation_id)
        if handle is None:
            return
        handle.status = status
        handle.ended_at = time.time()
        self.repository.save_invocation(
            AgentInvocationRecord(
                invocation_id=handle.invocation_id,
                workflow_id=self.workflow_id,
                agent_id=handle.agent_id,
                start_time=handle.started_at,
                end_time=handle.ended_at,
                parent_invocation_id=handle.parent_invocation_id,
                status=status,
                metadata=handle.metadata,
            )
        )
        self.event_bus.publish(
            EventType.AGENT_RETURNED,
            workflow_id=self.workflow_id,
            agent_id=handle.agent_id,
            invocation_id=invocation_id,
            metadata={"status": status},
        )

    # ------------------------------------------------------------------
    def _persist_workflow(self) -> None:
        self.repository.save_workflow(
            WorkflowRecord(
                workflow_id=self.workflow_id,
                scenario=self.scenario,
                start_time=self.start_time or time.time(),
                end_time=self.end_time,
                status=str(self.status),
                configuration=self.configuration,
                runtime_mode=str(self.mode),
                random_seed=self.random_seed,
                label=self.label,
                error=self.error,
            )
        )
