"""Tool 4 - ContentDigestAnalyzer.

Reads a set of files, hashes them and performs a configurable amount of
*real* CPU work (repeated SHA-256 rounds over a content derived buffer) so
that the benefit of shared execution can be measured with a genuine workload
instead of a simulated ``sleep()``.

The CPU work is interleaved with the reads: every file is read, hashed and
then crunched before the next file is read.  This ordering is what makes the
online dependency validation observable - a follower can still join while the
leader is working on the earlier files.

The result never depends on the absolute location of the workspace: only the
workspace relative paths and the file contents are hashed.
"""

from __future__ import annotations

import hashlib
from typing import Any

from .base import ToolContext, ToolError

_BUFFER_SIZE = 32 * 1024


def _work_buffer(digest: bytes) -> bytes:
    """Derive a deterministic work buffer from the file digest."""

    out = bytearray()
    seed = digest
    while len(out) < _BUFFER_SIZE:
        seed = hashlib.sha256(seed).digest()
        out.extend(seed)
    return bytes(out[:_BUFFER_SIZE])


def content_digest_analyzer(context: ToolContext) -> dict[str, Any]:
    files = context.arg_list("files")
    if not files:
        raise ToolError("content_digest_analyzer requires a non empty 'files' list")
    rounds = int(context.arg("rounds", 200))
    if rounds < 0:
        raise ToolError("rounds must be >= 0")

    per_file: list[dict[str, Any]] = []
    total_rounds = 0
    total_bytes = 0

    for relative in sorted(files):
        data = context.reader.read_bytes(relative)
        digest = hashlib.sha256(data).digest()
        total_bytes += len(data)

        buffer = _work_buffer(digest)
        state = digest
        for _ in range(rounds):
            state = hashlib.sha256(state + buffer).digest()

        total_rounds += rounds
        per_file.append(
            {
                "path": relative,
                "sha256": digest.hex(),
                "size": len(data),
                "rounds": rounds,
                "work_digest": state.hex(),
            }
        )

    combined = hashlib.sha256()
    for entry in per_file:
        combined.update(entry["path"].encode("utf-8"))
        combined.update(b"\0")
        combined.update(entry["sha256"].encode("utf-8"))
        combined.update(b"\n")

    return {
        "file_count": len(per_file),
        "files": per_file,
        "combined_digest": combined.hexdigest(),
        "rounds_per_file": rounds,
        "total_rounds": total_rounds,
        "total_bytes": total_bytes,
    }
