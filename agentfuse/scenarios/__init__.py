"""Offline multi-agent demo scenarios."""

from __future__ import annotations

from typing import Any

from ..core.state import RuntimeMode
from .common import (
    DEMO_FILES,
    create_branch,
    mutate_readme,
    mutate_variant_source,
    prepare_base_snapshot,
    wait_for_event,
    wait_for_first_dependency,
)
from .catalogue import CATALOGUE, ScenarioSpec, catalogue, describe
from .definitions import (
    SCENARIO_ALIASES,
    SCENARIOS,
    ScenarioResult,
    list_scenarios,
    scenario_cancel_failure,
    scenario_divergent,
    scenario_different_workspace,
    scenario_multi_agent,
    scenario_shared,
    scenario_shared_heavy,
)

__all__ = [
    "CATALOGUE",
    "DEMO_FILES",
    "ScenarioSpec",
    "catalogue",
    "describe",
    "SCENARIOS",
    "SCENARIO_ALIASES",
    "ScenarioResult",
    "create_branch",
    "list_scenarios",
    "mutate_readme",
    "mutate_variant_source",
    "prepare_base_snapshot",
    "run_scenario",
    "scenario_cancel_failure",
    "scenario_divergent",
    "scenario_different_workspace",
    "scenario_multi_agent",
    "scenario_shared",
    "scenario_shared_heavy",
    "wait_for_event",
    "wait_for_first_dependency",
]


def resolve_scenario(name: str) -> str:
    """Resolve a scenario alias to its canonical name."""

    key = (name or "").strip().lower()
    if key in SCENARIOS:
        return key
    if key in SCENARIO_ALIASES:
        return SCENARIO_ALIASES[key]
    raise KeyError(f"unknown scenario: {name!r} (known: {', '.join(list_scenarios())})")


async def run_scenario(
    app: Any,
    scenario: str,
    mode: RuntimeMode = RuntimeMode.ONLINE,
    options: dict[str, Any] | None = None,
) -> ScenarioResult:
    """Execute one demo scenario and return its result."""

    name = resolve_scenario(scenario)
    handler = SCENARIOS[name]
    return await handler(app, mode, dict(options or {}))
