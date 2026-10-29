"""Unit tests for the event bus and the execution graph."""

from __future__ import annotations

import asyncio

from agentfuse.core.events import EventBus
from agentfuse.core.execution_graph import ExecutionGraph
from agentfuse.core.state import EventType


def test_event_ids_are_unique_and_sequences_are_stable():
    bus = EventBus()
    first = bus.publish(EventType.WORKFLOW_STARTED, workflow_id="wf-1")
    second = bus.publish(EventType.WORKFLOW_STARTED, workflow_id="wf-1")
    third = bus.publish(EventType.AGENT_BOUND, workflow_id="wf-2")
    assert first.event_id != second.event_id
    assert (first.sequence, second.sequence) == (0, 1)
    assert third.sequence == 0  # per workflow sequence


def test_subscribers_receive_the_events():
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append)
    bus.publish(EventType.AGENT_BOUND, workflow_id="wf-1", agent_id="agent-1")
    assert len(seen) == 1
    assert seen[0].event_type == "AGENT_BOUND"


async def test_wait_for_resolves_on_the_matching_event():
    bus = EventBus()
    loop = asyncio.get_running_loop()

    async def publish_later():
        await asyncio.sleep(0.01)
        bus.publish(EventType.TOOL_READ_OBSERVED, workflow_id="wf-1")

    task = loop.create_task(publish_later())
    event = await bus.wait_for(
        lambda item: item.event_type == EventType.TOOL_READ_OBSERVED, timeout=2.0
    )
    await task
    assert event.workflow_id == "wf-1"


def test_the_graph_only_contains_observed_relations():
    bus = EventBus()
    graph = ExecutionGraph(workflow_id="wf-1")
    bus.subscribe(graph.apply_event)

    bus.publish(EventType.AGENT_BOUND, workflow_id="wf-1", agent_id="agent-1")
    bus.publish(
        EventType.AGENT_INVOKED,
        workflow_id="wf-1",
        agent_id="agent-1",
        invocation_id="inv-1",
    )
    bus.publish(
        EventType.TOOL_CALL_REQUESTED,
        workflow_id="wf-1",
        agent_id="agent-1",
        invocation_id="inv-1",
        logical_call_id="call-1",
        metadata={"tool_name": "ast_analyzer"},
    )

    payload = graph.to_dict()
    kinds = {edge["kind"] for edge in payload["edges"]}
    assert "AGENT_INVOKES" in kinds
    assert "CALLS_TOOL" in kinds
    # no execution was observed -> no execution node and no binding edge
    assert "BOUND_TO_EXECUTION" not in kinds
    assert all(node["kind"] != "physical_execution" for node in payload["nodes"])


def test_the_graph_records_shared_executions():
    bus = EventBus()
    graph = ExecutionGraph(workflow_id="wf-1")
    bus.subscribe(graph.apply_event)
    bus.publish(
        EventType.TOOL_CALL_REQUESTED,
        workflow_id="wf-1",
        invocation_id="inv-1",
        logical_call_id="call-1",
    )
    bus.publish(
        EventType.TOOL_EXECUTION_STARTED,
        workflow_id="wf-1",
        logical_call_id="call-1",
        physical_execution_id="exec-1",
        metadata={"tool_name": "ast_analyzer"},
    )
    bus.publish(
        EventType.TOOL_CALL_WAITING,
        workflow_id="wf-1",
        invocation_id="inv-2",
        logical_call_id="call-2",
        physical_execution_id="exec-1",
        metadata={"share_group_id": "sg-1"},
    )
    kinds = {edge["kind"] for edge in graph.to_dict()["edges"]}
    assert "BOUND_TO_EXECUTION" in kinds
    assert "SHARES_EXECUTION_WITH" in kinds
