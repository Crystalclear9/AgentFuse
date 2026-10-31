"""Integration tests: concurrency, races and cancellation semantics."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from agentfuse.core.state import EventType
from agentfuse.scenarios.common import (
    DEMO_FILES,
    create_branch,
    mutate_variant_source,
    wait_for_event,
    wait_for_first_dependency,
    wait_for_running_execution,
)


def _process_alive(pid: int | None) -> bool:
    """True when the process with the given pid still exists."""

    if not pid:
        return False
    try:
        import psutil

        process = psutil.Process(int(pid))
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


async def test_many_concurrent_identical_calls_create_one_execution(app, snapshot):
    """Ten agents submit the same call at the same time."""

    workflow = app.create_workflow("test-race")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    results = await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments=arguments,
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(10)
        ]
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    calls = app.repository.list_logical_calls(workflow.workflow_id)
    assert len(executions) == 1
    assert len(calls) == 10
    assert len({result.result_hash for result in results}) == 1


async def test_no_logical_call_is_delivered_twice(app, snapshot):
    workflow = app.create_workflow("test-idempotency")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    delivered: list[str] = []

    def observer(event):
        if event.event_type == EventType.TOOL_RESULT_DELIVERED and event.logical_call_id:
            delivered.append(event.logical_call_id)

    app.event_bus.subscribe(observer)

    arguments = {"files": list(DEMO_FILES)}
    await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments=arguments,
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(6)
        ]
    )

    assert len(delivered) == len(set(delivered)) == 6


async def test_a_cancelled_follower_does_not_stop_the_leader(app, snapshot):
    workflow = app.create_workflow("test-cancel-follower")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 1500}
    leader = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.05)
    follower = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.1)

    cancelled = False
    for call in app.coordinator.active_calls(workflow.workflow_id):
        if call.status.value in ("SHARED_WAIT", "WAITING"):
            await app.coordinator.cancel_logical_call(call.logical_call_id, "test cancel")
            cancelled = True
            break
    assert cancelled, "the follower never entered the shared wait state"

    with pytest.raises(asyncio.CancelledError):
        await follower
    result = await asyncio.wait_for(leader, timeout=30)
    assert result.result is not None


async def test_a_cancelled_leader_keeps_the_follower_alive(app, snapshot):
    workflow = app.create_workflow("test-cancel-leader")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 1500}
    leader = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.05)
    follower = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.1)

    leader_call = None
    for call in app.coordinator.active_calls(workflow.workflow_id):
        if call.physical_execution_id and call.status.value == "RUNNING_INDEPENDENTLY":
            leader_call = call
            break
    assert leader_call is not None, "no leader call was found"
    await app.coordinator.cancel_logical_call(leader_call.logical_call_id, "leader cancelled")

    with pytest.raises(asyncio.CancelledError):
        await leader
    result = await asyncio.wait_for(follower, timeout=30)
    assert result.result is not None


async def test_cancelling_every_participant_terminates_the_execution(app, snapshot):
    workflow = app.create_workflow("test-cancel-all")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    # The workload is far longer than the time until the cancellation, so
    # the execution is really running when it is cancelled - the margin must
    # not depend on the speed of the machine.
    arguments = {"files": list(DEMO_FILES), "rounds": 20000}
    tasks = [
        asyncio.create_task(
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="content_digest",
                arguments=arguments,
                snapshot_id=snapshot.snapshot_id,
            )
        )
        for _ in range(2)
    ]
    await asyncio.sleep(0.4)
    for call in list(app.coordinator.active_calls(workflow.workflow_id)):
        await app.coordinator.cancel_logical_call(call.logical_call_id, "cancel all")

    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=30)

    executions = app.repository.list_executions(workflow.workflow_id)
    assert executions
    # the execution must really have been terminated - a completed or failed
    # execution would mean that the workers kept running
    assert all(
        execution["execution_status"] == "CANCELLED" for execution in executions
    ), [execution["execution_status"] for execution in executions]
    # and the worker processes must be gone
    for execution in executions:
        assert not _process_alive(execution["worker_pid"])
        assert not _process_alive(execution["worker_root_pid"])


async def test_a_follower_that_joins_while_the_leader_ends_gets_no_shared_result(
    app, snapshot
):
    """§17.4 - the follower joins while the leader is finishing.

    The race window is the commit phase of the share group: the leader has
    finished, the group is validating and the result is about to be
    delivered.  A follower that arrives in that window must not receive the
    result of the leader.
    """

    workflow = app.create_workflow("test-join-during-commit")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 3000}
    leader_task = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )

    # the follower submits its call exactly when the leader's execution ends
    await wait_for_event(
        app.event_bus,
        EventType.TOOL_EXECUTION_FINISHED,
        workflow_id=workflow.workflow_id,
        timeout=30.0,
    )
    follower = await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="content_digest",
        arguments=arguments,
        snapshot_id=snapshot.snapshot_id,
    )
    leader = await leader_task

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2, (
        "a call that arrives while the leader finishes must run on its own"
    )
    # the follower did not reuse the leader's result
    assert follower.physical_execution_id != leader.physical_execution_id


async def test_a_follower_that_cancels_during_the_delivery_gets_one_result(app, snapshot):
    """§17.4 - the follower cancels while the result is being returned.

    The cancellation and the delivery happen under the same lock; the call
    must receive the result at most once and the leader must not be affected.
    """

    workflow = app.create_workflow("test-cancel-during-delivery")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 400}
    delivered: list[str] = []

    def observer(event):
        if event.event_type == EventType.TOOL_RESULT_DELIVERED:
            delivered.append(str(event.logical_call_id))

    app.event_bus.subscribe(observer)

    leader_task = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await wait_for_first_dependency(app.event_bus, workflow.workflow_id)
    follower_task = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    # the rendezvous must be deterministic: the follower has to have joined
    # the group before it can be cancelled
    for _ in range(200):
        joined = False
        for call in list(app.coordinator.active_calls(workflow.workflow_id)):
            if not call.share_group_id:
                continue
            group = app.coordinator.get_group(call.share_group_id)
            if group is None:
                continue
            participant = group.participants.get(call.logical_call_id)
            if participant is not None and not participant.is_leader:
                joined = True
                await app.coordinator.cancel_logical_call(call.logical_call_id, "cancelled")
        if joined:
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the follower did not join the share group")

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(follower_task, timeout=30)
    leader = await asyncio.wait_for(leader_task, timeout=30)

    # the leader still received its result
    assert leader.result_hash
    # and no call was delivered twice
    assert len(delivered) == len(set(delivered))


async def test_a_failing_execution_releases_the_participants(app, snapshot):
    """Several callers wait for one failing execution - nobody may hang."""

    workflow = app.create_workflow("test-shared-failure")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    missing = ["src/does_not_exist.py"]
    results = await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments={"files": missing},
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(3)
        ],
        return_exceptions=True,
    )
    assert all(isinstance(result, Exception) for result in results)
    for result in results:
        assert "not found" in str(result) or "does not exist" in str(result)


async def test_the_participants_of_a_failed_group_are_failed(app, snapshot):
    """§10.1 - every waiting participant receives a clear status change.

    A participant must never stay in the waiting state when the shared
    execution failed - the database would show a group that failed while its
    participants still wait for a result that will never arrive.
    """

    workflow = app.create_workflow("test-failed-participants")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    missing = ["src/does_not_exist.py"]
    await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments={"files": missing},
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(3)
        ],
        return_exceptions=True,
    )

    groups = app.repository.list_share_groups(workflow.workflow_id)
    assert groups, "the calls must have formed a share group"
    participants = app.repository.list_participants(workflow.workflow_id)
    assert participants, "the group must have participants"
    for participant in participants:
        assert participant["status"] == "FAILED", (
            "a participant of a failed group must be FAILED, not "
            f"{participant['status']}"
        )
        assert participant["detach_reason"]


async def test_a_crashing_worker_is_detected(app, snapshot):
    workflow = app.create_workflow("test-crash")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    with pytest.raises(Exception) as error:
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "exit", "exit_code": 9},
            snapshot_id=snapshot.snapshot_id,
        )
    assert "exit" in str(error.value).lower() or "crash" in str(error.value).lower()

    # the crashed worker must be reaped - not only detected
    executions = app.repository.list_executions(workflow.workflow_id)
    assert executions
    assert executions[0]["execution_status"] in ("CRASHED", "FAILED")
    assert not _process_alive(executions[0]["worker_pid"])
    assert not _process_alive(executions[0]["worker_root_pid"])


async def test_a_hanging_worker_is_terminated_by_the_timeout(app, snapshot):
    workflow = app.create_workflow("test-timeout")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    with pytest.raises(Exception) as error:
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "sleep", "seconds": 30},
            snapshot_id=snapshot.snapshot_id,
            timeout_seconds=1.0,
        )
    assert "timed out" in str(error.value)


async def test_no_worker_processes_are_leaked(app, snapshot):
    import psutil

    workflow = app.create_workflow("test-leak")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
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
    await asyncio.sleep(0.3)

    pids = {execution["worker_pid"] for execution in app.repository.list_executions(workflow.workflow_id)}

    def running(pids: set) -> list:
        """The pids that are still alive *and* are still the same process.

        A pid can be reused by the operating system once the worker is gone -
        the creation time tells the two apart.
        """

        alive = []
        for pid in pids:
            if pid is None:
                continue
            try:
                process = psutil.Process(int(pid))
                if process.is_running():
                    alive.append(pid)
            except psutil.NoSuchProcess:
                continue
        return alive

    def identity(pids: set) -> dict:
        """The creation time of every worker that is currently alive."""

        known = {}
        for pid in pids:
            if pid is None:
                continue
            try:
                known[pid] = psutil.Process(int(pid)).create_time()
            except psutil.NoSuchProcess:
                continue
        return known

    # Workers of a warm pool stay alive on purpose (they wait for the next
    # job) - but they must not execute anything any more.
    for pid in running(pids):
        process = psutil.Process(int(pid))
        assert not process.is_running() or process.cpu_times().user < 10

    # after the runtime shuts down no worker may survive
    known = identity(pids)
    await app.coordinator.shutdown()
    await asyncio.sleep(0.3)

    survivors = []
    for pid in running(pids):
        try:
            process = psutil.Process(int(pid))
        except psutil.NoSuchProcess:
            continue
        if pid in known and process.create_time() != known[pid]:
            # the pid was reused by an unrelated process
            continue
        survivors.append(pid)
    assert not survivors, f"worker processes are still running: {survivors}"


async def test_a_detached_follower_never_gets_a_second_execution(app, snapshot):
    """§8.2 - a logical call belongs to at most one running execution.

    The follower is separated while the leader is still computing.  When the
    leader then fails with a retryable error, the failure must not start a
    second execution for the follower: it already owns one of its own.
    """

    branch = create_branch(app, snapshot, mutate_variant_source, "branch-b")
    workflow = app.create_workflow("test-no-second-execution")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    # The workload is chosen so that the conflict is found **before** the
    # timeout of the leader fires: the follower reads the second file, the
    # timeout is two seconds later.  The margin keeps the test independent of
    # the speed of the machine.
    arguments = {"files": list(DEMO_FILES), "rounds": 30000}
    leader = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
            timeout_seconds=2.0,
        )
    )
    # the follower joins while the leader is reading
    await wait_for_first_dependency(app.event_bus, workflow.workflow_id)
    follower = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=branch.snapshot_id,
            timeout_seconds=60.0,
        )
    )
    await asyncio.gather(leader, follower, return_exceptions=True)

    calls = app.repository.list_logical_calls(workflow.workflow_id)
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(calls) == 2

    # the executions a call started - the events are the runtime's own truth
    started: dict[str, list[str]] = {}
    for event in app.repository.list_events(workflow.workflow_id):
        if event["event_type"] != EventType.TOOL_EXECUTION_STARTED:
            continue
        started.setdefault(str(event["logical_call_id"]), []).append(
            str(event["physical_execution_id"])
        )

    # the follower was separated and owns an execution of its own - it must
    # not get a second one when the leader fails
    separated = [
        participant
        for participant in app.repository.list_participants(workflow.workflow_id)
        if participant["detach_kind"] == "after_join"
    ]
    assert separated, "the follower must have been separated"
    follower_call_id = separated[0]["logical_call_id"]
    assert len(started.get(follower_call_id, [])) == 1, (
        "the separated follower must own exactly one physical execution, "
        f"it owns {started.get(follower_call_id)}"
    )
    assert all(
        execution["execution_status"] != "RUNNING"
        for execution in executions
        if execution["execution_id"] in started.get(follower_call_id, [])
    ), "no execution of the follower may be left running"


async def test_every_call_owns_at_most_one_running_execution(app, snapshot):
    """§8.2 - the invariant holds after a normal shared execution as well."""

    workflow = app.create_workflow("test-one-execution-per-call")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES), "rounds": 200}
    await asyncio.gather(
        *[
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="content_digest",
                arguments=arguments,
                snapshot_id=snapshot.snapshot_id,
            )
            for _ in range(4)
        ]
    )

    calls = app.repository.list_logical_calls(workflow.workflow_id)
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(calls) == 4
    for call in calls:
        assert call["physical_execution_id"], "every call has its own execution"
    # the shared execution is used by all four calls
    assert len({call["physical_execution_id"] for call in calls}) == 1
    assert len(executions) == 1


async def test_a_runtime_failure_is_not_reported_as_a_worker_crash(app, snapshot):
    """§10.1 - the failure kinds have to be distinguishable.

    A runtime that cannot start the computation did not lose a worker - it
    is a runtime error, not a crash.
    """

    workflow = app.create_workflow("test-internal-error")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    with pytest.raises(Exception) as error:
        await app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments={"files": list(DEMO_FILES)},
            # the snapshot does not exist -> the worker cannot be started
            snapshot_id="snap-does-not-exist",
        )
    assert getattr(error.value, "kind", None) == "internal_error", (
        "a runtime failure must not be reported as a worker crash: "
        f"{getattr(error.value, 'kind', None)}"
    )

    # the distinction has to survive in the database as well (requirement
    # 10.1): an internal error is neither a tool error nor a crash
    calls = app.repository.list_logical_calls(workflow.workflow_id)
    executions = app.repository.list_executions(workflow.workflow_id)
    assert calls and executions
    assert calls[0]["error_kind"] == "internal_error", (
        f"the kind has to be persisted: {calls[0]['error_kind']}"
    )
    assert executions[0]["error_kind"] == "internal_error"
    assert executions[0]["execution_status"] == "RUNTIME_ERROR", (
        "an internal error must not be recorded as a crash: "
        f"{executions[0]['execution_status']}"
    )


async def test_a_retried_participant_is_not_recorded_as_failed(app, snapshot):
    """§10.1 - the participant row and the call row must agree.

    A shared execution that fails with a **retryable** error (a crash or a
    timeout) releases its participants into an independent retry.  Such a
    participant is not failed - it runs on its own and delivers a result.
    """

    workflow = app.create_workflow("test-retry-participant")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    # the leader times out (a retryable error) while the follower waits for
    # its result
    # the workload is longer than the timeout of the leader, so the leader
    # really fails - and the follower has time to join
    arguments = {"files": list(DEMO_FILES), "rounds": 5000}
    leader = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
            timeout_seconds=0.3,
        )
    )
    await wait_for_first_dependency(app.event_bus, workflow.workflow_id)
    follower = await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="content_digest",
        arguments=arguments,
        snapshot_id=snapshot.snapshot_id,
        timeout_seconds=10.0,
    )
    # the leader fails (it is retried once before it gives up)
    with contextlib.suppress(Exception):
        await leader
    # the follower was released into an independent retry and got a result
    assert follower.result_hash

    calls = app.repository.list_logical_calls(workflow.workflow_id)
    participants = app.repository.list_participants(workflow.workflow_id)
    assert calls, "the calls have to be recorded"
    for participant in participants:
        call = next(
            item
            for item in calls
            if item["logical_call_id"] == participant["logical_call_id"]
        )
        assert not (
            participant["status"] == "FAILED" and call["status"] == "COMPLETED"
        ), (
            "a participant that is FAILED must not have a delivered call: "
            f"{participant['status']} vs {call['status']}"
        )
    # a retried call does not keep a reference to the closed group
    for call in calls:
        if call["retry_count"]:
            assert call["share_group_id"] is None, (
                "a retried call must not reference the closed group"
            )

    # the group only counts its current members
    groups = app.repository.list_share_groups(workflow.workflow_id)
    for group in groups:
        members = set(group["participant_calls"] or [])
        for call in calls:
            if call["share_group_id"] != group["share_group_id"]:
                assert call["logical_call_id"] not in members, (
                    "the group must not count a call that left it"
                )
    # the participant that left is terminal - the group must not keep it as a
    # live participant
    for participant in app.repository.list_participants(workflow.workflow_id):
        assert participant["status"] in (
            "COMPLETED",
            "FAILED",
            "CANCELLED",
            "DETACHED",
        ), (
            "a participant of a finished group must be terminal: "
            f"{participant['status']}"
        )
    # a participant that left for a retry is **not** a separation
    from agentfuse.monitoring.metrics import collect_workflow_metrics

    metrics = collect_workflow_metrics(app.repository, workflow.workflow_id)
    assert metrics["detach_count"] == 0, (
        "a retried participant must not be counted as a separation"
    )
    assert metrics["retried_participants"] >= 1


async def test_a_participant_that_left_is_not_failed_by_the_group_it_left(app, snapshot):
    """§10.1 - the failure of a group does not concern the calls that left it.

    A participant that left the group runs on its own.  When the execution of
    the group fails afterwards, the call must keep running - it must not be
    failed or retried because of a group it is not a member of any more.
    """

    from agentfuse.core.state import ParticipantStatus
    from agentfuse.runtime.execution import ExecutionOutcome
    from agentfuse.runtime.protocol import ERROR_CRASH

    workflow = app.create_workflow("test-left-participant")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    leader = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await wait_for_running_execution(app.event_bus, workflow.workflow_id)
    follower = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.2)

    groups = list(app.coordinator._groups.values())
    assert groups, "the two calls have to share one group"
    group = groups[0]
    participants = list(group.participants.values())
    assert len(participants) >= 2

    # the follower is separated - it stays in the group as a terminal
    # participant, but it runs on its own
    follower_participant = next(
        item for item in group.participants.values() if not item.is_leader
    )
    follower_call = app.coordinator._calls[follower_participant.logical_call_id]
    assert follower_call is not None
    app.coordinator._detach_participant_locked(
        group, follower_participant, "the test separates it"
    )
    left = app.coordinator._calls[follower_participant.logical_call_id]
    assert left.status.value != "FAILED"

    # the execution of the group fails now
    execution = next(
        item
        for item in app.coordinator._executions.values()
        if item.leader_call_id in {p.logical_call_id for p in participants}
    )
    failed_calls: list[str] = []
    original_fail = app.coordinator._fail_call_locked

    def record_failure(call, reason):
        failed_calls.append(call.logical_call_id)
        return original_fail(call, reason)

    app.coordinator._fail_call_locked = record_failure
    try:
        async with app.coordinator._lock:
            app.coordinator._commit_failure_locked(
                execution,
                group,
                ExecutionOutcome(
                    ok=False,
                    kind=ERROR_CRASH,
                    message="the group failed",
                    exit_status="crash",
                ),
            )
    finally:
        app.coordinator._fail_call_locked = original_fail

    # the call that left the group is untouched
    assert follower_participant.logical_call_id not in failed_calls, (
        "a call that left the group must not be failed by the failure of the "
        "group it left"
    )
    after = app.coordinator._calls[follower_participant.logical_call_id]
    assert after.status.value not in ("FAILED",), (
        "a call that left the group must not be failed by its failure"
    )

    # clean up: the calls of this workflow must not keep the test running
    for call in list(app.coordinator._calls.values()):
        if call.workflow_id == workflow.workflow_id and not call.future.done():
            await app.coordinator.cancel_logical_call(call.logical_call_id, "test done")


async def test_a_separated_call_is_not_shared_anymore(app, snapshot):
    """A separated call leaves the group - it runs on its own afterwards.

    The call must not reference the group it left any more and it must never
    own two running executions.
    """

    workflow = app.create_workflow("test-separated-shared")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    # a workload that runs long enough for the separation to happen while the
    # calls are still running
    arguments = {"files": list(DEMO_FILES), "rounds": 20000}
    asyncio.create_task(
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
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.1)

    group = next(iter(app.coordinator._groups.values()))
    participant = next(
        item for item in group.participants.values() if not item.is_leader
    )
    call = app.coordinator._calls[participant.logical_call_id]
    app.coordinator._detach_participant_locked(group, participant, "the test separates it")

    # the call must not reference the group it left any more
    assert call.share_group_id != group.share_group_id, (
        "a separated call must not reference the group it left"
    )
    # and it must own at most one running execution
    running = [
        item
        for item in app.coordinator._executions.values()
        if item.leader_call_id == call.logical_call_id
        and item.status
        not in ("COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "CRASHED")
    ]
    assert len(running) <= 1, "a call must never own two running executions"

    rows = app.repository.list_logical_calls(workflow.workflow_id)
    for row in rows:
        if row["logical_call_id"] == call.logical_call_id:
            assert row["share_group_id"] != group.share_group_id, (
                "the row of a separated call must not reference the group it left"
            )
            if row["share_group_id"] is None:
                assert not row["shared"], (
                    "a call that runs on its own must not be marked as shared"
                )
    await app.coordinator.cancel_logical_call(call.logical_call_id, "test done")
    follower.cancel()


async def test_every_failure_kind_is_persisted(app, snapshot):
    """§10.1 - the failure kind has to survive in the database.

    An internal error, a timeout and a cancellation must be distinguishable
    **in the database** - not only in the event stream.
    """

    # 1) an internal error (the snapshot does not exist)
    internal = app.create_workflow("test-kind-internal")
    internal.default_snapshot_id = snapshot.snapshot_id
    internal.start()
    with pytest.raises(Exception):
        await app.coordinator.submit_tool_call(
            workflow_id=internal.workflow_id,
            tool_name="ast_analyzer",
            arguments={"files": list(DEMO_FILES)},
            snapshot_id="snap-does-not-exist",
        )
    internal_calls = app.repository.list_logical_calls(internal.workflow_id)
    internal_executions = app.repository.list_executions(internal.workflow_id)
    assert internal_calls[0]["error_kind"] == "internal_error"
    assert internal_executions[0]["error_kind"] == "internal_error"
    assert internal_executions[0]["execution_status"] == "RUNTIME_ERROR"

    # 2) a timeout
    timing = app.create_workflow("test-kind-timeout")
    timing.default_snapshot_id = snapshot.snapshot_id
    timing.start()
    with pytest.raises(Exception):
        await app.coordinator.submit_tool_call(
            workflow_id=timing.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "sleep", "seconds": 30},
            snapshot_id=snapshot.snapshot_id,
            timeout_seconds=0.3,
        )
    timeout_calls = app.repository.list_logical_calls(timing.workflow_id)
    timeout_executions = app.repository.list_executions(timing.workflow_id)
    assert timeout_calls[0]["error_kind"] == "timeout"
    assert timeout_executions[0]["error_kind"] == "timeout"
    assert timeout_executions[0]["execution_status"] == "TIMEOUT"

    # 3) a cancellation
    cancelled = app.create_workflow("test-kind-cancel")
    cancelled.default_snapshot_id = snapshot.snapshot_id
    cancelled.start()
    task = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=cancelled.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "sleep", "seconds": 30},
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.2)
    for call in app.repository.list_logical_calls(cancelled.workflow_id):
        await app.coordinator.cancel_logical_call(call["logical_call_id"], "audit")
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=30)
    cancelled_calls = app.repository.list_logical_calls(cancelled.workflow_id)
    assert cancelled_calls[0]["error_kind"] == "cancelled"
    assert cancelled_calls[0]["status"] == "CANCELLED"


async def test_an_interrupted_run_is_marked_with_its_kind(app, snapshot):
    """§10.5 - the recovery marks the runs with the kind ``interrupted``."""

    workflow = app.create_workflow("test-kind-interrupted")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()
    task = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "sleep", "seconds": 30},
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.2)
    # the runtime "crashes": the records stay in the running state
    app.repository.db.write(
        "UPDATE logical_tool_calls SET status = 'RUNNING_INDEPENDENTLY' "
        "WHERE workflow_id = ?",
        (workflow.workflow_id,),
    )
    app.repository.db.write(
        "UPDATE physical_executions SET execution_status = 'RUNNING' "
        "WHERE workflow_id = ?",
        (workflow.workflow_id,),
    )
    app.repository.db.flush()

    recovered = app.repository.recover_interrupted_workflows()
    assert workflow.workflow_id in recovered

    calls = app.repository.list_logical_calls(workflow.workflow_id)
    executions = app.repository.list_executions(workflow.workflow_id)
    assert calls[0]["error_kind"] == "interrupted"
    assert executions[0]["error_kind"] == "interrupted"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
