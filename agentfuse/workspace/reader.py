"""Controlled workspace reader.

The reader is the *only* sanctioned way for a shareable tool to touch the
workspace.  Every access is

1. normalised to a workspace relative POSIX path,
2. checked against the workspace boundary (no ``..`` escapes, no symlink
   escapes, no absolute paths),
3. fingerprinted,
4. reported to the runtime **immediately** (before the value is handed back to
   the tool) so that the share coordinator can validate participants online.

This is a *contract* for trusted, controlled tools - it is deliberately not
presented as an operating system level sandbox (see
``docs/KNOWN_LIMITATIONS.md``).
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .fingerprint import (
    directory_listing_from_disk,
    fingerprint_bytes,
    fingerprint_directory_listing,
    fingerprint_existence,
    fingerprint_stat,
)

DependencyReporter = Callable[[str, str, str], None]


class WorkspaceAccessViolation(RuntimeError):
    """A tool read a workspace file without using the controlled reader.

    Such an execution cannot be shared: its dependency ledger would be
    incomplete, therefore the share coordinator has to fall back to an
    independent execution.
    """


#: Thread local flag that marks the controlled accesses of the reader.
_ACCESS = threading.local()


@contextmanager
def controlled_access():
    """Mark a file access as performed by the controlled reader."""

    previous = getattr(_ACCESS, "active", False)
    _ACCESS.active = True
    try:
        yield
    finally:
        _ACCESS.active = previous


def in_controlled_access() -> bool:
    """True while the controlled reader performs a file access."""

    return bool(getattr(_ACCESS, "active", False))


def install_access_audit_hook(snapshot_root: str | os.PathLike[str]) -> list[str]:
    """Detect workspace reads that bypass the controlled reader.

    The hook uses the CPython audit interface (``sys.addaudithook``): every
    ``open`` of a file inside the snapshot that does not happen inside
    :func:`controlled_access` is recorded.  The returned list is filled while
    the tool runs.

    This is what turns the declaration of a tool into an *enforced* contract:
    a tool that reads the workspace directly is detected at runtime and its
    execution is never shared.
    """

    import sys

    root = os.path.realpath(str(snapshot_root)).lower()
    violations: list[str] = []

    def hook(event: str, args: tuple[Any, ...]) -> None:
        if event not in ("open", "os.open"):
            return
        if in_controlled_access():
            return
        target = args[0]
        if not isinstance(target, (str, bytes, os.PathLike)):
            return
        try:
            path = os.path.realpath(os.fsdecode(target)).lower()
        except Exception:  # pragma: no cover - defensive
            return
        if path.startswith(root + os.sep) or path == root:
            violations.append(os.fsdecode(target))

    sys.addaudithook(hook)
    return violations


class WorkspaceBoundaryError(RuntimeError):
    """Raised when a tool tries to escape the controlled workspace root."""


@dataclass(slots=True)
class ObservedDependency:
    dependency_type: str
    relative_path: str
    fingerprint: str
    sequence_number: int
    observed_time: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependency_type": self.dependency_type,
            "relative_path": self.relative_path,
            "fingerprint": self.fingerprint,
            "sequence_number": self.sequence_number,
            "observed_time": self.observed_time,
        }


def normalize_relative_path(path: str | os.PathLike[str]) -> str:
    """Normalise ``path`` to a workspace relative POSIX path.

    Raises :class:`WorkspaceBoundaryError` for absolute paths and for any path
    that tries to leave the workspace via ``..``.
    """

    text = str(path).replace("\\", "/").strip()
    if not text:
        return "."
    if text.startswith("/") or text.startswith("//"):
        raise WorkspaceBoundaryError(f"absolute paths are not allowed: {path!r}")
    if len(text) > 1 and text[1] == ":":
        raise WorkspaceBoundaryError(f"absolute paths are not allowed: {path!r}")

    parts: list[str] = []
    for part in text.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise WorkspaceBoundaryError(f"path escapes the workspace root: {path!r}")
        parts.append(part)
    return "/".join(parts) if parts else "."


class WorkspaceReader:
    """Read-only view over one immutable workspace snapshot."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        reporter: DependencyReporter | None = None,
        snapshot_id: str | None = None,
        strict_boundary: bool = True,
    ) -> None:
        self.root = Path(root)
        self._root_real = os.path.realpath(self.root)
        self._reporter = reporter
        self.snapshot_id = snapshot_id
        self.strict_boundary = strict_boundary
        self._sequence = 0
        self._seen: set[tuple[str, str]] = set()
        self.observed: list[ObservedDependency] = []

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _resolve(self, path: str | os.PathLike[str]) -> tuple[str, Path]:
        relative = normalize_relative_path(path)
        target = self.root / relative if relative != "." else self.root
        if self.strict_boundary:
            real = os.path.realpath(target)
            if real != self._root_real and not real.startswith(self._root_real + os.sep):
                raise WorkspaceBoundaryError(
                    f"path {relative!r} resolves outside of the workspace root"
                )
        return relative, target

    def _report(self, dependency_type: str, relative_path: str, fingerprint: str) -> None:
        key = (dependency_type, relative_path)
        if key in self._seen:
            return
        self._seen.add(key)
        self._sequence += 1
        observed = ObservedDependency(
            dependency_type=dependency_type,
            relative_path=relative_path,
            fingerprint=fingerprint,
            sequence_number=self._sequence,
            observed_time=time.time(),
        )
        self.observed.append(observed)
        if self._reporter is not None:
            self._reporter(dependency_type, relative_path, fingerprint)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def read_bytes(self, path: str | os.PathLike[str]) -> bytes:
        relative, target = self._resolve(path)
        with controlled_access():
            if not target.is_file():
                self._report("PATH_NOT_EXISTS", relative, fingerprint_existence(False))
                raise FileNotFoundError(f"file not found in workspace: {relative}")
            data = target.read_bytes()
        self._report("FILE_READ", relative, fingerprint_bytes(data))
        return data

    def read_text(self, path: str | os.PathLike[str], encoding: str = "utf-8") -> str:
        return self.read_bytes(path).decode(encoding, errors="replace")

    def read_json(self, path: str | os.PathLike[str]) -> Any:
        import json

        return json.loads(self.read_text(path))

    def list_dir(self, path: str | os.PathLike[str] = ".") -> list[dict[str, Any]]:
        relative, target = self._resolve(path)
        with controlled_access():
            if not target.is_dir():
                self._report("PATH_NOT_EXISTS", relative, fingerprint_existence(False))
                raise NotADirectoryError(f"directory not found in workspace: {relative}")
            entries = directory_listing_from_disk(target)
        self._report("DIRECTORY_LIST", relative, fingerprint_directory_listing(entries))
        return [
            {"name": name, "kind": kind, "path": f"{relative}/{name}" if relative != "." else name}
            for name, kind in sorted(entries)
        ]

    def exists(self, path: str | os.PathLike[str]) -> bool:
        relative, target = self._resolve(path)
        with controlled_access():
            present = target.exists()
        self._report(
            "PATH_EXISTS" if present else "PATH_NOT_EXISTS",
            relative,
            fingerprint_existence(present),
        )
        return present

    def stat(self, path: str | os.PathLike[str]) -> dict[str, Any]:
        relative, target = self._resolve(path)
        if not target.exists():
            self._report("PATH_NOT_EXISTS", relative, fingerprint_existence(False))
            raise FileNotFoundError(f"path not found in workspace: {relative}")
        info = target.stat()
        self._report(
            "FILE_STAT",
            relative,
            fingerprint_stat(info.st_size, info.st_mtime_ns, target.is_dir()),
        )
        return {
            "path": relative,
            "size": info.st_size,
            "mtime_ns": info.st_mtime_ns,
            "is_dir": target.is_dir(),
        }

    def iter_files(self, root: str | os.PathLike[str] = ".") -> Iterable[str]:
        """Recursively list regular files below ``root`` (reporting every list)."""

        stack = [normalize_relative_path(root)]
        seen_dirs: set[str] = set()
        while stack:
            current = stack.pop()
            if current in seen_dirs:
                continue
            seen_dirs.add(current)
            try:
                entries = self.list_dir(current)
            except (NotADirectoryError, FileNotFoundError):
                continue
            for entry in entries:
                if entry["kind"] == "dir":
                    stack.append(entry["path"])
                elif entry["kind"] == "file":
                    yield entry["path"]
