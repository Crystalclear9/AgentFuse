"""Beta module: text helpers that build on alpha."""

from __future__ import annotations

from .alpha import average


def normalise(text: str) -> str:
    """Return a lower case, whitespace stripped version of ``text``."""

    return " ".join(text.lower().split())


def summarise(text: str) -> dict[str, float]:
    """Return simple statistics about ``text``."""

    words = normalise(text).split()
    lengths = [float(len(word)) for word in words]
    return {"words": float(len(words)), "avg_word_length": average(lengths)}
