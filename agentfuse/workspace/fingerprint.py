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
``DIRECTORY_LIST`` SHA-256 over the canonical listing of the *direct* children
                   of a directory: ``"<name>\\t<kind>"`` lines, sorted by name,
                   where kind is ``file`` / ``dir`` / ``symlink``.
``PATH_EXISTS``    SHA-256 of the literal string ``exists``.
``PATH_NOT_EXISTS``SHA-256 of the literal string ``not_exists``.
``FILE_STAT``      SHA-256 over ``"<size>|<is_dir>"`` (the modification time is
                   intentionally excluded, see :func:`fingerprint_stat`).
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

    lines = sorted(f"{name}\t{kind}" for name, kind in entries)
    return "\n".join(lines)


def fingerprint_directory_listing(entries: Iterable[tuple[str, str]]) -> str:
    return sha256_hex(canonical_directory_listing(entries).encode("utf-8"))


def fingerprint_existence(exists: bool) -> str:
    return sha256_hex(b"exists" if exists else b"not_exists")


def fingerprint_stat(size: int, mtime_ns: int, is_dir: bool) -> str:
    """Fingerprint of a stat observation.

    The modification time is deliberately *not* part of the fingerprint: a
    fork of a snapshot keeps the content but the timestamp is irrelevant for
    the (read only) result of a tool.  Content level differences are captured
    by the ``FILE_READ`` dependency of the same path.
    """

    payload = f"{size}|{'1' if is_dir else '0'}"
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
