"""Canonical fingerprinting of workspace dependencies.

This module is the **single** implementation of dependency fingerprints.  It is
imported both by the tool worker (child process) which observes a dependency
and by the share validator (parent process) which re-computes the same
dependency inside another workspace snapshot.  Sharing the implementation
guarantees that both sides use an identical definition and therefore cannot
drift apart.

Fingerprint definitions
-----------------------
``FILE_READ``      SHA-256 over the raw file bytes.
``DIRECTORY_LIST`` SHA-256 over sorted JSON pairs of direct child names and kinds.
``PATH_EXISTS``    SHA-256 of the literal string ``exists``.
``PATH_NOT_EXISTS``SHA-256 of the literal string ``not_exists``.
``FILE_STAT``      SHA-256 over size, modification time and directory flag.
``FILE_STAT_SIZE`` SHA-256 over size and directory flag only.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

_CHUNK = 1 << 20


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any) -> str:
    """Stable JSON representation used for argument canonicalisation."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fingerprint_bytes(data: bytes) -> str:
    return sha256_hex(data)


def fingerprint_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def entry_kind(name: str, is_dir: bool, is_symlink: bool) -> str:
    if is_symlink:
        return "symlink"
    return "dir" if is_dir else "file"


def canonical_directory_listing(entries: Iterable[tuple[str, str]]) -> str:
    """Build the canonical listing payload for a directory."""

    return canonical_json(sorted(entries))


def fingerprint_directory_listing(entries: Iterable[tuple[str, str]]) -> str:
    return sha256_hex(canonical_directory_listing(entries).encode("utf-8"))


def fingerprint_existence(exists: bool) -> str:
    return sha256_hex(b"exists" if exists else b"not_exists")


def fingerprint_stat(size: int, mtime_ns: int | None, is_dir: bool) -> str:
    """Fingerprint of a stat observation.

    Omit the modification time only when the reader omitted it from the value
    exposed to the tool.
    """

    payload = canonical_json([size, mtime_ns, is_dir])
    return sha256_hex(payload.encode("utf-8"))


def hash_arguments(value: Any) -> str:
    """Hash of canonicalised tool arguments."""

    return sha256_hex(canonical_json(value).encode("utf-8"))


def hash_manifest(manifest: dict[str, dict[str, Any]]) -> str:
    """Hash of a snapshot manifest (path -> {sha256, size})."""

    payload = canonical_json(
        {path: {"sha256": entry["sha256"], "size": entry["size"]} for path, entry in manifest.items()}
    )
    return sha256_hex(payload.encode("utf-8"))


def directory_listing_from_disk(path: Path) -> list[tuple[str, str]]:
    """Return the canonical direct-children listing of ``path``."""

    entries: list[tuple[str, str]] = []
    with os.scandir(path) as iterator:
        for item in iterator:
            entries.append((item.name, entry_kind(item.name, item.is_dir(follow_symlinks=False), item.is_symlink())))
    return entries
