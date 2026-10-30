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

try:  # pragma: no cover - psutil is a hard dependency of the runtime
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

#: The seed that the benchmark uses when the caller does not specify one.
#: Every mode and every repetition uses the same seed - that is what makes
#: the comparison fair and the run reproducible.
from ..scenarios.common import DEFAULT_SEED  # noqa: E402

#: How much the resident size of the runtime may grow before the benchmark
#: reports a leak (64 MiB).
RSS_TOLERANCE_BYTES = 64 * 1024 * 1024


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
    online_detach = [
        1.0 if m.get("online_detach_before_finish") else 0.0 for m in metrics
    ]
    return {
        "runs": len(outcomes),
        "makespan": _aggregate([m.get("makespan", 0.0) for m in metrics]),
        "total_cpu_time": _aggregate([m.get("total_cpu_time", 0.0) for m in metrics]),
        "peak_rss": _aggregate([m.get("peak_rss", 0) for m in metrics]),
        "physical_executions": _aggregate([m.get("physical_executions", 0) for m in metrics]),
        "logical_calls": _aggregate([m.get("logical_calls", 0) for m in metrics]),
        "saved_executions": _aggregate([m.get("saved_executions", 0) for m in metrics]),
        "share_success_rate": _aggregate([m.get("share_success_rate", 0.0) for m in metrics]),
        "shared_calls": _aggregate([m.get("shared_calls", 0) for m in metrics]),
        "rejected_candidates": _aggregate([m.get("rejected_candidates", 0) for m in metrics]),
        "avg_share_wait": _aggregate([m.get("avg_share_wait", 0.0) for m in metrics]),
        "validation_count": _aggregate([m.get("validation_count", 0) for m in metrics]),
        "validation_failures": _aggregate([m.get("validation_failures", 0) for m in metrics]),
        "validation_ms": _aggregate([m.get("validation_ms", 0.0) for m in metrics]),
        "detach_count": _aggregate([m.get("detach_count", 0) for m in metrics]),
        "failed_calls": _aggregate([m.get("failed_calls", 0) for m in metrics]),
        # --- the benefit of the online dependency check --------------------
        # How long before the leader finished the conflict was detected - the
        # time that the follower did not have to wait for a result it must not
        # use.  This is the value that the online mode adds over the deferred
        # mode.
        "detach_lead_ms_max": _aggregate(
            [m.get("detach_lead_ms_max") or 0.0 for m in metrics]
        ),
        "detach_lead_ms_median": _aggregate(
            [m.get("detach_lead_ms_median") or 0.0 for m in metrics]
        ),
        "online_detach_before_finish_rate": _aggregate(online_detach),
        "saved_wait_ms_max": _aggregate(
            [m.get("saved_wait_ms_max") or 0.0 for m in metrics]
        ),
        "saved_wait_ms_median": _aggregate(
            [m.get("saved_wait_ms_median") or 0.0 for m in metrics]
        ),
        "rejected_candidates": _aggregate(
            [m.get("rejected_candidates", 0) for m in metrics]
        ),
        "detection_delay": _aggregate(
            [m.get("detection_delay") or 0.0 for m in metrics]
        ),
        "detach_to_independent_ms": _aggregate(
            [m.get("detach_to_independent_ms") or 0.0 for m in metrics]
        ),
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


def count_live_workers(app: Any) -> int:
    """Return the number of worker processes that are currently alive."""

    coordinator = getattr(app, "coordinator", None)
    if coordinator is None:
        return 0
    return coordinator.live_worker_count()


def sample_resources(app: Any) -> dict[str, Any]:
    """Take a snapshot of the resources that a repeated run must not grow.

    The *runtime* memory is the interesting value - the workers are short
    lived processes that the runtime starts and terminates again.  A leak in
    the runtime would show up as a steadily growing resident size.
    """

    rss: int | None = None
    if psutil is not None:
        try:
            rss = int(psutil.Process().memory_info().rss)
        except Exception:  # pragma: no cover - defensive
            rss = None
    return {"runtime_rss": rss, "workers": count_live_workers(app)}


def check_resource_stability(
    samples: list[dict[str, Any]], rss_baseline: int | None = None
) -> dict[str, Any]:
    """Verify that the repeated runs do not consume more and more resources.

    Two signals are checked:

    * the number of worker processes must not accumulate - the warm pool
      keeps its workers alive, therefore the count may only grow by the size
      of the pool, not with every repetition,
    * the resident size of the runtime must not grow beyond the tolerance -
      that is the signal of a real leak.
    """

    if not samples:
        return {
            "samples": 0,
            "stable": True,
            "workers_grew": False,
            "worker_leftovers": 0,
            "runtime_rss_growth": 0,
            "runtime_rss_growth_mib": 0.0,
            "peak_rss_series": {},
        }

    worker_counts = [int(sample["workers_after"]) for sample in samples]
    workers_grew = worker_counts[-1] > worker_counts[0]
    leftovers = [sample for sample in samples if sample["workers_after"] > 0]

    rss_values = [
        int(sample["runtime_rss"])
        for sample in samples
        if sample.get("runtime_rss") is not None
    ]
    if rss_baseline is not None:
        rss_values.insert(0, int(rss_baseline))
    rss_growth = 0
    if len(rss_values) >= 2:
        rss_growth = rss_values[-1] - rss_values[0]
    rss_grew = rss_growth > RSS_TOLERANCE_BYTES

    by_mode: dict[str, list[int]] = {}
    for sample in samples:
        by_mode.setdefault(sample["mode"], []).append(int(sample["peak_rss"]))
    growing_modes = []
    for mode, series in by_mode.items():
        if len(series) > 1 and series[-1] > max(series[:-1]) * 1.3 and series[-1] > 0:
            growing_modes.append(mode)

    return {
        "samples": len(samples),
        "stable": not workers_grew and not rss_grew,
        "workers_grew": workers_grew,
        "worker_series": worker_counts,
        "worker_leftovers": len(leftovers),
        "runtime_rss_series": rss_values,
        "runtime_rss_growth": rss_growth,
        "runtime_rss_growth_mib": round(rss_growth / 1048576, 2),
        "peak_rss_series": by_mode,
        "peak_rss_growing_modes": growing_modes,
    }


def check_reproducibility(outcomes: dict[str, list[BenchmarkOutcome]]) -> dict[str, Any]:
    """Verify that the repeats of one mode produced the same decisions.

    The seed is only effective if a run with the same seed produces the same
    result hashes - otherwise the seed would be a decoration.
    """

    report: dict[str, Any] = {}
    reproducible = True
    for mode, items in outcomes.items():
        if len(items) < 2:
            report[mode] = {"repeats": len(items), "identical": True, "compared": 0}
            continue
        identical = True
        compared = 0
        reference = dict(items[0].call_hashes)
        for item in items[1:]:
            compared += 1
            if dict(item.call_hashes) != reference:
                identical = False
                break
        report[mode] = {
            "repeats": len(items),
            "compared": compared,
            "identical": identical,
        }
        reproducible = reproducible and identical
    return {"reproducible": reproducible, "modes": report}


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

    Every run uses the **same random seed** - that is the sixth fairness rule
    of the module docstring.  The seed is also what makes the comparison
    reproducible: two runs with the same seed have to produce the same
    decisions.
    """

    run_options = dict(options or {})
    seed = run_options.get("seed")
    if seed is None:
        seed = DEFAULT_SEED
    run_options["seed"] = int(seed)

    outcomes: dict[str, list[BenchmarkOutcome]] = {mode: [] for mode in modes}
    resource_samples: list[dict[str, Any]] = []
    rss_baseline = sample_resources(app)["runtime_rss"]
    for repeat in range(repeats):
        for mode in modes:
            before = sample_resources(app)
            outcome = await run_single(
                app, scenario, RuntimeMode(mode), repeat, options=run_options, workers=workers
            )
            after = sample_resources(app)
            outcomes[mode].append(outcome)
            resource_samples.append(
                {
                    "mode": mode,
                    "repeat_index": repeat,
                    "workers_before": before["workers"],
                    "workers_after": after["workers"],
                    "runtime_rss": after["runtime_rss"],
                    "peak_rss": int(outcome.metrics.get("peak_rss", 0)),
                }
            )
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

    stability = check_resource_stability(resource_samples, rss_baseline)
    reproducibility = check_reproducibility(outcomes)

    return {
        "scenario": scenario,
        "repeats": repeats,
        "modes": list(modes),
        "seed": int(seed),
        "workers": workers or app.scheduler.max_workers,
        "runs": {mode: [item.to_dict() for item in items] for mode, items in outcomes.items()},
        "aggregated": aggregated,
        "correctness": correctness,
        "speedup": speedup,
        "resource_stability": stability,
        "reproducibility": reproducibility,
        "generated_at": time.time(),
    }
