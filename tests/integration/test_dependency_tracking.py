"""The dependency tracking contract of a tool.

The specification requires:

    "无法确认完整依赖的工具必须退化为独立执行。"

The contract is enforced in two independent ways:

1. a tool **declares** whether its dependency tracking is complete
   (``ToolSpec.dependency_tracking``); a tool that does not confirm it is
   never admitted to the share coordinator;
2. the worker **detects** at runtime that a tool read the workspace without
   the controlled ``WorkspaceReader`` (CPython audit hook).  Such an execution
   has an incomplete dependency ledger and its result is not shared either.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from agentfuse.core.state import EventType
from agentfuse.scenarios.common import DEMO_FILES
from agentfuse.tools.base import ToolContext, ToolError
from agentfuse.tools.registry import ToolRegistry, build_default_registry
from agentfuse.workspace.reader import WorkspaceReader, install_access_audit_hook


def test_every_tool_declares_its_dependency_tracking():
    registry = build_default_registry()
    for spec in registry.list_tools():
        assert isinstance(spec.dependency_tracking, bool), spec.tool_name


def test_the_fault_injector_is_not_shareable():
    registry = build_default_registry()
    spec = registry.get("fault_injector")
    assert spec.dependency_tracking is False
    assert spec.shareable is False


def test_a_tool_without_a_complete_ledger_is_not_shareable():
    registry = ToolRegistry()
    spec = build_default_registry().get("ast_analyzer")
    registry.register(
        type(spec)(
            tool_name="copycat",
            tool_version="1.0.0",
            deterministic=True,
            read_only=True,
            dependency_tracking=False,
            resource_estimate=spec.resource_estimate,
            input_schema=spec.input_schema,
            output_schema=spec.output_schema,
            implementation=spec.implementation,
            implementation_reference=spec.implementation_reference,
        )
    )
    assert registry.get("copycat").shareable is False


def test_the_audit_hook_detects_a_bypass(tmp_path):
    """A tool that opens a workspace file directly is detected."""

    root = tmp_path / "tree"
    root.mkdir()
    (root / "secret.py").write_text("VALUE = 1\n", encoding="utf-8")

    violations = install_access_audit_hook(root)
    # a controlled read is fine
    reader = WorkspaceReader(root)
    reader.read_text("secret.py")
    assert violations == []

    # a direct read bypasses the reader and is detected
    (root / "secret.py").read_text(encoding="utf-8")
    assert len(violations) == 1
    assert "secret.py" in violations[0]


async def test_a_bypassing_tool_is_never_shared(app, snapshot, monkeypatch):
    """The result of a bypassing tool must not reach another agent."""

    # the worker builds its own registry - the fixture tool is enabled through
    # the environment so that the worker registers it as well
    monkeypatch.setenv("AGENTFUSE_TEST_TOOLS", "1")
    # the application was created before the environment variable was set
    app.coordinator.registry = build_default_registry()
    # the pre warmed workers were started before the variable was set as well
    app.coordinator.worker_pool_size = 0

    workflow = app.create_workflow("test-bypass")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    events = []
    app.event_bus.subscribe(events.append)

    results = await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="raw_reader",
            arguments={},
            snapshot_id=snapshot.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="raw_reader",
            arguments={},
            snapshot_id=snapshot.snapshot_id,
        ),
    )

    # the bypass is reported
    bypass_events = [
        event for event in events if event.event_type == EventType.DEPENDENCY_BYPASS
    ]
    assert bypass_events, "the bypass must be reported"

    # and the two calls did not share one execution: the follower was detached
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2, "a bypassing tool must not share its result"

    # both calls still got their own correct result
    assert len({result.result_hash for result in results}) == 1
    assert results[0].result["functions"] == results[1].result["functions"]


async def test_a_controlled_tool_is_shared(app, snapshot):
    """The normal case still shares - the audit hook does not interfere."""

    workflow = app.create_workflow("test-controlled")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments=arguments,
            snapshot_id=snapshot.snapshot_id,
        ),
    )
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 1
