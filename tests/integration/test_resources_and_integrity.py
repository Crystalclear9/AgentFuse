"""Integration tests: resource admission and snapshot integrity."""

from __future__ import annotations

import asyncio

from agentfuse.core.state import EventType
from agentfuse.runtime.scheduler import LocalResourceController
from agentfuse.scenarios.common import DEMO_FILES, create_branch
from tests.support import wait_until


# ----------------------------------------------------------------------
# resource admission
# ----------------------------------------------------------------------
async def test_the_rss_soft_limit_blocks_new_admissions():
    """The admission has to use the **real** path of the scheduler.

    The resource block lives in the scheduling strategy - the scheduler only
    asks it.  A second (dead) implementation of the same rules could drift
    away from the one that really admits the executions.
    """

    scheduler = LocalResourceController(max_workers=4, max_memory_budget_mb=1024)
    await scheduler.acquire("exec-1", 8)
    scheduler.record_rss("exec-1", 900 * 1024 * 1024)

    scheduler.max_rss_soft_limit_mb = 512
    assert scheduler.rss_limit_exceeded() is True

    # the execution waits - the strategy refuses it
    task = asyncio.create_task(scheduler.acquire("exec-2", 8))
    assert await wait_until(lambda: scheduler.snapshot()["waiting"] >= 1)
    reason = scheduler.snapshot()["pending"][0]["reason"]
    assert "RSS" in reason, reason

    # lowering the limit again lets the execution pass - the scheduler
    # re-evaluates the queue when the observed memory changes
    scheduler.max_rss_soft_limit_mb = 0
    assert scheduler.effective_rss_limit_mb == 1024
    scheduler.record_rss("exec-1", 900 * 1024 * 1024)
    await asyncio.wait_for(task, timeout=5)
    scheduler.release("exec-1")
    scheduler.release("exec-2")


async def test_the_pending_queue_explains_the_waiting_reason():
    scheduler = LocalResourceController(max_workers=1, max_memory_budget_mb=1024)
    await scheduler.acquire("exec-1", 8)
    task = asyncio.create_task(scheduler.acquire("exec-2", 8))
    assert await wait_until(lambda: scheduler.snapshot()["waiting"] >= 1)

    snapshot = scheduler.snapshot()
    assert snapshot["waiting"] == 1
    assert snapshot["pending"][0]["execution_id"] == "exec-2"
    assert snapshot["pending"][0]["reason"]
    assert snapshot["pending"][0]["waiting_seconds"] >= 0

    scheduler.release("exec-1")
    await asyncio.wait_for(task, timeout=1.0)


async def test_the_resource_snapshot_exposes_the_limits():
    scheduler = LocalResourceController(max_workers=3, max_memory_budget_mb=256)
    snapshot = scheduler.snapshot()
    assert snapshot["max_workers"] == 3
    assert snapshot["max_memory_budget_mb"] == 256
    assert snapshot["effective_rss_limit_mb"] == 256
    assert snapshot["rss_limit_exceeded"] is False
    assert snapshot["pending"] == []


async def test_a_shared_call_does_not_consume_a_second_worker_slot(app, snapshot):
    """§9.2 / §17.5 - a shared call occupies exactly one execution slot.

    The admission is granted per **physical execution**, therefore a follower
    that joins a running group must not take a second slot.  The scheduler is
    observed while the shared execution runs.
    """

    app.scheduler.max_workers = 1
    workflow = app.create_workflow("test-shared-slot")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    observed: list[dict] = []

    def observer(event):
        if event.event_type == EventType.TOOL_READ_OBSERVED:
            observed.append(app.scheduler.snapshot())

    app.event_bus.subscribe(observer)

    arguments = {"files": list(DEMO_FILES), "rounds": 800}
    await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 1, "the two calls must share one physical execution"

    assert observed, "the shared execution must have been observed"
    # while the shared execution runs, exactly one slot is occupied and
    # nothing waits for a second one
    assert all(item["running"] == 1 for item in observed), (
        "a shared call must not occupy a second worker slot"
    )
    assert all(item["waiting"] == 0 for item in observed)
    # and the whole share group consumed exactly one admission
    assert app.scheduler.snapshot()["admitted_total"] == 1


async def test_the_resource_samples_are_written_for_the_real_worker(app, snapshot):
    """§9.3 - the RSS is measured on the process that really computes."""

    workflow = app.create_workflow("test-resource-samples")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="content_digest",
        arguments={"files": list(DEMO_FILES), "rounds": 400},
        snapshot_id=snapshot.snapshot_id,
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    assert executions[0]["peak_rss"], "the peak RSS must be recorded"
    samples = app.repository.list_resource_samples(executions[0]["execution_id"])
    assert samples, "the resource samples must be stored"
    assert all(sample["rss_bytes"] for sample in samples)


# ----------------------------------------------------------------------
# snapshot integrity
# ----------------------------------------------------------------------
async def test_a_modified_snapshot_is_not_shared(app, snapshot):
    """A snapshot that was modified outside of AgentFuse must not be shared."""

    branch = create_branch(app, snapshot, None, "branch")
    workflow = app.create_workflow("test-integrity")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    # somebody modifies the snapshot behind the runtime's back
    target = branch.tree_path / "src" / "alpha.py"
    target.write_text("tampered = True\n", encoding="utf-8")

    assert app.snapshots.check_integrity(branch.snapshot_id) is False

    arguments = {"files": list(DEMO_FILES)}
    results = await asyncio.gather(
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
            snapshot_id=branch.snapshot_id,
        ),
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2, "a tampered snapshot must not join a shared execution"

    groups = app.repository.list_share_groups(workflow.workflow_id)
    # the tampered call leads its own group, it never becomes a follower
    followers = [
        participant
        for participant in app.repository.list_participants(workflow.workflow_id)
        if not participant["is_leader"]
    ]
    assert followers == []
    assert results[0].result_hash != results[1].result_hash


async def test_an_intact_snapshot_passes_the_integrity_check(app, snapshot):
    assert app.snapshots.check_integrity(snapshot.snapshot_id) is True
    branch = create_branch(app, snapshot, None, "branch")
    assert app.snapshots.check_integrity(branch.snapshot_id) is True


async def test_a_snapshot_tampered_after_the_join_is_not_shared_in_the_deferred_mode(
    app, snapshot
):
    """The final commit check has to run in **every** mode.

    The deferred mode validates the dependency prefix when the leader
    finishes.  A snapshot that was modified **after** the join does not change
    the prefix - only the integrity check can catch it.  The result must not be
    shared in that case either.
    """

    from agentfuse.core.state import EventType, RuntimeMode
    from agentfuse.scenarios.common import wait_for_event, wait_for_running_execution

    branch = create_branch(app, snapshot, None, "branch")
    workflow = app.create_workflow("test-deferred-integrity", mode=RuntimeMode.DEFERRED)
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 12000}
    leader = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await wait_for_running_execution(app.event_bus, workflow.workflow_id)
    follower = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=branch.snapshot_id,
        )
    )
    # the follower waits for the leader - that is the moment of the join
    await wait_for_event(
        app.event_bus,
        EventType.TOOL_CALL_WAITING,
        workflow_id=workflow.workflow_id,
        timeout=30,
    )

    # somebody modifies the snapshot **after** the join - and outside of the
    # files the tool reads, so that the dependency prefix stays valid
    target = branch.tree_path / "README.md"
    target.write_text("tampered = True\n", encoding="utf-8")
    # the check has to be forced - the result of the join is cached
    assert app.snapshots.check_integrity(branch.snapshot_id, force=True) is False

    await asyncio.gather(leader, follower, return_exceptions=True)

    participants = app.repository.list_participants(workflow.workflow_id)
    followers = [
        participant
        for participant in participants
        if not participant["is_leader"]
    ]
    assert followers, "the follower has to join the group"
    assert followers[0]["status"] == "DETACHED", (
        "the tampered snapshot must not receive the shared result - "
        f"the participant is {followers[0]['status']}"
    )
