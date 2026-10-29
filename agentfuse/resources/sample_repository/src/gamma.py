"""Gamma module: a small pipeline on top of beta and the helper package."""

from __future__ import annotations

from .beta import summarise
from .pkg.helper import chunk, digest_text


def analyse(text: str) -> dict[str, object]:
    """Return a combined analysis of ``text``."""

    stats = summarise(text)
    return {
        "stats": stats,
        "chunks": len(chunk(text.split())),
        "digest": digest_text(text),
    }
