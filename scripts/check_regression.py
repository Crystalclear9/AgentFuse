"""Performance regression check.

Runs the benchmark comparison and compares the result with the recorded
reference values in ``benchmarks/baseline.json``.

    python scripts/check_regression.py                       # all scenarios
    python scripts/check_regression.py --scenario shared --repeat 3
    python scripts/check_regression.py --update-baseline     # re-record

The check is a **guard**, not a benchmark: it answers "did the runtime get
slower than the recorded reference?" with a clear exit code so that it can be
used in CI.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentfuse.app import AgentFuseApp  # noqa: E402
from agentfuse.benchmarks.runner import compare  # noqa: E402
from agentfuse.core.state import RuntimeMode  # noqa: E402
from agentfuse.scenarios import list_scenarios  # noqa: E402
from agentfuse.settings import load_settings  # noqa: E402

BASELINE_PATH = ROOT / "benchmarks" / "baseline.json"

#: performance metrics - a regression is a *slowdown*
PERFORMANCE_METRICS = ("makespan", "total_cpu_time")

#: correctness metrics - these must stay exactly the same
STRICT_METRICS = ("physical_executions", "detach_count", "validation_count")

#: metrics below this value are dominated by the measurement noise and are
#: therefore not compared (the signal would be smaller than the noise)
MIN_SIGNAL_SECONDS = 0.25

#: scenarios whose event counts are deterministic.  The cancellation scenario
#: intentionally races (the number of validations depends on the timing), its
#: correctness is asserted by the test suite instead.
NONDETERMINISTIC_SCENARIOS = ("cancel-failure", "divergent")


def load_baseline() -> dict[str, Any]:
    if not BASELINE_PATH.exists():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def save_baseline(data: dict[str, Any]) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def run(scenario: str, repeat: int, modes: tuple[str, ...], workers: int | None) -> dict[str, Any]:
    settings = load_settings(home=str(ROOT / ".agentfuse" / "regression"))
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    try:
        return await compare(app, scenario, repeats=repeat, modes=modes, workers=workers)
    finally:
        await app.close()


def summarise(report: dict[str, Any]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for mode, data in report["aggregated"].items():
        summary[mode] = {
            "makespan": data["makespan"]["median"],
            "makespan_mean": data["makespan"]["mean"],
            "makespan_stdev": data["makespan"]["stdev"],
            "makespan_max": data["makespan"]["max"],
            "total_cpu_time": data["total_cpu_time"]["median"],
            "total_cpu_time_mean": data["total_cpu_time"]["mean"],
            "total_cpu_time_stdev": data["total_cpu_time"]["stdev"],
            "total_cpu_time_max": data["total_cpu_time"]["max"],
            "physical_executions": data["physical_executions"]["mean"],
            "detach_count": data["detach_count"]["mean"],
            "validation_count": data["validation_count"]["mean"],
        }
    return summary


def check(
    scenario: str,
    mode: str,
    metrics: dict[str, float],
    reference: dict[str, Any] | None,
    tolerance: float,
    absolute_slack: float = 0.05,
) -> tuple[bool, list[str]]:
    """Compare the measured metrics with the recorded reference."""

    problems: list[str] = []
    if not reference:
        return True, ["no reference recorded"]

    for name in STRICT_METRICS:
        if scenario in NONDETERMINISTIC_SCENARIOS:
            continue
        value = metrics.get(name)
        expected = reference.get(name)
        if value is None or expected is None:
            continue
        if abs(float(value) - float(expected)) > 1e-9:
            problems.append(
                f"{scenario}/{mode}/{name}: {value} differs from the baseline {expected} "
                "(this metric must stay identical)"
            )

    for name in PERFORMANCE_METRICS:
        value = metrics.get(name)
        expected = reference.get(name)
        if value is None or expected is None:
            continue
        expected = float(expected)
        if expected <= 0:
            continue
        if expected < MIN_SIGNAL_SECONDS:
            # the signal is below the noise floor of the measurement
            continue
        stdev = float(reference.get(f"{name}_stdev", 0.0))
        worst = float(reference.get(f"{name}_max", expected))
        slack = max(
            tolerance / 100.0,
            (3.0 * stdev / expected) if expected else 0.0,
            (absolute_slack / expected) if expected else 0.0,
            # the worst run that was recorded is a legitimate reference value
            ((worst - expected) / expected) if expected else 0.0,
        )
        delta = (float(value) - expected) / expected
        if delta > slack:
            problems.append(
                f"{scenario}/{mode}/{name}: {value:.4f} is {delta * 100:+.1f}% vs baseline "
                f"{expected:.4f} (allowed {slack * 100:.0f}%)"
            )
    return (not problems), problems


async def main_async(args: argparse.Namespace) -> int:
    baseline = load_baseline()
    recorded = dict(baseline.get("scenarios", {}))
    updated: dict[str, Any] = {}
    failures: list[str] = []

    scenarios = [args.scenario] if args.scenario else list_scenarios()
    modes = tuple(args.modes)

    for scenario in scenarios:
        report = await run(scenario, args.repeat, modes, args.workers)
        summary = summarise(report)
        updated[scenario] = summary
        reference = recorded.get(scenario)
        for mode, metrics in summary.items():
            ok, problems = check(
                scenario,
                mode,
                metrics,
                (reference or {}).get(mode),
                args.tolerance,
                args.absolute_slack,
            )
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {scenario:<20} {mode:<9} {json.dumps(metrics)}")
            failures.extend(problems)

    if args.update_baseline:
        baseline["scenarios"] = updated
        baseline["metadata"] = {
            "repeat": args.repeat,
            "modes": list(modes),
            "workers": args.workers,
            "platform": sys.platform,
            "python": sys.version.split()[0],
        }
        save_baseline(baseline)
        print(f"\nbaseline written to {BASELINE_PATH.relative_to(ROOT)}")
        return 0

    if failures:
        print("\nperformance regressions detected:")
        for problem in failures:
            print(f"  - {problem}")
        return 1
    print("\nno performance regression detected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=None, help="only check this scenario")
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument(
        "--modes", nargs="+", default=["baseline", "online", "deferred"]
    )
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument(
        "--tolerance", type=float, default=30.0, help="allowed regression in percent"
    )
    parser.add_argument(
        "--absolute-slack",
        type=float,
        default=0.05,
        help="absolute slack in seconds (the noise floor of the measurement)",
    )
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
