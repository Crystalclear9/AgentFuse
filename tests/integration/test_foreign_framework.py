"""Requirement 3.1 and 21.5 - a **foreign** framework takes part.

The framework of ``examples/external_framework/agent_fwk.py`` is a program that
lives outside of the AgentFuse package: it cannot be imported by the runtime
and it knows nothing about the internal events.  The test starts it, lets it
drive the runtime over the protocol and checks that its result is a **real**
result of the real tool.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRAMEWORK = ROOT / "examples" / "external_framework" / "agent_fwk.py"
BRIDGE = ROOT / "agentfuse" / "adapters" / "external_adapter.py"


def test_the_framework_is_outside_of_the_package():
    """The framework must not be importable by the runtime."""

    assert "agentfuse" not in FRAMEWORK.read_text(encoding="utf-8").split('"""')[2], (
        "the framework must not use the package of the runtime"
    )
    # it does not import anything of the package
    for line in FRAMEWORK.read_text(encoding="utf-8").splitlines():
        if line.startswith(("import ", "from ")):
            assert "agentfuse" not in line, f"the framework imports the runtime: {line}"


def test_the_foreign_framework_gets_a_real_result(tmp_path: Path):
    """The framework drives the runtime and gets the real result of the tool."""

    home = tmp_path / "home"
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"

    # the framework starts the runtime itself and drives it over the protocol
    framework = subprocess.Popen(
        [sys.executable, str(FRAMEWORK), str(home)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=environment,
    )
    try:
        _out, errors = framework.communicate(timeout=180)
    finally:
        if framework.poll() is None:
            framework.terminate()
            try:
                framework.wait(timeout=30)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                framework.kill()

    assert framework.returncode == 0, f"the framework failed: {errors}"
    assert "the framework received the result" in errors, errors

    # the runtime really executed the tool - the database knows it
    import sqlite3

    database = home / "db" / "agentfuse.sqlite3"
    assert database.exists(), "the runtime has to write its database"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        executions = connection.execute(
            "SELECT tool_name, execution_status FROM physical_executions"
        ).fetchall()
        agents = connection.execute(
            "SELECT external_identity, identity_status FROM agents"
        ).fetchall()
    assert executions, "the runtime has to execute the tool of the framework"
    assert executions[0][0] == "ast_analyzer", executions
    assert executions[0][1] == "COMPLETED", executions
    # the framework bound its agent **explicitly** - the runtime confirmed the
    # identity, because the framework vouched for it
    assert agents, "the runtime has to bind the agent of the framework"
    assert agents[0][0] == "framework-planner", agents
    assert agents[0][1] == "CONFIRMED", agents
