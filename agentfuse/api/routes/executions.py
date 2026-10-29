"""Physical execution endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...monitoring.dependencies import build_dependency_inspection
from .deps import runtime

router = APIRouter(prefix="/api/executions", tags=["executions"])


@router.get("")
async def list_executions(request: Request, workflow_id: str | None = None) -> dict[str, Any]:
    app = runtime(request)
    if workflow_id:
        return {"executions": app.repository.list_executions(workflow_id)}
    rows = app.repository.list_workflows(limit=25)
    executions: list[dict[str, Any]] = []
    for workflow in rows:
        executions.extend(app.repository.list_executions(workflow["workflow_id"]))
    return {"executions": executions}


@router.get("/{execution_id}")
async def execution_detail(request: Request, execution_id: str) -> dict[str, Any]:
    app = runtime(request)
    execution = app.repository.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail=f"unknown execution: {execution_id}")
    samples = app.repository.list_resource_samples(execution_id)
    return {
        "execution": execution,
        "resource_samples": samples,
        "peak_rss": max((row["rss_bytes"] or 0) for row in samples) if samples else None,
    }


@router.get("/{execution_id}/dependencies")
async def execution_dependencies(request: Request, execution_id: str) -> dict[str, Any]:
    app = runtime(request)
    payload = build_dependency_inspection(app.repository, execution_id)
    if not payload:
        raise HTTPException(status_code=404, detail=f"unknown execution: {execution_id}")
    return payload
