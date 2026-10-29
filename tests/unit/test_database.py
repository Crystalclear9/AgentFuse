"""Unit tests for the SQLite schema, the migrations and the crash recovery."""

from __future__ import annotations

import sqlite3

import pytest

from agentfuse.storage.database import SCHEMA_VERSION, Database
from agentfuse.storage.repository import Repository


def _columns(db: Database, table: str) -> set[str]:
    conn = sqlite3.connect(db.path)
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return {str(row[1]) for row in rows}


def test_a_fresh_database_has_the_current_schema(tmp_path):
    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    assert db.schema_version() == SCHEMA_VERSION
    assert "admission_reason" in _columns(db, "physical_executions")
    assert "queued_time" in _columns(db, "physical_executions")
    db.close()


def test_an_old_database_is_migrated(tmp_path):
    """A v1 database is upgraded to the current schema without data loss."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()

    # Build the "old" state: drop the columns that v2 introduced.
    conn = sqlite3.connect(db.path)
    try:
        conn.execute("ALTER TABLE physical_executions DROP COLUMN admission_reason")
        conn.execute("ALTER TABLE physical_executions DROP COLUMN queued_time")
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "INSERT INTO workflows (workflow_id, scenario, start_time, status, runtime_mode) "
            "VALUES ('wf-old', 'demo', 1.0, 'COMPLETED', 'online')"
        )
        conn.commit()
    except sqlite3.OperationalError as exc:  # pragma: no cover - old sqlite
        conn.close()
        db.close()
        pytest.skip(f"the sqlite build cannot drop columns: {exc}")
    finally:
        conn.close()

    db.initialize()
    assert db.schema_version() == SCHEMA_VERSION
    assert "admission_reason" in _columns(db, "physical_executions")
    assert "queued_time" in _columns(db, "physical_executions")

    db.start()
    repository = Repository(db)
    workflow = repository.get_workflow("wf-old")
    assert workflow is not None, "the migration must not lose data"
    assert workflow["scenario"] == "demo"
    db.close()


def test_a_v2_database_is_migrated_to_v3(tmp_path):
    """The share group table gains the participant_calls column."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()

    conn = sqlite3.connect(db.path)
    try:
        conn.execute("ALTER TABLE share_groups DROP COLUMN participant_calls")
        conn.execute("PRAGMA user_version = 2")
        conn.commit()
    except sqlite3.OperationalError as exc:  # pragma: no cover - old sqlite
        conn.close()
        db.close()
        pytest.skip(f"the sqlite build cannot drop columns: {exc}")
    finally:
        conn.close()

    db.initialize()
    assert db.schema_version() == 3
    assert "participant_calls" in _columns(db, "share_groups")
    db.close()


def test_the_migration_creates_a_backup(tmp_path):
    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    conn = sqlite3.connect(db.path)
    try:
        conn.execute("ALTER TABLE physical_executions DROP COLUMN admission_reason")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    except sqlite3.OperationalError:  # pragma: no cover
        conn.close()
        db.close()
        pytest.skip("the sqlite build cannot drop columns")
    finally:
        conn.close()

    db.initialize()
    backups = list((tmp_path / "backups").glob("*.bak"))
    assert backups, "a backup must be created before a migration"
    db.close()


def test_interrupted_workflows_are_recovered(tmp_path):
    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()
    repository = Repository(db)

    db.write(
        "INSERT INTO workflows (workflow_id, scenario, start_time, status, runtime_mode) "
        "VALUES ('wf-run', 'demo', 1.0, 'RUNNING', 'online')"
    )
    db.write(
        "INSERT INTO physical_executions (execution_id, workflow_id, tool_name, tool_version, "
        "arguments_hash, execution_status) "
        "VALUES ('exec-1', 'wf-run', 'ast_analyzer', '1.0.0', 'hash', 'RUNNING')"
    )
    db.write(
        "INSERT INTO logical_tool_calls (logical_call_id, workflow_id, tool_name, tool_version, "
        "normalized_arguments, request_time, status) "
        "VALUES ('call-1', 'wf-run', 'ast_analyzer', '1.0.0', '{}', 1.0, 'SHARED_WAIT')"
    )
    db.flush()

    recovered = repository.recover_interrupted_workflows()
    assert recovered == ["wf-run"]

    workflow = repository.get_workflow("wf-run")
    assert workflow["status"] == "INTERRUPTED"
    assert workflow["end_time"] is not None

    execution = repository.get_execution("exec-1")
    assert execution["execution_status"] == "INTERRUPTED"

    call = repository.get_logical_call("call-1")
    assert call["status"] == "INTERRUPTED"

    # a second recovery must not change anything
    assert repository.recover_interrupted_workflows() == []
    db.close()


def test_completed_workflows_are_not_recovered(tmp_path):
    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()
    repository = Repository(db)
    db.write(
        "INSERT INTO workflows (workflow_id, scenario, start_time, end_time, status, runtime_mode) "
        "VALUES ('wf-done', 'demo', 1.0, 2.0, 'COMPLETED', 'online')"
    )
    db.flush()
    assert repository.recover_interrupted_workflows() == []
    assert repository.get_workflow("wf-done")["status"] == "COMPLETED"
    db.close()
