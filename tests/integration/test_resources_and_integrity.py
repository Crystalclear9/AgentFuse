"""Integration tests: resource admission and snapshot integrity."""

from __future__ import annotations

import asyncio

from agentfuse.runtime.scheduler import ResourceScheduler
from agentfuse.scenarios.common import DEMO_FILES, create_branch


# ----------------------------------------------------------------------
# resource admission
# ----------------------------------------------------------------------
async def test_the_rss_soft_limit_blocks_new_admissions():
    scheduler = ResourceScheduler(max_workers=4, max_memory_budget_mb=1024)
    await scheduler.acquire("exec-1", 8)
    scheduler.record_rss("exec-1", 900 * 1024 * 1024)

    scheduler.max_rss_soft_limit_mb = 512
    assert scheduler.rss_limit_exceeded() is True
    allowed, reason = scheduler.admission_check(8)
    assert allowed is False
    assert "RSS" in reason

    # lowering the limit again lets the execution pass
    scheduler.max_rss_soft_limit_mb = 0
    assert scheduler.effective_rss_limit_mb == 1024
    allowed, _ = scheduler.admission_check(8)
    assert allowed is True


async def test_the_pending_queue_explains_the_waiting_reason():
    scheduler = ResourceScheduler(max_workers=1, max_memory_budget_mb=1024)
    await scheduler.acquire("exec-1", 8)
    task = asyncio.create_task(scheduler.acquire("exec-2", 8))
    await asyncio.sleep(0.05)

    snapshot = scheduler.snapshot()
    assert snapshot["waiting"] == 1
    assert snapshot["pending"][0]["execution_id"] == "exec-2"
    assert snapshot["pending"][0]["reason"]
    assert snapshot["pending"][0]["waiting_seconds"] >= 0

    scheduler.release("exec-1")
    await asyncio.wait_for(task, timeout=1.0)


async def test_the_resource_snapshot_exposes_the_limits():
    scheduler = ResourceScheduler(max_workers=3, max_memory_budget_mb=256)
    snapshot = scheduler.snapshot()
    assert snapshot["max_workers"] == 3
    assert snapshot["max_memory_budget_mb"] == 256
    assert snapshot["effective_rss_limit_mb"] == 256
    assert snapshot["rss_limit_exceeded"] is False
    assert snapshot["pending"] == []


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
