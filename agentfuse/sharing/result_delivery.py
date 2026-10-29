"""Result storage and delivery.

Tool payloads are stored as files (they can be large) while SQLite keeps the
path, the size and the content hash.  The content hash is the canonical
identity of a result and is used by the benchmark to prove that a shared
result is identical to an independently computed one.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.ids import new_id
from ..workspace.fingerprint import canonical_json, sha256_hex


@dataclass
class StoredResult:
    result_id: str
    result_hash: str
    payload_path: str
    payload_size: int
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id": self.result_id,
            "result_hash": self.result_hash,
            "payload_path": self.payload_path,
            "payload_size": self.payload_size,
            "created_at": self.created_at,
        }


def compute_result_hash(payload: Any) -> str:
    """Canonical hash of a tool payload."""

    return sha256_hex(canonical_json(payload).encode("utf-8"))


class ResultStore:
    """Persist tool payloads on disk and keep an index in SQLite."""

    def __init__(self, root: str | Path, repository: Any = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.repository = repository

    def store(
        self,
        payload: Any,
        workflow_id: str | None = None,
        execution_id: str | None = None,
        logical_call_id: str | None = None,
    ) -> StoredResult:
        result_hash = compute_result_hash(payload)
        directory = self.root / (workflow_id or "unassigned")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{result_hash}.json"
        if not path.exists():
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
        stored = StoredResult(
            result_id=new_id("res"),
            result_hash=result_hash,
            payload_path=str(path),
            payload_size=path.stat().st_size,
            created_at=time.time(),
        )
        if self.repository is not None:
            self.repository.save_tool_result(
                stored.result_id,
                workflow_id,
                execution_id,
                logical_call_id,
                stored.result_hash,
                stored.payload_path,
                stored.payload_size,
                stored.created_at,
            )
        return stored

    def load(self, result_hash: str) -> Any:
        for path in self.root.glob(f"*/{result_hash}.json"):
            return json.loads(path.read_text(encoding="utf-8"))
        return None
