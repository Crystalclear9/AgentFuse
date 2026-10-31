"""Benchmark scenario catalogue.

The scenario implementations **and** their descriptions live in
``agentfuse.scenarios`` - this module only re-exports them.

A second copy of the catalogue would drift away from the runtime: the
workloads, the conflicting file and the number of rounds are defined once, in
``agentfuse/scenarios/common.py``, and the catalogue is derived from those
constants.

    python benchmarks/runner.py --scenario shared --repeat 5
    python benchmarks/runner.py --scenario divergent --repeat 3 --modes baseline online deferred
"""

from __future__ import annotations

from agentfuse.scenarios.catalogue import (
    CATALOGUE,
    ScenarioSpec,
    catalogue,
    describe,
)
from agentfuse.scenarios.definitions import SCENARIOS, list_scenarios

__all__ = [
    "CATALOGUE",
    "SCENARIOS",
    "ScenarioSpec",
    "catalogue",
    "describe",
    "list_scenarios",
]
