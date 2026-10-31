"""Dashboard endpoints."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Request

from ...core.state import EventType
from .deps import runtime

router = APIRouter(prefix="/api", tags=["overview"])


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    app = runtime(request)
    return {
        "status": "ok",
        "version": "0.1.0",
        "home": str(app.settings.home),
        "mode": str(app.mode),
        "database": str(app.settings.db_path),
        "timestamp": time.time(),
    }


@router.get("/overview")
async def overview(request: Request) -> dict[str, Any]:
    """Everything the dashboard needs - all of it read from real state."""

    app = runtime(request)
    repository = app.repository
    workflows = repository.list_workflows(limit=25)

    running = [row for row in workflows if row["status"] == "RUNNING"]
    invocations = 0
    total_invocations = 0
    calls = 0
    executions = 0
    waiting = 0
    detached = 0
    rejected = 0
    for workflow in workflows:
        workflow_invocations = repository.list_invocations(workflow["workflow_id"])
        total_invocations += len(workflow_invocations)
        # only the invocations that are still running are "active"
        invocations += sum(
            1 for row in workflow_invocations if row["status"] == "RUNNING"
        )
        workflow_calls = repository.list_logical_calls(workflow["workflow_id"])
        calls += len(workflow_calls)
        executions += len(repository.list_executions(workflow["workflow_id"]))
        for participant in repository.list_participants(workflow["workflow_id"]):
            if participant["status"] == "SHARED_WAIT":
                waiting += 1
            elif participant["status"] == "DETACHED":
                # a candidate that was rejected at join time never belonged to
                # the group - it is not a separation
                if (participant["detach_kind"] or "after_join") == "rejected_at_join":
                    rejected += 1
                else:
                    detached += 1

    events = repository.list_events(limit=40)
    recent_events = list(reversed(events))
    share_events = [
        event
        for event in recent_events
        if event["event_type"]
        in (
            EventType.SHARE_CANDIDATE_FOUND,
            EventType.SHARE_VALIDATION_PASSED,
            EventType.SHARE_VALIDATION_FAILED,
            EventType.SHARE_PARTICIPANT_DETACHED,
            EventType.TOOL_CALL_WAITING,
        )
    ]

    live = app.coordinator.live_snapshot()
    return {
        "running_workflows": len(running),
        "total_workflows": len(workflows),
        "active_invocations": invocations,
        "total_invocations": total_invocations,
        "logical_tool_calls": calls,
        "physical_executions": executions,
        "shared_waiting": waiting,
        "detached": detached,
        "rejected_candidates": rejected,
        "workers": live["resources"],
        "mode": live["mode"],
        "workflows": workflows,
        "recent_events": recent_events,
        "recent_share_events": share_events,
        "tools": [
            {
                "tool_name": spec.tool_name,
                "tool_version": spec.tool_version,
                "shareable": spec.shareable,
                "deterministic": spec.deterministic,
                "read_only": spec.read_only,
                "dependency_tracking": spec.dependency_tracking,
            }
            for spec in app.registry.list_tools()
        ],
    }


@router.get("/resources")
async def resources(request: Request) -> dict[str, Any]:
    """Live resource snapshot of the local worker pool."""

    app = runtime(request)
    snapshot = app.coordinator.live_snapshot()
    snapshot["timestamp"] = time.time()
    return snapshot
