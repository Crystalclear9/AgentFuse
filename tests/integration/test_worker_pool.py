"""Integration tests: the warm worker pool.

The pool has to be deterministic: a worker that just finished a job must be
reused by the next one, and the runtime shutdown must not leave a single
process behind.  Both properties were violated by a race between the
background warm up and the first execution - the tests below reproduce that
race on purpose.
"""

from __future__ import annotations

import asyncio
import time

import psutil
import pytest

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


async def test_the_first_execution_waits_for_the_warm_up(app, snapshot):
    """The warm up must win the race against the first execution.

    Without the wait the first execution starts its own worker, the pool is
    full when that worker is released and the *next* execution would pick a
    different process - the reuse would break depending on the machine speed.
    """

    import threading

    app.coordinator.worker_pool_size = 2
    # simulate a warm up that is still running: the event is only set a moment
    # later, exactly like on a slow machine.
    app.coordinator._pool_warmup_done.clear()
    timer = threading.Timer(0.05, app.coordinator._pool_warmup_done.set)
    timer.start()
    try:
        workflow = app.create_workflow("test-pool-race")
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
    finally:
        timer.cancel()

    executions = app.repository.list_executions(workflow.workflow_id)
    assert first.result_hash == second.result_hash
    assert executions[0]["worker_pid"] == executions[1]["worker_pid"], (
        "the worker that finished the first job must be reused"
    )


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


async def test_the_idle_pool_never_exceeds_its_size(app, snapshot):
    app.coordinator.worker_pool_size = 2
    workflow = app.create_workflow("test-pool-size")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    for _ in range(3):
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    assert len(app.coordinator._idle_workers) <= app.coordinator.worker_pool_size


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


async def test_the_shutdown_leaves_no_worker_behind(app, snapshot):
    """After the shutdown no worker process may survive - not even one that
    the background warm up creates afterwards."""

    app.coordinator.worker_pool_size = 2
    workflow = app.create_workflow("test-pool-shutdown")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]},
        snapshot_id=snapshot.snapshot_id,
    )

    pids = {
        execution["worker_pid"]
        for execution in app.repository.list_executions(workflow.workflow_id)
    }
    pids.update(
        handle.process.pid for handle in app.coordinator._worker_handles
    )
    pids = {pid for pid in pids if pid}

    await app.coordinator.shutdown()

    assert not app.coordinator._worker_handles
    assert not app.coordinator._idle_workers

    time.sleep(0.2)
    alive = []
    for pid in pids:
        try:
            if psutil.Process(int(pid)).is_running():
                alive.append(pid)
        except psutil.NoSuchProcess:
            continue
    assert not alive, f"worker processes are still running: {alive}"


async def test_the_pool_is_enabled_by_default(app, snapshot):
    """By default the runtime keeps warm workers instead of starting one per call."""

    from agentfuse.settings import load_settings

    # the *configured* default (the CI matrix may override the runtime value)
    assert load_settings().worker_pool_size >= 1

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
    if app.coordinator.worker_pool_size >= 1:
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


@pytest.mark.parametrize("pool_size", [0, 1, 2])
async def test_every_pool_size_produces_correct_results(app, snapshot, pool_size):
    app.coordinator.worker_pool_size = pool_size
    workflow = app.create_workflow(f"test-pool-{pool_size}")
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
            for _ in range(3)
        ]
    )
    assert len({result.result_hash for result in results}) == 1
    executions = app.repository.list_executions(workflow.workflow_id)
    assert all(execution["execution_status"] == "COMPLETED" for execution in executions)
    assert len(app.coordinator._idle_workers) <= max(pool_size, 0)
