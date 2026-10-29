"""Execution timeline builder.

The timeline is derived from the persisted runtime events.  Every swim lane
corresponds to one *observed* entity (an agent invocation or a physical
execution) and every marker corresponds to an event that really happened.
"""

from __future__ import annotations

from typing import Any

from ..core.state import EventType

_MARKER_TYPES = {
    EventType.TOOL_CALL_REQUESTED: "requested",
    EventType.TOOL_CALL_WAITING: "shared_wait",
    EventType.TOOL_EXECUTION_STARTED: "execution_started",
    EventType.TOOL_READ_OBSERVED: "dependency_read",
    EventType.SHARE_VALIDATION_PASSED: "validation_passed",
    EventType.SHARE_VALIDATION_FAILED: "validation_failed",
    EventType.SHARE_PARTICIPANT_DETACHED: "detached",
    EventType.TOOL_EXECUTION_FINISHED: "execution_finished",
    EventType.TOOL_RESULT_DELIVERED: "delivered",
    EventType.TOOL_CALL_FAILED: "failed",
    EventType.TOOL_CALL_CANCELLED: "cancelled",
}


def build_timeline(repository: Any, workflow_id: str) -> dict[str, Any]:
    """Build the swim lane timeline of one workflow."""

    events = repository.list_events(workflow_id)
    invocations = {row["invocation_id"]: row for row in repository.list_invocations(workflow_id)}
    calls = {row["logical_call_id"]: row for row in repository.list_logical_calls(workflow_id)}
    executions = {
        row["execution_id"]: row for row in repository.list_executions(workflow_id)
    }
    agents = {row["agent_id"]: row for row in repository.list_agents(workflow_id)}

    lanes: dict[str, dict[str, Any]] = {}

    def lane(lane_id: str, kind: str, label: str) -> dict[str, Any]:
        if lane_id not in lanes:
            lanes[lane_id] = {
                "id": lane_id,
                "kind": kind,
                "label": label,
                "start": None,
                "end": None,
                "markers": [],
                "segments": [],
            }
        return lanes[lane_id]

    for event in events:
        kind = event["event_type"]
        timestamp = float(event["timestamp"])
        metadata = event.get("metadata") or {}

        if kind == EventType.AGENT_INVOKED and event["invocation_id"]:
            invocation = invocations.get(event["invocation_id"], {})
            agent = agents.get(event["agent_id"], {})
            label = agent.get("external_identity") or event["agent_id"]
            entry = lane(
                f"invocation:{event['invocation_id']}",
                "invocation",
                f"{label} #{event['invocation_id'][-6:]}",
            )
            entry["start"] = timestamp
            entry["agent_id"] = event["agent_id"]
            entry["parent_invocation_id"] = metadata.get("parent_invocation_id")
            entry["segments"].append(
                {"type": "invocation", "start": timestamp, "end": None}
            )
            continue

        if kind == EventType.AGENT_RETURNED and event["invocation_id"]:
            entry = lanes.get(f"invocation:{event['invocation_id']}")
            if entry is not None:
                entry["end"] = timestamp
                for segment in entry["segments"]:
                    if segment["type"] == "invocation" and segment["end"] is None:
                        segment["end"] = timestamp
            continue

        if kind == EventType.TOOL_EXECUTION_STARTED and event["physical_execution_id"]:
            execution = executions.get(event["physical_execution_id"], {})
            entry = lane(
                f"execution:{event['physical_execution_id']}",
                "execution",
                f"{metadata.get('tool_name', 'tool')} #{event['physical_execution_id'][-6:]}",
            )
            entry["start"] = timestamp
            entry["tool_name"] = metadata.get("tool_name")
            entry["pid"] = metadata.get("pid")
            entry["snapshot_id"] = metadata.get("snapshot_id")
            entry["share_group_id"] = metadata.get("share_group_id")
            entry["segments"].append({"type": "execution", "start": timestamp, "end": None})
            continue

        if kind == EventType.TOOL_EXECUTION_FINISHED and event["physical_execution_id"]:
            entry = lanes.get(f"execution:{event['physical_execution_id']}")
            if entry is not None:
                entry["end"] = timestamp
                entry["status"] = metadata.get("status")
                entry["cpu_time"] = metadata.get("cpu_time")
                entry["peak_rss"] = metadata.get("peak_rss")
                for segment in entry["segments"]:
                    if segment["type"] == "execution" and segment["end"] is None:
                        segment["end"] = timestamp
            continue

        marker = _MARKER_TYPES.get(kind)
        if marker is None:
            continue

        target_lane = None
        if event["invocation_id"]:
            target_lane = lanes.get(f"invocation:{event['invocation_id']}")
        if target_lane is None and event["physical_execution_id"]:
            target_lane = lanes.get(f"execution:{event['physical_execution_id']}")
        if target_lane is None:
            continue

        payload: dict[str, Any] = {"type": marker, "timestamp": timestamp}
        if event["logical_call_id"]:
            payload["logical_call_id"] = event["logical_call_id"]
            call = calls.get(event["logical_call_id"])
            if call is not None:
                payload["tool_name"] = call["tool_name"]
        if marker == "dependency_read":
            payload["path"] = metadata.get("relative_path")
            payload["dependency_type"] = metadata.get("dependency_type")
            payload["fingerprint"] = metadata.get("fingerprint")
        if marker in ("validation_passed", "validation_failed"):
            payload["reason"] = metadata.get("reason")
            payload["dependency_sequence"] = metadata.get("dependency_sequence")
        if marker == "detached":
            payload["reason"] = metadata.get("reason")
        target_lane["markers"].append(payload)

    for entry in lanes.values():
        if entry["start"] is None:
            entry["start"] = 0.0
        if entry["end"] is None:
            entry["end"] = entry["start"]

    timeline = sorted(lanes.values(), key=lambda item: (item["kind"], item["start"]))
    starts = [entry["start"] for entry in timeline] or [0.0]
    ends = [entry["end"] for entry in timeline] or [0.0]
    return {
        "workflow_id": workflow_id,
        "lanes": timeline,
        "start_time": min(starts),
        "end_time": max(ends),
        "events": len(events),
    }
