"""Workspace subsystem: immutable snapshots, manifests and the controlled reader.

The re-exports are **lazy** (:pep:`562`): the tool worker only needs the
reader and the fingerprint helpers, while the snapshot store pulls in the
storage layer.
"""

from __future__ import annotations

from typing import Any

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
    "build_manifest",
    "compare_manifests",
    "manifest_hash",
    "read_manifest",
    "write_manifest",
]

_EXPORTS = {
    "canonical_json": ("agentfuse.workspace.fingerprint", "canonical_json"),
    "fingerprint_bytes": ("agentfuse.workspace.fingerprint", "fingerprint_bytes"),
    "fingerprint_directory_listing": (
        "agentfuse.workspace.fingerprint",
        "fingerprint_directory_listing",
    ),
    "fingerprint_existence": ("agentfuse.workspace.fingerprint", "fingerprint_existence"),
    "fingerprint_file": ("agentfuse.workspace.fingerprint", "fingerprint_file"),
    "fingerprint_stat": ("agentfuse.workspace.fingerprint", "fingerprint_stat"),
    "hash_arguments": ("agentfuse.workspace.fingerprint", "hash_arguments"),
    "hash_manifest": ("agentfuse.workspace.fingerprint", "hash_manifest"),
    "ObservedDependency": ("agentfuse.workspace.reader", "ObservedDependency"),
    "WorkspaceBoundaryError": ("agentfuse.workspace.reader", "WorkspaceBoundaryError"),
    "WorkspaceReader": ("agentfuse.workspace.reader", "WorkspaceReader"),
    "normalize_relative_path": ("agentfuse.workspace.reader", "normalize_relative_path"),
    "SnapshotError": ("agentfuse.workspace.snapshots", "SnapshotError"),
    "SnapshotInfo": ("agentfuse.workspace.snapshots", "SnapshotInfo"),
    "SnapshotStore": ("agentfuse.workspace.snapshots", "SnapshotStore"),
    "build_manifest": ("agentfuse.workspace.manifest", "build_manifest"),
    "compare_manifests": ("agentfuse.workspace.manifest", "compare_manifests"),
    "manifest_hash": ("agentfuse.workspace.manifest", "manifest_hash"),
    "read_manifest": ("agentfuse.workspace.manifest", "read_manifest"),
    "write_manifest": ("agentfuse.workspace.manifest", "write_manifest"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    import importlib

    value = getattr(importlib.import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
