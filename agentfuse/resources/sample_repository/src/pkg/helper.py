"""Helper utilities of the sample project."""

from __future__ import annotations

import hashlib


def chunk(items: list[str], size: int = 4) -> list[list[str]]:
    """Split ``items`` into chunks of at most ``size`` elements."""

    if size <= 0:
        raise ValueError("size must be positive")
    return [items[index : index + size] for index in range(0, len(items), size)]


def digest_text(text: str) -> str:
    """Return the hex SHA-256 digest of ``text``."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()
