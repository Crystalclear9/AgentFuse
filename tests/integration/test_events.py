"""Integration tests: the event protocol guarantees."""

from __future__ import annotations

import asyncio

import pytest

from agentfuse.core.state import EventType
from agentfuse.scenarios.common import DEMO_FILES


async def test_the_candidate_event_is_published(app, snapshot):
    """A potential sharing candidate must be observable."""

    workflow = app.create_workflow("test-candidate-event")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    events = []
    app.event_bus.subscribe(events.append)

    arguments = {"files": list(DEMO_FILES)}
    await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
    )

    candidates = [
        event
        for event in events
        if event.event_type == EventType.SHARE_CANDIDATE_FOUND
    ]
    assert candidates, "SHARE_CANDIDATE_FOUND must be published"
    for event in candidates:
        assert "candidate_key" in event.metadata
        assert "share_group_id" in event.metadata
        assert "joinable" in event.metadata
    # the second call found the group of the first one
    assert any(event.metadata.get("joinable") for event in candidates)


async def test_the_delivery_event_is_published_exactly_once(app, snapshot):
    """The runtime publishes exactly one authoritative delivery event."""

    workflow = app.create_workflow("test-delivery")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    events = []
    app.event_bus.subscribe(events.append)

    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": list(DEMO_FILES)},
        snapshot_id=snapshot.snapshot_id,
    )

    delivered = [
        event
        for event in events
        if event.event_type == EventType.TOOL_RESULT_DELIVERED
    ]
    assert len(delivered) == 1


async def test_the_framework_boundary_does_not_duplicate_the_delivery(app, snapshot):
    """The adapter reports the framework side boundary with its own event."""

    from agentfuse.adapters.scripted_adapter import ScriptedAdapter

    workflow = app.create_workflow("test-framework-delivery")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    events = []
    app.event_bus.subscribe(events.append)

    adapter = ScriptedAdapter(workflow)
    await adapter.deliver_result("inv-1", "call-1", {"ok": True})

    types = [event.event_type for event in events]
    assert EventType.FRAMEWORK_RESULT_RECEIVED in types
    assert EventType.TOOL_RESULT_DELIVERED not in types


async def test_a_retry_is_reported_with_its_own_event(app, snapshot):
    """A retry must not be reported as "waiting for a shared result"."""

    workflow = app.create_workflow("test-retry-event")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    events = []
    app.event_bus.subscribe(events.append)

    with_error = False
    try:
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "exit", "exit_code": 3},
            snapshot_id=snapshot.snapshot_id,
        )
    except Exception:
        with_error = True
    assert with_error

    types = [event.event_type for event in events]
    assert EventType.TOOL_CALL_RETRYING in types
    assert EventType.TOOL_CALL_WAITING not in types


async def test_out_of_order_events_do_not_create_wrong_edges(app):
    """An event that arrives before its parent must not create a false edge."""

    from agentfuse.core.events import EventBus
    from agentfuse.core.execution_graph import ExecutionGraph

    bus = EventBus()
    graph = ExecutionGraph(workflow_id="wf-1")
    bus.subscribe(graph.apply_event)

    # the result arrives before the call that requested it
    bus.publish(
        EventType.TOOL_RESULT_DELIVERED,
        workflow_id="wf-1",
        logical_call_id="call-1",
        physical_execution_id="exec-1",
        metadata={"result_hash": "abc"},
    )
    bus.publish(
        EventType.TOOL_CALL_REQUESTED,
        workflow_id="wf-1",
        invocation_id="inv-1",
        logical_call_id="call-1",
        metadata={"tool_name": "ast_analyzer"},
    )

    payload = graph.to_dict()
    node_ids = {node["id"] for node in payload["nodes"]}
    assert "call:call-1" in node_ids
    # the execution was never observed -> it exists as an explicit placeholder
    placeholder = next(
        node for node in payload["nodes"] if node["id"] == "execution:exec-1"
    )
    assert placeholder["status"] == "UNKNOWN"
    assert placeholder["attributes"]["placeholder"] is True
    # no edge points to a node that does not exist
    for edge in payload["edges"]:
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids


async def test_a_burst_of_events_does_not_block_the_event_loop(app, snapshot):
    """§16.5 - the event writes must not block the main loop.

    The events are written by a background thread.  A burst of events therefore
    has to leave the event loop responsive: a coroutine that runs in parallel
    has to be scheduled while the events are written.
    """

    workflow = app.create_workflow("test-event-burst")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.005)

    async def publisher():
        # the events are written while the loop keeps running
        for index in range(200):
            app.event_bus.publish(
                EventType.TOOL_READ_OBSERVED,
                workflow_id=workflow.workflow_id,
                physical_execution_id=f"exec-burst-{index}",
                metadata={"relative_path": f"src/file{index}.py"},
            )
            await asyncio.sleep(0.001)

    task = asyncio.create_task(ticker())
    try:
        await publisher()
        # the loop was still running while the events were written
        assert ticks > 0, "the event loop was blocked by the event writes"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    events = app.repository.list_events(workflow.workflow_id)
    assert len(events) >= 200, "every event has to be written"
