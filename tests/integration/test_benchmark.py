"""Integration tests: the benchmark comparison."""

from __future__ import annotations

from agentfuse.benchmarks.runner import (
    DEFAULT_SEED,
    RSS_TOLERANCE_BYTES,
    check_correctness,
    collect_call_hashes,
    compare,
)
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


async def test_the_benchmark_uses_a_fixed_seed(app):
    """The seed must be effective - every mode and every repeat uses it."""

    report = await compare(app, "shared", repeats=2, modes=("baseline", "online"))
    assert report["seed"] == DEFAULT_SEED
    # the seed reached the workflow records
    workflow = app.repository.get_workflow(report["runs"]["online"][0]["workflow_id"])
    assert workflow["random_seed"] == DEFAULT_SEED


async def test_the_same_seed_produces_the_same_results(app):
    """Two repetitions with the same seed have to decide the same way."""

    report = await compare(app, "shared", repeats=2, modes=("online",))
    reproducibility = report["reproducibility"]
    assert reproducibility["reproducible"] is True
    assert reproducibility["modes"]["online"]["identical"] is True


async def test_the_repeated_runs_do_not_grow_the_resources(app):
    """The repetitions must not accumulate workers or runtime memory."""

    report = await compare(app, "shared", repeats=2, modes=("online",))
    stability = report["resource_stability"]
    assert stability["samples"] == 2
    assert stability["workers_grew"] is False
    assert stability["stable"] is True
    assert stability["runtime_rss_growth"] <= RSS_TOLERANCE_BYTES


async def test_the_detachment_benefit_reaches_the_aggregation(app):
    """The value of the online check has to be visible in the aggregation."""

    report = await compare(app, "divergent", repeats=1, modes=("online",))
    aggregated = report["aggregated"]["online"]
    assert "detach_lead_ms_max" in aggregated
    assert "online_detach_before_finish_rate" in aggregated
    assert aggregated["detach_lead_ms_max"]["mean"] > 0
    assert aggregated["online_detach_before_finish_rate"]["mean"] == 1.0
