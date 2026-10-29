"""SQLite persistence layer.

Design notes
------------
* A dedicated writer thread owns the *write* connection.  Events are produced
  at a high rate by the coordinator; pushing them through a queue keeps the
  asyncio event loop free of disk IO and keeps transactions short.
* Reads use a per-thread connection.  The database runs in WAL mode so readers
  never block the writer.
* ``PRAGMA user_version`` stores the schema version; :func:`Database.initialize`
  performs the automatic bootstrap and any pending migration.
"""

from __future__ import annotations

import queue
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 4

_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workflows (
        workflow_id   TEXT PRIMARY KEY,
        scenario      TEXT NOT NULL,
        start_time    REAL NOT NULL,
        end_time      REAL,
        status        TEXT NOT NULL,
        configuration TEXT,
        runtime_mode  TEXT NOT NULL,
        random_seed   INTEGER,
        label         TEXT,
        error         TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agents (
        agent_id            TEXT PRIMARY KEY,
        workflow_id         TEXT NOT NULL,
        external_identity   TEXT,
        agent_description   TEXT,
        registration_source TEXT,
        metadata            TEXT,
        created_at          REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_invocations (
        invocation_id        TEXT PRIMARY KEY,
        workflow_id          TEXT NOT NULL,
        agent_id             TEXT NOT NULL,
        start_time           REAL NOT NULL,
        end_time             REAL,
        parent_invocation_id TEXT,
        status               TEXT NOT NULL,
        metadata             TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS logical_tool_calls (
        logical_call_id       TEXT PRIMARY KEY,
        workflow_id          TEXT NOT NULL,
        invocation_id        TEXT,
        agent_id             TEXT,
        tool_name            TEXT NOT NULL,
        tool_version         TEXT NOT NULL,
        normalized_arguments TEXT NOT NULL,
        workspace_snapshot_id TEXT,
        request_time         REAL NOT NULL,
        status               TEXT NOT NULL,
        physical_execution_id TEXT,
        result_reference     TEXT,
        result_hash          TEXT,
        candidate_key        TEXT,
        share_group_id       TEXT,
        error                TEXT,
        completed_time       REAL,
        retry_count          INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS physical_executions (
        execution_id      TEXT PRIMARY KEY,
        workflow_id       TEXT,
        tool_name         TEXT NOT NULL,
        tool_version      TEXT NOT NULL,
        arguments_hash    TEXT NOT NULL,
        candidate_key     TEXT,
        source_snapshot_id TEXT,
        worker_pid        INTEGER,
        start_time        REAL,
        finish_time       REAL,
        execution_status  TEXT NOT NULL,
        cpu_time          REAL,
        peak_rss          INTEGER,
        exit_status       TEXT,
        result_hash       TEXT,
        result_reference  TEXT,
        error             TEXT,
        timeout_seconds   REAL,
        share_group_id    TEXT,
        attempt           INTEGER DEFAULT 1,
        admission_reason  TEXT,
        queued_time       REAL,
        worker_root_pid   INTEGER,
        worker_command    TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS share_groups (
        share_group_id        TEXT PRIMARY KEY,
        workflow_id           TEXT,
        candidate_key         TEXT NOT NULL,
        leader_execution_id   TEXT,
        leader_logical_call_id TEXT,
        creation_time         REAL NOT NULL,
        closed_time           REAL,
        status                TEXT NOT NULL,
        participant_count     INTEGER DEFAULT 0,
        mode                  TEXT,
        tool_name             TEXT,
        tool_version          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS share_participants (
        share_group_id        TEXT NOT NULL,
        logical_call_id       TEXT NOT NULL,
        workflow_id           TEXT,
        agent_id              TEXT,
        invocation_id         TEXT,
        snapshot_id           TEXT,
        status                TEXT NOT NULL,
        validated_prefix_length INTEGER DEFAULT 0,
        validation_status     TEXT,
        attached_time         REAL,
        detached_time         REAL,
        delivered_time        REAL,
        wait_duration         REAL,
        detach_reason         TEXT,
        is_leader             INTEGER DEFAULT 0,
        PRIMARY KEY (share_group_id, logical_call_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_snapshots (
        snapshot_id        TEXT PRIMARY KEY,
        parent_snapshot_id TEXT,
        workspace_id       TEXT NOT NULL,
        snapshot_path      TEXT NOT NULL,
        creation_time      REAL NOT NULL,
        manifest_hash      TEXT NOT NULL,
        source_reference   TEXT,
        metadata           TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dependency_records (
        execution_id    TEXT NOT NULL,
        sequence_number INTEGER NOT NULL,
        dependency_type TEXT NOT NULL,
        relative_path   TEXT NOT NULL,
        fingerprint     TEXT,
        observed_time   REAL NOT NULL,
        snapshot_id     TEXT,
        PRIMARY KEY (execution_id, sequence_number)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS validation_records (
        validation_id      TEXT PRIMARY KEY,
        share_group_id     TEXT NOT NULL,
        logical_call_id    TEXT NOT NULL,
        execution_id       TEXT,
        dependency_sequence INTEGER,
        validation_status  TEXT NOT NULL,
        validation_time    REAL NOT NULL,
        mismatch_reason    TEXT,
        elapsed_ms         REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_events (
        event_id             TEXT PRIMARY KEY,
        workflow_id          TEXT,
        sequence             INTEGER NOT NULL,
        event_type           TEXT NOT NULL,
        timestamp            REAL NOT NULL,
        agent_id             TEXT,
        invocation_id        TEXT,
        logical_call_id      TEXT,
        physical_execution_id TEXT,
        parent_event_id      TEXT,
        metadata             TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS resource_samples (
        sample_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        workflow_id  TEXT,
        execution_id TEXT,
        timestamp    REAL NOT NULL,
        rss_bytes    INTEGER,
        cpu_seconds  REAL,
        system_cpu_percent REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_results (
        result_id      TEXT PRIMARY KEY,
        workflow_id    TEXT,
        execution_id   TEXT,
        logical_call_id TEXT,
        result_hash    TEXT NOT NULL,
        payload_path   TEXT,
        payload_size   INTEGER,
        created_at     REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS benchmark_runs (
        run_id                TEXT PRIMARY KEY,
        scenario              TEXT NOT NULL,
        mode                  TEXT NOT NULL,
        repeat_index          INTEGER NOT NULL,
        workflow_id           TEXT,
        started_at            REAL,
        finished_at           REAL,
        makespan              REAL,
        physical_executions   INTEGER,
        logical_calls         INTEGER,
        saved_executions      INTEGER,
        eligible_calls        INTEGER,
        shared_calls          INTEGER,
        share_success_rate    REAL,
        total_cpu_time        REAL,
        peak_rss              INTEGER,
        avg_share_wait        REAL,
        total_share_wait      REAL,
        validation_count      INTEGER,
        validation_failures   INTEGER,
        validation_ms         REAL,
        detach_count          INTEGER,
        first_conflict_at     REAL,
        follower_independent_at REAL,
        correct               INTEGER,
        mismatched            INTEGER,
        payload                TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_workflow ON runtime_events(workflow_id, sequence)",
    "CREATE INDEX IF NOT EXISTS idx_calls_workflow ON logical_tool_calls(workflow_id)",
    "CREATE INDEX IF NOT EXISTS idx_calls_execution ON logical_tool_calls(physical_execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_exec_workflow ON physical_executions(workflow_id)",
    "CREATE INDEX IF NOT EXISTS idx_dep_execution ON dependency_records(execution_id)",
    "CREATE INDEX IF NOT EXISTS idx_validation_group ON validation_records(share_group_id)",
    "CREATE INDEX IF NOT EXISTS idx_participant_call ON share_participants(logical_call_id)",
)

_INSERT_EVENT = """
INSERT OR REPLACE INTO runtime_events
(event_id, workflow_id, sequence, event_type, timestamp, agent_id, invocation_id,
 logical_call_id, physical_execution_id, parent_event_id, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

#: Ordered schema migrations.  ``(target_version, statements)``.
#: A migration is applied when the database is older than ``target_version``.
_MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (
        2,
        (
            "ALTER TABLE physical_executions ADD COLUMN admission_reason TEXT",
            "ALTER TABLE physical_executions ADD COLUMN queued_time REAL",
        ),
    ),
    (
        3,
        (
            "ALTER TABLE share_groups ADD COLUMN participant_calls TEXT",
        ),
    ),
    (
        4,
        (
            "ALTER TABLE physical_executions ADD COLUMN worker_root_pid INTEGER",
            "ALTER TABLE physical_executions ADD COLUMN worker_command TEXT",
        ),
    ),
)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names of an existing table."""

    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {str(row[1]) for row in rows}


def _parse_alter(statement: str) -> tuple[str, str | None]:
    """Return ``(table, column)`` of an ``ALTER TABLE ... ADD COLUMN`` statement."""

    parts = statement.split()
    if len(parts) < 6 or parts[0].upper() != "ALTER" or parts[2].upper() == "":
        return "", None
    if parts[3].upper() != "ADD":
        return "", None
    table = parts[2].split(".")[-1]
    column = parts[5].split()[0]
    return table, column


class Database:
    """Threaded SQLite wrapper with a dedicated writer thread."""

    def __init__(self, path: str | Path, batch_size: int = 64, flush_interval: float = 0.05) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue = queue.Queue()
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._writer_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._local = threading.local()
        self._write_conn: sqlite3.Connection | None = None
        self._closed = False

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Create the schema and apply the pending migrations."""

        conn = self._connect()
        with conn:
            for statement in _SCHEMA:
                conn.execute(statement)
            current = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            for version, statements in _MIGRATIONS:
                if current >= version:
                    continue
                self._backup(conn)
                for statement in statements:
                    table, column = _parse_alter(statement)
                    if column and column in _table_columns(conn, table):
                        continue
                    conn.execute(statement)
                current = version
            conn.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _backup(self, conn: sqlite3.Connection) -> Path | None:
        """Copy the database file before a migration is applied."""

        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.Error:  # pragma: no cover - best effort
            pass
        backup_dir = self.path.parent.parent / "backups"
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            target = backup_dir / f"{self.path.name}.v{self.schema_version()}.bak"
            shutil.copy2(self.path, target)
            return target
        except OSError:  # pragma: no cover - best effort
            return None

    def schema_version(self) -> int:
        """Return the schema version currently stored in the database file."""

        try:
            conn = self._connect()
            try:
                return int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            finally:
                conn.close()
        except sqlite3.Error:  # pragma: no cover
            return 0

    def start(self) -> None:
        if self._writer_thread is not None:
            return
        self._stop.clear()
        self._writer_thread = threading.Thread(
            target=self._writer_loop, name="agentfuse-db-writer", daemon=True
        )
        self._writer_thread.start()

    def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._stop.set()
        self._queue.put(None)
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=timeout)
            self._writer_thread = None
        if self._write_conn is not None:
            self._write_conn.close()
            self._write_conn = None
        self._closed = True

    # ------------------------------------------------------------------
    # connections
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        """Open a configured connection.

        WAL mode is preferred because it allows a reader to work while the
        writer commits.  Some file systems do not support it - a Windows drive
        that is mounted inside WSL2 for example cannot provide the shared
        memory mapping that WAL requires and answers with a disk I/O error.
        In that case the runtime falls back to the rollback journal mode.
        """

        try:
            return self._open(journal_mode="WAL")
        except sqlite3.OperationalError as wal_error:
            try:
                return self._open(journal_mode="TRUNCATE")
            except sqlite3.OperationalError:
                raise sqlite3.OperationalError(
                    f"cannot open the AgentFuse database at {self.path}: {wal_error}. "
                    "Another AgentFuse instance may be using the same home directory "
                    "(for example the local web server) - stop it or point "
                    "AGENTFUSE_HOME / --home to a different directory."
                ) from wal_error

    def _open(self, journal_mode: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(f"PRAGMA journal_mode={journal_mode}")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=30000")
        except Exception:
            conn.close()
            raise
        return conn

    def _read_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    # ------------------------------------------------------------------
    # writing
    # ------------------------------------------------------------------
    def write(self, sql: str, params: Sequence[Any] = ()) -> None:
        """Queue a single statement for execution."""

        if self._closed:
            return
        self._queue.put((sql, tuple(params)))

    def write_many(self, rows: Iterable[tuple[str, Sequence[Any]]]) -> None:
        for sql, params in rows:
            self.write(sql, params)

    def insert_event(self, event: dict[str, Any]) -> None:
        import json

        self.write(
            _INSERT_EVENT,
            (
                event["event_id"],
                event.get("workflow_id"),
                event.get("sequence", 0),
                event["event_type"],
                event.get("timestamp", 0.0),
                event.get("agent_id"),
                event.get("invocation_id"),
                event.get("logical_call_id"),
                event.get("physical_execution_id"),
                event.get("parent_event_id"),
                json.dumps(event.get("metadata") or {}, ensure_ascii=False, default=str),
            ),
        )

    def flush(self, timeout: float = 5.0) -> None:
        """Block until all queued statements have been committed."""

        done = threading.Event()
        self._queue.put(("__flush__", (done,)))
        done.wait(timeout)

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        # Flush the pending writes first so that a reader always observes the
        # writes that were already produced by the runtime (read-your-writes).
        self.flush()
        conn = self._read_conn()
        return list(conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = self.query_one(sql, params)
        if row is None:
            return None
        return row[0]

    # ------------------------------------------------------------------
    # writer thread
    # ------------------------------------------------------------------
    def _writer_loop(self) -> None:
        self._write_conn = self._connect()
        pending: list[tuple[str, tuple[Any, ...]]] = []
        last_flush = time.monotonic()
        while True:
            timeout = max(0.0, self._flush_interval - (time.monotonic() - last_flush))
            try:
                item = self._queue.get(timeout=timeout)
            except queue.Empty:
                item = None

            if item is None:
                # nothing arrived within the flush interval
                if self._stop.is_set() and self._queue.empty():
                    break
                if pending:
                    self._commit(pending)
                    pending = []
                last_flush = time.monotonic()
                continue

            if isinstance(item, tuple) and item[0] == "__flush__":
                self._commit(pending)
                pending = []
                done: threading.Event = item[1][0]
                done.set()
                last_flush = time.monotonic()
                continue

            pending.append(item)
            if len(pending) >= self._batch_size:
                self._commit(pending)
                pending = []
                last_flush = time.monotonic()

        self._commit(pending)
        try:
            self._write_conn.close()
        except Exception:  # pragma: no cover
            pass
        self._write_conn = None

    def _commit(self, pending: list[tuple[str, tuple[Any, ...]]]) -> None:
        if not pending or self._write_conn is None:
            return
        with self._write_conn:
            for sql, params in pending:
                try:
                    self._write_conn.execute(sql, params)
                except sqlite3.Error:  # pragma: no cover - keep the writer alive
                    import logging

                    logging.getLogger("agentfuse.storage").exception(
                        "failed to persist statement: %s", sql[:120]
                    )
