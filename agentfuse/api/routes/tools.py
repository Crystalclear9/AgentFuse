"""Tool registry endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from .deps import runtime

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
async def list_tools(request: Request) -> dict[str, Any]:
    app = runtime(request)
    return {
        "tools": [
            {
                "tool_name": spec.tool_name,
                "tool_version": spec.tool_version,
                "deterministic": spec.deterministic,
                "read_only": spec.read_only,
                "shareable": spec.shareable,
                "description": spec.description,
                "implementation_reference": spec.implementation_reference,
                "resource_estimate": spec.resource_estimate,
                "input_schema": spec.input_schema,
            }
            for spec in app.registry.list_tools()
        ]
    }
