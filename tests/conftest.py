"""Shared pytest fixtures.

Every test runs against an isolated AgentFuse home directory so that the
tests never touch the real database of the installation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentfuse.app import AgentFuseApp  # noqa: E402
from agentfuse.core.state import RuntimeMode  # noqa: E402
from agentfuse.settings import RuntimeSettings, load_settings  # noqa: E402


@pytest.fixture()
def settings(tmp_path: Path) -> RuntimeSettings:
    """An isolated runtime configuration."""

    return load_settings(home=tmp_path / "agentfuse-home")


@pytest.fixture()
async def app(settings: RuntimeSettings):
    """A running AgentFuse application."""

    application = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    try:
        yield application
    finally:
        await application.close()


@pytest.fixture()
def sample_repository() -> Path:
    from agentfuse.resources import sample_repository_path

    return sample_repository_path()


@pytest.fixture()
def snapshot(app: AgentFuseApp):
    """An immutable snapshot of the packaged sample repository."""

    from agentfuse.resources import sample_repository_path

    return app.snapshots.create_workspace(source_dir=sample_repository_path())
