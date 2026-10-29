"""Tool 1 - SourceFileIndex.

Walks a source directory through the controlled reader and reports a real
inventory of the source files it found.  Every directory listing produces a
``DIRECTORY_LIST`` dependency, every inspected file a ``FILE_STAT`` dependency.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .base import ToolContext

DEFAULT_EXTENSIONS = (".py",)


def source_file_index(context: ToolContext) -> dict[str, Any]:
    root = context.arg("root", "src")
    extensions = tuple(context.arg("extensions", list(DEFAULT_EXTENSIONS)))
    max_files = int(context.arg("max_files", 2000))

    files: list[dict[str, Any]] = []
    extension_stats: Counter[str] = Counter()
    total_bytes = 0
    smallest: int | None = None
    largest: int | None = None

    for relative in sorted(context.reader.iter_files(root)):
        if extensions and not relative.endswith(extensions):
            continue
        if len(files) >= max_files:
            break
        info = context.reader.stat(relative)
        size = int(info["size"])
        extension = relative.rsplit(".", 1)[-1] if "." in relative.rsplit("/", 1)[-1] else "<none>"
        extension_stats[extension] += 1
        total_bytes += size
        smallest = size if smallest is None else min(smallest, size)
        largest = size if largest is None else max(largest, size)
        files.append({"path": relative, "size": size})

    return {
        "root": root,
        "file_count": len(files),
        "files": files,
        "extension_stats": dict(sorted(extension_stats.items())),
        "size_stats": {
            "total_bytes": total_bytes,
            "min_bytes": smallest or 0,
            "max_bytes": largest or 0,
            "avg_bytes": round(total_bytes / len(files), 2) if files else 0.0,
        },
    }
