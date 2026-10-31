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

from agentfuse.console import configure_console  # noqa: E402

configure_console()

from agentfuse.app import AgentFuseApp  # noqa: E402
from agentfuse.benchmarks.runner import compare  # noqa: E402
from agentfuse.core.state import RuntimeMode  # noqa: E402
from agentfuse.scenarios import list_scenarios  # noqa: E402
from agentfuse.settings import load_settings  # noqa: E402

BASELINE_PATH = ROOT / "benchmarks" / "baseline.json"

#: performance metrics - a regression is a *slowdown*
PERFORMANCE_METRICS = ("makespan", "total_cpu_time")

#: correctness metrics - these must stay exactly the same.  The number of
#: dependency validations is **not** part of them: it counts the validations
#: that happened before the leader finished, which depends on the scheduling
#: of the two processes.
STRICT_METRICS = ("physical_executions", "detach_count")

#: The number of dependency validations is deterministic as well - as long as
#: the recorded reference is a whole number.  A broken sharing optimisation
#: would change it, therefore it is compared whenever the reference allows it.
DETERMINISTIC_METRICS = ("validation_count",)

#: A measurement below this value is dominated by the noise of the machine:
#: the start up of a worker alone costs about 0.15 s.  Such a measurement is
#: **not** compared - reporting the noise as a regression would be worse than
#: reporting nothing.  The scenarios that are skipped are named in the report.
MIN_SIGNAL_SECONDS = 0.15

#: The worker start up costs about 0.15 s.  A light scenario therefore has a
#: noise floor of that size - a smaller slack would report the noise as a
#: regression.
NOISE_FLOOR_SECONDS = 0.15

#: scenarios whose event counts are deterministic.  The cancellation scenario
#: intentionally races (the number of validations depends on the timing), its
#: correctness is asserted by the test suite instead.
NONDETERMINISTIC_SCENARIOS = ("cancel-failure",)


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


def merge_baseline(baseline: dict[str, Any], updated: dict[str, Any]) -> dict[str, Any]:
    """Merge the measured scenarios into the recorded baseline.

    The baseline is **merged**, never replaced: an update of a single scenario
    must not delete the reference of the other ones.  A missing reference
    would switch the guard off silently - it would have nothing to compare
    against.
    """

    scenarios = dict(baseline.get("scenarios", {}))
    scenarios.update(updated)
    baseline["scenarios"] = scenarios
    return baseline


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
            # the benefit of the online dependency check
            "detach_lead_ms_max": data["detach_lead_ms_max"]["mean"],
            "online_detach_before_finish_rate": data[
                "online_detach_before_finish_rate"
            ]["mean"],
        }
    return summary


def skipped_metrics(
    scenario: str,
    mode: str,
    reference: dict[str, Any] | None,
    compare_timings: bool,
) -> list[str]:
    """The measurements that could **not** be compared.

    A guard has to say what it did not check - otherwise a silent skip looks
    like a pass.
    """

    skipped: list[str] = []
    if not reference:
        return [f"{scenario}/{mode}: no reference recorded"]
    if not compare_timings:
        return [f"{scenario}/{mode}: the wall clock is not comparable across platforms"]
    for name in PERFORMANCE_METRICS:
        expected = reference.get(name)
        if expected is None:
            continue
        if float(expected) <= 0:
            skipped.append(f"{scenario}/{mode}/{name}: the reference is zero")
        elif float(expected) < MIN_SIGNAL_SECONDS:
            skipped.append(
                f"{scenario}/{mode}/{name}: the reference {float(expected):.4f} s is below "
                f"the noise floor of {MIN_SIGNAL_SECONDS:.2f} s"
            )
    for name in DETERMINISTIC_METRICS:
        expected = reference.get(name)
        if expected is None:
            continue
        if abs(float(expected) - round(float(expected))) > 1e-9:
            skipped.append(
                f"{scenario}/{mode}/{name}: the reference {float(expected):.3f} is not a "
                "whole number - the metric depends on the scheduling"
            )
    return skipped


def check(
    scenario: str,
    mode: str,
    metrics: dict[str, float],
    reference: dict[str, Any] | None,
    tolerance: float,
    absolute_slack: float = 0.05,
    compare_timings: bool = True,
) -> tuple[bool, list[str]]:
    """Compare the measured metrics with the recorded reference.

    A **missing reference is a failure**, not a pass: a guard that cannot
    compare anything must not report "no regression".  Otherwise the guard
    could be switched off silently - for example by a baseline update that
    only covered one scenario.
    """

    problems: list[str] = []
    if not reference:
        return False, [
            f"{scenario}/{mode}: no reference recorded - the baseline does not "
            "cover this scenario, the guard cannot compare anything"
        ]

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

    for name in DETERMINISTIC_METRICS:
        if scenario in NONDETERMINISTIC_SCENARIOS:
            continue
        value = metrics.get(name)
        expected = reference.get(name)
        if value is None or expected is None:
            continue
        # a fractional reference means the metric depends on the scheduling of
        # the two processes - it is not deterministic and must not be compared
        if abs(float(expected) - round(float(expected))) > 1e-9:
            continue
        if abs(float(value) - float(expected)) > 1e-9:
            problems.append(
                f"{scenario}/{mode}/{name}: {value} differs from the baseline {expected} "
                "(this metric must stay identical)"
            )

    if not compare_timings:
        # The baseline was recorded on another platform: the wall clock of a
        # different operating system is not comparable.  The correctness
        # metrics above are platform independent and stay in force.
        return (not problems), problems

    for name in PERFORMANCE_METRICS:
        value = metrics.get(name)
        expected = reference.get(name)
        if value is None or expected is None:
            continue
        expected = float(expected)
        if expected <= 0:
            continue
        if expected < MIN_SIGNAL_SECONDS:
            # the measurement is below the noise floor of the machine - it is
            # not comparable, therefore it is skipped (and named in the report)
            continue
        stdev = float(reference.get(f"{name}_stdev", 0.0))
        worst = float(reference.get(f"{name}_max", expected))
        slack = max(
            tolerance / 100.0,
            (3.0 * stdev / expected) if expected else 0.0,
            (absolute_slack / expected) if expected else 0.0,
            # the worst run that was recorded is a legitimate reference value
            ((worst - expected) / expected) if expected else 0.0,
            # the start up of a worker is the noise floor of a light scenario
            (NOISE_FLOOR_SECONDS / expected) if expected else 0.0,
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
    # The wall clock of another operating system is not comparable: the
    # baseline of this repository was recorded on Windows.  The correctness
    # metrics (the number of executions, the number of separations) are
    # platform independent and are compared on every platform.
    baseline_platform = str(baseline.get("metadata", {}).get("platform", ""))
    same_platform = baseline_platform in ("", sys.platform)
    if not same_platform:
        print(
            f"the baseline was recorded on {baseline_platform!r}, this run uses "
            f"{sys.platform!r}: the wall clock is not compared, the correctness "
            "metrics are"
        )

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
                compare_timings=same_platform,
            )
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {scenario:<20} {mode:<9} {json.dumps(metrics)}")
            failures.extend(problems)
            for note in skipped_metrics(
                scenario, mode, (reference or {}).get(mode), same_platform
            ):
                print(f"       [SKIP] {note}")

    if args.update_baseline:
        # The baseline is **merged**, never replaced: an update of a single
        # scenario must not delete the reference of the other ones - a
        # missing reference would silently switch the guard off.
        baseline = merge_baseline(baseline, updated)
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
        help=(
            "absolute slack in seconds that is added to the allowed regression. "
            "It covers the noise of the measurement (the start up of a worker "
            "costs about 0.15 s - a measurement below that is not compared at all)."
        ),
    )
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
