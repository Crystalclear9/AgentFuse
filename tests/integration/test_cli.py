"""Integration tests: the command line interface is really usable.

The acceptance checklist of the requirement says "the CLI can be run" - the
proof is that the commands are **executed** here, not that they are mentioned
somewhere.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(arguments: list[str], home: Path) -> subprocess.CompletedProcess:
    """Run the CLI in a home directory of its own."""

    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "agentfuse", "--home", str(home), *arguments],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        env=environment,
    )


def test_the_cli_is_usable(tmp_path: Path):
    """``init`` creates the data directory, ``--help`` lists the commands."""

    home = tmp_path / "home"

    help_result = _run(["--help"], home)
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    for command in ("init", "serve", "demo", "compare", "trace", "inspect"):
        assert command in help_result.stdout, (
            f"the help does not mention {command}: {help_result.stdout}"
        )

    init_result = _run(["init"], home)
    assert init_result.returncode == 0, init_result.stdout + init_result.stderr
    assert home.exists(), "init has to create the home directory"
    assert (home / "db" / "agentfuse.sqlite3").exists(), (
        "init has to create the database"
    )
    assert (home / "logs").is_dir(), "init has to create the log directory"
    assert "logs" in init_result.stdout, init_result.stdout


def test_the_cli_runs_a_scenario(tmp_path: Path):
    """``demo`` really executes the scenario and prints the real metrics."""

    home = tmp_path / "home"
    result = _run(["demo", "--scenario", "shared", "--mode", "online"], home)
    assert result.returncode == 0, result.stdout + result.stderr
    # the deterministic part of the output: two logical calls, one execution
    assert "2" in result.stdout
    assert "50.0%" in result.stdout, result.stdout
    # the result is really in the database of this home directory
    import sqlite3

    database = home / "db" / "agentfuse.sqlite3"
    assert database.exists()
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT COUNT(*) FROM physical_executions"
        ).fetchone()[0]
    assert rows == 1, f"the scenario has to produce exactly one execution, got {rows}"
