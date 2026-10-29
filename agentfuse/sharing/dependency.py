"""Dependency ledger.

Every physical execution owns one append only ledger of the dependencies that
the tool actually observed::

    R = [r1, r2, ..., rk]

The ledger is the only basis for the decision "is the result of this execution
reusable by another workspace state?".  It grows *while* the tool runs, which
is what enables online validation.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass
class DependencyRecord:
    execution_id: str
    sequence_number: int
    dependency_type: str
    relative_path: str
    fingerprint: str | None
    observed_time: float
    snapshot_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "sequence_number": self.sequence_number,
            "dependency_type": self.dependency_type,
            "relative_path": self.relative_path,
            "fingerprint": self.fingerprint,
            "observed_time": self.observed_time,
            "snapshot_id": self.snapshot_id,
        }


class DependencyLedger:
    """Thread safe append only ledger of observed dependencies."""

    def __init__(self, execution_id: str, snapshot_id: str | None = None) -> None:
        self.execution_id = execution_id
        self.snapshot_id = snapshot_id
        self._records: list[DependencyRecord] = []
        self._lock = threading.Lock()

    def append(
        self,
        dependency_type: str,
        relative_path: str,
        fingerprint: str | None,
        observed_time: float | None = None,
    ) -> DependencyRecord:
        with self._lock:
            record = DependencyRecord(
                execution_id=self.execution_id,
                sequence_number=len(self._records),
                dependency_type=dependency_type,
                relative_path=relative_path,
                fingerprint=fingerprint,
                observed_time=observed_time if observed_time is not None else time.time(),
                snapshot_id=self.snapshot_id,
            )
            self._records.append(record)
            return record

    def append_record(self, record: DependencyRecord) -> DependencyRecord:
        """Append an already built record (the sequence number is re-assigned)."""

        with self._lock:
            record.sequence_number = len(self._records)
            self._records.append(record)
            return record

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)

    def __iter__(self) -> Iterator[DependencyRecord]:
        with self._lock:
            return iter(list(self._records))

    def at(self, index: int) -> DependencyRecord | None:
        with self._lock:
            if 0 <= index < len(self._records):
                return self._records[index]
            return None

    def records(self) -> list[DependencyRecord]:
        with self._lock:
            return list(self._records)
