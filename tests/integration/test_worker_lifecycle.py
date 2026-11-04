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
import time

import psutil
import pytest

from tests.support import wait_for_process_exit, wait_until

from agentfuse.scenarios.common import DEMO_FILES

ME = os.getpid()
OWNED_WORKERS: dict[int, float] = {}


def worker_processes() -> list[int]:
    """The pids of the processes that really run the worker child."""

    found: list[int] = []
    for process in psutil.process_iter(["pid", "name"]):
        pid = process.pid
        if pid == ME:
            continue
        if "python" not in (process.info["name"] or "").lower():
            continue
        try:
            cmdline = process.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if len(cmdline) >= 2 and "python" in cmdline[0].lower() and cmdline[-1].endswith(
            "worker_child"
        ):
            try:
                created = process.create_time()
                if OWNED_WORKERS.get(pid) == created or any(parent.pid == ME for parent in process.parents()):
                    OWNED_WORKERS[pid] = created
                    found.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    for pid in list(OWNED_WORKERS):
        if pid not in found:
            OWNED_WORKERS.pop(pid)
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
    # the baseline is taken **here** and not at the beginning of the test: a
    # worker of an earlier test may still be shutting down and would be
    # counted as a survivor although it does not belong to this runtime
    before = set(worker_processes())
    # the runtime closes while the worker is still being created
    await app.coordinator.shutdown()
    call.cancel()
    # the worker must be gone - the wait is bounded, the test fails if it is
    # not.  Only the workers that appeared **during** this test are waited
    # for: a worker of an earlier test may still be shutting down and does
    # not belong to this runtime.
    new_workers = [pid for pid in worker_processes() if pid not in before]
    assert await wait_for_process_exit(new_workers), (
        "worker processes survived the shutdown"
    )

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
    assert await wait_for_process_exit([handle.process.pid]), (
        "the killed worker is still running"
    )
    assert not psutil.pid_exists(handle.process.pid)


async def test_the_real_worker_process_is_terminated(app, snapshot, monkeypatch):
    """The process that really executes the tool must be gone, not only the launcher.

    A virtual environment on Windows starts the interpreter through a launcher
    stub; everywhere else the started process *is* the worker.  The test
    therefore asserts the invariant instead of a platform specific shape.
    """

    import json

    from agentfuse.runtime.pool import start_worker
    from agentfuse.runtime import pool
    from tests.support import wait_until

    # Force the slow-start race: early child discovery is only a best effort.
    # The authoritative hello message must still let us terminate the worker.
    monkeypatch.setattr(pool, "discover_children", lambda pid: [])
    handle = start_worker()
    started_pid = handle.process.pid
    children = list(handle.child_pids)
    identities = {}
    try:
        # the worker reports which process really executes the tool
        message = json.loads(handle.process.stdout.readline())
        assert message["t"] == "hello"
        real_pid = int(message["pid"])
        handle.worker_pid = real_pid
        if real_pid != started_pid:
            assert started_pid in {parent.pid for parent in psutil.Process(real_pid).parents()}
        for pid in {started_pid, real_pid, *children}:
            identities[pid] = psutil.Process(pid).create_time()
    finally:
        handle.terminate()
    def all_original_processes_are_gone():
        for pid, created in identities.items():
            try:
                process = psutil.Process(pid)
                if process.create_time() == created and process.is_running():
                    return False
            except psutil.NoSuchProcess:
                continue
        return True
    assert await wait_until(all_original_processes_are_gone), "original worker survived"


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
    assert await wait_for_process_exit(list(pids)), (
        "worker processes survived the shutdown"
    )

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

    tasks = [
        asyncio.create_task(
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments={"files": list(DEMO_FILES)},
                snapshot_id=snapshot.snapshot_id,
            )
        )
        for _ in range(4)
    ]
    await asyncio.gather(*tasks)
    # The invariant has to hold once the runtime has settled: a worker that is
    # being terminated is still registered for a moment - the test waits for
    # the state in which the two lists agree.
    state: dict[str, list] = {}

    def settled() -> bool:
        handles = list(app.coordinator._worker_handles)
        live = [pid for pid in app.coordinator.worker_pids() if pid]
        if len(handles) != len(live):
            return False
        # the two lists are read in the same moment - otherwise the test would
        # compare two different states
        state["handles"] = handles
        state["live"] = live
        return True

    assert await wait_until(settled), (
        "every worker that is registered has to be alive - "
        f"handles={len(list(app.coordinator._worker_handles))} "
        f"live={len([pid for pid in app.coordinator.worker_pids() if pid])}"
    )

    handles = state["handles"]
    live = state["live"]
    assert len(handles) == len(live)
    for handle in handles:
        assert handle.is_alive()
        assert handle.terminated is False
