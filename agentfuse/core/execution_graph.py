"""Online execution graph reconstruction.

The graph is built **from observed events only**.  There is no static DAG, no
name similarity heuristic and no timing heuristic: an edge exists because a
real boundary was observed.

Node kinds
    ``workflow``, ``agent``, ``invocation``, ``logical_call``,
    ``physical_execution``, ``tool_result``

Edge kinds
    ``AGENT_INVOKES``, ``CALLS_TOOL``, ``BOUND_TO_EXECUTION``,
    ``EXECUTION_PRODUCES_RESULT``, ``RESULT_DELIVERED_TO``,
    ``SHARES_EXECUTION_WITH``, ``EXPLICIT_INVOCATION_DEPENDENCY``
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .events import RuntimeEvent
from .state import AgentIdentityStatus

NODE_WORKFLOW = "workflow"
NODE_AGENT = "agent"
NODE_INVOCATION = "invocation"
NODE_LOGICAL_CALL = "logical_call"
NODE_PHYSICAL_EXECUTION = "physical_execution"
NODE_TOOL_RESULT = "tool_result"

EDGE_AGENT_INVOKES = "AGENT_INVOKES"
EDGE_CALLS_TOOL = "CALLS_TOOL"
EDGE_BOUND_TO_EXECUTION = "BOUND_TO_EXECUTION"
EDGE_EXECUTION_PRODUCES_RESULT = "EXECUTION_PRODUCES_RESULT"
EDGE_RESULT_DELIVERED_TO = "RESULT_DELIVERED_TO"
EDGE_SHARES_EXECUTION_WITH = "SHARES_EXECUTION_WITH"
EDGE_EXPLICIT_INVOCATION_DEPENDENCY = "EXPLICIT_INVOCATION_DEPENDENCY"

#: Maps a node id prefix to the node kind (used for placeholder nodes).
_NODE_KINDS = {
    "workflow": NODE_WORKFLOW,
    "agent": NODE_AGENT,
    "invocation": NODE_INVOCATION,
    "call": NODE_LOGICAL_CALL,
    "execution": NODE_PHYSICAL_EXECUTION,
    "result": NODE_TOOL_RESULT,
}


@dataclass
class GraphNode:
    node_id: str
    kind: str
    label: str
    workflow_id: str | None = None
    status: str | None = None
    start_time: float | None = None
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "kind": self.kind,
            "label": self.label,
            "workflow_id": self.workflow_id,
            "status": self.status,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "attributes": self.attributes,
        }


@dataclass
class GraphEdge:
    source: str
    target: str
    kind: str
    attributes: dict[str, Any] = field(default_factory=dict)

    def key(self) -> tuple[str, str, str]:
        return (self.source, self.target, self.kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "kind": self.kind,
            "attributes": self.attributes,
        }


class ExecutionGraph:
    """Incrementally built execution graph."""

    def __init__(self, workflow_id: str | None = None) -> None:
        self.workflow_id = workflow_id
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[tuple[str, str, str], GraphEdge] = {}
        self.unknown_edges: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def add_node(self, node: GraphNode) -> GraphNode:
        existing = self.nodes.get(node.node_id)
        if existing is None:
            self.nodes[node.node_id] = node
            return node
        for key, value in node.__dict__.items():
            if value not in (None, {}, "", []):
                setattr(existing, key, value)
        existing.attributes.update(node.attributes)
        return existing

    def add_edge(
        self, source: str, target: str, kind: str, attributes: dict[str, Any] | None = None
    ) -> GraphEdge:
        # An edge may be observed before the node it points to (out of order
        # delivery).  The referenced node is then created as an explicit
        # placeholder with the status UNKNOWN - the graph never invents a
        # relation, it only marks what has not been observed yet.
        self._ensure_node(source)
        self._ensure_node(target)
        edge = GraphEdge(source=source, target=target, kind=kind, attributes=dict(attributes or {}))
        self.edges[edge.key()] = edge
        return edge

    def _ensure_node(self, node_id: str) -> GraphNode:
        node = self.nodes.get(node_id)
        if node is not None:
            return node
        prefix, _, remainder = node_id.partition(":")
        kind = _NODE_KINDS.get(prefix, "unknown")
        return self.add_node(
            GraphNode(
                node_id=node_id,
                kind=kind,
                label=remainder or node_id,
                status="UNKNOWN",
                attributes={"placeholder": True},
            )
        )

    # ------------------------------------------------------------------
    def apply_event(self, event: RuntimeEvent) -> None:
        handler = getattr(self, f"_on_{event.event_type.lower()}", None)
        if handler is not None:
            handler(event)

    def apply_events(self, events: Iterable[RuntimeEvent]) -> None:
        for event in events:
            self.apply_event(event)

    # ------------------------------------------------------------------
    # event handlers
    # ------------------------------------------------------------------
    def _on_workflow_started(self, event: RuntimeEvent) -> None:
        self.add_node(
            GraphNode(
                node_id=f"workflow:{event.workflow_id}",
                kind=NODE_WORKFLOW,
                label=str(event.metadata.get("scenario") or event.workflow_id or "workflow"),
                workflow_id=event.workflow_id,
                start_time=event.timestamp,
                attributes={"mode": event.metadata.get("mode")},
            )
        )

    def _on_workflow_finished(self, event: RuntimeEvent) -> None:
        node = self.nodes.get(f"workflow:{event.workflow_id}")
        if node is not None:
            node.end_time = event.timestamp
            node.status = str(event.metadata.get("status") or "COMPLETED")

    def _on_agent_bound(self, event: RuntimeEvent) -> None:
        if not event.agent_id:
            # The identity of the agent could not be established.  The event is
            # not dropped - it is recorded as an undetermined identity
            # (requirement 5.2).
            self.add_node(
                GraphNode(
                    node_id=f"agent:undetermined:{event.event_id}",
                    kind=NODE_AGENT,
                    label="undetermined agent",
                    workflow_id=event.workflow_id,
                    status="UNDETERMINED",
                    start_time=event.timestamp,
                    attributes={
                        "identity_status": "UNDETERMINED",
                        "placeholder": True,
                    },
                )
            )
            return
        identity_status = str(
            event.metadata.get("identity_status") or AgentIdentityStatus.CONFIRMED
        )
        self.add_node(
            GraphNode(
                node_id=f"agent:{event.agent_id}",
                kind=NODE_AGENT,
                label=str(event.metadata.get("external_identity") or event.agent_id),
                workflow_id=event.workflow_id,
                status=(
                    "UNDETERMINED"
                    if identity_status == str(AgentIdentityStatus.UNDETERMINED)
                    else "CONFIRMED"
                ),
                start_time=event.timestamp,
                attributes={
                    "agent_description": event.metadata.get("agent_description"),
                    "registration_source": event.metadata.get("registration_source"),
                    "identity_status": identity_status,
                },
            )
        )

    def _on_agent_invoked(self, event: RuntimeEvent) -> None:
        if not event.invocation_id:
            return
        if not event.agent_id:
            # The invocation is recorded, but it is **not** connected to an
            # agent: the identity could not be established and two events of
            # an unknown agent must never be merged (requirement 5.2).
            self.add_node(
                GraphNode(
                    node_id=f"invocation:{event.invocation_id}",
                    kind=NODE_INVOCATION,
                    label="invocation of an undetermined agent",
                    workflow_id=event.workflow_id,
                    status="UNDETERMINED",
                    start_time=event.timestamp,
                    attributes={"identity_status": "UNDETERMINED"},
                )
            )
            return
        self.add_node(
            GraphNode(
                node_id=f"invocation:{event.invocation_id}",
                kind=NODE_INVOCATION,
                label=str(event.metadata.get("agent_label") or event.agent_id),
                workflow_id=event.workflow_id,
                status="RUNNING",
                start_time=event.timestamp,
                attributes={"agent_id": event.agent_id},
            )
        )
        self.add_edge(
            f"agent:{event.agent_id}",
            f"invocation:{event.invocation_id}",
            EDGE_AGENT_INVOKES,
        )
        parent = event.metadata.get("parent_invocation_id")
        if parent:
            self.add_edge(
                f"invocation:{parent}",
                f"invocation:{event.invocation_id}",
                EDGE_EXPLICIT_INVOCATION_DEPENDENCY,
            )

    def _on_agent_returned(self, event: RuntimeEvent) -> None:
        if not event.invocation_id:
            return
        node = self.nodes.get(f"invocation:{event.invocation_id}")
        if node is not None:
            node.end_time = event.timestamp
            node.status = str(event.metadata.get("status") or "COMPLETED")

    def _on_tool_call_requested(self, event: RuntimeEvent) -> None:
        if not event.logical_call_id:
            return
        self.add_node(
            GraphNode(
                node_id=f"call:{event.logical_call_id}",
                kind=NODE_LOGICAL_CALL,
                label=str(event.metadata.get("tool_name") or "tool call"),
                workflow_id=event.workflow_id,
                status="PENDING",
                start_time=event.timestamp,
                attributes={
                    "tool_name": event.metadata.get("tool_name"),
                    "tool_version": event.metadata.get("tool_version"),
                    "agent_id": event.agent_id,
                    "invocation_id": event.invocation_id,
                    "snapshot_id": event.metadata.get("snapshot_id"),
                    "candidate_key": event.metadata.get("candidate_key"),
                },
            )
        )
        if event.invocation_id:
            self.add_edge(
                f"invocation:{event.invocation_id}",
                f"call:{event.logical_call_id}",
                EDGE_CALLS_TOOL,
            )

    def _on_tool_execution_started(self, event: RuntimeEvent) -> None:
        if not event.physical_execution_id:
            return
        self.add_node(
            GraphNode(
                node_id=f"execution:{event.physical_execution_id}",
                kind=NODE_PHYSICAL_EXECUTION,
                label=str(event.metadata.get("tool_name") or "execution"),
                workflow_id=event.workflow_id,
                status="RUNNING",
                start_time=event.timestamp,
                attributes={
                    "tool_name": event.metadata.get("tool_name"),
                    "pid": event.metadata.get("pid"),
                    "snapshot_id": event.metadata.get("snapshot_id"),
                    "share_group_id": event.metadata.get("share_group_id"),
                },
            )
        )
        if event.logical_call_id:
            self.add_edge(
                f"call:{event.logical_call_id}",
                f"execution:{event.physical_execution_id}",
                EDGE_BOUND_TO_EXECUTION,
            )

    def _on_tool_execution_finished(self, event: RuntimeEvent) -> None:
        if not event.physical_execution_id:
            return
        node = self.nodes.get(f"execution:{event.physical_execution_id}")
        if node is not None:
            node.end_time = event.timestamp
            node.status = str(event.metadata.get("status") or "COMPLETED")
            node.attributes.update(
                {
                    "cpu_time": event.metadata.get("cpu_time"),
                    "peak_rss": event.metadata.get("peak_rss"),
                    "error": event.metadata.get("error"),
                }
            )

    def _on_tool_call_waiting(self, event: RuntimeEvent) -> None:
        if not event.logical_call_id:
            return
        node = self.nodes.get(f"call:{event.logical_call_id}")
        if node is not None:
            node.status = "SHARED_WAIT"
        if event.physical_execution_id:
            self.add_edge(
                f"call:{event.logical_call_id}",
                f"execution:{event.physical_execution_id}",
                EDGE_SHARES_EXECUTION_WITH,
                {"share_group_id": event.metadata.get("share_group_id")},
            )

    def _on_share_participant_detached(self, event: RuntimeEvent) -> None:
        node = self.nodes.get(f"call:{event.logical_call_id}")
        if node is not None:
            node.status = "DETACHED"
            node.attributes["detach_reason"] = event.metadata.get("reason")

    def _on_tool_result_delivered(self, event: RuntimeEvent) -> None:
        result_hash = str(event.metadata.get("result_hash") or event.logical_call_id)
        node_id = f"result:{result_hash}"
        self.add_node(
            GraphNode(
                node_id=node_id,
                kind=NODE_TOOL_RESULT,
                label=f"result {result_hash[:10]}",
                workflow_id=event.workflow_id,
                start_time=event.timestamp,
                attributes={"result_hash": result_hash, "shared": event.metadata.get("shared")},
            )
        )
        if event.physical_execution_id:
            self.add_edge(
                f"execution:{event.physical_execution_id}",
                node_id,
                EDGE_EXECUTION_PRODUCES_RESULT,
            )
        if event.logical_call_id:
            self.add_edge(node_id, f"call:{event.logical_call_id}", EDGE_RESULT_DELIVERED_TO)
            call = self.nodes.get(f"call:{event.logical_call_id}")
            if call is not None:
                call.status = "COMPLETED"
                call.end_time = event.timestamp

    def _on_tool_call_failed(self, event: RuntimeEvent) -> None:
        node = self.nodes.get(f"call:{event.logical_call_id}")
        if node is not None:
            node.status = "FAILED"
            node.attributes["error"] = event.metadata.get("reason")

    def _on_tool_call_cancelled(self, event: RuntimeEvent) -> None:
        node = self.nodes.get(f"call:{event.logical_call_id}")
        if node is not None:
            node.status = "CANCELLED"

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges.values()],
            "generated_at": time.time(),
        }

    @classmethod
    def from_events(cls, events: Iterable[RuntimeEvent], workflow_id: str | None = None) -> "ExecutionGraph":
        graph = cls(workflow_id=workflow_id)
        graph.apply_events(events)
        return graph
