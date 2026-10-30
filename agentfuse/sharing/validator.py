"""Dependency validation against a workspace snapshot.

The validator re-computes the fingerprint of a dependency inside *another*
workspace snapshot.  It uses exactly the same fingerprint functions as the
controlled reader in the worker process, so leader and follower always agree
on what "the same dependency" means.

``UNKNOWN`` is a first class outcome: it is never treated as ``MATCH``.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..core.state import ValidationStatus
from ..workspace.fingerprint import (
    directory_listing_from_disk,
    fingerprint_bytes,
    fingerprint_directory_listing,
    fingerprint_existence,
    fingerprint_stat,
)
from ..workspace.reader import WorkspaceBoundaryError, normalize_relative_path
from ..workspace.snapshots import SnapshotStore
from .dependency import DependencyRecord


class DependencyValidator:
    """Compare dependencies between the leader snapshot and follower snapshots."""

    def __init__(self, snapshots: SnapshotStore) -> None:
        self.snapshots = snapshots

    # ------------------------------------------------------------------
    def _tree(self, snapshot_id: str) -> Path | None:
        try:
            return self.snapshots.get(snapshot_id).tree_path
        except Exception:
            return None

    def _resolve(
        self, tree: Path, relative_path: str
    ) -> tuple[Path | None, str | None]:
        """Resolve a dependency path inside the snapshot tree.

        The path is normalised and checked against the workspace boundary -
        a dependency record must never make the validator read a file outside
        of the snapshot.
        """

        try:
            relative = normalize_relative_path(relative_path)
        except WorkspaceBoundaryError as exc:
            return None, str(exc)
        target = tree / relative if relative != "." else tree
        root = os.path.realpath(tree)
        real = os.path.realpath(target)
        if real != root and not real.startswith(root + os.sep):
            return None, f"{relative_path} resolves outside of the snapshot"
        return target, None

    def fingerprint_for(
        self, snapshot_id: str, dependency: DependencyRecord
    ) -> tuple[str | None, str | None]:
        """Return ``(fingerprint, reason)``.

        ``fingerprint`` is ``None`` when the dependency cannot be evaluated in
        the given snapshot (missing file, missing directory, unknown
        snapshot).  In that case the validation result is ``UNKNOWN``.
        """

        tree = self._tree(snapshot_id)
        if tree is None:
            return None, f"unknown snapshot {snapshot_id}"

        relative = dependency.relative_path
        target, boundary_error = self._resolve(tree, relative)
        if target is None:
            return None, boundary_error
        kind = dependency.dependency_type

        try:
            if kind == "FILE_READ":
                if not target.is_file():
                    return None, f"{relative} does not exist in snapshot {snapshot_id}"
                return fingerprint_bytes(target.read_bytes()), None
            if kind == "DIRECTORY_LIST":
                if not target.is_dir():
                    return None, f"{relative} is not a directory in snapshot {snapshot_id}"
                return fingerprint_directory_listing(directory_listing_from_disk(target)), None
            if kind == "PATH_EXISTS":
                return fingerprint_existence(os.path.exists(target)), None
            if kind == "PATH_NOT_EXISTS":
                return fingerprint_existence(os.path.exists(target)), None
            if kind == "FILE_STAT":
                if not target.exists():
                    return None, f"{relative} does not exist in snapshot {snapshot_id}"
                info = target.stat()
                return fingerprint_stat(info.st_size, info.st_mtime_ns, target.is_dir()), None
        except OSError as exc:
            return None, f"cannot evaluate {relative}: {exc}"

        return None, f"unsupported dependency type {kind}"

    def compare(
        self, snapshot_id: str, dependency: DependencyRecord
    ) -> tuple[ValidationStatus, str | None]:
        """Compare a dependency against a snapshot."""

        fingerprint, reason = self.fingerprint_for(snapshot_id, dependency)
        if fingerprint is None:
            return ValidationStatus.UNKNOWN, reason
        if fingerprint != dependency.fingerprint:
            return (
                ValidationStatus.MISMATCH,
                f"{dependency.relative_path} differs: leader={dependency.fingerprint} "
                f"follower={fingerprint}",
            )
        return ValidationStatus.MATCH, None

    def validate_prefix(
        self, snapshot_id: str, dependencies: list[DependencyRecord]
    ) -> tuple[ValidationStatus, str | None, int]:
        """Validate a list of dependencies against a snapshot.

        Returns the overall status, a mismatch reason and the number of
        successfully validated leading entries.
        """

        matched = 0
        for dependency in dependencies:
            status, reason = self.compare(snapshot_id, dependency)
            if status is ValidationStatus.MATCH:
                matched += 1
                continue
            return status, reason, matched
        return ValidationStatus.MATCH, None, matched
