"""Integration tests: online dependency validation and detachment."""

from __future__ import annotations

import asyncio

from agentfuse.core.state import EventType, RuntimeMode, ValidationStatus
from agentfuse.scenarios.common import DEMO_FILES, create_branch, mutate_variant_source
from agentfuse.scenarios.definitions import scenario_divergent


async def test_a_conflicting_dependency_detaches_the_follower(app, snapshot):
    branch = create_branch(app, snapshot, mutate_variant_source, "branch-b")
    workflow = app.create_workflow("test-divergent")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 1200}
    tasks = [
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
            snapshot_id=branch.snapshot_id,
        ),
    ]
    results = await asyncio.gather(*tasks)

    executions = app.repository.list_executions(workflow.workflow_id)
    participants = app.repository.list_participants(workflow.workflow_id)
    validations = app.repository.list_validations()

    # the follower had to run its own execution
    assert len(executions) == 2
    assert any(participant["status"] == "DETACHED" for participant in participants)
    assert any(
        validation["validation_status"] == str(ValidationStatus.MISMATCH)
        for validation in validations
    )
    # both results are correct for their own workspace state
    assert results[0].result_hash != results[1].result_hash


async def test_the_detection_happens_while_the_leader_is_still_running(app, snapshot):
    branch = create_branch(app, snapshot, mutate_variant_source, "branch-b")
    workflow = app.create_workflow("test-divergent-timing")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 1200}
    detached_at: list[float] = []
    leader_finished: list[float] = []

    def observer(event):
        if event.event_type == EventType.SHARE_PARTICIPANT_DETACHED:
            detached_at.append(event.timestamp)
        if event.event_type == EventType.TOOL_EXECUTION_FINISHED:
            leader_finished.append(event.timestamp)

    app.event_bus.subscribe(observer)

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
            snapshot_id=branch.snapshot_id,
        ),
    )

    assert detached_at, "the follower must have been detached"
    assert leader_finished, "the leader must have finished"
    # the conflict was observed before the leader finished its computation
    assert detached_at[0] <= leader_finished[0]


async def test_the_detached_follower_uses_its_own_snapshot(app, snapshot):
    branch = create_branch(app, snapshot, mutate_variant_source, "branch-b")
    workflow = app.create_workflow("test-divergent-snapshot")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 200}
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
            snapshot_id=branch.snapshot_id,
        ),
    )

    calls = {call["logical_call_id"]: call for call in app.repository.list_logical_calls(workflow.workflow_id)}
    executions = {
        execution["execution_id"]: execution
        for execution in app.repository.list_executions(workflow.workflow_id)
    }
    snapshots_used = {
        executions[call["physical_execution_id"]]["source_snapshot_id"]
        for call in calls.values()
        if call["physical_execution_id"] in executions
    }
    assert snapshots_used == {snapshot.snapshot_id, branch.snapshot_id}


async def test_the_divergent_scenario_reports_the_expected_numbers(app):
    result = await scenario_divergent(app, RuntimeMode.ONLINE, {"rounds": 1200})
    metrics = result.metrics
    assert metrics["logical_calls"] == 2
    assert metrics["physical_executions"] == 2
    assert metrics["detach_count"] == 1
    assert metrics["validation_failures"] >= 1
    assert metrics["saved_executions"] == 0
    # the detection happened before the leader finished -> the follower could
    # start its independent execution while the leader was still running
    assert metrics["detach_lead_ms_max"] is not None


async def test_a_late_joiner_validates_the_existing_ledger(app, snapshot):
    """A call that arrives after the leader finished must not reuse the result."""

    workflow = app.create_workflow("test-late-join")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments=arguments,
        snapshot_id=snapshot.snapshot_id,
    )
    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments=arguments,
        snapshot_id=snapshot.snapshot_id,
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2, "the second call must not reuse a finished execution"


async def test_the_dependency_ledger_records_every_read(app, snapshot):
    workflow = app.create_workflow("test-ledger")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": list(DEMO_FILES)},
        snapshot_id=snapshot.snapshot_id,
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    dependencies = app.repository.list_dependencies(executions[0]["execution_id"])
    paths = [dependency["relative_path"] for dependency in dependencies]
    assert paths == sorted(DEMO_FILES)
    assert all(dependency["dependency_type"] == "FILE_READ" for dependency in dependencies)
    assert all(dependency["fingerprint"] for dependency in dependencies)
