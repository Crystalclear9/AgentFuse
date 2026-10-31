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

import logging
import queue
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA_VERSION = 9

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
        workflow_id          TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
        external_identity   TEXT,
        agent_description   TEXT,
        registration_source TEXT,
        metadata            TEXT,
        created_at          REAL NOT NULL,
        identity_status     TEXT DEFAULT 'CONFIRMED'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_invocations (
        invocation_id        TEXT PRIMARY KEY,
        workflow_id          TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
        agent_id             TEXT NOT NULL REFERENCES agents(agent_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
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
        workflow_id          TEXT NOT NULL REFERENCES workflows(workflow_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
        invocation_id        TEXT,
        agent_id             TEXT,
        tool_name            TEXT NOT NULL,
        tool_version         TEXT NOT NULL,
        normalized_arguments TEXT NOT NULL,
        workspace_snapshot_id TEXT,
        request_time         REAL NOT NULL,
        status               TEXT NOT NULL,
        physical_execution_id TEXT REFERENCES physical_executions(execution_id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
        result_reference     TEXT,
        result_hash          TEXT,
        candidate_key        TEXT,
        share_group_id       TEXT,
        error                TEXT,
        error_kind           TEXT,
        completed_time       REAL,
        retry_count          INTEGER DEFAULT 0
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS physical_executions (
        execution_id      TEXT PRIMARY KEY,
        workflow_id          TEXT REFERENCES workflows(workflow_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
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
        error_kind        TEXT,
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
        workflow_id          TEXT REFERENCES workflows(workflow_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
        candidate_key         TEXT NOT NULL,
        leader_execution_id  TEXT REFERENCES physical_executions(execution_id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
        leader_logical_call_id TEXT,
        creation_time         REAL NOT NULL,
        closed_time           REAL,
        status                TEXT NOT NULL,
        participant_count     INTEGER DEFAULT 0,
            participant_calls     TEXT,
        mode                  TEXT,
        tool_name             TEXT,
        tool_version          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS share_participants (
        share_group_id       TEXT NOT NULL REFERENCES share_groups(share_group_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
        logical_call_id      TEXT NOT NULL REFERENCES logical_tool_calls(logical_call_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
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
        wait_reason           TEXT,
        detach_reason         TEXT,
        detach_kind           TEXT,
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
        execution_id         TEXT NOT NULL REFERENCES physical_executions(execution_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
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
        share_group_id       TEXT NOT NULL REFERENCES share_groups(share_group_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
        logical_call_id      TEXT NOT NULL REFERENCES logical_tool_calls(logical_call_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
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
        execution_id         TEXT REFERENCES physical_executions(execution_id) ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED,
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

#: The schema as one text - the table definitions are taken from here so
#: that the create statements and the migrations cannot drift apart.
_SCHEMA_TEXT = "\n".join(_SCHEMA)

_INSERT_EVENT = """
INSERT OR REPLACE INTO runtime_events
(event_id, workflow_id, sequence, event_type, timestamp, agent_id, invocation_id,
 logical_call_id, physical_execution_id, parent_event_id, metadata)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

#: The tables that own a relation.  SQLite cannot add a foreign key to an
#: existing table, therefore the migration rebuilds them.  The definitions are
#: taken from the schema above so that the two cannot drift apart.
_REBUILD_TABLES = (
    "agents",
    "agent_invocations",
    "logical_tool_calls",
    "physical_executions",
    "share_groups",
    "share_participants",
    "dependency_records",
    "validation_records",
    "resource_samples",
)

#: The tables that carry a foreign key - used by the consistency check.
_FOREIGN_KEY_TABLES = _REBUILD_TABLES

#: The marker of the copy step inside a rebuild migration.
_COPY_PREFIX = "__copy__:"


def _table_definition(table: str) -> str:
    """Return the CREATE TABLE statement of a table from the schema."""

    marker = f"CREATE TABLE IF NOT EXISTS {table} ("
    index = _SCHEMA_TEXT.find(marker)
    if index < 0:  # pragma: no cover - defensive
        raise KeyError(f"no schema definition for {table}")
    # find the matching closing parenthesis - the definitions contain nested
    # parentheses (the foreign key references)
    depth = 0
    position = index + len(marker) - 1
    while position < len(_SCHEMA_TEXT):
        character = _SCHEMA_TEXT[position]
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                break
        position += 1
    return _SCHEMA_TEXT[index : position + 1] + ";"


def _table_columns(definition: str) -> list[str]:
    """Return the column names of a CREATE TABLE statement."""

    columns: list[str] = []
    for line in definition.splitlines()[1:-1]:
        stripped = line.strip().rstrip(",")
        if not stripped or stripped.upper().startswith("PRIMARY KEY"):
            continue
        columns.append(stripped.split()[0])
    return columns


def _rebuild_statements() -> tuple[str, ...]:
    """Build the statements that recreate the tables with their foreign keys.

    The copy step is a placeholder - the columns that are copied are decided
    when the migration runs, because only then it is known which columns the
    old table actually has.
    """

    statements: list[str] = []
    for table in _REBUILD_TABLES:
        definition = _table_definition(table)
        # a table that a failed migration left behind must not block the
        # next attempt
        statements.append(f"DROP TABLE IF EXISTS {table}_legacy")
        statements.append(f"ALTER TABLE {table} RENAME TO {table}_legacy")
        statements.append(
            definition.replace(
                f"CREATE TABLE IF NOT EXISTS {table}", f"CREATE TABLE {table}"
            )
        )
        statements.append(f"{_COPY_PREFIX}{table}")
        statements.append(f"DROP TABLE {table}_legacy")
    return tuple(statements)


def _copy_columns(conn: sqlite3.Connection, table: str) -> str:
    """Return the columns that the copy of a rebuilt table has to use.

    A table that was created by an older version does not have the columns
    that were added later - the copy may only use the columns that both
    tables have.
    """

    definition = _table_definition(table)
    new_columns = _table_columns(definition)
    old_columns = _table_columns_of(conn, f"{table}_legacy")
    shared = [column for column in new_columns if column in old_columns]
    if not shared:
        raise sqlite3.OperationalError(
            f"the table {table} has no column that can be copied"
        )
    return ", ".join(shared)


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
    (
        5,
        _rebuild_statements(),
    ),
    (
        6,
        (
            "ALTER TABLE share_participants ADD COLUMN wait_reason TEXT",
        ),
    ),
    (
        7,
        (
            "ALTER TABLE share_participants ADD COLUMN detach_kind TEXT",
        ),
    ),
    (
        8,
        (
            "ALTER TABLE agents ADD COLUMN identity_status TEXT DEFAULT 'CONFIRMED'",
        ),
    ),
    (
        9,
        (
            "ALTER TABLE logical_tool_calls ADD COLUMN error_kind TEXT",
            "ALTER TABLE physical_executions ADD COLUMN error_kind TEXT",
        ),
    ),
)


def _table_columns_of(conn: sqlite3.Connection, table: str) -> set[str]:
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
        #: number of statements that could not be persisted - a foreign key
        #: violation or a constraint error shows up here
        self.write_errors = 0
        self.last_error: str | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        """Create the schema and apply the pending migrations.

        The whole migration runs in **one** transaction: a migration that
        fails half way would leave the database in a state that no later run
        could repair.
        """

        conn = self._connect()
        try:
            # the connections of the module manage their transactions
            # explicitly - the pragmas below would be ignored inside one
            conn.isolation_level = None
            # the foreign key enforcement has to be off while the tables are
            # rebuilt - the rename / copy / drop cycle would otherwise fail.
            # Without the legacy behaviour SQLite rewrites the foreign key
            # references of the *other* tables when a table is renamed, which
            # would point them at the temporary "_legacy" tables.
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA legacy_alter_table=ON")
            current = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
            if any(current < version for version, _ in _MIGRATIONS):
                self._backup(conn)
            conn.execute("BEGIN")
            try:
                for statement in _SCHEMA:
                    conn.execute(statement)
                for version, statements in _MIGRATIONS:
                    if current >= version:
                        continue
                    self._apply_migration(conn, statements)
                    current = version
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),),
                )
                # A database that was written by a **newer** build must not be
                # stamped back to an older version - the next run of this code
                # would apply its migrations to a newer schema.
                version_to_write = max(SCHEMA_VERSION, current)
                conn.execute(f"PRAGMA user_version = {version_to_write}")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            # the connection has to be released - an open handle would keep the
            # file locked on Windows
            conn.close()

    @staticmethod
    def _apply_migration(
        conn: sqlite3.Connection, statements: tuple[str, ...]
    ) -> None:
        """Apply the statements of one migration.

        The caller has already switched the foreign key enforcement off and
        opened the transaction.
        """

        for statement in statements:
            if statement.startswith(_COPY_PREFIX):
                # the copy of a rebuilt table - the columns are decided
                # now, because only the old table knows which of them
                # existed before
                table = statement[len(_COPY_PREFIX) :]
                columns = _copy_columns(conn, table)
                conn.execute(
                    f"INSERT INTO {table} ({columns}) "
                    f"SELECT {columns} FROM {table}_legacy"
                )
                continue
            table, column = _parse_alter(statement)
            if column and column in _table_columns_of(conn, table):
                continue
            conn.execute(statement)

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
        # the read connections live in the threads that opened them - an open
        # handle would keep the file locked on Windows
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover
                pass
            self._local.conn = None
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
            self._report_dropped_write(sql, params)
            return
        self._queue.put((sql, tuple(params)))

    def write_many(self, rows: Iterable[tuple[str, Sequence[Any]]]) -> None:
        for sql, params in rows:
            self.write(sql, params)

    def transaction(self, rows: Iterable[tuple[str, Sequence[Any]]]) -> None:
        """Persist a group of statements in **one** transaction.

        A state transition that touches several tables must not be split
        across two transactions - otherwise a crash in between would leave the
        runtime with an inconsistent state.
        """

        statements = [(sql, tuple(params)) for sql, params in rows]
        if not statements:
            return
        if self._closed:
            self._report_dropped_write("__tx__", ())
            return
        self._queue.put(("__tx__", statements))

    def _report_dropped_write(self, sql: str, params: Sequence[Any]) -> None:
        """A write after ``close()`` is lost - it must not vanish silently."""

        self.write_errors += 1
        self.last_error = f"the database is closed: {sql} | {tuple(params)}"
        logging.getLogger("agentfuse.storage").error(
            "could not persist statement: %s", self.last_error[:400]
        )

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

        if self._closed:
            return
        done = threading.Event()
        self._queue.put(("__flush__", (done,)))
        if not done.wait(timeout):
            # the writer is stuck - the caller must know, otherwise it would
            # read rows that are not durable yet
            raise TimeoutError(
                f"the database did not flush within {timeout:.1f} s"
            )

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

    def foreign_key_violations(self) -> list[tuple[Any, ...]]:
        """Return the rows that violate a foreign key constraint.

        An empty list means that the stored data is referentially consistent.
        """

        self.flush()
        conn = self._read_conn()
        return [tuple(row) for row in conn.execute("PRAGMA foreign_key_check").fetchall()]

    def foreign_keys(self) -> list[tuple[Any, ...]]:
        """Return the foreign key constraints of the database."""

        conn = self._read_conn()
        rows: list[tuple[Any, ...]] = []
        for table in _FOREIGN_KEY_TABLES:
            for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
                rows.append((table, *tuple(row)))
        return rows

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

            try:
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

                if isinstance(item, tuple) and item[0] == "__tx__":
                    # an explicit transaction: the statements are committed
                    # together, independently of the batch size.  The statements
                    # that are already queued are committed first - otherwise the
                    # transaction could reference a row that is not committed yet.
                    if pending:
                        self._commit(pending)
                        pending = []
                    statements: list[tuple[str, tuple[Any, ...]]] = item[1]
                    self._commit(statements, atomic=True)
                    last_flush = time.monotonic()
                    continue

                pending.append(item)
                if len(pending) >= self._batch_size:
                    self._commit(pending)
                    pending = []
                    last_flush = time.monotonic()
            except Exception as exc:  # pragma: no cover - the writer must live
                # The writer thread is the only way into the database.  If it
                # died, every following write would be lost **silently**.
                self.write_errors += 1
                self.last_error = f"the writer thread failed: {type(exc).__name__}: {exc}"
                logging.getLogger("agentfuse.storage").exception(
                    "the writer thread could not process an item: %s", self.last_error
                )

        self._commit(pending)
        try:
            self._write_conn.close()
        except Exception:  # pragma: no cover
            pass
        self._write_conn = None

    def _commit(
        self, pending: list[tuple[str, tuple[Any, ...]]], atomic: bool = False
    ) -> None:
        """Execute the statements in **one** transaction.

        An explicit transaction (``atomic=True``) is **all or nothing**: a
        statement that violates a constraint rolls the whole transaction back.
        A half written state transition is worse than a lost one - the
        runtime would continue with an inconsistent state.

        The ordinary batch carries independent statements.  There a single
        failing statement is skipped (and counted), the others are written:
        the batch may contain unrelated statements of different tables.
        """

        if not pending or self._write_conn is None:
            return
        try:
            with self._write_conn:
                for sql, params in pending:
                    try:
                        self._write_conn.execute(sql, params)
                    except sqlite3.Error as exc:
                        if atomic:
                            # the transaction has to be rolled back
                            raise
                        # a statement that violates a constraint must not vanish
                        # silently - the counter is checked by the acceptance test
                        self.write_errors += 1
                        self.last_error = f"{type(exc).__name__}: {exc} | {sql} | {params}"
                        logging.getLogger("agentfuse.storage").error(
                            "could not persist statement: %s", self.last_error[:400]
                        )
        except sqlite3.Error as exc:
            # a deferred foreign key is checked when the transaction is
            # committed - the failure must not take the writer thread down
            self.write_errors += 1
            self.last_error = f"transaction failed: {type(exc).__name__}: {exc}"
            logging.getLogger("agentfuse.storage").error(
                "could not commit transaction: %s", self.last_error
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.write_errors += 1
            self.last_error = f"transaction failed: {type(exc).__name__}: {exc}"
            logging.getLogger("agentfuse.storage").exception(
                "could not commit transaction: %s", self.last_error
            )
