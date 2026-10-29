"""Benchmark runner.

Three execution modes are compared:

``baseline``
    every tool call is executed independently (no sharing at all),
``online``
    candidate detection + online dependency validation + detachment,
``deferred``
    the candidate may join the running execution, but the dependency check is
    postponed until the leader finished (used to measure the value of the
    online checks).

Fairness rules (identical for every mode):

* same input repository and same snapshots,
* same tool implementations,
* same request sequence,
* same worker limit, memory budget and timeout,
* same random seed.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..core.ids import new_id
from ..core.state import RuntimeMode
from ..scenarios import run_scenario
from ..storage.models import BenchmarkRunRecord


@dataclass
class BenchmarkOutcome:
    """One single benchmark repetition."""

    run_id: str
    scenario: str
    mode: str
    repeat_index: int
    workflow_id: str
    metrics: dict[str, Any] = field(default_factory=dict)
    call_hashes: list[tuple[str, str]] = field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "mode": self.mode,
            "repeat_index": self.repeat_index,
            "workflow_id": self.workflow_id,
            "metrics": self.metrics,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def collect_call_hashes(repository: Any, workflow_id: str) -> list[tuple[str, str]]:
    """Return ``(input_manifest_hash, result_hash)`` of every logical call.

    The *manifest hash* identifies the input state of a call.  Snapshot ids
    differ between two benchmark runs (every run materialises its own
    snapshots), the content however is identical - therefore the manifest hash
    is the stable key that allows comparing the modes with each other.
    """

    snapshots = {
        snapshot["snapshot_id"]: snapshot["manifest_hash"]
        for snapshot in repository.list_snapshots()
    }
    calls = repository.list_logical_calls(workflow_id)
    rows = []
    for call in calls:
        snapshot_id = str(call["workspace_snapshot_id"] or "")
        rows.append(
            (
                str(snapshots.get(snapshot_id, snapshot_id)),
                str(call["result_hash"] or f"FAILED:{call['status']}"),
            )
        )
    return rows


async def run_single(
    app: Any,
    scenario: str,
    mode: RuntimeMode,
    repeat_index: int,
    options: dict[str, Any] | None = None,
    workers: int | None = None,
) -> BenchmarkOutcome:
    """Execute one benchmark repetition and persist its record."""

    if workers:
        app.scheduler.max_workers = int(workers)
    started = time.time()
    result = await run_scenario(app, scenario, mode, dict(options or {}))
    finished = time.time()
    metrics = dict(result.metrics)
    hashes = collect_call_hashes(app.repository, result.workflow_id)

    outcome = BenchmarkOutcome(
        run_id=new_id("bench"),
        scenario=scenario,
        mode=str(mode),
        repeat_index=repeat_index,
        workflow_id=result.workflow_id,
        metrics=metrics,
        call_hashes=hashes,
        started_at=started,
        finished_at=finished,
    )

    app.repository.save_benchmark_run(
        BenchmarkRunRecord(
            run_id=outcome.run_id,
            scenario=scenario,
            mode=str(mode),
            repeat_index=repeat_index,
            workflow_id=result.workflow_id,
            started_at=started,
            finished_at=finished,
            makespan=metrics.get("makespan"),
            physical_executions=metrics.get("physical_executions"),
            logical_calls=metrics.get("logical_calls"),
            saved_executions=metrics.get("saved_executions"),
            eligible_calls=metrics.get("eligible_calls"),
            shared_calls=metrics.get("shared_calls"),
            share_success_rate=metrics.get("share_success_rate"),
            total_cpu_time=metrics.get("total_cpu_time"),
            peak_rss=metrics.get("peak_rss"),
            avg_share_wait=metrics.get("avg_share_wait"),
            total_share_wait=metrics.get("total_share_wait"),
            validation_count=metrics.get("validation_count"),
            validation_failures=metrics.get("validation_failures"),
            validation_ms=metrics.get("validation_ms"),
            detach_count=metrics.get("detach_count"),
            first_conflict_at=metrics.get("first_conflict_at"),
            follower_independent_at=metrics.get("follower_independent_at"),
            correct=len(hashes),
            mismatched=0,
            payload={"notes": result.notes, "snapshots": result.snapshots},
        )
    )
    return outcome


def _aggregate(values: Iterable[float]) -> dict[str, float]:
    data = [float(value) for value in values]
    if not data:
        return {"mean": 0.0, "min": 0.0, "max": 0.0, "stdev": 0.0, "samples": 0}
    return {
        "mean": round(statistics.fmean(data), 6),
        "median": round(statistics.median(data), 6),
        "min": round(min(data), 6),
        "max": round(max(data), 6),
        "stdev": round(statistics.pstdev(data), 6) if len(data) > 1 else 0.0,
        "samples": len(data),
    }


def aggregate_outcomes(outcomes: list[BenchmarkOutcome]) -> dict[str, Any]:
    """Aggregate the repetitions of one mode."""

    metrics = [outcome.metrics for outcome in outcomes]
    return {
        "runs": len(outcomes),
        "makespan": _aggregate([m.get("makespan", 0.0) for m in metrics]),
        "total_cpu_time": _aggregate([m.get("total_cpu_time", 0.0) for m in metrics]),
        "peak_rss": _aggregate([m.get("peak_rss", 0) for m in metrics]),
        "physical_executions": _aggregate([m.get("physical_executions", 0) for m in metrics]),
        "logical_calls": _aggregate([m.get("logical_calls", 0) for m in metrics]),
        "saved_executions": _aggregate([m.get("saved_executions", 0) for m in metrics]),
        "share_success_rate": _aggregate([m.get("share_success_rate", 0.0) for m in metrics]),
        "avg_share_wait": _aggregate([m.get("avg_share_wait", 0.0) for m in metrics]),
        "validation_count": _aggregate([m.get("validation_count", 0) for m in metrics]),
        "validation_failures": _aggregate([m.get("validation_failures", 0) for m in metrics]),
        "validation_ms": _aggregate([m.get("validation_ms", 0.0) for m in metrics]),
        "detach_count": _aggregate([m.get("detach_count", 0) for m in metrics]),
        "failed_calls": _aggregate([m.get("failed_calls", 0) for m in metrics]),
    }


def check_correctness(baseline: list[BenchmarkOutcome], other: list[BenchmarkOutcome]) -> dict[str, Any]:
    """Compare the per call result hashes of two modes."""

    if not baseline or not other:
        return {"compared": 0, "identical": 0, "mismatched": 0, "details": []}
    details = []
    identical = 0
    mismatched = 0
    for index, (left, right) in enumerate(zip(baseline, other)):
        left_map = dict(left.call_hashes)
        right_map = dict(right.call_hashes)
        same = left_map == right_map
        if same:
            identical += 1
        else:
            mismatched += 1
        details.append(
            {
                "repeat_index": index,
                "identical": same,
                "baseline": left_map,
                "compared": right_map,
            }
        )
    return {
        "compared": len(details),
        "identical": identical,
        "mismatched": mismatched,
        "details": details,
    }


async def compare(
    app: Any,
    scenario: str,
    repeats: int = 3,
    modes: tuple[str, ...] = ("baseline", "online"),
    options: dict[str, Any] | None = None,
    workers: int | None = None,
) -> dict[str, Any]:
    """Run the benchmark comparison for one scenario.

    ``workers`` limits the number of concurrently running tool workers.  With
    ``workers=1`` the two modes are executed under *resource contention*,
    which is where the shared execution actually saves wall clock time.
    """

    outcomes: dict[str, list[BenchmarkOutcome]] = {mode: [] for mode in modes}
    for repeat in range(repeats):
        for mode in modes:
            outcome = await run_single(
                app, scenario, RuntimeMode(mode), repeat, options=options, workers=workers
            )
            outcomes[mode].append(outcome)
            await asyncio.sleep(0.05)

    aggregated = {mode: aggregate_outcomes(items) for mode, items in outcomes.items()}
    correctness: dict[str, Any] = {}
    if "baseline" in outcomes:
        for mode, items in outcomes.items():
            if mode == "baseline":
                continue
            correctness[mode] = check_correctness(outcomes["baseline"], items)

    speedup: dict[str, float] = {}
    baseline_makespan = aggregated.get("baseline", {}).get("makespan", {}).get("mean", 0.0)
    for mode, data in aggregated.items():
        if mode == "baseline":
            continue
        other = data.get("makespan", {}).get("mean", 0.0)
        speedup[mode] = round(baseline_makespan / other, 4) if other > 0 else 0.0

    return {
        "scenario": scenario,
        "repeats": repeats,
        "modes": list(modes),
        "workers": workers or app.scheduler.max_workers,
        "runs": {mode: [item.to_dict() for item in items] for mode, items in outcomes.items()},
        "aggregated": aggregated,
        "correctness": correctness,
        "speedup": speedup,
        "generated_at": time.time(),
    }
