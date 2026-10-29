"""Benchmark endpoints: run a comparison and inspect the history."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ...benchmarks.runner import compare
from ...core.ids import new_id
from ...scenarios import list_scenarios, resolve_scenario
from .deps import jobs, runtime

router = APIRouter(prefix="/api/benchmarks", tags=["benchmarks"])


class BenchmarkRequest(BaseModel):
    scenario: str = Field(default="shared", description="演示场景名称")
    repeat: int = Field(default=3, ge=1, le=20, description="重复次数")
    modes: list[str] = Field(
        default_factory=lambda: ["baseline", "online"], description="参与对比的模式"
    )
    workers: int | None = Field(default=None, ge=1, le=64, description="worker 数量")
    memory_budget_mb: int | None = Field(default=None, ge=64, le=65536, description="内存预算")


@router.get("")
async def list_benchmarks(request: Request, scenario: str | None = None) -> dict[str, Any]:
    app = runtime(request)
    return {"runs": app.repository.list_benchmark_runs(scenario)}


@router.get("/scenarios")
async def benchmark_scenarios() -> dict[str, Any]:
    """The scenario catalogue with the expectation of every scenario."""

    from ...scenarios.catalogue import catalogue

    return {"scenarios": list_scenarios(), "catalogue": catalogue()}


@router.get("/jobs/{job_id}")
async def benchmark_job(request: Request, job_id: str) -> dict[str, Any]:
    job = jobs(request).get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown benchmark job: {job_id}")
    return job


@router.post("/run")
async def run_benchmark(request: Request, payload: BenchmarkRequest) -> dict[str, Any]:
    """Start a benchmark comparison in the background."""

    app = runtime(request)
    try:
        scenario = resolve_scenario(payload.scenario)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    modes = []
    for mode in payload.modes:
        key = str(mode).strip().lower()
        if key not in ("baseline", "online", "deferred"):
            raise HTTPException(status_code=400, detail=f"unknown mode: {mode}")
        modes.append(key)

    if payload.workers:
        app.settings = app.settings.with_overrides(max_workers=payload.workers)
        app.scheduler.max_workers = payload.workers
    if payload.memory_budget_mb:
        app.settings = app.settings.with_overrides(
            max_memory_budget_mb=payload.memory_budget_mb
        )
        app.scheduler.max_memory_budget_mb = payload.memory_budget_mb

    job_id = new_id("job")
    job = {
        "job_id": job_id,
        "scenario": scenario,
        "modes": modes,
        "repeat": payload.repeat,
        "status": "RUNNING",
        "started_at": time.time(),
        "result": None,
        "error": None,
    }
    jobs(request)[job_id] = job

    async def worker() -> None:
        try:
            job["result"] = await compare(app, scenario, repeats=payload.repeat, modes=tuple(modes))
            job["status"] = "COMPLETED"
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            job["status"] = "FAILED"
            job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            job["finished_at"] = time.time()

    asyncio.create_task(worker())
    return job


@router.get("/{run_id}")
async def benchmark_run(request: Request, run_id: str) -> dict[str, Any]:
    app = runtime(request)
    run = app.repository.get_benchmark_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"unknown benchmark run: {run_id}")
    return run
