"""Regression tests for the worker lifecycle.

The worker pool used to leak processes:

* a worker that was created while the runtime was shutting down registered
  itself *after* the shutdown had swept the handles and survived forever;
* a virtual environment on Windows starts the real interpreter through a
  launcher stub - killing the launcher does not stop a worker that is busy
  computing, because it never reads stdin again.

The tests below reproduce both situations on purpose.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

import psutil
import pytest

from agentfuse.core.state import RuntimeMode
from agentfuse.scenarios.common import DEMO_FILES

ME = os.getpid()


def worker_processes() -> list[int]:
    """The pids of the processes that really run the worker child."""

    found: list[int] = []
    for pid in psutil.pids():
        if pid == ME:
            continue
        try:
            cmdline = psutil.Process(pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if len(cmdline) >= 2 and "python" in cmdline[0].lower() and cmdline[-1].endswith(
            "worker_child"
        ):
            found.append(pid)
    return found


@pytest.fixture()
def slow_worker(monkeypatch):
    """Make the worker start slow - like it is on a loaded CI machine."""

    import agentfuse.runtime.coordinator as coordinator

    original = coordinator.start_worker

    def slow_start():
        time.sleep(1.0)
        return original()

    monkeypatch.setattr(coordinator, "start_worker", slow_start)
    return original


async def test_a_worker_created_during_the_shutdown_does_not_leak(app, snapshot, slow_worker):
    """The worker that is created while the runtime closes must not survive."""

    before = set(worker_processes())
    workflow = app.create_workflow("test-leak-race")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    call = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments={"files": ["src/alpha.py"]},
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.3)
    # the runtime closes while the worker is still being created
    await app.coordinator.shutdown()
    call.cancel()
    await asyncio.sleep(2.0)

    leaked = [pid for pid in worker_processes() if pid not in before]
    assert not leaked, f"worker processes survived the shutdown: {leaked}"


async def test_the_shutdown_is_idempotent(app, snapshot):
    await app.coordinator.shutdown()
    await app.coordinator.shutdown()
    assert not app.coordinator._worker_handles
    assert app.coordinator.worker_pids() == []


async def test_terminate_retries_until_the_process_is_gone(app, snapshot):
    """A failed termination must be retried, not silently skipped."""

    from agentfuse.runtime.pool import start_worker

    handle = start_worker()
    assert handle.process.pid
    # simulate a failing first attempt
    original_kill = handle.process.kill

    def broken_kill(*args, **kwargs):
        raise OSError("simulated failure")

    handle.process.kill = broken_kill
    try:
        handle.terminate()
        assert handle.terminated is False or handle.confirmed_gone is True
    finally:
        handle.process.kill = original_kill
    # the second attempt has to kill the worker
    assert handle.terminate() is True
    assert handle.confirmed_gone is True
    time.sleep(0.3)
    assert not psutil.pid_exists(handle.process.pid)


async def test_the_real_worker_process_is_terminated(app, snapshot):
    """The process that really executes the tool must be gone, not only the launcher.

    A virtual environment on Windows starts the interpreter through a launcher
    stub; everywhere else the started process *is* the worker.  The test
    therefore asserts the invariant instead of a platform specific shape.
    """

    import json

    from agentfuse.runtime.pool import start_worker

    handle = start_worker()
    started_pid = handle.process.pid
    children = list(handle.child_pids)
    try:
        # the worker reports which process really executes the tool
        message = json.loads(handle.process.stdout.readline())
        assert message["t"] == "hello"
        real_pid = int(message["pid"])
        assert real_pid == started_pid or real_pid in children
    finally:
        handle.terminate()
    time.sleep(0.5)

    for pid in {started_pid, real_pid, *children}:
        try:
            assert not psutil.Process(pid).is_running(), f"process {pid} survived"
        except psutil.NoSuchProcess:
            continue


async def test_no_worker_survives_the_runtime_lifetime(app, snapshot):
    """The invariant: after the runtime is closed no worker of it survives."""

    workflow = app.create_workflow("test-lifetime")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments={"files": list(DEMO_FILES)},
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(4)
        ]
    )
    pids = {
        execution["worker_pid"]
        for execution in app.repository.list_executions(workflow.workflow_id)
    }
    assert pids

    await app.coordinator.shutdown()
    time.sleep(0.5)

    alive = []
    for pid in pids:
        try:
            if psutil.Process(int(pid)).is_running():
                alive.append(pid)
        except psutil.NoSuchProcess:
            continue
    assert not alive, f"worker processes are still running: {alive}"


async def test_the_worker_reports_its_real_pid(app, snapshot):
    """The worker tells the runtime which process really executes the tool."""

    import json

    from agentfuse.runtime.pool import start_worker

    handle = start_worker()
    try:
        # the first protocol message is the hello with the real pid
        line = handle.process.stdout.readline()
        message = json.loads(line)
        assert message["t"] == "hello"
        # the reported pid is the process that really executes the tool: the
        # child of the launcher (Windows) or the started process itself
        assert message["pid"] == handle.process.pid or message["pid"] in handle.child_pids
        assert psutil.pid_exists(message["pid"])
        assert psutil.Process(message["pid"]).is_running()
    finally:
        handle.terminate()


async def test_the_runtime_owns_exactly_its_workers(app, snapshot):
    """Invariant: the number of handles equals the number of live workers.

    Every worker that the runtime starts is registered, and every worker that
    is registered is alive.  A mismatch means that a worker was lost (leak) or
    that a stale handle is kept around.
    """

    workflow = app.create_workflow("test-invariant")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments={"files": list(DEMO_FILES)},
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(4)
        ]
    )
    await asyncio.sleep(0.2)

    handles = list(app.coordinator._worker_handles)
    live = [pid for pid in app.coordinator.worker_pids() if pid]
    assert len(handles) == len(live)
    for handle in handles:
        assert handle.is_alive()
        assert handle.terminated is False
