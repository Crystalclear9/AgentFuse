"""Integration tests: concurrency, races and cancellation semantics."""

from __future__ import annotations

import asyncio

import pytest

from agentfuse.core.state import EventType
from agentfuse.scenarios.common import DEMO_FILES


async def test_many_concurrent_identical_calls_create_one_execution(app, snapshot):
    """Ten agents submit the same call at the same time."""

    workflow = app.create_workflow("test-race")
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
            for _ in range(10)
        ]
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    calls = app.repository.list_logical_calls(workflow.workflow_id)
    assert len(executions) == 1
    assert len(calls) == 10
    assert len({result.result_hash for result in results}) == 1


async def test_no_logical_call_is_delivered_twice(app, snapshot):
    workflow = app.create_workflow("test-idempotency")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    delivered: list[str] = []

    def observer(event):
        if event.event_type == EventType.TOOL_RESULT_DELIVERED and event.logical_call_id:
            delivered.append(event.logical_call_id)

    app.event_bus.subscribe(observer)

    arguments = {"files": list(DEMO_FILES)}
    await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments=arguments,
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(6)
        ]
    )

    assert len(delivered) == len(set(delivered)) == 6


async def test_a_cancelled_follower_does_not_stop_the_leader(app, snapshot):
    workflow = app.create_workflow("test-cancel-follower")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 1500}
    leader = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.05)
    follower = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.1)

    cancelled = False
    for call in app.coordinator.active_calls(workflow.workflow_id):
        if call.status.value in ("SHARED_WAIT", "WAITING"):
            await app.coordinator.cancel_logical_call(call.logical_call_id, "test cancel")
            cancelled = True
            break
    assert cancelled, "the follower never entered the shared wait state"

    with pytest.raises(asyncio.CancelledError):
        await follower
    result = await asyncio.wait_for(leader, timeout=30)
    assert result.result is not None


async def test_a_cancelled_leader_keeps_the_follower_alive(app, snapshot):
    workflow = app.create_workflow("test-cancel-leader")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 1500}
    leader = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.05)
    follower = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.1)

    leader_call = None
    for call in app.coordinator.active_calls(workflow.workflow_id):
        if call.physical_execution_id and call.status.value == "RUNNING_INDEPENDENTLY":
            leader_call = call
            break
    assert leader_call is not None, "no leader call was found"
    await app.coordinator.cancel_logical_call(leader_call.logical_call_id, "leader cancelled")

    with pytest.raises(asyncio.CancelledError):
        await leader
    result = await asyncio.wait_for(follower, timeout=30)
    assert result.result is not None


async def test_cancelling_every_participant_terminates_the_execution(app, snapshot):
    workflow = app.create_workflow("test-cancel-all")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 5000}
    tasks = [
        asyncio.create_task(
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="content_digest",
                arguments=arguments,
                snapshot_id=snapshot.snapshot_id,
            )
        )
        for _ in range(2)
    ]
    await asyncio.sleep(0.4)
    for call in list(app.coordinator.active_calls(workflow.workflow_id)):
        await app.coordinator.cancel_logical_call(call.logical_call_id, "cancel all")

    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=30)

    executions = app.repository.list_executions(workflow.workflow_id)
    assert executions
    assert all(
        execution["execution_status"] in ("CANCELLED", "TIMEOUT", "CRASHED", "COMPLETED", "FAILED")
        for execution in executions
    )


async def test_a_failing_execution_releases_the_participants(app, snapshot):
    """Several callers wait for one failing execution - nobody may hang."""

    workflow = app.create_workflow("test-shared-failure")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    missing = ["src/does_not_exist.py"]
    results = await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments={"files": missing},
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(3)
        ],
        return_exceptions=True,
    )
    assert all(isinstance(result, Exception) for result in results)
    for result in results:
        assert "not found" in str(result) or "does not exist" in str(result)


async def test_a_crashing_worker_is_detected(app, snapshot):
    workflow = app.create_workflow("test-crash")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    with pytest.raises(Exception) as error:
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "exit", "exit_code": 9},
            snapshot_id=snapshot.snapshot_id,
        )
    assert "exit" in str(error.value).lower() or "crash" in str(error.value).lower()


async def test_a_hanging_worker_is_terminated_by_the_timeout(app, snapshot):
    workflow = app.create_workflow("test-timeout")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    with pytest.raises(Exception) as error:
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "sleep", "seconds": 30},
            snapshot_id=snapshot.snapshot_id,
            timeout_seconds=1.0,
        )
    assert "timed out" in str(error.value)


async def test_no_worker_processes_are_leaked(app, snapshot):
    import psutil

    workflow = app.create_workflow("test-leak")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    await asyncio.gather(
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
    await asyncio.sleep(0.3)

    pids = {execution["worker_pid"] for execution in app.repository.list_executions(workflow.workflow_id)}

    def running(pids: set) -> list:
        """The pids that are still alive *and* are still the same process.

        A pid can be reused by the operating system once the worker is gone -
        the creation time tells the two apart.
        """

        alive = []
        for pid in pids:
            if pid is None:
                continue
            try:
                process = psutil.Process(int(pid))
                if process.is_running():
                    alive.append(pid)
            except psutil.NoSuchProcess:
                continue
        return alive

    def identity(pids: set) -> dict:
        """The creation time of every worker that is currently alive."""

        known = {}
        for pid in pids:
            if pid is None:
                continue
            try:
                known[pid] = psutil.Process(int(pid)).create_time()
            except psutil.NoSuchProcess:
                continue
        return known

    # Workers of a warm pool stay alive on purpose (they wait for the next
    # job) - but they must not execute anything any more.
    for pid in running(pids):
        process = psutil.Process(int(pid))
        assert not process.is_running() or process.cpu_times().user < 10

    # after the runtime shuts down no worker may survive
    known = identity(pids)
    await app.coordinator.shutdown()
    await asyncio.sleep(0.3)

    survivors = []
    for pid in running(pids):
        try:
            process = psutil.Process(int(pid))
        except psutil.NoSuchProcess:
            continue
        if pid in known and process.create_time() != known[pid]:
            # the pid was reused by an unrelated process
            continue
        survivors.append(pid)
    assert not survivors, f"worker processes are still running: {survivors}"
