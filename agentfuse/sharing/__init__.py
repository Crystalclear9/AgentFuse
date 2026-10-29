"""Shared execution subsystem: candidate keys, groups, validation, delivery."""

from __future__ import annotations

from .candidate import compute_candidate_key
from .dependency import DependencyLedger, DependencyRecord
from .result_delivery import ResultStore, StoredResult, compute_result_hash
from .share_group import IllegalTransition, Participant, ShareGroup
from .validator import DependencyValidator

__all__ = [
    "compute_candidate_key",
    "DependencyLedger",
    "DependencyRecord",
    "ResultStore",
    "StoredResult",
    "compute_result_hash",
    "IllegalTransition",
    "Participant",
    "ShareGroup",
    "DependencyValidator",
]
