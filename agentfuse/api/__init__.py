"""Local HTTP API of AgentFuse."""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
