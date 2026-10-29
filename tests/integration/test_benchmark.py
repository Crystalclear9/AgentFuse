"""Integration tests: the benchmark comparison."""

from __future__ import annotations

from agentfuse.benchmarks.runner import check_correctness, collect_call_hashes, compare
from agentfuse.core.state import RuntimeMode


async def test_the_comparison_reports_a_real_reduction(app):
    report = await compare(app, "shared", repeats=1, modes=("baseline", "online"))

    baseline = report["aggregated"]["baseline"]
    online = report["aggregated"]["online"]
    assert baseline["physical_executions"]["mean"] == 2
    assert online["physical_executions"]["mean"] == 1
    assert online["saved_executions"]["mean"] == 1
    assert report["speedup"]["online"] > 0


async def test_the_shared_results_are_identical_to_the_baseline(app):
    report = await compare(app, "shared", repeats=1, modes=("baseline", "online"))
    correctness = report["correctness"]["online"]
    assert correctness["mismatched"] == 0
    assert correctness["identical"] == 1


async def test_the_divergent_scenario_keeps_both_results_correct(app):
    """The detached follower must still return the result of its own branch."""

    report = await compare(app, "divergent", repeats=1, modes=("baseline", "online"))
    correctness = report["correctness"]["online"]
    assert correctness["mismatched"] == 0
    assert report["aggregated"]["online"]["detach_count"]["mean"] == 1
    assert report["aggregated"]["baseline"]["detach_count"]["mean"] == 0


async def test_the_deferred_mode_detects_the_conflict_later(app):
    report = await compare(
        app, "divergent", repeats=1, modes=("online", "deferred")
    )
    online = report["aggregated"]["online"]
    deferred = report["aggregated"]["deferred"]
    # the deferred mode validates the whole ledger at the end -> fewer checks
    assert deferred["validation_count"]["mean"] <= online["validation_count"]["mean"]
    assert deferred["detach_count"]["mean"] == 1


async def test_the_call_hashes_are_stable_between_runs(app):
    from agentfuse.scenarios.definitions import scenario_shared

    first = await scenario_shared(app, RuntimeMode.ONLINE, {})
    second = await scenario_shared(app, RuntimeMode.ONLINE, {})
    first_hashes = collect_call_hashes(app.repository, first.workflow_id)
    second_hashes = collect_call_hashes(app.repository, second.workflow_id)
    assert first_hashes == second_hashes


async def test_the_correctness_check_detects_a_difference(app):
    from agentfuse.scenarios.definitions import scenario_shared

    first = await scenario_shared(app, RuntimeMode.ONLINE, {})
    second = await scenario_shared(app, RuntimeMode.ONLINE, {})
    outcome_a = type("Outcome", (), {"call_hashes": collect_call_hashes(app.repository, first.workflow_id)})()
    outcome_b = type("Outcome", (), {"call_hashes": [("other", "different")]})()
    report = check_correctness([outcome_a], [outcome_b])
    assert report["mismatched"] == 1
