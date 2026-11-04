"""Regressions found by the full-plan delivery review."""
import asyncio
import contextlib
import threading
import time
import subprocess
import sys

import httpx
import pytest

from agentfuse.core.state import EventType
from agentfuse.scenarios.common import wait_for_event


async def test_idle_warm_workers_do_not_scan_process_trees(app, monkeypatch):
    sampled = []
    original = app.coordinator._resource_monitor.sample
    def sample(pid):
        sampled.append(pid)
        return original(pid)
    monkeypatch.setattr(app.coordinator._resource_monitor, "sample", sample)
    await asyncio.to_thread(app.coordinator._pool_warmup_done.wait, 10)
    await asyncio.sleep(0.3)
    before = len(sampled)
    await asyncio.sleep(0.3)
    assert len(sampled) == before


def test_resource_monitor_counts_parent_and_child_memory():
    import psutil
    from agentfuse.monitoring.resource_monitor import ResourceMonitor
    executable = getattr(sys, "_base_executable", None) or sys.executable
    child = subprocess.Popen([executable, "-c", "import time; memory=bytearray(32*1024*1024); print('ready',flush=True); time.sleep(30)"],
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        parent = psutil.Process()
        expected = parent.memory_info().rss + psutil.Process(child.pid).memory_info().rss
        measured = ResourceMonitor().sample(parent.pid)
        assert measured >= expected - 1024*1024
    finally:
        child.kill()
        child.wait(timeout=10)
        child.stdout.close()


async def test_cancellation_kills_once_without_blocking_the_event_loop(app, snapshot, monkeypatch):
    import agentfuse.runtime.cancellation as cancellation
    original = cancellation.terminate_worker
    entered, release = threading.Event(), threading.Event()
    kills = []
    def slow_termination(*args, **kwargs):
        kills.append(args[1] if len(args) > 1 else kwargs.get("pid"))
        entered.set()
        assert release.wait(5), "test did not release the termination gate"
        return original(*args, **kwargs)
    monkeypatch.setattr(cancellation, "terminate_worker", slow_termination)
    workflow = app.create_workflow("termination-once", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    call = asyncio.create_task(app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id, tool_name="fault_injector",
        arguments={"mode": "sleep", "seconds": 30}, snapshot_id=snapshot.snapshot_id))
    event = await wait_for_event(app.event_bus, EventType.TOOL_EXECUTION_STARTED,
                                 workflow_id=workflow.workflow_id)
    cancellation_task = asyncio.create_task(app.coordinator.cancel_logical_call(event.logical_call_id))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        started = time.perf_counter()
        await asyncio.sleep(0.02)
        assert time.perf_counter() - started < 0.5, "termination blocked the runtime event loop"
    finally:
        release.set()
        await cancellation_task
        with contextlib.suppress(asyncio.CancelledError):
            await call
    execution = app.coordinator.get_execution(event.physical_execution_id)
    await asyncio.wait_for(asyncio.shield(execution.termination_task), 10)
    await app.close()
    assert kills.count(execution.pid) == 1


async def test_api_documentation_has_no_cdn_dependency(settings):
    from agentfuse.api.app import create_app
    api = create_app(settings)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://local") as client:
            document = await client.get("/api/docs")
            assert document.status_code == 200
            assert "https://" not in document.text and "http://" not in document.text
            script = await client.get("/static/api-docs.js")
            assert script.status_code == 200
            assert "https://" not in script.text and "http://" not in script.text
            schema = (await client.get("/api/openapi.json")).json()
            assert "/api/extensions/inference" in schema["paths"]
            assert (await client.get("/redoc")).status_code == 404
    finally:
        await api.state.runtime.close()


async def test_cancellation_acknowledges_persisted_state_before_the_runner_resumes(app, snapshot, monkeypatch):
    coordinator = app.coordinator
    original = coordinator._finalize_execution
    runner_waiting, resume_runner = asyncio.Event(), asyncio.Event()
    async def delayed_runner(execution, outcome):
        if asyncio.current_task() in coordinator._tasks:
            runner_waiting.set()
            await resume_runner.wait()
        await original(execution, outcome)
    monkeypatch.setattr(coordinator, "_finalize_execution", delayed_runner)
    workflow = app.create_workflow("cancel-ack", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    call = asyncio.create_task(coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id, tool_name="fault_injector",
        arguments={"mode": "sleep", "seconds": 30}, snapshot_id=snapshot.snapshot_id))
    event = await wait_for_event(app.event_bus, EventType.TOOL_EXECUTION_STARTED,
                                 workflow_id=workflow.workflow_id)
    cancel = asyncio.create_task(coordinator.cancel_logical_call(event.logical_call_id))
    try:
        await asyncio.wait_for(runner_waiting.wait(), 10)
        await asyncio.wait_for(asyncio.shield(cancel), 10)
        assert app.repository.list_executions(workflow.workflow_id)[0]["execution_status"] == "CANCELLED"
        assert app.scheduler.snapshot()["running"] == 0
    finally:
        resume_runner.set()
        await cancel
        with contextlib.suppress(asyncio.CancelledError):
            await call
    await asyncio.wait_for(asyncio.gather(*list(coordinator._tasks)), 10)
    finished = [e for e in app.repository.list_events(workflow.workflow_id)
                if e["event_type"] == EventType.TOOL_EXECUTION_FINISHED.value]
    assert len(finished) == 1
