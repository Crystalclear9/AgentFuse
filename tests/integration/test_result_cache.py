"""Integration tests: the session scoped result cache (opt in extension)."""

from __future__ import annotations


from agentfuse.core.state import EventType
from agentfuse.scenarios.common import DEMO_FILES, create_branch, mutate_variant_source


async def test_an_identical_request_is_served_from_the_cache(app, snapshot):
    """A repeated request with the same input content must not compute twice."""

    app.coordinator.result_cache.max_entries = 16
    workflow = app.create_workflow("test-cache")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    events = []
    app.event_bus.subscribe(events.append)

    arguments = {"files": list(DEMO_FILES)}
    first = await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments=arguments,
        snapshot_id=snapshot.snapshot_id,
    )
    second = await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments=arguments,
        snapshot_id=snapshot.snapshot_id,
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 1, "the second request must reuse the finished result"
    assert first.result_hash == second.result_hash
    assert first.result == second.result

    cached = [
        event
        for event in events
        if event.event_type == EventType.TOOL_RESULT_DELIVERED
        and (event.metadata or {}).get("cached")
    ]
    assert cached, "the cache hit must be visible in the delivery event"


async def test_a_different_input_state_is_not_served_from_the_cache(app, snapshot):
    """A different workspace content must never reuse a cached result."""

    app.coordinator.result_cache.max_entries = 16
    branch = create_branch(app, snapshot, mutate_variant_source, "branch")
    workflow = app.create_workflow("test-cache-divergent")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments=arguments,
        snapshot_id=snapshot.snapshot_id,
    )
    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments=arguments,
        snapshot_id=branch.snapshot_id,
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2, "a different input state must be executed again"


async def test_the_cache_is_bounded(app, snapshot):
    app.coordinator.result_cache.max_entries = 2
    workflow = app.create_workflow("test-cache-bounded")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    for index in range(5):
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments={"files": ["src/alpha.py"]},
            snapshot_id=snapshot.snapshot_id,
        )
    snapshot_state = app.coordinator.result_cache.snapshot()
    assert snapshot_state["entries"] <= 2
    assert snapshot_state["max_entries"] == 2


async def test_the_cache_is_session_scoped(app, snapshot):
    """The cache lives in the runtime memory - it never survives a restart."""

    app.coordinator.result_cache.max_entries = 16
    workflow = app.create_workflow("test-cache-session")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]},
        snapshot_id=snapshot.snapshot_id,
    )
    assert app.coordinator.result_cache.snapshot()["entries"] == 1

    # a fresh runtime starts with an empty cache
    from agentfuse.app import AgentFuseApp
    from agentfuse.settings import load_settings

    other = AgentFuseApp(settings=load_settings(home=str(app.settings.home / "other")))
    try:
        assert other.coordinator.result_cache.snapshot()["entries"] == 0
    finally:
        await other.close()
