"""The long running observation has to be a **gate**.

The soak script used to return 0 in every case: it printed the growth of the
worker processes and of their memory and then ignored it.  A CI job that
cannot fail is not a gate - the test below proves that the script reports a
problem and exits non zero.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _soak(arguments: list[str]) -> subprocess.CompletedProcess:
    import os

    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "scripts/soak.py", *arguments],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        env=environment,
    )


def test_the_soak_gate_reports_a_problem():
    """An impossible threshold has to make the soak fail."""

    # the thresholds have to be exceeded for sure - a growth can be negative
    # (the pool shrinks), therefore the limit is far below any real value
    result = _soak(
        ["--minutes", "0.5", "--max-rss-growth-mb", "-100000", "--max-worker-growth", "-100000"]
    )
    assert result.returncode != 0, (
        "the soak has to fail when the thresholds are exceeded:\n" + result.stdout
    )
    assert "\u957f\u8dd1\u89c2\u6d4b\u672a\u901a\u8fc7" in result.stdout, result.stdout


def test_the_soak_gate_passes_on_a_healthy_run():
    """A healthy run has to pass - otherwise the gate would be useless."""

    # a loaded machine needs more than a few seconds for the first round
    result = _soak(["--minutes", "0.5", "--sample-every", "2"])
    assert result.returncode == 0, (
        "the soak has to pass on a healthy runtime:\n" + result.stdout + result.stderr
    )
    assert "\u957f\u8dd1\u89c2\u6d4b\u901a\u8fc7" in result.stdout, result.stdout
