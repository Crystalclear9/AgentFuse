"""Integration tests: the offline multi-agent demo scenarios."""

from __future__ import annotations

from agentfuse.core.state import EventType, RuntimeMode
from agentfuse.scenarios.definitions import (
    scenario_cancel_failure,
    scenario_different_workspace,
    scenario_multi_agent,
    scenario_shared,
)


async def test_the_shared_scenario_shares_one_execution(app):
    result = await scenario_shared(app, RuntimeMode.ONLINE, {})
    assert result.metrics["logical_calls"] == 2
    assert result.metrics["physical_executions"] == 1
    assert result.metrics["saved_executions"] == 1
    assert result.metrics["detach_count"] == 0
    assert len(result.results) == 2
    assert result.results[0].result_hash == result.results[1].result_hash


async def test_the_different_workspace_scenario_shares(app):
    result = await scenario_different_workspace(app, RuntimeMode.ONLINE, {})
    assert result.metrics["physical_executions"] == 1
    assert result.metrics["detach_count"] == 0
    note = result.notes["snapshot_difference"]
    assert note["modified"] == ["README.md"]


async def test_the_multi_agent_scenario_builds_the_expected_graph(app):
    result = await scenario_multi_agent(app, RuntimeMode.ONLINE, {})
    payload = result.workflow.graph.to_dict()
    kinds = [node["kind"] for node in payload["nodes"]]
    assert kinds.count("agent") == 5  # supervisor + 3 workers + aggregator
    assert kinds.count("invocation") == 5
    assert kinds.count("logical_call") == 4
    assert kinds.count("physical_execution") == 3  # A+B share one execution
    edge_kinds = [edge["kind"] for edge in payload["edges"]]
    assert edge_kinds.count("EXPLICIT_INVOCATION_DEPENDENCY") == 4
    assert "SHARES_EXECUTION_WITH" in edge_kinds


async def test_the_aggregator_only_starts_after_the_workers_finished(app):
    result = await scenario_multi_agent(app, RuntimeMode.ONLINE, {})
    events = app.repository.list_events(result.workflow_id)
    aggregator_call = None
    worker_results = []
    for event in events:
        if event["event_type"] != EventType.TOOL_RESULT_DELIVERED:
            continue
        metadata = event["metadata"] or {}
        if metadata.get("tool_name") == "source_index":
            aggregator_call = event["timestamp"]
        else:
            worker_results.append(event["timestamp"])
    assert aggregator_call is not None
    assert worker_results
    assert aggregator_call >= max(worker_results)


async def test_the_cancel_failure_scenario_covers_every_case(app):
    result = await scenario_cancel_failure(app, RuntimeMode.ONLINE, {})
    notes = result.notes
    assert notes["follower_cancel"]["follower_result"] == {"cancelled": True}
    assert notes["follower_cancel"]["leader_result"]
    assert notes["leader_cancel"]["leader_result"] == {"cancelled": True}
    assert notes["leader_cancel"]["follower_received_result"]
    assert "timed out" in notes["timeout"]["error"]
    assert notes["crash"]["kind"] in ("worker_crash", "internal_error")
    assert "not found" in notes["shared_failure"]["agent_a"]["error"]
    assert "not found" in notes["shared_failure"]["agent_b"]["error"]
