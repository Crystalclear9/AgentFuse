"""Workflow endpoints: list, detail, graph, timeline, events and metrics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...core.events import RuntimeEvent
from ...core.execution_graph import ExecutionGraph
from ...monitoring.metrics import collect_workflow_metrics
from ...monitoring.timeline import build_timeline
from .deps import runtime

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


@router.get("")
async def list_workflows(request: Request, limit: int = 50) -> dict[str, Any]:
    app = runtime(request)
    return {"workflows": app.repository.list_workflows(limit=limit)}


@router.get("/{workflow_id}")
async def workflow_detail(request: Request, workflow_id: str) -> dict[str, Any]:
    app = runtime(request)
    workflow = app.repository.get_workflow(workflow_id)
    if workflow is None:
        raise HTTPException(status_code=404, detail=f"unknown workflow: {workflow_id}")
    repository = app.repository
    executions = repository.list_executions(workflow_id)
    # the confirmed dependencies of every execution - this is what the
    # dependency check actually confirmed, not what the tool declared
    dependencies: dict[str, list[dict[str, Any]]] = {}
    for execution in executions:
        dependencies[execution["execution_id"]] = repository.list_dependencies(
            execution["execution_id"]
        )
    validations = repository.list_validations()
    group_ids = {
        group["share_group_id"]
        for group in repository.list_share_groups(workflow_id)
    }
    return {
        "workflow": workflow,
        "agents": repository.list_agents(workflow_id),
        "invocations": repository.list_invocations(workflow_id),
        "logical_calls": repository.list_logical_calls(workflow_id),
        "physical_executions": executions,
        "share_groups": repository.list_share_groups(workflow_id),
        "share_participants": repository.list_participants(workflow_id),
        "dependencies": dependencies,
        "validations": [
            validation
            for validation in validations
            if validation["share_group_id"] in group_ids
        ],
        "metrics": collect_workflow_metrics(repository, workflow_id, app.registry),
    }


@router.get("/{workflow_id}/graph")
async def workflow_graph(request: Request, workflow_id: str) -> dict[str, Any]:
    """Rebuild the execution graph from the recorded events."""

    app = runtime(request)
    repository = app.repository
    rows = repository.list_events(workflow_id)
    events = [
        RuntimeEvent(
            event_id=row["event_id"],
            workflow_id=row["workflow_id"],
            event_type=row["event_type"],
            timestamp=float(row["timestamp"]),
            sequence=int(row["sequence"]),
            agent_id=row["agent_id"],
            invocation_id=row["invocation_id"],
            logical_call_id=row["logical_call_id"],
            physical_execution_id=row["physical_execution_id"],
            parent_event_id=row["parent_event_id"],
            metadata=row["metadata"] or {},
        )
        for row in rows
    ]
    graph = ExecutionGraph.from_events(events, workflow_id=workflow_id)
    return graph.to_dict()


@router.get("/{workflow_id}/timeline")
async def workflow_timeline(request: Request, workflow_id: str) -> dict[str, Any]:
    app = runtime(request)
    return build_timeline(app.repository, workflow_id)


@router.get("/{workflow_id}/events")
async def workflow_events(request: Request, workflow_id: str) -> dict[str, Any]:
    app = runtime(request)
    return {"events": app.repository.list_events(workflow_id)}


@router.get("/{workflow_id}/metrics")
async def workflow_metrics(request: Request, workflow_id: str) -> dict[str, Any]:
    app = runtime(request)
    return collect_workflow_metrics(app.repository, workflow_id, app.registry)
