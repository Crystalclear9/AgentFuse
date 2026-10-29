"""Integration tests: the dependency types that carry a workspace difference.

The three cases verified here are the ones that a naive "compare the
arguments" implementation would get wrong:

* a directory listing changed (a file was added or removed),
* the existence state of a file changed (present <-> missing),
* a file that only exists in one branch.
"""

from __future__ import annotations

import asyncio

from agentfuse.core.state import ValidationStatus
from agentfuse.scenarios.common import create_branch


def _add_file(working: Path) -> None:
    (working / "src" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")


def _remove_file(working: Path) -> None:
    target = working / "src" / "extra.py"
    if target.exists():
        target.unlink()


async def test_a_changed_directory_listing_is_detected(app, snapshot):
    """A new file in the directory changes the DIRECTORY_LIST fingerprint."""

    branch = create_branch(app, snapshot, _add_file, "with-extra")
    workflow = app.create_workflow("test-directory-change")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="source_index",
            arguments={"root": "src"},
            snapshot_id=snapshot.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="source_index",
            arguments={"root": "src"},
            snapshot_id=branch.snapshot_id,
        ),
    )

    validations = app.repository.list_validations()
    mismatches = [
        validation
        for validation in validations
        if validation["validation_status"] == str(ValidationStatus.MISMATCH)
    ]
    assert mismatches, "the changed directory listing must be detected"
    assert any("src" in (validation["mismatch_reason"] or "") for validation in mismatches)

    participants = app.repository.list_participants(workflow.workflow_id)
    assert any(participant["status"] == "DETACHED" for participant in participants)
    assert len(app.repository.list_executions(workflow.workflow_id)) == 2


async def test_a_changed_file_existence_is_detected(app, snapshot):
    """A file that exists in one branch and not in the other must be detected."""

    with_extra = create_branch(app, snapshot, _add_file, "with-extra")
    without_extra = create_branch(app, snapshot, _remove_file, "without-extra")
    workflow = app.create_workflow("test-existence-change")
    workflow.default_snapshot_id = with_extra.snapshot_id
    workflow.start()

    files = ["src/alpha.py", "src/extra.py"]
    results = await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="duplicate_blocks",
            arguments={"files": files, "block_size": 3},
            snapshot_id=with_extra.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="duplicate_blocks",
            arguments={"files": files, "block_size": 3},
            snapshot_id=without_extra.snapshot_id,
        ),
    )

    validations = app.repository.list_validations()
    mismatches = [
        validation
        for validation in validations
        if validation["validation_status"] == str(ValidationStatus.MISMATCH)
    ]
    assert mismatches, "the changed existence state must be detected"

    participants = app.repository.list_participants(workflow.workflow_id)
    assert any(participant["status"] == "DETACHED" for participant in participants)

    # both calls still return the correct result for their own workspace
    assert results[0].result["missing_files"] == []
    assert results[1].result["missing_files"] == ["src/extra.py"]
    assert results[0].result_hash != results[1].result_hash


async def test_a_single_agent_call_produces_one_execution(app, snapshot):
    """The simplest possible workflow: one agent, one tool call."""

    workflow = app.create_workflow("test-single-agent")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    result = await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": ["src/alpha.py"]},
        snapshot_id=snapshot.snapshot_id,
    )

    calls = app.repository.list_logical_calls(workflow.workflow_id)
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(calls) == 1
    assert len(executions) == 1
    assert calls[0]["status"] == "COMPLETED"
    assert calls[0]["physical_execution_id"] == executions[0]["execution_id"]
    assert result.result["totals"]["functions"] >= 2

    # the call leads its own share group - but nobody else joins it
    groups = app.repository.list_share_groups(workflow.workflow_id)
    assert len(groups) == 1
    participants = app.repository.list_participants(workflow.workflow_id)
    assert len(participants) == 1
    assert participants[0]["is_leader"] == 1


async def test_the_dependency_event_precedes_the_execution_end(app, snapshot):
    """Dependencies must be observable *while* the tool is running."""

    workflow = app.create_workflow("test-dependency-timing")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="content_digest",
        arguments={"files": ["src/alpha.py"], "rounds": 800},
        snapshot_id=snapshot.snapshot_id,
    )

    events = app.repository.list_events(workflow.workflow_id)
    reads = [event for event in events if event["event_type"] == "TOOL_READ_OBSERVED"]
    finished = [event for event in events if event["event_type"] == "TOOL_EXECUTION_FINISHED"]
    assert reads and finished
    assert min(event["timestamp"] for event in reads) < min(
        event["timestamp"] for event in finished
    )
