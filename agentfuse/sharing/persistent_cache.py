"""A **durable** cache of finished tool results.

The session cache of :mod:`agentfuse.sharing.result_cache` lives in the memory
of the runtime: it never survives a restart.  This module implements the
extension of requirement section 19 ("\u8de8\u4f1a\u8bdd\u6301\u4e45\u5316\u7ed3\u679c\u7f13\u5b58").

Correctness argument
--------------------

A durable entry is only served when **all** of the following hold - the same
three conditions as the session cache, and the last one is the reason why a
durable cache is safe at all:

1. the candidate key matches - same tool, same tool version, same canonical
   arguments and the same environment signature;
2. the *content* of the caller's workspace snapshot is identical - proven by
   the manifest hash of the snapshot, not by its identity;
3. **every** dependency that the original execution observed is validated
   against the caller's own snapshot - the same check the share coordinator
   performs for an in-flight follower.

The third condition is what makes the cache safe across sessions: the entry
does not trust the snapshot it was created in, it **re-validates** the
dependencies against the snapshot of the caller.  A workspace that changed in
the meantime therefore does not get a stale result - the entry is dropped.

The payload itself is not copied into the database: the result store already
keeps it as a file, the cache only remembers its hash.  The database holds
the key, the hash and the dependency ledger.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dependency import DependencyRecord
from .result_cache import CacheEntry


@dataclass
class PersistentCacheEntry:
    """One durable entry: the reference to the result and its ledger."""

    candidate_key: str
    snapshot_hash: str
    result_hash: str
    execution_id: str
    dependencies: list[DependencyRecord] = field(default_factory=list)
    created_at: float = 0.0

    def to_cache_entry(self) -> CacheEntry:
        """The entry in the shape the share coordinator expects."""

        return CacheEntry(
            candidate_key=self.candidate_key,
            snapshot_hash=self.snapshot_hash,
            payload=None,  # the payload is loaded from the result store
            result_hash=self.result_hash,
            execution_id=self.execution_id,
            dependencies=list(self.dependencies),
            created_at=self.created_at,
        )


class PersistentResultCache:
    """A bounded cache of finished results that survives a restart.

    The cache is **off** by default (``persistent_cache_entries = 0``): a
    durable cache changes the observable behaviour of a workflow (a repeated
    request no longer starts a physical execution), so it has to be turned on
    deliberately.
    """

    def __init__(self, database_path: str | Path, max_entries: int = 0) -> None:
        self.database_path = Path(database_path)
        self.max_entries = max(0, int(max_entries))
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.max_entries > 0

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=10.0)
        connection.row_factory = sqlite3.Row
        return connection

    def _ensure_table(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS result_cache_entries (
                    candidate_key TEXT NOT NULL,
                    snapshot_hash TEXT NOT NULL,
                    result_hash TEXT NOT NULL,
                    execution_id TEXT NOT NULL,
                    dependencies TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (candidate_key, snapshot_hash)
                )
                """
            )

    # ------------------------------------------------------------------
    def get(self, candidate_key: str, snapshot_hash: str) -> PersistentCacheEntry | None:
        """The entry for the key - ``None`` when there is none."""

        if not self.enabled:
            return None
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS result_cache_entries (
                        candidate_key TEXT NOT NULL,
                        snapshot_hash TEXT NOT NULL,
                        result_hash TEXT NOT NULL,
                        execution_id TEXT NOT NULL,
                        dependencies TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        PRIMARY KEY (candidate_key, snapshot_hash)
                    )
                    """
                )
                row = connection.execute(
                    "SELECT * FROM result_cache_entries WHERE candidate_key = ? AND snapshot_hash = ?",
                    (candidate_key, snapshot_hash),
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            self.misses += 1
            return None
        self.hits += 1
        records: list[DependencyRecord] = []
        try:
            for item in json.loads(row["dependencies"]):
                records.append(
                    DependencyRecord(
                        execution_id=str(item.get("execution_id", "")),
                        sequence_number=int(item.get("sequence_number", 0)),
                        dependency_type=str(item.get("dependency_type", "")),
                        relative_path=str(item.get("relative_path", "")),
                        fingerprint=item.get("fingerprint"),
                        observed_time=float(item.get("observed_time", 0.0)),
                        snapshot_id=item.get("snapshot_id"),
                    )
                )
        except (TypeError, ValueError):
            # a broken entry must not be served
            self.invalidate(candidate_key)
            return None
        return PersistentCacheEntry(
            candidate_key=str(row["candidate_key"]),
            snapshot_hash=str(row["snapshot_hash"]),
            result_hash=str(row["result_hash"]),
            execution_id=str(row["execution_id"]),
            dependencies=records,
            created_at=float(row["created_at"]),
        )

    def put(
        self,
        *,
        candidate_key: str,
        snapshot_hash: str,
        result_hash: str,
        execution_id: str,
        dependencies: list[DependencyRecord],
        created_at: float = 0.0,
    ) -> PersistentCacheEntry | None:
        """Remember a finished result."""

        if not self.enabled:
            return None
        payload = json.dumps(
            [record.to_dict() for record in dependencies], ensure_ascii=False
        )
        entry = PersistentCacheEntry(
            candidate_key=candidate_key,
            snapshot_hash=snapshot_hash,
            result_hash=result_hash,
            execution_id=execution_id,
            dependencies=list(dependencies),
            created_at=created_at or time.time(),
        )
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS result_cache_entries (
                        candidate_key TEXT NOT NULL,
                        snapshot_hash TEXT NOT NULL,
                        result_hash TEXT NOT NULL,
                        execution_id TEXT NOT NULL,
                        dependencies TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        PRIMARY KEY (candidate_key, snapshot_hash)
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT OR REPLACE INTO result_cache_entries
                        (candidate_key, snapshot_hash, result_hash, execution_id, dependencies, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.candidate_key,
                        entry.snapshot_hash,
                        entry.result_hash,
                        entry.execution_id,
                        payload,
                        entry.created_at,
                    ),
                )
                # the cache is bounded: the oldest entries go out first
                connection.execute(
                    """
                    DELETE FROM result_cache_entries WHERE (candidate_key, snapshot_hash) IN (
                        SELECT candidate_key, snapshot_hash FROM result_cache_entries
                        ORDER BY created_at DESC LIMIT -1 OFFSET ?
                    )
                    """,
                    (self.max_entries,),
                )
        except sqlite3.Error:
            return None
        return entry

    def invalidate(self, candidate_key: str | None = None) -> int:
        """Drop the entries (optionally only those of one candidate key)."""

        if not self.enabled:
            return 0
        try:
            with self._lock, self._connect() as connection:
                if candidate_key is None:
                    count = connection.execute(
                        "SELECT COUNT(*) FROM result_cache_entries"
                    ).fetchone()[0]
                    connection.execute("DELETE FROM result_cache_entries")
                    return int(count)
                count = connection.execute(
                    "SELECT COUNT(*) FROM result_cache_entries WHERE candidate_key = ?",
                    (candidate_key,),
                ).fetchone()[0]
                connection.execute(
                    "DELETE FROM result_cache_entries WHERE candidate_key = ?", (candidate_key,)
                )
                return int(count)
        except sqlite3.Error:
            return 0

    def clear(self) -> None:
        self.invalidate(None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_entries": self.max_entries,
            "hits": self.hits,
            "misses": self.misses,
        }
