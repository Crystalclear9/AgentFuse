"""Shared helpers for the API routes."""

from __future__ import annotations

from typing import Any

from fastapi import Request


def runtime(request: Request) -> Any:
    """Return the runtime application object of the current server."""

    return request.app.state.runtime


def repository(request: Request) -> Any:
    return runtime(request).repository


def jobs(request: Request) -> dict[str, dict[str, Any]]:
    return request.app.state.benchmark_jobs
