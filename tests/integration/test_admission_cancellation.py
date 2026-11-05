"""A cancellation before admission must not be undone by delayed dispatch."""
import asyncio
import pytest


@pytest.mark.parametrize("shutdown", [False, True])
async def test_cancelled_request_cannot_resume_into_execution(app, snapshot, monkeypatch, shutdown):
    coordinator = app.coordinator
    entered, resume = asyncio.Event(), asyncio.Event()
    original = coordinator._serve_from_cache
    async def delayed_cache(call):
        entered.set()
        await resume.wait()
        return await original(call)
    monkeypatch.setattr(coordinator, "_serve_from_cache", delayed_cache)
    workflow = app.create_workflow("cancel-before-admission", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    task = asyncio.create_task(coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id, tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]}, snapshot_id=snapshot.snapshot_id))
    await asyncio.wait_for(entered.wait(), 10)
    call = next(c for c in coordinator._calls.values() if c.workflow_id == workflow.workflow_id)
    try:
        if shutdown:
            await coordinator.shutdown()
        else:
            assert await coordinator.cancel_logical_call(call.logical_call_id)
    finally:
        resume.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert app.repository.list_executions(workflow.workflow_id) == []
    assert app.repository.list_logical_calls(workflow.workflow_id)[0]["status"] == "CANCELLED"
    assert coordinator.scheduler.snapshot()["running"] == 0
