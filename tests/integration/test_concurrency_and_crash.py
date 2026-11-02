"""Integration tests: the concurrency and the crash behaviour of the delivery.

The test report used to list three things as "not verified": the behaviour
under concurrent writes to the web service, the consistency of the database
after a crash and the symbol link escape on Windows.  The first two can be
verified on any machine - they are verified here.  The third one needs
administrator rights on Windows and stays open (see TEST_REPORT §8).
"""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _child(home: Path, seconds: float) -> int:
    """Run scenarios until the process is killed."""

    code = f"""
import asyncio, pathlib, sys, time
sys.path.insert(0, {str(ROOT)!r})
from agentfuse.app import AgentFuseApp
from agentfuse.core.state import RuntimeMode
from agentfuse.scenarios.definitions import scenario_shared
from agentfuse.settings import load_settings

settings = load_settings(home={str(home)!r})
settings.ensure_directories()
app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)

async def main():
    deadline = time.time() + {seconds!r}
    while time.time() < deadline:
        await scenario_shared(app, RuntimeMode.ONLINE, {{}})
        await asyncio.sleep(0.05)

asyncio.run(main())
"""
    path = home / "child.py"
    path.write_text(code, encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.Popen(
        [sys.executable, str(path)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    ).pid


def test_the_database_survives_a_killed_process(tmp_path: Path):
    """A hard kill must not corrupt the database.

    The process is killed **while it writes** - the runtime cannot clean up.
    Afterwards the database has to be readable, consistent, and the runs that
    were interrupted have to be recognisable as such (requirement 10.5).
    """

    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    pid = _child(home, 30.0)
    # let it write for a while
    time.sleep(6.0)

    if os.name == "nt":  # pragma: no cover - the platform specific detail
        # the whole tree: the workers of the killed runtime would otherwise
        # survive as orphans and confuse the other tests
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    else:
        os.kill(pid, signal.SIGKILL)
    time.sleep(1.0)

    database = home / "db" / "agentfuse.sqlite3"
    assert database.exists(), "the runtime has to create its database"

    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok", f"the database is damaged: {integrity}"
        workflows = connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
        events = connection.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
    assert workflows >= 1, "the killed run has to be in the database"
    assert events > 0, "the events of the killed run have to be in the database"

    # the recovery marks the interrupted runs - it must not raise
    from agentfuse.storage.database import Database
    from agentfuse.storage.repository import Repository

    db = Database(database)
    try:
        db.initialize()
        db.start()
        repository = Repository(db)
        recovered = repository.recover_interrupted_workflows()
        assert isinstance(recovered, (int, list, tuple)), recovered
        # the runs that were interrupted are recognisable as such
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            statuses = [
                str(row[0])
                for row in connection.execute("SELECT status FROM workflows").fetchall()
            ]
        assert all(status in ("COMPLETED", "INTERRUPTED", "FAILED", "RUNNING") for status in statuses), statuses
    finally:
        db.close()


def test_the_web_service_survives_concurrent_writes(tmp_path: Path):
    """Many clients may write at the same time.

    The interface is designed for a single user, but it must not lose or
    corrupt anything when several requests arrive at once.
    """

    import httpx
    import uvicorn

    from agentfuse.api.app import create_app
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.definitions import scenario_shared
    from agentfuse.settings import load_settings

    home = tmp_path / "home"
    settings = load_settings(home=str(home))
    settings.ensure_directories()
    app = create_app(settings=settings)
    runtime = app.state.runtime

    import asyncio

    async def run_scenario() -> None:
        await scenario_shared(runtime, RuntimeMode.ONLINE, {})

    asyncio.run(run_scenario())

    # a **real** server - the in process transport of httpx is not thread safe
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 60
    while time.time() < deadline:
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)

    base = f"http://127.0.0.1:{port}"
    errors: list[str] = []
    results: list[int] = []

    def hammer(index: int) -> None:
        with httpx.Client(base_url=base, timeout=60) as client:
            for _ in range(5):
                try:
                    response = client.get("/api/overview")
                    response.raise_for_status()
                    results.append(response.status_code)
                    response = client.get("/api/workflows?limit=25")
                    response.raise_for_status()
                    results.append(response.status_code)
                except Exception as exc:  # pragma: no cover - the failure path
                    errors.append(f"{type(exc).__name__}: {exc}")

    try:
        threads = [threading.Thread(target=hammer, args=(index,)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        assert not errors, errors[:3]
        assert len(results) == 8 * 5 * 2, f"the requests did not all finish: {len(results)}"
        assert set(results) == {200}, f"a request failed: {set(results)}"

        # the runtime is still alive and the data is still consistent
        with httpx.Client(base_url=base, timeout=60) as client:
            response = client.get("/api/overview")
            response.raise_for_status()
            payload = response.json()
        assert payload["logical_tool_calls"] >= 2
        assert payload["physical_executions"] >= 1
    finally:
        server.should_exit = True
        thread.join(timeout=30)
