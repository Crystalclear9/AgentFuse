"""Unit tests for the share group state machine."""

from __future__ import annotations

import pytest

from agentfuse.core.state import ParticipantStatus, ShareGroupStatus
from agentfuse.sharing.share_group import IllegalTransition, Participant, ShareGroup


def make_group() -> ShareGroup:
    return ShareGroup(
        share_group_id="sg-1",
        workflow_id="wf-1",
        candidate_key="key",
        tool_name="ast_analyzer",
        tool_version="1.0.0",
        leader_execution_id="exec-1",
        leader_logical_call_id="call-1",
    )


def test_a_participant_cannot_be_added_twice():
    group = make_group()
    group.add_participant(Participant(logical_call_id="call-1"))
    with pytest.raises(IllegalTransition):
        group.add_participant(Participant(logical_call_id="call-1"))


def test_illegal_group_transitions_are_rejected():
    group = make_group()
    group.transition(ShareGroupStatus.EXECUTING)
    group.transition(ShareGroupStatus.COMPLETED)
    with pytest.raises(IllegalTransition):
        group.transition(ShareGroupStatus.EXECUTING)


def test_illegal_participant_transitions_are_rejected():
    participant = Participant(logical_call_id="call-1")
    participant.transition(ParticipantStatus.RUNNING_INDEPENDENTLY)
    participant.transition(ParticipantStatus.COMPLETED)
    with pytest.raises(IllegalTransition):
        participant.transition(ParticipantStatus.SHARED_WAIT)


def test_a_detached_participant_may_run_independently():
    participant = Participant(logical_call_id="call-2", status=ParticipantStatus.SHARED_WAIT)
    participant.transition(ParticipantStatus.DETACHED)
    participant.transition(ParticipantStatus.RUNNING_INDEPENDENTLY)
    participant.transition(ParticipantStatus.COMPLETED)
    assert participant.status is ParticipantStatus.COMPLETED


def test_active_participants_exclude_finished_ones():
    group = make_group()
    group.add_participant(Participant(logical_call_id="call-1", status=ParticipantStatus.SHARED_WAIT))
    group.add_participant(Participant(logical_call_id="call-2", status=ParticipantStatus.DETACHED))
    group.add_participant(Participant(logical_call_id="call-3", status=ParticipantStatus.COMPLETED))
    assert [p.logical_call_id for p in group.active_participants()] == ["call-1"]


def test_wait_duration_is_measured_between_attach_and_delivery():
    participant = Participant(logical_call_id="call-1", attached_time=100.0)
    participant.delivered_time = 101.5
    assert participant.wait_duration == pytest.approx(1.5)
