"""Immutable workspace snapshots and branch management.

A snapshot is a plain copy of a small source tree together with a SHA-256
manifest.  Snapshots are *immutable*: once created the runtime never writes
into a snapshot tree.  Branch edits happen in a separate writable working
directory which is turned into a new snapshot afterwards.

Layout on disk::

    <home>/workspaces/<workspace_id>/<snapshot_id>/
        tree/            # the copied files
        manifest.json    # {relative_path: {"sha256": ..., "size": ...}}

Deliberate first-version limitations (documented in
``docs/KNOWN_LIMITATIONS.md``): no copy-on-write, no block level dedup, no
process level checkpointing, no file system level isolation.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..core.ids import new_id
from .fingerprint import fingerprint_file, hash_manifest
from .manifest import build_manifest, compare_manifests, read_manifest, write_manifest


class SnapshotError(RuntimeError):
    """Raised for snapshot creation / integrity problems."""


@dataclass
class SnapshotInfo:
    snapshot_id: str
    workspace_id: str
    parent_snapshot_id: str | None
    path: Path
    creation_time: float
    manifest_hash: str
    manifest: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def tree_path(self) -> Path:
        return self.path / "tree"

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "workspace_id": self.workspace_id,
            "parent_snapshot_id": self.parent_snapshot_id,
            "path": str(self.path),
            "creation_time": self.creation_time,
            "manifest_hash": self.manifest_hash,
            "file_count": len(self.manifest),
            "source_reference": self.source_reference,
            "metadata": self.metadata,
        }


class SnapshotStore:
    """Create, fork, inspect, compare and delete workspace snapshots."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        max_files: int = 5000,
        max_total_bytes: int = 64 * 1024 * 1024,
        exclude: Iterable[str] = (),
        repository: Any = None,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.exclude = tuple(exclude)
        self.repository = repository
        self._cache: dict[str, SnapshotInfo] = {}
        self._integrity_cache: dict[str, bool] = {}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _is_excluded(self, relative: str, name: str) -> bool:
        for pattern in self.exclude:
            if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(relative, pattern):
                return True
        return False

    def snapshot_dir(self, workspace_id: str, snapshot_id: str) -> Path:
        return self.root / workspace_id / snapshot_id

    def tree_path(self, snapshot_id: str) -> Path:
        info = self.get(snapshot_id)
        return info.tree_path

    # ------------------------------------------------------------------
    # creation
    # ------------------------------------------------------------------
    def create_workspace(
        self,
        source_dir: str | os.PathLike[str],
        workspace_id: str | None = None,
        snapshot_id: str | None = None,
        source_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SnapshotInfo:
        """Materialise ``source_dir`` as the first snapshot of a workspace."""

        workspace_id = workspace_id or new_id("ws")
        return self.create_snapshot(
            workspace_id=workspace_id,
            source_dir=source_dir,
            parent_snapshot_id=None,
            snapshot_id=snapshot_id,
            source_reference=source_reference,
            metadata=metadata,
        )

    def create_snapshot(
        self,
        workspace_id: str,
        source_dir: str | os.PathLike[str],
        parent_snapshot_id: str | None = None,
        snapshot_id: str | None = None,
        source_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SnapshotInfo:
        source = Path(source_dir)
        if not source.is_dir():
            raise SnapshotError(f"source directory does not exist: {source}")

        snapshot_id = snapshot_id or new_id("snap")
        target_dir = self.snapshot_dir(workspace_id, snapshot_id)
        tree = target_dir / "tree"
        if target_dir.exists():
            shutil.rmtree(target_dir)
        tree.mkdir(parents=True, exist_ok=True)

        file_count = 0
        total_bytes = 0
        manifest: dict[str, dict[str, Any]] = {}

        for root, dirs, files in os.walk(source):
            root_path = Path(root)
            rel_root = root_path.relative_to(source)
            rel_root_str = "." if str(rel_root) == "." else rel_root.as_posix()

            kept_dirs = []
            for name in sorted(dirs):
                rel = f"{rel_root_str}/{name}" if rel_root_str != "." else name
                if self._is_excluded(rel, name):
                    continue
                kept_dirs.append(name)
            dirs[:] = kept_dirs

            for name in sorted(files):
                rel = f"{rel_root_str}/{name}" if rel_root_str != "." else name
                if self._is_excluded(rel, name):
                    continue
                source_file = root_path / name
                try:
                    size = source_file.stat().st_size
                except OSError:
                    continue
                file_count += 1
                total_bytes += size
                if file_count > self.max_files:
                    raise SnapshotError(
                        f"snapshot exceeds the maximum number of files ({self.max_files})"
                    )
                if total_bytes > self.max_total_bytes:
                    raise SnapshotError(
                        f"snapshot exceeds the maximum total size ({self.max_total_bytes} bytes)"
                    )
                destination = tree / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, destination)
                manifest[rel] = {"sha256": fingerprint_file(destination), "size": size}

        manifest_hash = hash_manifest(manifest)
        info = SnapshotInfo(
            snapshot_id=snapshot_id,
            workspace_id=workspace_id,
            parent_snapshot_id=parent_snapshot_id,
            path=target_dir,
            creation_time=time.time(),
            manifest_hash=manifest_hash,
            manifest=manifest,
            source_reference=source_reference or str(source),
            metadata=dict(metadata or {}),
        )
        self._write_manifest(info)
        self._cache[snapshot_id] = info
        self._persist(info)
        return info

    def fork_snapshot(
        self,
        parent_snapshot_id: str,
        snapshot_id: str | None = None,
        workspace_id: str | None = None,
        source_reference: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SnapshotInfo:
        """Create a new snapshot that starts from ``parent_snapshot_id``."""

        parent = self.get(parent_snapshot_id)
        snapshot_id = snapshot_id or new_id("snap")
        workspace_id = workspace_id or parent.workspace_id
        target_dir = self.snapshot_dir(workspace_id, snapshot_id)
        tree = target_dir / "tree"
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(parent.tree_path, tree)

        manifest = {
            path: dict(entry) for path, entry in parent.manifest.items()
        }
        info = SnapshotInfo(
            snapshot_id=snapshot_id,
            workspace_id=workspace_id,
            parent_snapshot_id=parent.snapshot_id,
            path=target_dir,
            creation_time=time.time(),
            manifest_hash=hash_manifest(manifest),
            manifest=manifest,
            source_reference=source_reference or f"fork:{parent.snapshot_id}",
            metadata=dict(metadata or {}),
        )
        self._write_manifest(info)
        self._cache[snapshot_id] = info
        self._persist(info)
        return info

    # ------------------------------------------------------------------
    # writable branch working copy
    # ------------------------------------------------------------------
    def materialise(self, snapshot_id: str, target_dir: str | os.PathLike[str]) -> Path:
        """Copy a snapshot tree into a writable directory for branch editing."""

        info = self.get(snapshot_id)
        target = Path(target_dir)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(info.tree_path, target)
        return target

    # ------------------------------------------------------------------
    # inspection
    # ------------------------------------------------------------------
    def get(self, snapshot_id: str) -> SnapshotInfo:
        cached = self._cache.get(snapshot_id)
        if cached is not None:
            return cached
        matches = list(self.root.glob(f"*/{snapshot_id}/manifest.json"))
        if not matches:
            raise SnapshotError(f"unknown snapshot: {snapshot_id}")
        manifest_path = matches[0]
        payload = read_manifest(manifest_path)
        snapshot_dir = manifest_path.parent
        info = SnapshotInfo(
            snapshot_id=snapshot_id,
            workspace_id=snapshot_dir.parent.name,
            parent_snapshot_id=payload.get("parent_snapshot_id"),
            path=snapshot_dir,
            creation_time=payload.get("creation_time", 0.0),
            manifest_hash=payload.get("manifest_hash", ""),
            manifest=payload.get("manifest", {}),
            source_reference=payload.get("source_reference"),
            metadata=payload.get("metadata", {}),
        )
        self._cache[snapshot_id] = info
        return info

    def inspect_snapshot(self, snapshot_id: str) -> SnapshotInfo:
        return self.get(snapshot_id)

    def list_snapshots(self, workspace_id: str | None = None) -> list[SnapshotInfo]:
        result: list[SnapshotInfo] = []
        root = self.root / workspace_id if workspace_id else self.root
        if not root.exists():
            return result
        for manifest_path in sorted(root.glob("*/manifest.json")):
            snapshot_id = manifest_path.parent.name
            try:
                result.append(self.get(snapshot_id))
            except SnapshotError:  # pragma: no cover
                continue
        return result

    def compare_snapshots(self, left_id: str, right_id: str) -> dict[str, Any]:
        """Compare two snapshots and report the differing paths."""

        left = self.get(left_id)
        right = self.get(right_id)
        comparison = compare_manifests(left.manifest, right.manifest)
        comparison["left"] = left_id
        comparison["right"] = right_id
        return comparison

    def verify_integrity(self, snapshot_id: str) -> bool:
        """Recompute the manifest and compare it with the recorded hash."""

        info = self.get(snapshot_id)
        manifest: dict[str, dict[str, Any]] = {}
        for root, _dirs, files in os.walk(info.tree_path):
            root_path = Path(root)
            for name in files:
                file_path = root_path / name
                relative = file_path.relative_to(info.tree_path).as_posix()
                manifest[relative] = {
                    "sha256": fingerprint_file(file_path),
                    "size": file_path.stat().st_size,
                }
        return hash_manifest(manifest) == info.manifest_hash

    def check_integrity(self, snapshot_id: str, force: bool = False) -> bool:
        """Verify the integrity of a snapshot, caching the result.

        A snapshot that was modified outside of AgentFuse must not take part in
        a shared execution: the dependency fingerprints of the followers would
        no longer describe the state the tool actually read.
        """

        if not force and snapshot_id in self._integrity_cache:
            return self._integrity_cache[snapshot_id]
        try:
            healthy = self.verify_integrity(snapshot_id)
        except Exception:
            healthy = False
        self._integrity_cache[snapshot_id] = healthy
        return healthy

    def delete_snapshot(self, snapshot_id: str) -> None:
        info = self.get(snapshot_id)
        if info.path.exists():
            shutil.rmtree(info.path)
        self._cache.pop(snapshot_id, None)

    # ------------------------------------------------------------------
    # persistence helpers
    # ------------------------------------------------------------------
    def _write_manifest(self, info: SnapshotInfo) -> None:
        write_manifest(
            info.path / "manifest.json",
            snapshot_id=info.snapshot_id,
            workspace_id=info.workspace_id,
            parent_snapshot_id=info.parent_snapshot_id,
            creation_time=info.creation_time,
            manifest=info.manifest,
            source_reference=info.source_reference,
            metadata=info.metadata,
        )

    def _persist(self, info: SnapshotInfo) -> None:
        if self.repository is None:
            return
        from ..storage.models import WorkspaceSnapshotRecord

        self.repository.save_snapshot(
            WorkspaceSnapshotRecord(
                snapshot_id=info.snapshot_id,
                parent_snapshot_id=info.parent_snapshot_id,
                workspace_id=info.workspace_id,
                snapshot_path=str(info.path),
                creation_time=info.creation_time,
                manifest_hash=info.manifest_hash,
                source_reference=info.source_reference,
                metadata=info.metadata,
            )
        )
