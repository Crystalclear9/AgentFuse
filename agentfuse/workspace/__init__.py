"""Workspace subsystem: immutable snapshots and the controlled reader."""

from __future__ import annotations

from .fingerprint import (
    canonical_json,
    fingerprint_bytes,
    fingerprint_directory_listing,
    fingerprint_existence,
    fingerprint_file,
    fingerprint_stat,
    hash_arguments,
    hash_manifest,
)
from .reader import (
    ObservedDependency,
    WorkspaceBoundaryError,
    WorkspaceReader,
    normalize_relative_path,
)
from .snapshots import SnapshotError, SnapshotInfo, SnapshotStore

__all__ = [
    "canonical_json",
    "fingerprint_bytes",
    "fingerprint_directory_listing",
    "fingerprint_existence",
    "fingerprint_file",
    "fingerprint_stat",
    "hash_arguments",
    "hash_manifest",
    "ObservedDependency",
    "WorkspaceBoundaryError",
    "WorkspaceReader",
    "normalize_relative_path",
    "SnapshotError",
    "SnapshotInfo",
    "SnapshotStore",
]
