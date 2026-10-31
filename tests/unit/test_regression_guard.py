"""The performance regression guard must not be switchable off.

A guard that reports "no regression" without having compared anything is
worse than no guard at all: it gives the impression that the performance is
watched.  These tests pin the two ways the guard could be silenced:

1. a baseline update that only covers one scenario deletes the reference of
   the other ones,
2. a missing reference is reported as "no regression".

Both would be invisible in the CI - the step would simply stay green.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def _load_guard() -> Any:
    """Load ``scripts/check_regression.py`` as a module.

    The script is not part of the package - it is a command line tool.  The
    test loads it the same way the CI does.
    """

    path = ROOT / "scripts" / "check_regression.py"
    spec = importlib.util.spec_from_file_location("check_regression_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_missing_reference_is_a_failure_not_a_pass():
    """Without a reference the guard has compared nothing."""

    guard = _load_guard()
    ok, problems = guard.check(
        "shared",
        "online",
        {"makespan": 0.07, "physical_executions": 1.0},
        None,
        30.0,
    )
    assert not ok, "a missing reference must not be reported as a pass"
    assert problems, "the guard has to say why it could not compare"


def test_a_baseline_update_keeps_the_other_scenarios():
    """An update of one scenario must not delete the other references."""

    guard = _load_guard()
    baseline = {
        "scenarios": {
            "shared": {"online": {"makespan": 0.07}},
            "divergent": {"online": {"makespan": 0.6}},
        }
    }
    merged = guard.merge_baseline(baseline, {"shared": {"online": {"makespan": 0.08}}})
    assert "divergent" in merged["scenarios"], (
        "the reference of the other scenarios has to survive the update"
    )
    assert merged["scenarios"]["shared"]["online"]["makespan"] == 0.08, (
        "the measured scenario has to be updated"
    )


def test_a_changed_execution_count_is_a_regression():
    """The correctness metrics have to stay identical."""

    guard = _load_guard()
    reference = {"physical_executions": 1.0, "detach_count": 0.0}
    ok, problems = guard.check(
        "shared",
        "online",
        {"physical_executions": 2.0, "detach_count": 0.0},
        reference,
        30.0,
    )
    assert not ok, "an extra physical execution is a regression"
    assert problems


def test_a_changed_validation_count_is_a_regression():
    """A whole number of validations is deterministic and must stay the same."""

    guard = _load_guard()
    reference = {"physical_executions": 1.0, "detach_count": 0.0, "validation_count": 5.0}
    ok, problems = guard.check(
        "shared",
        "online",
        {"physical_executions": 1.0, "detach_count": 0.0, "validation_count": 1.0},
        reference,
        30.0,
    )
    assert not ok, "a broken sharing optimisation must be reported"
    assert problems


def test_a_slowdown_beyond_the_tolerance_is_a_regression():
    """The wall clock of a heavy scenario has to be watched."""

    guard = _load_guard()
    reference = {
        "physical_executions": 2.0,
        "detach_count": 0.0,
        "makespan": 0.5,
        "makespan_stdev": 0.01,
        "makespan_max": 0.52,
    }
    ok, problems = guard.check(
        "divergent",
        "baseline",
        {"physical_executions": 2.0, "detach_count": 0.0, "makespan": 1.0},
        reference,
        30.0,
        0.05,
    )
    assert not ok, "a doubled makespan is a regression"
    assert problems


def test_the_wall_clock_of_another_platform_is_not_compared():
    """A wall clock of another operating system is not comparable."""

    guard = _load_guard()
    reference = {
        "physical_executions": 2.0,
        "detach_count": 0.0,
        "makespan": 0.5,
        "makespan_stdev": 0.01,
        "makespan_max": 0.52,
    }
    ok, problems = guard.check(
        "divergent",
        "baseline",
        {"physical_executions": 2.0, "detach_count": 0.0, "makespan": 5.0},
        reference,
        30.0,
        0.05,
        compare_timings=False,
    )
    assert ok, "the timing must not be compared across platforms"
    assert not problems


def test_the_correctness_metrics_are_compared_on_every_platform():
    """The execution count does not depend on the operating system."""

    guard = _load_guard()
    reference = {"physical_executions": 2.0, "detach_count": 1.0}
    ok, problems = guard.check(
        "divergent",
        "online",
        {"physical_executions": 3.0, "detach_count": 1.0},
        reference,
        30.0,
        compare_timings=False,
    )
    assert not ok, "an extra execution must be reported on every platform"
    assert problems


def test_the_guard_names_the_measurements_it_skipped():
    """A skip has to be visible - otherwise it looks like a pass."""

    guard = _load_guard()
    skipped = guard.skipped_metrics(
        "shared",
        "online",
        {"makespan": 0.07, "total_cpu_time": 0.0},
        True,
    )
    assert skipped, "the skipped measurements have to be named"
    text = " ".join(skipped)
    assert "shared/online/makespan" in text
    assert "shared/online/total_cpu_time" in text
