"""The fairness comparison of the benchmark modes has to be a real one.

The modes of a comparison have to use the same tool, the same request and the
same limits - otherwise the comparison measures the difference of the **setup**
and not the difference of the sharing.  The audit compares the data of a real
comparison run; this test makes sure that the comparison can actually report a
difference.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_audit() -> object:
    path = ROOT / "scripts" / "audit_requirements.py"
    spec = importlib.util.spec_from_file_location("audit_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_fairness_comparison_reports_the_differences():
    """Two different setups have to be reported as different."""

    audit = _load_audit()
    same_tools = {("ast_analyzer", "1.0.0", "hash")}
    different_tools = {("ast_analyzer", "1.0.0", "other-hash")}
    limits = {"baseline": {60.0}, "online": {60.0}}
    different_limits = {"baseline": {60.0}, "online": {30.0}}

    identical = audit._compare_modes(
        {"baseline": same_tools, "online": same_tools}, limits
    )
    assert identical, "the comparison has to produce a result"
    assert all(identical.values()), f"the modes are identical: {identical}"

    other_tool = audit._compare_modes(
        {"baseline": same_tools, "online": different_tools}, limits
    )
    assert other_tool["same tool implementations and requests"] is False, (
        "a different request has to be reported"
    )

    other_limit = audit._compare_modes(
        {"baseline": same_tools, "online": same_tools}, different_limits
    )
    assert other_limit["same timeout"] is False, (
        "a different timeout has to be reported"
    )


def test_the_fairness_comparison_of_a_real_run():
    """A real comparison of the same scenario uses the same setup."""

    audit = _load_audit()
    result = audit._fairness_of_the_modes()
    assert result, "the comparison has to produce data"
    assert set(result) == {
        "same tool implementations and requests",
        "same timeout",
    }
    assert all(result.values()), f"the modes differ: {result}"
