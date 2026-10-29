"""Workspace manifests.

A manifest describes the content of a snapshot:

```text
{
    "src/alpha.py": {"sha256": "<hex>", "size": 812},
    ...
}
```

The manifest hash is the SHA-256 over the canonical representation of the
manifest, therefore two snapshots with the same content have the same hash and
a snapshot that was modified behind the runtime's back can be detected.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .fingerprint import fingerprint_file, hash_manifest


def build_manifest(tree: str | os.PathLike[str]) -> dict[str, dict[str, Any]]:
    """Build the manifest of a directory tree."""

    root = Path(tree)
    manifest: dict[str, dict[str, Any]] = {}
    for directory, _dirs, files in os.walk(root):
        directory_path = Path(directory)
        for name in sorted(files):
            file_path = directory_path / name
            relative = file_path.relative_to(root).as_posix()
            try:
                size = file_path.stat().st_size
                digest = fingerprint_file(file_path)
            except OSError:  # pragma: no cover - the file vanished meanwhile
                continue
            manifest[relative] = {"sha256": digest, "size": size}
    return manifest


def manifest_hash(manifest: dict[str, dict[str, Any]]) -> str:
    """Return the canonical hash of a manifest."""

    return hash_manifest(manifest)


def write_manifest(
    path: str | os.PathLike[str],
    *,
    snapshot_id: str,
    workspace_id: str,
    parent_snapshot_id: str | None,
    creation_time: float,
    manifest: dict[str, dict[str, Any]],
    source_reference: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Write the manifest of a snapshot next to its tree."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot_id": snapshot_id,
        "workspace_id": workspace_id,
        "parent_snapshot_id": parent_snapshot_id,
        "creation_time": creation_time,
        "manifest_hash": manifest_hash(manifest),
        "manifest": manifest,
        "source_reference": source_reference,
        "metadata": metadata or {},
    }
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload["manifest_hash"]


def read_manifest(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read a manifest file."""

    return json.loads(Path(path).read_text(encoding="utf-8"))


def compare_manifests(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Compare two manifests and report the differing paths."""

    left_paths = set(left)
    right_paths = set(right)
    added = sorted(right_paths - left_paths)
    removed = sorted(left_paths - right_paths)
    modified = sorted(
        path
        for path in left_paths & right_paths
        if left[path]["sha256"] != right[path]["sha256"]
    )
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "identical": not (added or removed or modified),
    }
