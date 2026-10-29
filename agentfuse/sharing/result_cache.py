"""Session scoped reuse of already finished results.

The first version of AgentFuse is an *in-flight* sharing runtime: two calls
that overlap in time share one physical execution.  This module implements the
extension that the protocol explicitly allows:

    "可以在同一次运行会话中扩展有限的已完成结果复用，但必须经过完整依赖验证。"

Correctness argument
--------------------
A cached result is only served when **all** of the following hold:

1. the candidate key matches - same tool, same tool version, same canonical
   arguments and the same environment signature;
2. the *content* of the caller's workspace snapshot is identical - proven by
   the manifest hash of the snapshot, not by its identity;
3. every dependency that the original execution observed is validated against
   the caller's own snapshot - the same check the share coordinator performs
   for an in-flight follower.

Because the tools are declared deterministic and read only, the three
conditions together imply that the result is identical.  The cache lives in the
memory of the runtime: it is **not** persisted and therefore never survives a
restart of the runtime (a cross session cache would need a durable dependency
ledger and is explicitly out of scope for the first version).
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .dependency import DependencyRecord


@dataclass
class CacheEntry:
    """One cached result together with the dependencies that produced it."""

    candidate_key: str
    snapshot_hash: str
    payload: Any
    result_hash: str
    execution_id: str
    dependencies: list[DependencyRecord] = field(default_factory=list)
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "snapshot_hash": self.snapshot_hash,
            "result_hash": self.result_hash,
            "execution_id": self.execution_id,
            "dependency_count": len(self.dependencies),
            "created_at": self.created_at,
        }


class ResultCache:
    """A bounded, session scoped cache of finished tool results."""

    def __init__(self, max_entries: int = 64) -> None:
        self.max_entries = max(0, int(max_entries))
        self._entries: "OrderedDict[tuple[str, str], CacheEntry]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self.max_entries > 0

    def key(self, candidate_key: str, snapshot_hash: str) -> tuple[str, str]:
        return (candidate_key, snapshot_hash)

    def get(self, candidate_key: str, snapshot_hash: str) -> CacheEntry | None:
        if not self.enabled:
            return None
        with self._lock:
            entry = self._entries.get(self.key(candidate_key, snapshot_hash))
            if entry is None:
                self.misses += 1
                return None
            self._entries.move_to_end(self.key(candidate_key, snapshot_hash))
            self.hits += 1
            return entry

    def put(
        self,
        *,
        candidate_key: str,
        snapshot_hash: str,
        payload: Any,
        result_hash: str,
        execution_id: str,
        dependencies: list[DependencyRecord],
        created_at: float = 0.0,
    ) -> CacheEntry | None:
        if not self.enabled:
            return None
        entry = CacheEntry(
            candidate_key=candidate_key,
            snapshot_hash=snapshot_hash,
            payload=payload,
            result_hash=result_hash,
            execution_id=execution_id,
            dependencies=list(dependencies),
            created_at=created_at,
        )
        with self._lock:
            key = self.key(candidate_key, snapshot_hash)
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
        return entry

    def invalidate(self, candidate_key: str | None = None) -> int:
        """Drop cached entries (optionally only those of one candidate key)."""

        with self._lock:
            if candidate_key is None:
                count = len(self._entries)
                self._entries.clear()
                return count
            keys = [key for key in self._entries if key[0] == candidate_key]
            for key in keys:
                self._entries.pop(key, None)
            return len(keys)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "entries": len(self._entries),
                "max_entries": self.max_entries,
                "hits": self.hits,
                "misses": self.misses,
            }
