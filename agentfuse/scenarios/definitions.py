"""The five offline demo scenarios.

Every scenario runs completely offline, uses only the controlled tools and
produces real events, real physical executions and real results.  The graph of
a scenario is *never* read from a preset DAG - it is reconstructed from the
events that the runtime emitted.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from ..adapters.scripted_adapter import CallableStep, ScriptedAdapter, ScriptedAgent, ToolStep
from ..core.state import EventType, RuntimeMode
from ..monitoring.metrics import collect_workflow_metrics
from .common import (
    DEFAULT_SEED,
    DEMO_FILES,
    DIVERGENT_ROUNDS,
    SHARED_HEAVY_ROUNDS,
    create_branch,
    mutate_readme,
    mutate_variant_source,
    prepare_base_snapshot,
    wait_for_any_event,
    wait_for_event,
    wait_for_first_dependency,
    wait_for_running_execution,
)


@dataclass
class ScenarioResult:
    """Everything a caller needs to inspect one scenario run."""

    scenario: str
    mode: str
    workflow_id: str
    makespan: float
    workflow: Any
    metrics: dict[str, Any] = field(default_factory=dict)
    snapshots: dict[str, str] = field(default_factory=dict)
    results: list[Any] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "mode": self.mode,
            "workflow_id": self.workflow_id,
            "makespan": self.makespan,
            "metrics": self.metrics,
            "snapshots": self.snapshots,
            "notes": self.notes,
        }


# ----------------------------------------------------------------------
# scenario 1 - full sharing
# ----------------------------------------------------------------------
async def scenario_shared(app: Any, mode: RuntimeMode, options: dict[str, Any]) -> ScenarioResult:
    """Two agents, one snapshot, one identical request -> one physical execution."""

    workflow = app.create_workflow(
        "shared", mode=mode, label="scenario: full sharing", random_seed=options.get("seed", DEFAULT_SEED)
    )
    base = prepare_base_snapshot(app)
    workflow.default_snapshot_id = base.snapshot_id
    workflow.start()

    adapter = ScriptedAdapter(workflow)
    files = list(options.get("files", DEMO_FILES))
    agent_a = ScriptedAgent("analysis-agent-a", [ToolStep("ast_analyzer", {"files": files})])

    async def follower_step(context: Any) -> Any:
        # Deterministic rendezvous: the follower only submits its call after
        # the leader's physical execution is really running.  Without the
        # barrier the scenario would depend on the scheduling of the two
        # agents - a late follower would start a second execution.
        await wait_for_running_execution(
            app.event_bus, workflow.workflow_id, tool_name="ast_analyzer"
        )
        return await context.call_tool("ast_analyzer", {"files": files})

    agent_b = ScriptedAgent("analysis-agent-b", [CallableStep(follower_step)])

    results = await asyncio.gather(
        agent_a.run(workflow, adapter),
        agent_b.run(workflow, adapter),
    )
    workflow.finish()

    return ScenarioResult(
        scenario="shared",
        mode=str(mode),
        workflow_id=workflow.workflow_id,
        makespan=workflow.makespan,
        workflow=workflow,
        metrics=collect_workflow_metrics(app.repository, workflow.workflow_id, app.registry),
        snapshots={"base": base.snapshot_id},
        results=list(results),
        notes={
            "expectation": "2 logical calls, 1 physical execution, identical results",
        },
    )


# ----------------------------------------------------------------------
# scenario 1b - full sharing with a CPU heavy workload
# ----------------------------------------------------------------------
async def scenario_shared_heavy(
    app: Any, mode: RuntimeMode, options: dict[str, Any]
) -> ScenarioResult:
    """Two agents share one *CPU heavy* execution.

    The light ``shared`` scenario is dominated by the worker start up cost.
    This variant uses a tool with a real CPU workload, therefore the benefit
    of the shared execution becomes visible in the makespan.
    """

    workflow = app.create_workflow(
        "shared-heavy",
        mode=mode,
        label="scenario: full sharing (cpu heavy)",
        random_seed=options.get("seed", DEFAULT_SEED),
    )
    base = prepare_base_snapshot(app)
    workflow.default_snapshot_id = base.snapshot_id
    workflow.start()

    adapter = ScriptedAdapter(workflow)
    files = list(options.get("files", DEMO_FILES))
    rounds = int(options.get("rounds", SHARED_HEAVY_ROUNDS))
    arguments = {"files": files, "rounds": rounds}

    agent_a = ScriptedAgent("analysis-agent-a", [ToolStep("content_digest", arguments)])
    agent_b = ScriptedAgent("analysis-agent-b", [ToolStep("content_digest", arguments)])

    results = await asyncio.gather(
        agent_a.run(workflow, adapter),
        agent_b.run(workflow, adapter),
    )
    workflow.finish()

    return ScenarioResult(
        scenario="shared-heavy",
        mode=str(mode),
        workflow_id=workflow.workflow_id,
        makespan=workflow.makespan,
        workflow=workflow,
        metrics=collect_workflow_metrics(app.repository, workflow.workflow_id, app.registry),
        snapshots={"base": base.snapshot_id},
        results=list(results),
        notes={
            "expectation": "2 logical calls, 1 physical execution, identical results",
            "rounds": rounds,
        },
    )


# ----------------------------------------------------------------------
# scenario 2 - different workspace, shareable result
# ----------------------------------------------------------------------
async def scenario_different_workspace(
    app: Any, mode: RuntimeMode, options: dict[str, Any]
) -> ScenarioResult:
    """Two different snapshots, but the analysed dependencies are identical."""

    workflow = app.create_workflow(
        "different-workspace",
        mode=mode,
        label="scenario: different workspace, identical dependencies",
        random_seed=options.get("seed", DEFAULT_SEED),
    )
    base = prepare_base_snapshot(app)
    branch_a = create_branch(app, base, mutate_readme, "branch-a")
    branch_b = create_branch(app, base, None, "branch-b")
    workflow.default_snapshot_id = branch_a.snapshot_id
    workflow.start()

    adapter = ScriptedAdapter(workflow)
    files = list(options.get("files", DEMO_FILES))
    agent_a = ScriptedAgent(
        "branch-a-agent",
        [ToolStep("ast_analyzer", {"files": files}, snapshot_id=branch_a.snapshot_id)],
        snapshot_id=branch_a.snapshot_id,
    )

    async def follower_step(context: Any) -> Any:
        # the barrier guarantees that the branch-a execution is already
        # running - otherwise the two branches could run one after the other
        # and the scenario would not test the sharing at all
        await wait_for_running_execution(
            app.event_bus, workflow.workflow_id, tool_name="ast_analyzer"
        )
        return await context.call_tool(
            "ast_analyzer", {"files": files}, snapshot_id=branch_b.snapshot_id
        )

    agent_b = ScriptedAgent(
        "branch-b-agent",
        [CallableStep(follower_step)],
        snapshot_id=branch_b.snapshot_id,
    )

    results = await asyncio.gather(
        agent_a.run(workflow, adapter),
        agent_b.run(workflow, adapter),
    )
    workflow.finish()

    comparison = app.snapshots.compare_snapshots(branch_a.snapshot_id, branch_b.snapshot_id)
    return ScenarioResult(
        scenario="different-workspace",
        mode=str(mode),
        workflow_id=workflow.workflow_id,
        makespan=workflow.makespan,
        workflow=workflow,
        metrics=collect_workflow_metrics(app.repository, workflow.workflow_id, app.registry),
        snapshots={"base": base.snapshot_id, "branch_a": branch_a.snapshot_id, "branch_b": branch_b.snapshot_id},
        results=list(results),
        notes={
            "expectation": "different snapshots but identical analysed dependencies -> sharing allowed",
            "snapshot_difference": comparison,
        },
    )


# ----------------------------------------------------------------------
# scenario 3 - dynamic dependency conflict
# ----------------------------------------------------------------------
async def scenario_divergent(app: Any, mode: RuntimeMode, options: dict[str, Any]) -> ScenarioResult:
    """The follower observes a conflicting dependency and detaches while the leader runs."""

    workflow = app.create_workflow(
        "divergent",
        mode=mode,
        label="scenario: dynamic dependency conflict",
        random_seed=options.get("seed", DEFAULT_SEED),
    )
    base = prepare_base_snapshot(app)
    branch_a = create_branch(app, base, None, "branch-a")
    branch_b = create_branch(app, base, mutate_variant_source, "branch-b")
    workflow.default_snapshot_id = branch_a.snapshot_id
    workflow.start()

    adapter = ScriptedAdapter(workflow)
    files = list(options.get("files", DEMO_FILES))
    # The rounds are the margin: the follower joins after the first read, so
    # the crunch of the first file has to be long enough for the join to
    # happen before the conflicting file is read.  5000 rounds are about
    # 75 ms per file - enough under load as well.
    rounds = int(options.get("rounds", DIVERGENT_ROUNDS))
    arguments = {"files": files, "rounds": rounds}

    async def leader_step(context: Any) -> Any:
        return await context.call_tool("content_digest", arguments)

    async def follower_step(context: Any) -> Any:
        # Deterministic rendezvous: the follower only submits its call after the
        # leader reported its *first* observed dependency.  The conflicting
        # file is read last, therefore the follower joins the group and is
        # detached online while the leader is still computing.
        await wait_for_first_dependency(app.event_bus, workflow.workflow_id)
        return await context.call_tool("content_digest", arguments)

    leader = ScriptedAgent(
        "branch-a-agent", [CallableStep(leader_step)], snapshot_id=branch_a.snapshot_id
    )
    follower = ScriptedAgent(
        "branch-b-agent", [CallableStep(follower_step)], snapshot_id=branch_b.snapshot_id
    )

    results = await asyncio.gather(
        leader.run(workflow, adapter),
        follower.run(workflow, adapter),
    )
    workflow.finish()

    return ScenarioResult(
        scenario="divergent",
        mode=str(mode),
        workflow_id=workflow.workflow_id,
        makespan=workflow.makespan,
        workflow=workflow,
        metrics=collect_workflow_metrics(app.repository, workflow.workflow_id, app.registry),
        snapshots={"base": base.snapshot_id, "branch_a": branch_a.snapshot_id, "branch_b": branch_b.snapshot_id},
        results=list(results),
        notes={
            "expectation": "follower detaches online, both calls return their own correct result",
            "rounds": rounds,
        },
    )


# ----------------------------------------------------------------------
# scenario 4 - parallel agents and a join
# ----------------------------------------------------------------------
async def scenario_multi_agent(app: Any, mode: RuntimeMode, options: dict[str, Any]) -> ScenarioResult:
    """Supervisor -> Agent A / Agent B / Agent C -> Aggregator."""

    workflow = app.create_workflow(
        "multi-agent",
        mode=mode,
        label="scenario: parallel agents and a join",
        random_seed=options.get("seed", DEFAULT_SEED),
    )
    base = prepare_base_snapshot(app)
    workflow.default_snapshot_id = base.snapshot_id
    workflow.start()

    adapter = ScriptedAdapter(workflow)
    files = list(options.get("files", DEMO_FILES))
    holder: dict[str, Any] = {}

    agent_a = ScriptedAgent(
        "worker-agent-a",
        [ToolStep("ast_analyzer", {"files": files})],
        description="analyses the sources",
    )

    async def worker_b_step(context: Any) -> Any:
        # the barrier makes the rendezvous deterministic: agent B joins the
        # execution of agent A instead of starting a second one
        await wait_for_running_execution(
            app.event_bus, workflow.workflow_id, tool_name="ast_analyzer"
        )
        return await context.call_tool("ast_analyzer", {"files": files})

    agent_b = ScriptedAgent(
        "worker-agent-b",
        [CallableStep(worker_b_step)],
        description="analyses the same sources (shareable with agent A)",
    )
    agent_c = ScriptedAgent(
        "worker-agent-c",
        [ToolStep("dependency_analyzer", {"files": files})],
        description="extracts the import relations",
    )

    async def aggregator_step(context: Any) -> Any:
        collected = holder.get("results", [])
        index = await context.call_tool("source_index", {"root": "src"})
        functions = 0
        for item in collected:
            payload = getattr(item, "result", None)
            if isinstance(payload, dict):
                functions += int(payload.get("totals", {}).get("functions", 0))
        return {
            "workers": len(collected),
            "aggregated_functions": functions,
            "indexed_files": index.result["file_count"],
        }

    aggregator = ScriptedAgent(
        "aggregator-agent",
        [CallableStep(aggregator_step)],
        description="joins the results of the workers",
    )

    async def supervisor_step(context: Any) -> Any:
        results = await asyncio.gather(
            context.invoke_agent(agent_a),
            context.invoke_agent(agent_b),
            context.invoke_agent(agent_c),
        )
        holder["results"] = list(results)
        # The aggregator may only start once every required result arrived.
        return await context.invoke_agent(aggregator)

    supervisor = ScriptedAgent(
        "supervisor-agent",
        [CallableStep(supervisor_step)],
        description="starts the workers and waits for their results",
    )

    result = await supervisor.run(workflow, adapter)
    workflow.finish()

    return ScenarioResult(
        scenario="multi-agent",
        mode=str(mode),
        workflow_id=workflow.workflow_id,
        makespan=workflow.makespan,
        workflow=workflow,
        metrics=collect_workflow_metrics(app.repository, workflow.workflow_id, app.registry),
        snapshots={"base": base.snapshot_id},
        results=[result],
        notes={
            "expectation": "3 concurrent workers, 1 shared execution (A+B), aggregator joins afterwards",
        },
    )


# ----------------------------------------------------------------------
# scenario 5 - cancellation and failure
# ----------------------------------------------------------------------
async def scenario_cancel_failure(
    app: Any, mode: RuntimeMode, options: dict[str, Any]
) -> ScenarioResult:
    """Follower cancel, leader cancel, timeout, crash and a shared failure."""

    workflow = app.create_workflow(
        "cancel-failure",
        mode=mode,
        label="scenario: cancellation and failure semantics",
        random_seed=options.get("seed", DEFAULT_SEED),
    )
    base = prepare_base_snapshot(app)
    workflow.default_snapshot_id = base.snapshot_id
    workflow.start()

    adapter = ScriptedAdapter(workflow)
    coordinator = app.coordinator
    files = list(options.get("files", DEMO_FILES))
    rounds = int(options.get("rounds", 1500))
    notes: dict[str, Any] = {}

    # -- case 1: a follower cancels while it waits for the shared result ---
    async def cancelling_follower(context: Any) -> Any:
        await wait_for_first_dependency(app.event_bus, workflow.workflow_id)
        task = asyncio.create_task(context.call_tool("content_digest", {"files": files, "rounds": rounds}))
        await asyncio.sleep(0.05)
        for call in coordinator.calls_for_invocation(context.invocation_id):
            await coordinator.cancel_logical_call(call.logical_call_id, "follower cancelled")
        try:
            await task
        except asyncio.CancelledError:
            return {"cancelled": True}
        return {"cancelled": False}

    leader_agent = ScriptedAgent(
        "leader-agent",
        [ToolStep("content_digest", {"files": files, "rounds": rounds})],
    )
    follower_agent = ScriptedAgent("cancelling-follower", [CallableStep(cancelling_follower)])
    results_a = await asyncio.gather(
        leader_agent.run(workflow, adapter), follower_agent.run(workflow, adapter)
    )
    notes["follower_cancel"] = {
        "leader_result": bool(results_a[0]),
        "follower_result": results_a[1],
    }

    # -- case 2: the leader's logical call is cancelled, the follower waits ---
    # The order has to be deterministic: the agent below must really *lead*
    # the share group, otherwise the cancellation would hit a follower and the
    # case would be identical to case 1.
    leader_ready = asyncio.Event()

    async def cancelling_leader(context: Any) -> Any:
        task = asyncio.create_task(
            context.call_tool("content_digest", {"files": files, "rounds": rounds})
        )
        # The rendezvous uses the event stream, not a polling loop: a poll
        # would depend on the timer resolution of the platform (15.6 ms on
        # Windows) and would make the scenario ten times slower.
        started = await wait_for_event(
            app.event_bus,
            EventType.TOOL_EXECUTION_STARTED,
            workflow_id=workflow.workflow_id,
            timeout=30.0,
            predicate=lambda event: any(
                call.logical_call_id == event.logical_call_id
                for call in coordinator.calls_for_invocation(context.invocation_id)
            ),
        )
        leader_call = coordinator.get_call(started.logical_call_id)
        leader_ready.set()
        # wait until the follower is active - it either joined the group or
        # (in the baseline mode) it started its own execution.  Then the
        # leader is cancelled immediately, the execution must still run.
        try:
            await wait_for_any_event(
                app.event_bus,
                (EventType.TOOL_CALL_WAITING, EventType.TOOL_EXECUTION_STARTED),
                workflow_id=workflow.workflow_id,
                timeout=5.0,
            )
        except Exception:
            # nobody joined (the baseline mode never shares) - the leader is
            # cancelled anyway
            pass
        if leader_call is not None:
            await coordinator.cancel_logical_call(
                leader_call.logical_call_id, "leader cancelled"
            )
        try:
            await task
        except asyncio.CancelledError:
            return {"cancelled": True, "was_leader": leader_call is not None}
        return {"cancelled": False, "was_leader": leader_call is not None}

    async def joining_follower(context: Any) -> Any:
        # the follower only joins after the leader exists
        await leader_ready.wait()
        return await context.call_tool(
            "content_digest", {"files": files, "rounds": rounds}
        )

    leader2 = ScriptedAgent("cancelled-leader", [CallableStep(cancelling_leader)])
    follower2 = ScriptedAgent("surviving-follower", [CallableStep(joining_follower)])
    results_b = await asyncio.gather(
        leader2.run(workflow, adapter), follower2.run(workflow, adapter)
    )
    notes["leader_cancel"] = {
        "leader_result": results_b[0],
        "follower_received_result": bool(results_b[1]),
        "cancelled_was_the_leader": bool(
            isinstance(results_b[0], dict) and results_b[0].get("was_leader")
        ),
    }

    # -- case 3: an execution times out -------------------------------------
    # The timeout can be shortened - the requirement audit uses a short one so
    # that the check stays fast.  The semantics do not change.
    timeout_seconds = float(options.get("timeout_seconds", 1.0))
    timeout_result: Any = None
    try:
        await coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "sleep", "seconds": 30},
            snapshot_id=base.snapshot_id,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        timeout_result = {"error": str(exc), "kind": getattr(exc, "kind", "error")}
    notes["timeout"] = timeout_result

    # -- case 4: the worker process crashes ---------------------------------
    crash_result: Any = None
    try:
        await coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            tool_name="fault_injector",
            arguments={"mode": "exit", "exit_code": 7},
            snapshot_id=base.snapshot_id,
        )
    except Exception as exc:
        crash_result = {"error": str(exc), "kind": getattr(exc, "kind", "error")}
    notes["crash"] = crash_result

    # -- case 5: several callers wait for one failing execution --------------
    missing = ["src/does_not_exist.py"]

    async def failing_agent(context: Any) -> Any:
        try:
            return await context.call_tool("ast_analyzer", {"files": missing})
        except Exception as exc:
            return {"error": str(exc), "kind": getattr(exc, "kind", "error")}

    failing_a = ScriptedAgent("failing-agent-a", [CallableStep(failing_agent)])
    failing_b = ScriptedAgent("failing-agent-b", [CallableStep(failing_agent)])
    results_c = await asyncio.gather(
        failing_a.run(workflow, adapter), failing_b.run(workflow, adapter)
    )
    notes["shared_failure"] = {"agent_a": results_c[0], "agent_b": results_c[1]}

    workflow.finish()
    return ScenarioResult(
        scenario="cancel-failure",
        mode=str(mode),
        workflow_id=workflow.workflow_id,
        makespan=workflow.makespan,
        workflow=workflow,
        metrics=collect_workflow_metrics(app.repository, workflow.workflow_id, app.registry),
        snapshots={"base": base.snapshot_id},
        notes=notes,
    )


SCENARIOS: dict[str, Callable[..., Any]] = {
    "shared": scenario_shared,
    "shared-heavy": scenario_shared_heavy,
    "different-workspace": scenario_different_workspace,
    "divergent": scenario_divergent,
    "multi-agent": scenario_multi_agent,
    "cancel-failure": scenario_cancel_failure,
}

#: aliases used by the CLI
SCENARIO_ALIASES = {
    "same": "shared",
    "full-share": "shared",
    "workspace": "different-workspace",
    "diverged": "divergent",
    "conflict": "divergent",
    "multi": "multi-agent",
    "cancel": "cancel-failure",
    "failure": "cancel-failure",
}


def list_scenarios() -> list[str]:
    return sorted(SCENARIOS)
