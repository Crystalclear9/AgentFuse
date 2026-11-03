"""Integration tests: a **foreign** framework can take part in the sharing.

The scripted adapter is part of this package, so it cannot prove that the
runtime is independent of a framework.  The external adapter is driven from
**another process** over a line based JSON protocol - the test below starts
that process and talks to it, which is exactly what a real framework would
do.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _wait_for_rows(database: Path, table: str, timeout: float = 30.0) -> None:
    """Wait until the writer thread has put the rows into the database.

    The runtime writes asynchronously - a test that reads the database right
    after the call would read an empty table.
    """

    import sqlite3
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if rows:
            return
        time.sleep(0.05)
    raise AssertionError(f"the table {table} stayed empty for {timeout} s")


class ExternalFramework:
    """A minimal framework that lives in its own process."""

    def __init__(self, home: Path) -> None:
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agentfuse.adapters.external_adapter",
                "--home",
                str(home),
                "--workflow",
                "external-framework",
            ],
            cwd=str(ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=environment,
        )

    def send(self, message: dict) -> dict:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        assert line, "the framework did not answer"
        return json.loads(line)

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=60)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            self.process.kill()


def test_a_foreign_framework_can_run_a_tool_call(tmp_path: Path):
    """The external framework binds an agent, calls a tool and gets the result.

    The framework does not know the internal ids: it sends the identity it
    happens to use and the runtime creates the internal one.  The result has
    to be the **real** result of the tool.
    """

    framework = ExternalFramework(tmp_path / "home")
    try:
        bound = framework.send({"type": "bind", "external_identity": "planner-7"})
        assert bound["type"] == "bound"
        agent_id = bound["agent_id"]
        assert agent_id, "the runtime has to create an internal identity"

        invoked = framework.send({"type": "invoke", "agent_id": "planner-7"})
        assert invoked["type"] == "invoked"
        invocation_id = invoked["invocation_id"]

        # the tool needs a snapshot - the framework does not have one, so it
        # asks for the default of the workflow
        import sqlite3

        database = tmp_path / "home" / "db" / "agentfuse.sqlite3"
        assert database.exists(), "the runtime has to create its database"
        _wait_for_rows(database, "agents")
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT snapshot_id FROM workspace_snapshots ORDER BY creation_time LIMIT 1"
            ).fetchall()
        assert rows, "the runtime has to provide a snapshot for the call"
        snapshot_id = rows[0][0]

        answer = framework.send(
            {
                "type": "call",
                "invocation_id": invocation_id,
                "tool": "ast_analyzer",
                "arguments": {"files": ["src/alpha.py"]},
                "snapshot_id": snapshot_id,
            }
        )
        assert answer["type"] == "result", answer
        assert answer["logical_call_id"], "the call has to have an id"
        result = answer["result"]
        assert result, "the framework has to receive the real result"

        ended = framework.send({"type": "end", "invocation_id": invocation_id})
        assert ended["type"] == "ended"
    finally:
        framework.close()


def test_the_external_framework_is_recorded_in_the_database(tmp_path: Path):
    """The events of the foreign framework are **observed** events.

    The graph is built from the events - therefore the database has to
    contain the agent, the invocation and the call of the external process.
    """

    import sqlite3

    framework = ExternalFramework(tmp_path / "home")
    try:
        bound = framework.send({"type": "bind", "external_identity": "worker-x"})
        invoked = framework.send({"type": "invoke", "agent_id": bound["agent_id"]})
        invocation_id = invoked["invocation_id"]

        database = tmp_path / "home" / "db" / "agentfuse.sqlite3"
        _wait_for_rows(database, "agents")
        _wait_for_rows(database, "agent_invocations")
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            agents = connection.execute(
                "SELECT agent_id, external_identity, registration_source FROM agents"
            ).fetchall()
            invocations = connection.execute(
                "SELECT invocation_id, agent_id FROM agent_invocations"
            ).fetchall()
            events = connection.execute(
                "SELECT event_type FROM runtime_events ORDER BY sequence"
            ).fetchall()

        assert any(row[1] == "worker-x" for row in agents), agents
        assert any(row[2] == "external-framework" for row in agents), agents
        assert any(row[0] == invocation_id for row in invocations), invocations
        types = [row[0] for row in events]
        assert "AGENT_BOUND" in types, types
        assert "AGENT_INVOKED" in types, types
    finally:
        framework.close()


def test_the_external_framework_cannot_invent_relations(tmp_path: Path):
    """An unknown invocation is refused and an unknown agent is not merged.

    The adapter **binds** an agent it has not seen before - the framework
    does not have to know the internal ids.  The identity of such an agent
    stays unconfirmed though, and it is never merged with another one
    (requirement 5.2).
    """

    framework = ExternalFramework(tmp_path / "home")
    try:
        answer = framework.send({"type": "invoke", "agent_id": "never-bound"})
        assert answer["type"] == "invoked", answer
        first_agent = answer["invocation_id"]

        # a second unknown identity is a **different** agent
        answer = framework.send({"type": "invoke", "agent_id": "never-bound-either"})
        assert answer["type"] == "invoked", answer
        assert answer["invocation_id"] != first_agent

        framework.send({"type": "bind", "external_identity": "known"})
        answer = framework.send(
            {"type": "call", "invocation_id": "inv-nope", "tool": "ast_analyzer"}
        )
        assert answer["type"] == "error", answer
        assert "unknown invocation" in answer["message"], answer

        answer = framework.send({"type": "nonsense"})
        assert answer["type"] == "error", answer
    finally:
        framework.close()


def test_an_automatically_bound_agent_is_undetermined(tmp_path: Path):
    """An agent that the adapter bound itself keeps an unconfirmed identity.

    The runtime must not merge it with another agent - the external framework
    only sent a label, it did not confirm anything.
    """

    import sqlite3

    framework = ExternalFramework(tmp_path / "home")
    try:
        framework.send({"type": "invoke", "agent_id": "unknown-one"})
        framework.send({"type": "invoke", "agent_id": "unknown-two"})
    finally:
        framework.close()

    database = tmp_path / "home" / "db" / "agentfuse.sqlite3"
    deadline = time.time() + 30
    rows: list[tuple] = []
    while time.time() < deadline:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT agent_id, external_identity, identity_status FROM agents"
            ).fetchall()
        if len(rows) >= 2:
            break
        time.sleep(0.05)

    assert len(rows) == 2, rows
    assert len({row[0] for row in rows}) == 2, "the two agents must stay separate"
    assert all(row[2] == "UNDETERMINED" for row in rows), rows
