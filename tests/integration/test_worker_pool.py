"""Integration tests: the warm worker pool."""

from __future__ import annotations

import asyncio

from agentfuse.core.state import RuntimeMode
from agentfuse.scenarios.common import DEMO_FILES


async def test_a_pooled_worker_is_reused(app, snapshot):
    """Two sequential executions share one warm worker process."""

    app.coordinator.worker_pool_size = 2
    workflow = app.create_workflow("test-pool")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

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
    assert len(executions) == 2
    assert first.result_hash == second.result_hash
    assert first.result == second.result
    # both executions ran inside the same (warm) worker process
    assert executions[0]["worker_pid"] == executions[1]["worker_pid"]


async def test_the_pool_keeps_the_results_correct_under_concurrency(app, snapshot):
    app.coordinator.worker_pool_size = 2
    workflow = app.create_workflow("test-pool-concurrent")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    results = await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments=arguments,
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(4)
        ]
    )
    assert len({result.result_hash for result in results}) == 1
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) <= 2
    assert all(execution["execution_status"] == "COMPLETED" for execution in executions)


async def test_a_failing_tool_does_not_poison_the_pool(app, snapshot):
    """A worker that raised a tool error stays usable for the next execution."""

    app.coordinator.worker_pool_size = 1
    workflow = app.create_workflow("test-pool-failure")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    from agentfuse.core.state import EventType

    failed = []
    app.event_bus.subscribe(
        lambda event: failed.append(event)
        if event.event_type == EventType.TOOL_CALL_FAILED
        else None
    )

    with_error = False
    try:
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "raise"},
            snapshot_id=snapshot.snapshot_id,
        )
    except Exception:
        with_error = True
    assert with_error
    assert failed

    result = await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]},
        snapshot_id=snapshot.snapshot_id,
    )
    assert result.result["totals"]["functions"] >= 2


async def test_the_pool_is_enabled_by_default(app, snapshot):
    """By default the runtime keeps warm workers instead of starting one per call."""

    assert app.coordinator.worker_pool_size >= 1
    workflow = app.create_workflow("test-pool-default", mode=RuntimeMode.ONLINE)
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()
    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]},
        snapshot_id=snapshot.snapshot_id,
    )
    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]},
        snapshot_id=snapshot.snapshot_id,
    )
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2
    assert executions[0]["worker_pid"] == executions[1]["worker_pid"], (
        "the second execution must reuse the warm worker"
    )


async def test_the_pool_can_be_disabled(app, snapshot):
    """With ``worker_pool_size = 0`` every execution gets its own process."""

    app.coordinator.worker_pool_size = 0
    workflow = app.create_workflow("test-pool-off", mode=RuntimeMode.ONLINE)
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()
    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]},
        snapshot_id=snapshot.snapshot_id,
    )
    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]},
        snapshot_id=snapshot.snapshot_id,
    )
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2
    assert executions[0]["worker_pid"] != executions[1]["worker_pid"]
