"""Core runtime primitives: events, identifiers and status vocabularies.

The re-exports are **lazy** (:pep:`562`): a tool worker only needs the
identifier helpers, and importing the event bus would pull in ``asyncio``
(~55 ms) for nothing.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "EventBus",
    "RuntimeEvent",
    "new_id",
    "sequence_id",
    "DependencyType",
    "EventType",
    "ExecutionStatus",
    "InvocationStatus",
    "LogicalCallStatus",
    "ParticipantStatus",
    "RuntimeMode",
    "ShareGroupStatus",
    "ValidationStatus",
    "WorkflowStatus",
]

_EXPORTS = {
    "EventBus": ("agentfuse.core.events", "EventBus"),
    "RuntimeEvent": ("agentfuse.core.events", "RuntimeEvent"),
    "new_id": ("agentfuse.core.ids", "new_id"),
    "sequence_id": ("agentfuse.core.ids", "sequence_id"),
    "DependencyType": ("agentfuse.core.state", "DependencyType"),
    "EventType": ("agentfuse.core.state", "EventType"),
    "ExecutionStatus": ("agentfuse.core.state", "ExecutionStatus"),
    "InvocationStatus": ("agentfuse.core.state", "InvocationStatus"),
    "LogicalCallStatus": ("agentfuse.core.state", "LogicalCallStatus"),
    "ParticipantStatus": ("agentfuse.core.state", "ParticipantStatus"),
    "RuntimeMode": ("agentfuse.core.state", "RuntimeMode"),
    "ShareGroupStatus": ("agentfuse.core.state", "ShareGroupStatus"),
    "ValidationStatus": ("agentfuse.core.state", "ValidationStatus"),
    "WorkflowStatus": ("agentfuse.core.state", "WorkflowStatus"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    import importlib

    value = getattr(importlib.import_module(module_name), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
