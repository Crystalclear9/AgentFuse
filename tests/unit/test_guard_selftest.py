"""The guard self test has to leave the working copy untouched.

The self test breaks the code on purpose.  If it is interrupted - a cancelled
CI job, a ``kill`` on the console, a full disk - the repository must not stay
broken.  These tests pin that behaviour.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_selftest() -> object:
    path = ROOT / "scripts" / "guard_selftest.py"
    spec = importlib.util.spec_from_file_location("guard_selftest_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_a_mutation_is_reverted_even_if_the_guard_fails(tmp_path):
    """A failing guard command must not leave the mutation behind."""

    selftest = _load_selftest()
    target = tmp_path / "module.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    original = target.read_text(encoding="utf-8")

    result = selftest._check_mutation(
        {
            "name": "the value changes",
            "clause": "1",
            "file": str(target),
            "old": "VALUE = 1",
            "new": "VALUE = 2",
            "guards": [[sys.executable, "-c", "raise SystemExit(1)"]],
        }
    )
    assert result["ok"], "the guard failed, therefore it caught the mutation"
    assert target.read_text(encoding="utf-8") == original, (
        "the mutation has to be reverted"
    )


def test_a_mutation_is_reverted_even_if_the_guard_passes(tmp_path):
    """A green guard must not leave the mutation behind either."""

    selftest = _load_selftest()
    target = tmp_path / "module.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    original = target.read_text(encoding="utf-8")

    result = selftest._check_mutation(
        {
            "name": "the value changes",
            "clause": "1",
            "file": str(target),
            "old": "VALUE = 1",
            "new": "VALUE = 2",
            "guards": [[sys.executable, "-c", "raise SystemExit(0)"]],
        }
    )
    assert not result["ok"], "the guard passed, therefore it did not catch anything"
    assert target.read_text(encoding="utf-8") == original


def test_a_timeout_does_not_count_as_caught():
    """A guard that hangs has not proven anything."""

    selftest = _load_selftest()
    caught, reason = selftest._caught(124, "", "")
    assert not caught, "a timeout is inconclusive"
    assert "timeout" in reason


def test_a_guard_that_fails_for_another_reason_does_not_count():
    """The output has to name the check that failed."""

    selftest = _load_selftest()
    caught, reason = selftest._caught(1, "SyntaxError: invalid syntax", "test_the_thing")
    assert not caught, "an unrelated failure is no proof"
    assert "test_the_thing" in reason


def test_a_guard_that_names_the_failing_check_counts():
    """The expected failure is recognised."""

    selftest = _load_selftest()
    caught, _reason = selftest._caught(1, "FAILED tests/test_x.py::test_the_thing", "test_the_thing")
    assert caught


def test_the_expected_signature_is_the_test_name():
    """The proof has to name the test that has to fail."""

    selftest = _load_selftest()
    signature = selftest._expected_signature(
        [sys.executable, "-m", "pytest", "tests/unit/test_x.py::test_the_thing", "-q"],
        {},
    )
    assert signature == "test_the_thing"
