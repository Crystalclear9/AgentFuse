"""Integration tests: online dependency validation and detachment."""

from __future__ import annotations

import asyncio
import json

from agentfuse.core.state import EventType, ParticipantStatus, RuntimeMode, ValidationStatus
from agentfuse.scenarios.common import (
    DEMO_FILES,
    DIVERGENT_FILE,
    create_branch,
    mutate_variant_source,
    wait_for_event,
    wait_for_first_dependency,
)
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
    results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=60)

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

    await asyncio.wait_for(
        asyncio.gather(
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
        ),
        timeout=60,
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
    await asyncio.wait_for(
        asyncio.gather(
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
        ),
        timeout=60,
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
    result = await scenario_divergent(app, RuntimeMode.ONLINE, {"rounds": 3000})
    metrics = result.metrics
    assert metrics["logical_calls"] == 2
    assert metrics["physical_executions"] == 2
    assert metrics["detach_count"] == 1
    assert metrics["validation_failures"] >= 1
    assert metrics["saved_executions"] == 0
    # the detection happened before the leader finished -> the follower could
    # start its independent execution while the leader was still running.
    # The margin must be real, not zero: requirement 12.4 asks for the
    # separation to happen while the leader still has work left.
    assert metrics["detach_lead_ms_max"] is not None
    assert metrics["detach_lead_ms_max"] > 0, (
        "the follower must be separated while the leader is still computing"
    )
    assert metrics["online_detach_before_finish"] is True
    # the follower waited less than it would have waited for the leader
    assert metrics["saved_wait_ms_max"] is not None
    assert metrics["saved_wait_ms_max"] > 0
    # a candidate that never joined is not a detachment
    assert metrics["rejected_candidates"] == 0


async def test_a_late_joiner_validates_the_existing_ledger(app, snapshot):
    """A call that arrives after the leader finished must not reuse the result."""

    workflow = app.create_workflow("test-late-join")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    await asyncio.wait_for(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
        timeout=60,
    )
    await asyncio.wait_for(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
        timeout=60,
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2, "the second call must not reuse a finished execution"


async def test_a_late_joiner_validates_the_early_dependencies(app, snapshot):
    """§17.3 - a participant that joins in the middle must not miss the
    dependencies that the leader already observed.

    The join time validation compares the existing ledger with the
    follower's own snapshot: the validated prefix has to cover **all** the
    entries the leader had read at that moment.
    """

    workflow = app.create_workflow("test-late-join-prefix")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 4000}
    leader_task = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )

    # the follower joins after the leader has read several dependencies
    reads = 0

    def observer(event):
        nonlocal reads
        if event.event_type == EventType.TOOL_READ_OBSERVED:
            reads += 1

    app.event_bus.subscribe(observer)
    while reads < 2:
        await asyncio.sleep(0.01)

    follower = await asyncio.wait_for(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
        timeout=60,
    )
    leader = await asyncio.wait_for(leader_task, timeout=60)

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 1, "the follower joined the running group"

    participants = app.repository.list_participants(workflow.workflow_id)
    follower_rows = [row for row in participants if not row["is_leader"]]
    assert follower_rows, "the follower must be a participant of the group"
    follower_row = follower_rows[0]
    # the follower validated the dependencies the leader had read before the
    # join - it did not start from zero
    assert follower_row["validated_prefix_length"] >= 2, (
        "a late joiner must validate the existing ledger"
    )
    assert follower_row["validation_status"] == str(ValidationStatus.MATCH)

    # and the validation is recorded in the validation records
    validations = app.repository.list_validations()
    follower_validations = [
        validation
        for validation in validations
        if validation["logical_call_id"] == follower_row["logical_call_id"]
    ]
    assert follower_validations, "the join time validation must be recorded"
    assert all(
        validation["validation_status"] == str(ValidationStatus.MATCH)
        for validation in follower_validations
    )
    assert follower.result_hash == leader.result_hash


async def test_a_rejected_candidate_is_not_a_member_of_the_group(app, snapshot):
    """A candidate that never belonged to the group must not be counted.

    The rejection is part of the audit trail (the participant row exists), but
    the **member list** of the group only contains the calls that really
    joined it.
    """

    branch = create_branch(app, snapshot, mutate_variant_source, "branch-b")
    workflow = app.create_workflow("test-rejected-not-member")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 1200}
    leader_task = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    # the leader has to have read the conflicting file - the follower then
    # finds a mismatch in the ledger when it tries to join
    await wait_for_event(
        app.event_bus,
        EventType.TOOL_READ_OBSERVED,
        workflow_id=workflow.workflow_id,
        predicate=lambda event: (getattr(event, "metadata", None) or {}).get(
            "relative_path"
        )
        == DIVERGENT_FILE,
    )
    follower = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=branch.snapshot_id,
        )
    )
    await asyncio.wait_for(asyncio.gather(leader_task, follower), timeout=60)

    participants = app.repository.list_participants(workflow.workflow_id)
    rejected = [
        participant
        for participant in participants
        if participant["detach_kind"] == "rejected_at_join"
    ]
    assert rejected, "the candidate has to be rejected at join time"

    groups = {
        group["share_group_id"]: group
        for group in app.repository.list_share_groups(workflow.workflow_id)
    }
    for participant in rejected:
        # the group it was rejected from must not count it as a member - it
        # may of course be a member of the group it started on its own
        group = groups.get(participant["share_group_id"])
        assert group is not None, "the rejected candidate has to point at its group"
        members = set(json.loads(group["participant_calls"] or "[]"))
        assert participant["logical_call_id"] not in members, (
            "a candidate that was rejected at join time must not be counted "
            "as a member of the group that refused it"
        )
        assert group["participant_count"] == len(members), (
            "the participant count has to match the member list"
        )


async def test_a_rejected_candidate_is_recorded_as_detached(app, snapshot):
    """A join that the validation refuses must leave a DETACHED participant.

    The rejection is part of the audit trail: the database has to show that
    the call was offered to the group and that the validation refused it.
    """

    branch = create_branch(app, snapshot, mutate_variant_source, "branch-b")
    workflow = app.create_workflow("test-rejected-candidate")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 1200}
    # the leader reads the conflicting file first, therefore the follower
    # finds a mismatch in the ledger when it tries to join
    leader = app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="content_digest",
        arguments=arguments,
        snapshot_id=snapshot.snapshot_id,
    )
    await asyncio.sleep(0.2)
    follower = app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="content_digest",
        arguments=arguments,
        snapshot_id=branch.snapshot_id,
    )
    await asyncio.wait_for(asyncio.gather(leader, follower), timeout=60)

    participants = app.repository.list_participants(workflow.workflow_id)
    detached = [
        participant
        for participant in participants
        if participant["status"] == "DETACHED"
    ]
    assert detached, "the rejected candidate must be recorded as detached"
    assert detached[0]["detach_reason"]
    assert detached[0]["validation_status"] == str(ValidationStatus.MISMATCH)

    events = app.repository.list_events(workflow.workflow_id)
    assert any(
        event["event_type"] == EventType.SHARE_PARTICIPANT_DETACHED for event in events
    )


async def test_the_dependency_ledger_records_every_read(app, snapshot):
    workflow = app.create_workflow("test-ledger")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await asyncio.wait_for(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments={"files": list(DEMO_FILES)},
            snapshot_id=snapshot.snapshot_id,
        ),
        timeout=60,
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    dependencies = app.repository.list_dependencies(executions[0]["execution_id"])
    paths = [dependency["relative_path"] for dependency in dependencies]
    assert paths == sorted(DEMO_FILES)
    assert all(dependency["dependency_type"] == "FILE_READ" for dependency in dependencies)
    assert all(dependency["fingerprint"] for dependency in dependencies)


async def test_a_follower_that_fails_the_final_validation_gets_no_result(app, snapshot, monkeypatch):
    """§8.7 - the final commit gate.

    "Any logical call whose dependency validation is not complete must not be
    exposed to the result of the shared execution."

    The incremental online validation keeps the validated prefix in sync with
    the dependency ledger, therefore the gate is a safety net for the case in
    which the validation of the later dependencies did **not** reach the
    participant before the leader finished.  The test simulates exactly that
    race: the dependency is written to the ledger, but the participant's
    validated prefix does not advance.
    """

    workflow = app.create_workflow("test-final-commit-gate")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    coordinator = app.coordinator
    original = coordinator._handle_dependency

    async def lagging_handle_dependency(execution_id, message):
        await original(execution_id, message)
        # the incremental validation of this dependency does not reach the
        # participant -> its validated prefix falls behind the ledger
        for group in coordinator._groups.values():
            for participant in group.participants.values():
                if participant.is_leader:
                    continue
                if participant.status is ParticipantStatus.SHARED_WAIT:
                    participant.validated_prefix_length = max(
                        0, participant.validated_prefix_length - 1
                    )

    monkeypatch.setattr(coordinator, "_handle_dependency", lagging_handle_dependency)

    arguments = {"files": list(DEMO_FILES), "rounds": 3000}
    delivered: list[tuple[str, str | None]] = []

    def observer(event):
        if event.event_type == EventType.TOOL_RESULT_DELIVERED:
            delivered.append(
                (str(event.logical_call_id), event.physical_execution_id)
            )

    app.event_bus.subscribe(observer)

    leader_task = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )

    # the follower joins while the leader is still reading
    await wait_for_first_dependency(app.event_bus, workflow.workflow_id)
    follower = await asyncio.wait_for(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
        timeout=60,
    )
    leader = await asyncio.wait_for(leader_task, timeout=60)

    participants = app.repository.list_participants(workflow.workflow_id)
    follower_rows = [
        row for row in participants if not row["is_leader"]
    ]
    assert follower_rows, "the follower must have joined the share group"
    follower_row = follower_rows[0]
    assert follower_row["status"] == "DETACHED"
    assert (
        follower_row["detach_reason"]
        == "dependency validation incomplete at commit time"
    ), "the final commit gate must be the recorded reason"

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2, "the follower must run its own execution"
    leader_execution = executions[0]["execution_id"]

    # the follower must not have received the result of the leader
    follower_deliveries = [
        execution_id
        for call_id, execution_id in delivered
        if call_id == follower_row["logical_call_id"]
    ]
    assert leader_execution not in follower_deliveries, (
        "a follower that failed the final validation must not receive the "
        "result of the shared execution"
    )
    # the follower computed the result on its own - the deterministic tool
    # produces the same hash for the same input, but on a *different*
    # execution
    assert follower.physical_execution_id != leader.physical_execution_id
    assert follower.physical_execution_id in {
        execution["execution_id"] for execution in executions
    }
    assert follower.result_hash == leader.result_hash


async def test_the_separation_is_reliable(app):
    """§12.4 - the follower is separated **while** the leader is computing.

    The follower joins after the first dependency; the conflict is on the
    third file.  The separation therefore has to happen through the **online**
    validation - a candidate that is rejected at join time would silently lose
    the property the scenario exists to demonstrate.  The test repeats the
    scenario to catch a timing that is too tight.
    """

    for _ in range(3):
        result = await scenario_divergent(app, RuntimeMode.ONLINE, {})
        metrics = result.metrics
        assert metrics["rejected_candidates"] == 0, (
            "the follower must not be rejected at join time - the separation "
            "has to happen through the online validation"
        )
        assert metrics["detach_count"] == 1
        assert metrics["detach_lead_ms_max"] > 0, (
            "the follower has to be separated while the leader is still "
            "computing"
        )
        assert metrics["online_detach_before_finish"] is True
