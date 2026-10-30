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
    assert db.schema_version() == SCHEMA_VERSION
    assert "participant_calls" in _columns(db, "share_groups")
    db.close()


def test_a_v3_database_is_migrated_to_v4(tmp_path):
    """The physical execution table gains the real worker pid."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()

    conn = sqlite3.connect(db.path)
    try:
        conn.execute("ALTER TABLE physical_executions DROP COLUMN worker_root_pid")
        conn.execute("ALTER TABLE physical_executions DROP COLUMN worker_command")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
    except sqlite3.OperationalError as exc:  # pragma: no cover - old sqlite
        conn.close()
        db.close()
        pytest.skip(f"the sqlite build cannot drop columns: {exc}")
    finally:
        conn.close()

    db.initialize()
    assert db.schema_version() == SCHEMA_VERSION
    assert "worker_root_pid" in _columns(db, "physical_executions")
    assert "worker_command" in _columns(db, "physical_executions")
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


# ----------------------------------------------------------------------
# foreign keys and transactions
# ----------------------------------------------------------------------
def test_the_schema_declares_the_foreign_keys(tmp_path):
    """Every relation of the schema is backed by a real foreign key."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()

    declared = db.foreign_keys()
    tables = {row[0] for row in declared}
    assert {"agents", "agent_invocations", "logical_tool_calls", "share_groups",
            "share_participants", "dependency_records", "validation_records"} <= tables
    assert len(declared) >= 14
    db.close()


def test_a_foreign_key_violation_is_rejected(tmp_path):
    """A row that references a missing workflow must not be stored."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()

    db.write(
        "INSERT INTO agents (agent_id, workflow_id, created_at) "
        "VALUES ('agent-x', 'wf-missing', 1.0)"
    )
    db.flush()

    assert db.write_errors >= 1, "the violation must be reported"
    assert "FOREIGN KEY" in (db.last_error or "")
    assert db.query("SELECT * FROM agents") == []
    db.close()


def test_the_migration_to_v5_keeps_the_data(tmp_path):
    """The rebuild that adds the foreign keys must not lose a single row."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()
    repository = Repository(db)

    db.write(
        "INSERT INTO workflows (workflow_id, scenario, start_time, status, runtime_mode) "
        "VALUES ('wf-keep', 'demo', 1.0, 'RUNNING', 'online')"
    )
    db.write(
        "INSERT INTO agents (agent_id, workflow_id, created_at) "
        "VALUES ('agent-keep', 'wf-keep', 1.0)"
    )
    db.write(
        "INSERT INTO physical_executions (execution_id, workflow_id, tool_name, tool_version, "
        "arguments_hash, execution_status, worker_root_pid, worker_command) "
        "VALUES ('exec-keep', 'wf-keep', 'ast_analyzer', '1.0.0', 'hash', 'RUNNING', 42, 'cmd')"
    )
    db.flush()

    # pretend that the database was created before the foreign keys existed
    conn = sqlite3.connect(db.path)
    try:
        for table in ("agents", "agent_invocations", "logical_tool_calls",
                      "physical_executions", "share_groups", "share_participants",
                      "dependency_records", "validation_records", "resource_samples"):
            conn.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
            conn.execute(
                f"CREATE TABLE {table} AS SELECT * FROM {table}_old"
            )
            conn.execute(f"DROP TABLE {table}_old")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    finally:
        conn.close()
    db.close()

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()

    assert db.schema_version() == SCHEMA_VERSION
    assert len(db.foreign_keys()) >= 14, "the rebuild must add the foreign keys"
    assert repository.get_workflow("wf-keep") is not None
    assert db.query("SELECT * FROM agents") != []
    execution = db.query_one("SELECT * FROM physical_executions WHERE execution_id = ?",
                             ("exec-keep",))
    assert execution is not None
    assert execution["worker_root_pid"] == 42
    assert execution["worker_command"] == "cmd"
    db.close()


def test_a_transaction_is_committed_at_once(tmp_path):
    """The statements of a transaction are committed together."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()

    db.transaction(
        [
            (
                "INSERT INTO workflows (workflow_id, scenario, start_time, status, runtime_mode) "
                "VALUES ('wf-tx', 'demo', 1.0, 'RUNNING', 'online')",
                (),
            ),
            (
                "INSERT INTO agents (agent_id, workflow_id, created_at) "
                "VALUES ('agent-tx', 'wf-tx', 1.0)",
                (),
            ),
        ]
    )
    db.flush()

    assert db.write_errors == 0
    assert db.query("SELECT * FROM agents WHERE agent_id = 'agent-tx'") != []
    db.close()


def test_a_failing_transaction_does_not_kill_the_writer(tmp_path):
    """A broken transaction must not stop the writer thread."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()

    db.transaction(
        [
            (
                "INSERT INTO agents (agent_id, workflow_id, created_at) "
                "VALUES ('agent-orphan', 'wf-missing', 1.0)",
                (),
            ),
        ]
    )
    db.flush()
    assert db.write_errors >= 1

    # the writer is still alive
    db.write(
        "INSERT INTO workflows (workflow_id, scenario, start_time, status, runtime_mode) "
        "VALUES ('wf-after', 'demo', 1.0, 'RUNNING', 'online')"
    )
    db.flush()
    assert db.write_errors == 1, "the second write must succeed"
    assert db.query("SELECT * FROM workflows WHERE workflow_id = 'wf-after'") != []
    db.close()


def test_the_migration_copies_only_the_columns_that_exist(tmp_path):
    """A column that was added later must not break the table rebuild."""

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()
    db.write(
        "INSERT INTO workflows (workflow_id, scenario, start_time, status, runtime_mode) "
        "VALUES ('wf-copy', 'demo', 1.0, 'RUNNING', 'online')"
    )
    db.flush()
    db.close()

    conn = sqlite3.connect(db.path)
    try:
        conn.execute("ALTER TABLE share_participants DROP COLUMN wait_reason")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
    finally:
        conn.close()

    db = Database(tmp_path / "db" / "agentfuse.sqlite3")
    db.initialize()
    db.start()
    assert db.schema_version() == SCHEMA_VERSION
    assert "wait_reason" in _columns(db, "share_participants")
    assert db.write_errors == 0
    assert db.query("SELECT * FROM workflows WHERE workflow_id = 'wf-copy'") != []
    db.close()
