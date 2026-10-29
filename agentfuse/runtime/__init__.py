"""Runtime subsystem: coordinator, worker processes, scheduler and cancellation.

The module uses **lazy** re-exports (:pep:`562`).  This matters for the start
up cost of a tool worker: ``python -m agentfuse.runtime.worker_child`` imports
this package, and eagerly importing the coordinator here would pull in the
storage, sharing and monitoring subsystems - roughly 150 ms of import time that
a worker never needs.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "ExecutionOutcome",
    "LogicalCall",
    "PhysicalExecution",
    "RuntimeCoordinator",
    "ToolCallResult",
    "ToolExecutionError",
    "LocalResourceController",
    "ResourceScheduler",
    "execute_tool",
    "build_strategy",
    "SchedulingStrategy",
]

_EXPORTS = {
    "ExecutionOutcome": ("agentfuse.runtime.execution", "ExecutionOutcome"),
    "PhysicalExecution": ("agentfuse.runtime.execution", "PhysicalExecution"),
    "LogicalCall": ("agentfuse.runtime.coordinator", "LogicalCall"),
    "RuntimeCoordinator": ("agentfuse.runtime.coordinator", "RuntimeCoordinator"),
    "ToolCallResult": ("agentfuse.runtime.coordinator", "ToolCallResult"),
    "ToolExecutionError": ("agentfuse.runtime.coordinator", "ToolExecutionError"),
    "LocalResourceController": ("agentfuse.runtime.scheduler", "LocalResourceController"),
    "ResourceScheduler": ("agentfuse.runtime.scheduler", "LocalResourceController"),
    "execute_tool": ("agentfuse.runtime.worker", "execute_tool"),
    "SchedulingStrategy": ("agentfuse.runtime.strategy", "SchedulingStrategy"),
    "build_strategy": ("agentfuse.runtime.strategy", "build_strategy"),
}


def __getattr__(name: str) -> Any:
    """Import the requested symbol on first use."""

    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    import importlib

    module = importlib.import_module(module_name)
    value = getattr(module, attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
