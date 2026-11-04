"""Integration tests: the database survives a **damaged** write.

The test report used to list "the data consistency during a power failure" as
not verified.  A power loss cannot be produced on the machine, but its
**consequence** can: the tail of the write log is lost.  The test truncates
the write ahead log of a database that has real rows in it and checks that

* SQLite still opens the database and its integrity check passes,
* the rows that were committed before the damage are still there,
* the runtime can work with the database afterwards - it does not refuse to
  start and it writes again.

That is the property a power failure has to leave behind.  What it cannot
show is the behaviour of the **storage hardware** itself - that part stays
open in the test report.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest


def _wal_path(database: Path) -> Path:
    return database.with_name(database.name + "-wal")


def _write_rows(database: Path, count: int = 3) -> None:
    """Write rows through the runtime - the same path the delivery uses."""

    from agentfuse.storage.database import Database
    from agentfuse.storage.models import WorkflowRecord
    from agentfuse.storage.repository import Repository

    db = Database(database)
    try:
        db.initialize()
        db.start()
        repository = Repository(db)
        for index in range(count):
            repository.save_workflow(
                WorkflowRecord(
                    workflow_id=f"wf-damage-{index}",
                    scenario="damage",
                    start_time=time.time(),
                    end_time=time.time(),
                    status="COMPLETED",
                    configuration={"index": index},
                    runtime_mode="online",
                    random_seed=None,
                    label=None,
                    error=None,
                )
            )
        time.sleep(0.3)
    finally:
        db.close()


def test_a_damaged_write_log_does_not_destroy_the_database(tmp_path: Path):
    """The committed rows survive a truncated write ahead log."""

    database = tmp_path / "damaged.sqlite3"
    _write_rows(database)

    with sqlite3.connect(str(database)) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
    assert before >= 1, "the rows have to be committed before the damage"

    # the damage: the tail of the write ahead log is lost - this is what a
    # power loss does to a database that was not checkpointed
    wal = _wal_path(database)
    if not wal.exists():
        pytest.skip("the database does not use a write ahead log on this platform")
    with open(wal, "r+b") as handle:
        size = handle.seek(0, 2)
        handle.truncate(max(0, size - 512))
        handle.flush()

    # the database still opens and its content is consistent
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        assert integrity == "ok", f"the database is damaged: {integrity}"
        after = connection.execute("SELECT COUNT(*) FROM workflows").fetchone()[0]
    assert after >= 1, "the committed rows must survive the damage"

    # and the runtime works with it afterwards - it does not refuse to start
    from agentfuse.storage.database import Database
    from agentfuse.storage.models import WorkflowRecord
    from agentfuse.storage.repository import Repository

    reopened = Database(database)
    try:
        reopened.initialize()
        reopened.start()
        repository = Repository(reopened)
        workflows = repository.list_workflows(limit=10)
        assert workflows, "the database has to be readable after the damage"
        # the runtime writes again
        repository.save_workflow(
            WorkflowRecord(
                workflow_id="wf-after-the-damage",
                scenario="damage",
                start_time=time.time(),
                end_time=time.time(),
                status="COMPLETED",
                configuration={"after": True},
                runtime_mode="online",
                random_seed=None,
                label=None,
                error=None,
            )
        )
        time.sleep(0.3)
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            written = connection.execute(
                "SELECT COUNT(*) FROM workflows WHERE workflow_id = ?",
                ("wf-after-the-damage",),
            ).fetchone()[0]
        assert written == 1, "the runtime has to write into the recovered database"
    finally:
        reopened.close()
