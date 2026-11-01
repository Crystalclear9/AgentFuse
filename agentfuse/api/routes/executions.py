"""Physical execution endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from ...monitoring.dependencies import build_dependency_inspection
from ._reads import read
from .deps import runtime

router = APIRouter(prefix="/api/executions", tags=["executions"])


def _executions(repository: Any, workflow_id: str | None) -> list[dict[str, Any]]:
    """Collect the executions (a blocking read)."""

    if workflow_id:
        return repository.list_executions(workflow_id)
    rows = repository.list_workflows(limit=25)
    executions: list[dict[str, Any]] = []
    for workflow in rows:
        executions.extend(repository.list_executions(workflow["workflow_id"]))
    return executions


@router.get("")
async def list_executions(request: Request, workflow_id: str | None = None) -> dict[str, Any]:
    app = runtime(request)
    return {"executions": await read(lambda: _executions(app.repository, workflow_id))}


@router.get("/{execution_id}")
async def execution_detail(request: Request, execution_id: str) -> dict[str, Any]:
    app = runtime(request)
    detail = await read(lambda: _execution_detail(app.repository, execution_id))
    if detail is None:
        raise HTTPException(status_code=404, detail=f"unknown execution: {execution_id}")
    return detail


def _execution_detail(repository: Any, execution_id: str) -> dict[str, Any] | None:
    """Collect the detail of an execution (a blocking read)."""

    execution = repository.get_execution(execution_id)
    if execution is None:
        return None
    samples = repository.list_resource_samples(execution_id)
    return {
        "execution": execution,
        "resource_samples": samples,
        "peak_rss": max((row["rss_bytes"] or 0) for row in samples) if samples else None,
    }


@router.get("/{execution_id}/dependencies")
async def execution_dependencies(request: Request, execution_id: str) -> dict[str, Any]:
    app = runtime(request)
    payload = await read(lambda: build_dependency_inspection(app.repository, execution_id))
    if not payload:
        raise HTTPException(status_code=404, detail=f"unknown execution: {execution_id}")
    return payload
