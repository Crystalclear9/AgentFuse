"""Packaged resources (the offline sample repository)."""

from __future__ import annotations

from pathlib import Path

RESOURCE_ROOT = Path(__file__).resolve().parent
SAMPLE_REPOSITORY = RESOURCE_ROOT / "sample_repository"


def sample_repository_path() -> Path:
    """Return the path of the packaged sample repository."""

    return SAMPLE_REPOSITORY
