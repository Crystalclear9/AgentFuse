"""Integration tests: shared execution correctness."""

from __future__ import annotations

import asyncio
import json

import pytest

from agentfuse.adapters.scripted_adapter import ScriptedAdapter, ScriptedAgent, ToolStep
from agentfuse.core.state import RuntimeMode
from agentfuse.scenarios.common import DEMO_FILES
from agentfuse.tools.registry import build_default_registry


async def test_two_equivalent_calls_share_a_single_physical_execution(app, snapshot):
    workflow = app.create_workflow("test-shared")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    results = await asyncio.gather(
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

    calls = app.repository.list_logical_calls(workflow.workflow_id)
    executions = app.repository.list_executions(workflow.workflow_id)
    groups = app.repository.list_share_groups(workflow.workflow_id)

    assert len(calls) == 2
    assert len(executions) == 1
    assert len(groups) == 1
    assert results[0].result_hash == results[1].result_hash
    assert results[0].result == results[1].result
    # both logical calls are bound to the very same physical execution
    assert {call["physical_execution_id"] for call in calls} == {executions[0]["execution_id"]}
    # and both logical calls stay separate identities
    assert len({call["logical_call_id"] for call in calls}) == 2


async def test_the_baseline_mode_never_shares(app, snapshot):
    workflow = app.create_workflow("test-baseline", mode=RuntimeMode.BASELINE)
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
    assert len(executions) == 2
    assert app.repository.list_share_groups(workflow.workflow_id) == []


async def test_different_arguments_are_not_shared(app, snapshot):
    workflow = app.create_workflow("test-different-args")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments={"files": ["src/alpha.py"]},
            snapshot_id=snapshot.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments={"files": ["src/beta.py"]},
            snapshot_id=snapshot.snapshot_id,
        ),
    )
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2


async def test_different_tool_versions_are_not_shared(app, snapshot):
    workflow = app.create_workflow("test-versions")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="ast_analyzer",
            arguments={"files": ["src/alpha.py"]},
            snapshot_id=snapshot.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="dependency_analyzer",
            arguments={"files": ["src/alpha.py"]},
            snapshot_id=snapshot.snapshot_id,
        ),
    )
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2


async def test_two_versions_of_the_same_tool_are_not_shared(app, snapshot, monkeypatch):
    """§17.2 - a different tool *version* cannot share an execution.

    The candidate key contains the tool version, therefore two versions of
    the same tool never end up in the same share group - even with identical
    arguments and identical dependencies.
    """

    # the worker builds its own registry - the fixture tool with its second
    # version is enabled through the environment
    monkeypatch.setenv("AGENTFUSE_TEST_TOOLS", "1")
    app.coordinator.registry = build_default_registry()
    app.coordinator.worker_pool_size = 0

    workflow = app.create_workflow("test-tool-version")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    await asyncio.gather(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="raw_reader",
            tool_version="1.0.0",
            arguments={},
            snapshot_id=snapshot.snapshot_id,
        ),
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="raw_reader",
            tool_version="2.0.0",
            arguments={},
            snapshot_id=snapshot.snapshot_id,
        ),
    )

    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2, "two versions must not share one execution"
    assert {execution["tool_version"] for execution in executions} == {"1.0.0", "2.0.0"}
    groups = app.repository.list_share_groups(workflow.workflow_id)
    assert len(groups) == 2


async def test_a_non_shareable_tool_is_never_shared(app, snapshot):
    workflow = app.create_workflow("test-fault")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    with pytest.raises(Exception):
        await asyncio.gather(
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="fault_injector",
                arguments={"mode": "raise"},
                snapshot_id=snapshot.snapshot_id,
            ),
            app.coordinator.submit_tool_call(
                workflow_id=workflow.workflow_id,
                tool_name="fault_injector",
                arguments={"mode": "raise"},
                snapshot_id=snapshot.snapshot_id,
            ),
        )
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 2
    assert app.repository.list_share_groups(workflow.workflow_id) == []


async def test_an_unrelated_file_change_does_not_prevent_sharing(app, snapshot):
    from agentfuse.scenarios.common import create_branch, mutate_readme

    branch = create_branch(app, snapshot, mutate_readme, "branch-a")
    workflow = app.create_workflow("test-unrelated-change")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    arguments = {"files": list(DEMO_FILES)}
    results = await asyncio.gather(
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
            snapshot_id=branch.snapshot_id,
        ),
    )
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) == 1
    assert results[0].result_hash == results[1].result_hash


async def test_two_scripted_agents_produce_a_real_execution_graph(app, snapshot):
    workflow = app.create_workflow("test-graph")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()
    adapter = ScriptedAdapter(workflow)

    agent_a = ScriptedAgent("agent-a", [ToolStep("ast_analyzer", {"files": list(DEMO_FILES)})])
    agent_b = ScriptedAgent("agent-b", [ToolStep("ast_analyzer", {"files": list(DEMO_FILES)})])
    await asyncio.gather(agent_a.run(workflow, adapter), agent_b.run(workflow, adapter))
    workflow.finish()

    payload = workflow.graph.to_dict()
    kinds = [node["kind"] for node in payload["nodes"]]
    assert kinds.count("agent") == 2
    assert kinds.count("invocation") == 2
    assert kinds.count("logical_call") == 2
    assert kinds.count("physical_execution") == 1
    edge_kinds = {edge["kind"] for edge in payload["edges"]}
    assert "CALLS_TOOL" in edge_kinds
    assert "SHARES_EXECUTION_WITH" in edge_kinds


async def test_the_two_agents_keep_their_own_identity(app, snapshot):
    """§17.1 - the concurrent agents do not get mixed up.

    The node counts alone do not prove the identity: the agent, its
    invocation and its logical call have to be connected to each other.
    """

    workflow = app.create_workflow("test-identity")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()
    adapter = ScriptedAdapter(workflow)

    agent_a = ScriptedAgent("agent-a", [ToolStep("ast_analyzer", {"files": list(DEMO_FILES)})])
    agent_b = ScriptedAgent("agent-b", [ToolStep("ast_analyzer", {"files": list(DEMO_FILES)})])
    await asyncio.gather(agent_a.run(workflow, adapter), agent_b.run(workflow, adapter))
    workflow.finish()

    agents = app.repository.list_agents(workflow.workflow_id)
    invocations = app.repository.list_invocations(workflow.workflow_id)
    calls = app.repository.list_logical_calls(workflow.workflow_id)
    participants = app.repository.list_participants(workflow.workflow_id)

    assert len(agents) == 2
    assert len(invocations) == 2
    assert len(calls) == 2
    # every agent has exactly one invocation and every invocation one call
    for invocation in invocations:
        owned = [call for call in calls if call["invocation_id"] == invocation["invocation_id"]]
        assert len(owned) == 1
        assert owned[0]["agent_id"] == invocation["agent_id"]

    # the participants keep the identity of the agent that issued the call
    for participant in participants:
        call = next(
            item
            for item in calls
            if item["logical_call_id"] == participant["logical_call_id"]
        )
        assert participant["agent_id"] == call["agent_id"]
        assert participant["invocation_id"] == call["invocation_id"]

    # the graph keeps the two invocations apart: each call is reached from
    # its own invocation
    edges = {
        (edge["source"], edge["target"]) for edge in workflow.graph.to_dict()["edges"]
    }
    for call in calls:
        assert (
            f"invocation:{call['invocation_id']}",
            f"call:{call['logical_call_id']}",
        ) in edges


async def test_two_invocations_of_the_same_agent_stay_distinct(app, snapshot):
    """§17.1 - an agent may be activated several times.

    The agent entity, its invocations and its tool calls are three different
    things and must not be mixed up.
    """

    workflow = app.create_workflow("test-same-agent")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()
    adapter = ScriptedAdapter(workflow)

    agent = ScriptedAgent("agent-x", [ToolStep("ast_analyzer", {"files": list(DEMO_FILES)})])
    await asyncio.gather(agent.run(workflow, adapter), agent.run(workflow, adapter))
    workflow.finish()

    agents = app.repository.list_agents(workflow.workflow_id)
    invocations = app.repository.list_invocations(workflow.workflow_id)
    calls = app.repository.list_logical_calls(workflow.workflow_id)

    assert len(agents) == 1, "the same agent must not be bound twice"
    assert len(invocations) == 2, "every activation is its own invocation"
    assert len({item["invocation_id"] for item in invocations}) == 2
    assert {item["agent_id"] for item in invocations} == {agents[0]["agent_id"]}
    assert len(calls) == 2
    assert len({item["invocation_id"] for item in calls}) == 2
    assert {item["invocation_id"] for item in calls} == {
        item["invocation_id"] for item in invocations
    }

    # the graph keeps the two invocations apart
    payload = workflow.graph.to_dict()
    kinds = [node["kind"] for node in payload["nodes"]]
    assert kinds.count("agent") == 1
    assert kinds.count("invocation") == 2
    assert kinds.count("logical_call") == 2


async def test_the_share_group_records_its_participants(app, snapshot):
    """The share group stores the logical calls that belong to it."""

    workflow = app.create_workflow("test-participant-calls")
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

    groups = app.repository.list_share_groups(workflow.workflow_id)
    assert len(groups) == 1
    calls = {call["logical_call_id"] for call in app.repository.list_logical_calls(workflow.workflow_id)}
    assert set(json.loads(groups[0]["participant_calls"])) == calls
    assert groups[0]["participant_count"] == len(calls)
