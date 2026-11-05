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


def test_concurrent_soaks_keep_independent_reports():
    import concurrent.futures
    import json
    import re
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(_soak, [["--minutes", "0.02"], ["--minutes", "0.03"]]))
    paths = []
    for run, minutes in zip(runs, (0.02, 0.03)):
        assert run.returncode == 0, run.stdout + run.stderr
        match = re.search(r"report\s+:\s*(.+)", run.stdout)
        assert match, run.stdout
        path = Path(match.group(1).strip())
        paths.append(path)
        assert json.loads(path.read_text(encoding="utf-8"))["minutes"] == minutes
    assert len(set(paths)) == 2


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
    # A different runtime starts AFTER the soak baseline. Its worker must not
    # be mistaken for a leak belonging to the subprocess under observation.
    import threading
    from agentfuse.runtime.pool import build_command, build_environment
    foreign = []
    def start_foreign():
        foreign.append(subprocess.Popen(build_command(), env=build_environment(),
                       stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    timer = threading.Timer(5, start_foreign)
    timer.start()
    try:
        result = _soak(["--minutes", "0.5", "--sample-every", "2"])
    finally:
        timer.cancel()
        timer.join()
        for process in foreign:
            process.kill()
            process.wait(timeout=10)
            process.stdin.close()
    assert result.returncode == 0, (
        "the soak has to pass on a healthy runtime:\n" + result.stdout + result.stderr
    )
    assert "\u957f\u8dd1\u89c2\u6d4b\u901a\u8fc7" in result.stdout, result.stdout
