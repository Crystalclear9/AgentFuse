"""Tool 6 - DuplicateBlockFinder.

Finds duplicated code blocks inside a set of source files.  The tool
normalises the source (comments and whitespace are removed) and compares
windows of consecutive lines, which makes the result independent of the
formatting and of the physical location of the files.

The tool is deterministic and read only; it is used to create realistic
workloads whose results can be shared between agents.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .base import ToolContext, ToolError

_COMMENT = re.compile(r"#.*$")


def _normalise(line: str) -> str:
    """Strip comments and whitespace so that formatting does not matter."""

    without_comment = _COMMENT.sub("", line)
    return without_comment.strip()


def duplicate_block_finder(context: ToolContext) -> dict[str, Any]:
    files = context.arg_list("files")
    if not files:
        raise ToolError("duplicate_block_finder requires a non empty 'files' list")
    block_size = int(context.arg("block_size", 5))
    if block_size < 2:
        raise ToolError("block_size must be >= 2")

    occurrences: dict[str, list[dict[str, Any]]] = {}
    file_stats: list[dict[str, Any]] = []
    missing: list[str] = []

    for relative in sorted(files):
        # The existence check is a dependency of its own: a file that is
        # present in one workspace and missing in another must be detected as
        # a difference, therefore it is reported through the controlled reader.
        if not context.reader.exists(relative):
            missing.append(relative)
            continue
        source = context.reader.read_text(relative)
        lines = source.splitlines()
        normalised = [_normalise(line) for line in lines]
        code_lines = [
            index for index, value in enumerate(normalised) if value and not value.startswith(('"""', "'''"))
        ]
        window: list[int] = []
        for index, value in enumerate(normalised):
            if value:
                window.append(index)
                if len(window) > block_size:
                    window.pop(0)
                if len(window) == block_size:
                    block = "\n".join(normalised[position] for position in window)
                    digest = hashlib.sha256(block.encode("utf-8")).hexdigest()
                    occurrences.setdefault(digest, []).append(
                        {
                            "path": relative,
                            "start_line": window[0] + 1,
                            "end_line": window[-1] + 1,
                        }
                    )
        file_stats.append(
            {
                "path": relative,
                "total_lines": len(lines),
                "code_lines": len(code_lines),
            }
        )

    duplicates = []
    for digest, positions in occurrences.items():
        if len(positions) < 2:
            continue
        duplicates.append(
            {
                "block_hash": digest,
                "occurrences": sorted(
                    positions, key=lambda item: (item["path"], item["start_line"])
                ),
                "occurrences_count": len(positions),
            }
        )
    duplicates.sort(key=lambda item: (-item["occurrences_count"], item["block_hash"]))

    duplicated_lines = sum(
        item["occurrences_count"] * block_size for item in duplicates
    )

    return {
        "file_count": len(files),
        "analyzed_file_count": len(file_stats),
        "missing_files": missing,
        "block_size": block_size,
        "files": file_stats,
        "duplicates": duplicates,
        "duplicate_group_count": len(duplicates),
        "duplicated_line_count": duplicated_lines,
        "stats": {
            "total_code_lines": sum(item["code_lines"] for item in file_stats),
            "duplicate_ratio": (
                round(duplicated_lines / sum(item["code_lines"] for item in file_stats), 4)
                if sum(item["code_lines"] for item in file_stats)
                else 0.0
            ),
        },
    }
