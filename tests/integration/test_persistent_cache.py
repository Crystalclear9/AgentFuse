"""Integration tests: the durable result cache (requirement 19, P2).

The cache survives a restart of the runtime - that is the whole point of it.
The correctness argument does **not** rely on the age of the entry: every
dependency of the original execution is validated against the snapshot of the
caller, so a workspace that changed in the meantime does not get a stale
result.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from agentfuse.app import AgentFuseApp
from agentfuse.core.state import RuntimeMode
from agentfuse.scenarios.common import DEMO_FILES, create_branch, prepare_base_snapshot
from agentfuse.settings import load_settings


def _run_once(home: Path, snapshot_id: str, *, mutate: bool = False) -> tuple[int, str | None]:
    """Run one tool call and return the number of physical executions.

    The runtime is created, used and closed again - like a restart of the
    process would do.
    """

    # the configuration has to be the same in **every** run - the cache is
    # switched off by default
    settings = load_settings(
        home=str(home), overrides={"runtime": {"persistent_cache_entries": 4}}
    )
    settings.ensure_directories()
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    try:
        workflow = app.create_workflow("persistent-cache")
        workflow.default_snapshot_id = snapshot_id
        workflow.start()
        result = asyncio.run(
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="ast_analyzer",
                arguments={"files": list(DEMO_FILES)},
                snapshot_id=snapshot_id,
            )
        )
        workflow.finish()
        executions = app.repository.list_executions(workflow.workflow_id)
        return len(executions), result.result_hash
    finally:
        app.close_sync()


def _snapshot(app: AgentFuseApp, home: Path) -> str:
    base = prepare_base_snapshot(app)
    return base.snapshot_id


def test_the_cache_is_off_by_default(tmp_path: Path):
    """Without the configuration the durable cache does nothing."""

    settings = load_settings(home=str(tmp_path))
    assert settings.persistent_cache_entries == 0
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    try:
        assert app.coordinator.persistent_cache.enabled is False
    finally:
        app.close_sync()


def test_a_result_survives_a_restart(tmp_path: Path):
    """The second run of the same request does not start a physical execution."""

    home = tmp_path / "home"
    settings = load_settings(
        home=str(home), overrides={"runtime": {"persistent_cache_entries": 4}}
    )
    settings.ensure_directories()
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    try:
        snapshot_id = _snapshot(app, home)
    finally:
        app.close_sync()

    # the first run: the tool really computes
    executions, result_hash = _run_once(home, snapshot_id)
    assert executions == 1, "the first run has to start one execution"
    assert result_hash

    # the second run: a **new** runtime, the same home - the entry of the
    # first run has to be found and the result has to be identical
    executions_again, hash_again = _run_once(home, snapshot_id)
    assert executions_again == 0, (
        "the second run has to be served from the durable cache, "
        f"but it started {executions_again} executions"
    )
    assert hash_again == result_hash, "the cached result has to be the same result"


def test_a_changed_workspace_is_not_served_from_the_cache(tmp_path: Path):
    """A workspace that changed in the meantime does not get a stale result."""

    home = tmp_path / "home"
    settings = load_settings(
        home=str(home), overrides={"runtime": {"persistent_cache_entries": 4}}
    )
    settings.ensure_directories()
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    try:
        base = prepare_base_snapshot(app)
        # the branch changes one of the files the tool reads
        branch = create_branch(
            app,
            base,
            lambda directory: (directory / "src" / "alpha.py").write_text(
                "def changed():\n    return 42\n", encoding="utf-8"
            ),
            "changed",
        )
    finally:
        app.close_sync()

    executions, _hash = _run_once(home, base.snapshot_id)
    assert executions == 1

    # the same tool, the same arguments - but the workspace of the caller is
    # different now.  The dependencies of the entry do not match any more, so
    # the entry has to be dropped and the tool has to run again.
    executions_again, _hash_again = _run_once(home, branch.snapshot_id)
    assert executions_again == 1, (
        "a changed workspace must not be served from the cache, "
        f"but it started {executions_again} executions"
    )
