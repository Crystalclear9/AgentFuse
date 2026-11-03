"""Integration tests: the preemptive scheduling.

Requirement section 19 lists the preemptive scheduling as a P2 capability.
The strategy is **off** by default - it changes the order of the executions,
so it has to be switched on deliberately.

The correctness argument: a preempted execution is terminated like a timeout,
so its logical call is retried once (requirement 10.1).  The order of the
executions changes, their **results** do not.
"""

from __future__ import annotations

import asyncio

import pytest

from agentfuse.core.state import RuntimeMode
from agentfuse.scenarios.common import DEMO_FILES


async def test_the_preemptive_strategy_admits_the_important_one_first():
    """The execution with the higher priority is admitted first."""

    from agentfuse.runtime.strategy import PendingRequest, build_strategy

    strategy = build_strategy("preemptive")
    low = PendingRequest(execution_id="low", priority=1)
    high = PendingRequest(execution_id="high", priority=5)
    decision = strategy.select(
        pending=[low, high],
        running=0,
        max_workers=1,
        reserved_mb=0.0,
        observed_rss_mb=0.0,
        budget_mb=1024,
        rss_limit_mb=0,
    )
    assert decision.allowed
    assert decision.index == 1, "the request with the higher priority comes first"


async def test_the_preemption_keeps_the_results_correct(app, snapshot, monkeypatch):
    """A preempted call is retried and still delivers the right result.

    The low priority tool is slow, the high priority one is fast.  With the
    preemptive strategy the slow execution is terminated for the fast one -
    the slow call is retried and both calls get their correct result.
    """

    # the digest tool gets a **low** priority, the AST analyzer keeps the
    # default (0).  The priority is a property of the tool - the worker does
    # not need it, only the coordinator does.
    digest = app.coordinator.registry.get("content_digest")
    digest.priority = -1
    ast = app.coordinator.registry.get("ast_analyzer")
    ast.priority = 5
    from agentfuse.runtime.strategy import build_strategy

    app.scheduler.strategy = build_strategy("preemptive")
    # with one worker the fast execution has to **wait** for the slow one -
    # only then is the preemption possible
    app.scheduler.max_workers = 1

    workflow = app.create_workflow("test-preemption")
    workflow.default_snapshot_id = snapshot.snapshot_id
    workflow.start()

    # the slow tool with many rounds, the fast one with few
    slow = asyncio.create_task(
        app.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="content_digest",
            arguments={"files": list(DEMO_FILES), "rounds": 40000},
            snapshot_id=snapshot.snapshot_id,
        )
    )
    await asyncio.sleep(0.3)
    fast = await app.coordinator.submit_tool_call(
        workflow_id=workflow.workflow_id,
        tool_name="ast_analyzer",
        arguments={"files": list(DEMO_FILES)},
        snapshot_id=snapshot.snapshot_id,
    )
    slow_result = await asyncio.wait_for(slow, timeout=120)
    workflow.finish()

    assert fast.result_hash, "the fast call has to deliver a result"
    assert slow_result.result_hash, "the preempted call has to deliver a result"

    calls = app.repository.list_logical_calls(workflow.workflow_id)
    assert len(calls) == 2
    for call in calls:
        assert call["status"] == "COMPLETED", calls
        assert call["physical_execution_id"], calls

    # the preemption has really happened: the slow call was terminated and
    # **retried**, so there is more than one physical execution for it
    executions = app.repository.list_executions(workflow.workflow_id)
    assert len(executions) >= 3, (
        "the preempted call has to be retried - the executions are: "
        + str([(row["tool_name"], row["execution_status"]) for row in executions])
    )
    retried = [row for row in calls if row["retry_count"]]
    assert retried, "the preempted call has to carry a retry" 
