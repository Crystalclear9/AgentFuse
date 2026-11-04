"""Cancellation must settle persisted state and release queued reservations."""
import asyncio

import pytest

from agentfuse.core.ids import new_id
from agentfuse.core.state import EventType
from agentfuse.runtime.scheduler import LocalResourceController
from agentfuse.scenarios.common import wait_for_event
from tests.support import wait_until, wait_for_process_exit


def test_identifiers_preserve_full_uuid_entropy(monkeypatch):
    import uuid
    values = iter([uuid.UUID("123456789abc00000000000000000001"),
                   uuid.UUID("123456789abc00000000000000000002")])
    monkeypatch.setattr(uuid, "uuid4", lambda: next(values))
    assert new_id("call") != new_id("call")


async def test_cancelled_admission_waiter_does_not_leave_a_reservation():
    scheduler = LocalResourceController(max_workers=1, gpu_slots=0)
    await scheduler.acquire("first", 1)
    waiting = asyncio.create_task(scheduler.acquire("second", 1))
    assert await wait_until(lambda: scheduler.snapshot()["waiting"] == 1)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert scheduler.snapshot()["waiting"] == 0
    scheduler.release("first")
    await asyncio.wait_for(scheduler.acquire("third", 1), 2)
    scheduler.release("third")
    assert scheduler.snapshot()["running"] == 0


async def _sleep_call(app, snapshot, workflow_id):
    return await app.coordinator.submit_tool_call(
        workflow_id=workflow_id, tool_name="fault_injector",
        arguments={"mode": "sleep", "seconds": 30}, snapshot_id=snapshot.snapshot_id)


async def test_caller_task_cancellation_persists_and_stops_worker(app, snapshot):
    workflow = app.create_workflow("caller-cancel", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    task = asyncio.create_task(_sleep_call(app, snapshot, workflow.workflow_id))
    event = await wait_for_event(app.event_bus, EventType.TOOL_EXECUTION_STARTED,
                                 workflow_id=workflow.workflow_id)
    execution = app.coordinator.get_execution(event.physical_execution_id)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert app.repository.list_logical_calls(workflow.workflow_id)[0]["status"] == "CANCELLED"
    assert app.repository.list_executions(workflow.workflow_id)[0]["execution_status"] == "CANCELLED"
    assert app.scheduler.snapshot()["running"] == 0
    assert await wait_for_process_exit([execution.pid], timeout=10)


@pytest.mark.parametrize("shutdown", [False, True])
async def test_queued_cancellation_never_starts_a_worker(app, snapshot, shutdown):
    app.scheduler.max_workers = 1
    workflow = app.create_workflow("queued-cancel", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    first = asyncio.create_task(_sleep_call(app, snapshot, workflow.workflow_id))
    event = await wait_for_event(app.event_bus, EventType.TOOL_EXECUTION_STARTED,
                                 workflow_id=workflow.workflow_id)
    second = asyncio.create_task(_sleep_call(app, snapshot, workflow.workflow_id))
    assert await wait_until(lambda: app.scheduler.snapshot()["waiting"] == 1)
    queued = next(e for e in app.coordinator._executions.values() if e.pid is None)
    queued_call = next(c for c in app.coordinator._calls.values()
                       if c.physical_execution_id == queued.execution_id)
    if shutdown:
        await app.coordinator.shutdown()
    else:
        assert await app.coordinator.cancel_logical_call(queued_call.logical_call_id)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(second, 10)
    assert queued.pid is None
    assert app.scheduler.snapshot()["waiting"] == 0
    if not shutdown:
        assert app.scheduler.snapshot()["running"] == 1
        await app.coordinator.cancel_logical_call(event.logical_call_id)
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, 10)
    assert app.scheduler.snapshot()["running"] == 0
    assert all(row["execution_status"] == "CANCELLED"
               for row in app.repository.list_executions(workflow.workflow_id))
    if shutdown:
        with pytest.raises(RuntimeError, match="shutting down"):
            await _sleep_call(app, snapshot, workflow.workflow_id)


async def test_monitor_thread_can_wake_admission_under_asyncio_debug():
    scheduler = LocalResourceController(max_workers=2, max_memory_budget_mb=100, gpu_slots=0)
    loop = asyncio.get_running_loop()
    debug = loop.get_debug()
    loop.set_debug(True)
    try:
        await scheduler.acquire("first", 1)
        scheduler.record_rss("first", 200 * 1024 * 1024)
        waiting = asyncio.create_task(scheduler.acquire("second", 1))
        assert await wait_until(lambda: scheduler.snapshot()["waiting"] == 1)
        await asyncio.to_thread(scheduler.record_rss, "first", 1024)
        await asyncio.wait_for(waiting, 2)
        scheduler.release("first")
        scheduler.release("second")
    finally:
        loop.set_debug(debug)


@pytest.mark.parametrize("cancel_index", [0, 1])
async def test_cancelling_one_shared_caller_keeps_the_other_alive(app, snapshot, monkeypatch, cancel_index):
    coordinator = app.coordinator
    original = coordinator._handle_result
    resume = asyncio.Event()
    async def gated(*args, **kwargs):
        await resume.wait()
        return await original(*args, **kwargs)
    monkeypatch.setattr(coordinator, "_handle_result", gated)
    workflow = app.create_workflow("shared-caller-cancel", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    async def call():
        return await coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id, tool_name="ast_analyzer",
            arguments={"files": ["src/alpha.py"]}, snapshot_id=snapshot.snapshot_id)
    tasks = [asyncio.create_task(call()), asyncio.create_task(call())]
    try:
        await wait_for_event(app.event_bus, EventType.TOOL_CALL_WAITING,
                             workflow_id=workflow.workflow_id)
        tasks[cancel_index].cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(tasks[cancel_index], 10)
    finally:
        resume.set()
    result = await asyncio.wait_for(tasks[1-cancel_index], 30)
    assert result is not None
    assert len(app.repository.list_executions(workflow.workflow_id)) == 1
    assert sorted(row["status"] for row in app.repository.list_logical_calls(workflow.workflow_id)) == ["CANCELLED", "COMPLETED"]
