"""Stress tests: many concurrent agents with diverging workspaces."""

from __future__ import annotations

import asyncio

from agentfuse.scenarios.common import DEMO_FILES, create_branch, mutate_variant_source
from agentfuse.tools.registry import build_default_registry
from agentfuse.workspace.reader import WorkspaceReader


async def test_many_agents_with_diverging_branches_get_correct_results(app, snapshot):
    """Ten agents, two different branches - every result must match its branch."""

    branch = create_branch(app, snapshot, mutate_variant_source, "branch")
    workflow = app.create_workflow("stress-diverging")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    snapshots = [snapshot.snapshot_id] * 5 + [branch.snapshot_id] * 5

    results = await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="content_digest",
                arguments=arguments,
                snapshot_id=snapshot_id,
            )
            for snapshot_id in snapshots
        ]
    )

    registry = build_default_registry()
    expected_base = registry.execute(
        registry.get("content_digest"),
        {"files": list(DEMO_FILES), "rounds": 0},
        WorkspaceReader(snapshot.tree_path),
    )["combined_digest"]
    expected_branch = registry.execute(
        registry.get("content_digest"),
        {"files": list(DEMO_FILES), "rounds": 0},
        WorkspaceReader(branch.tree_path),
    )["combined_digest"]
    assert expected_base != expected_branch

    for result, snapshot_id in zip(results, snapshots):
        expected = expected_base if snapshot_id == snapshot.snapshot_id else expected_branch
        assert result.result["combined_digest"] == expected

    # the two branches never share an execution, but identical branches do
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2
    assert app.repository.list_share_groups(workflow.workflow_id)


async def test_repeated_submissions_do_not_duplicate_executions(app, snapshot):
    workflow = app.create_workflow("stress-repeat")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    for _ in range(5):
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

    calls = app.repository.list_logical_calls(workflow.workflow_id)
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(calls) == 20
    # five sequential batches -> at most one execution per batch
    assert len(executions) <= 5
    assert all(call["status"] == "COMPLETED" for call in calls)
