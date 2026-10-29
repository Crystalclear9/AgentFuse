"""Workflow metrics.

All numbers are derived from the persisted runtime records (physical
executions, participants, validation records, resource samples).  Nothing is
estimated: a "saved execution" is a difference between two *real* physical
execution counts.
"""

from __future__ import annotations

import time
from typing import Any

from ..storage.repository import Repository
from ..tools.registry import ToolRegistry


def collect_workflow_metrics(
    repository: Repository,
    workflow_id: str,
    registry: ToolRegistry | None = None,
) -> dict[str, Any]:
    """Collect the metric set of one workflow run."""

    workflow = repository.get_workflow(workflow_id) or {}
    calls = repository.list_logical_calls(workflow_id)
    executions = repository.list_executions(workflow_id)
    groups = repository.list_share_groups(workflow_id)
    participants = repository.list_participants(workflow_id)
    validations = repository.list_validations()

    group_ids = {group["share_group_id"] for group in groups}
    group_validations = [v for v in validations if v["share_group_id"] in group_ids]

    shareable_names: set[str] = set()
    if registry is not None:
        shareable_names = {spec.tool_name for spec in registry.list_tools() if spec.shareable}

    eligible_calls = [
        call
        for call in calls
        if not shareable_names or call["tool_name"] in shareable_names
    ]
    shared_calls = [
        participant
        for participant in participants
        if not participant["is_leader"]
        and participant["status"] == "COMPLETED"
        and participant["snapshot_id"] is not None
    ]
    detached = [
        participant
        for participant in participants if participant["status"] == "DETACHED"
    ]
    failed_calls = [call for call in calls if call["status"] in ("FAILED", "CANCELLED")]

    total_cpu_time = sum(float(execution["cpu_time"] or 0.0) for execution in executions)
    peak_rss = max((int(execution["peak_rss"] or 0) for execution in executions), default=0)
    wait_durations = [
        float(participant["wait_duration"])
        for participant in participants
        if participant["wait_duration"] is not None
    ]
    total_share_wait = sum(wait_durations)
    avg_share_wait = total_share_wait / len(wait_durations) if wait_durations else 0.0

    validation_ms = sum(float(v["elapsed_ms"] or 0.0) for v in group_validations)
    validation_failures = [v for v in group_validations if v["validation_status"] != "MATCH"]

    start_time = float(workflow.get("start_time") or 0.0)
    end_time = float(workflow.get("end_time") or 0.0)
    makespan = max(0.0, end_time - start_time) if start_time and end_time else 0.0

    first_conflict_at: float | None = None
    follower_independent_at: float | None = None
    calls_by_id = {call["logical_call_id"]: call for call in calls}
    for participant in detached:
        detached_time = participant["detached_time"]
        if detached_time is None:
            continue
        if first_conflict_at is None or detached_time < first_conflict_at:
            first_conflict_at = float(detached_time)
        call = calls_by_id.get(participant["logical_call_id"])
        if call is None:
            continue
        execution = next(
            (
                item
                for item in executions
                if item["execution_id"] == call["physical_execution_id"]
            ),
            None,
        )
        if execution is not None and execution["start_time"]:
            started = float(execution["start_time"])
            if follower_independent_at is None or started < follower_independent_at:
                follower_independent_at = started

    logical_calls = len(calls)
    physical_executions = len(executions)
    eligible = len(eligible_calls)
    shared = len(shared_calls)

    # How long before the leader finished was the detachment observed?
    groups_by_id = {group["share_group_id"]: group for group in groups}
    executions_by_id = {execution["execution_id"]: execution for execution in executions}
    detach_lead_ms: list[float] = []
    for participant in detached:
        group = groups_by_id.get(participant["share_group_id"])
        if group is None:
            continue
        leader = executions_by_id.get(group["leader_execution_id"])
        if leader is None or not leader["finish_time"] or not participant["detached_time"]:
            continue
        # positive value -> the conflict was detected *before* the leader finished
        detach_lead_ms.append(
            (float(leader["finish_time"]) - float(participant["detached_time"])) * 1000.0
        )

    return {
        "workflow_id": workflow_id,
        "scenario": workflow.get("scenario"),
        "mode": workflow.get("runtime_mode"),
        "makespan": round(makespan, 6),
        "logical_calls": logical_calls,
        "physical_executions": physical_executions,
        "saved_executions": logical_calls - physical_executions,
        "eligible_calls": eligible,
        "shared_calls": shared,
        "share_success_rate": round(shared / eligible, 6) if eligible else 0.0,
        "total_cpu_time": round(total_cpu_time, 6),
        "peak_rss": peak_rss,
        "share_groups": len(groups),
        "detach_count": len(detached),
        "failed_calls": len(failed_calls),
        "validation_count": len(group_validations),
        "validation_failures": len(validation_failures),
        "validation_ms": round(validation_ms, 4),
        "total_share_wait": round(total_share_wait, 6),
        "avg_share_wait": round(avg_share_wait, 6),
        "first_conflict_at": first_conflict_at,
        "follower_independent_at": follower_independent_at,
        "detection_delay": (
            round(follower_independent_at - first_conflict_at, 6)
            if first_conflict_at and follower_independent_at
            else None
        ),
        "detach_to_independent_ms": (
            round((follower_independent_at - first_conflict_at) * 1000.0, 2)
            if first_conflict_at and follower_independent_at
            else None
        ),
        "detach_lead_ms_max": round(max(detach_lead_ms), 2) if detach_lead_ms else None,
        "online_detach_before_finish": bool(detach_lead_ms) and max(detach_lead_ms) > 0,
        "collected_at": time.time(),
    }
