"""Unified runtime event protocol.

The event protocol is the *only* interface between the controlled adapter
layer and the runtime.  It is intentionally framework agnostic: any external
multi-agent middleware can be mapped onto it by implementing
:class:`agentfuse.adapters.base.AgentAdapter`.

Guarantees implemented here:

* ``event_id`` is globally unique.
* every workflow owns a stable, gap-free ``sequence`` counter, so ordering
  never depends on wall clock resolution;
* events are appended to an in-memory ring buffer (for live inspection) and
  forwarded to the storage writer (never blocking the event loop for long).
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .ids import new_id
from .state import EventType

EventCallback = Callable[["RuntimeEvent"], None]


@dataclass(slots=True)
class RuntimeEvent:
    """A single observed runtime fact."""

    event_id: str
    workflow_id: str | None
    event_type: str
    timestamp: float
    sequence: int
    agent_id: str | None = None
    invocation_id: str | None = None
    logical_call_id: str | None = None
    physical_execution_id: str | None = None
    parent_event_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "workflow_id": self.workflow_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "sequence": self.sequence,
            "agent_id": self.agent_id,
            "invocation_id": self.invocation_id,
            "logical_call_id": self.logical_call_id,
            "physical_execution_id": self.physical_execution_id,
            "parent_event_id": self.parent_event_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RuntimeEvent":
        return cls(
            event_id=payload["event_id"],
            workflow_id=payload.get("workflow_id"),
            event_type=payload["event_type"],
            timestamp=float(payload.get("timestamp", 0.0)),
            sequence=int(payload.get("sequence", 0)),
            agent_id=payload.get("agent_id"),
            invocation_id=payload.get("invocation_id"),
            logical_call_id=payload.get("logical_call_id"),
            physical_execution_id=payload.get("physical_execution_id"),
            parent_event_id=payload.get("parent_event_id"),
            metadata=dict(payload.get("metadata") or {}),
        )


class EventBus:
    """Synchronous publisher with per-workflow sequencing.

    The bus is *loop confined*: it must only be used from the thread that owns
    the asyncio event loop.  Worker processes communicate through
    ``call_soon_threadsafe`` so this invariant holds.
    """

    def __init__(self, history_limit: int = 20000) -> None:
        self._lock = threading.RLock()
        self._sequences: dict[str, itertools.count] = defaultdict(itertools.count)
        self._callbacks: list[EventCallback] = []
        self._history: deque[RuntimeEvent] = deque(maxlen=history_limit)
        self._history_by_workflow: dict[str, deque[RuntimeEvent]] = defaultdict(
            lambda: deque(maxlen=history_limit)
        )
        self._waiters: list[tuple[Callable[[RuntimeEvent], bool], asyncio.Future]] = []

    # ------------------------------------------------------------------
    # subscription
    # ------------------------------------------------------------------
    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        with self._lock:
            self._callbacks.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._callbacks:
                    self._callbacks.remove(callback)

        return unsubscribe

    # ------------------------------------------------------------------
    # publishing
    # ------------------------------------------------------------------
    def publish(
        self,
        event_type: str | EventType,
        *,
        workflow_id: str | None = None,
        agent_id: str | None = None,
        invocation_id: str | None = None,
        logical_call_id: str | None = None,
        physical_execution_id: str | None = None,
        parent_event_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> RuntimeEvent:
        event_type_value = str(event_type)
        with self._lock:
            sequence = next(self._sequences[workflow_id or "-"])
            event = RuntimeEvent(
                event_id=new_id("evt"),
                workflow_id=workflow_id,
                event_type=event_type_value,
                timestamp=timestamp if timestamp is not None else time.time(),
                sequence=sequence,
                agent_id=agent_id,
                invocation_id=invocation_id,
                logical_call_id=logical_call_id,
                physical_execution_id=physical_execution_id,
                parent_event_id=parent_event_id,
                metadata=dict(metadata or {}),
            )
            self._history.append(event)
            self._history_by_workflow[workflow_id or "-"].append(event)
            callbacks = list(self._callbacks)
            waiters = list(self._waiters)

        for callback in callbacks:
            try:
                callback(event)
            except Exception:  # pragma: no cover - subscribers must not break the runtime
                import logging

                logging.getLogger("agentfuse.events").exception(
                    "event subscriber failed for %s", event_type_value
                )

        for predicate, future in waiters:
            if future.done():
                continue
            try:
                matched = predicate(event)
            except Exception:
                matched = False
            if matched:
                future.set_result(event)
        return event

    # ------------------------------------------------------------------
    # inspection
    # ------------------------------------------------------------------
    def history(
        self, workflow_id: str | None = None, limit: int | None = None
    ) -> list[RuntimeEvent]:
        with self._lock:
            if workflow_id is None:
                events = list(self._history)
            else:
                events = list(self._history_by_workflow.get(workflow_id, ()))
        if limit is not None:
            events = events[-limit:]
        return events

    async def wait_for(
        self,
        predicate: Callable[[RuntimeEvent], bool],
        timeout: float = 10.0,
    ) -> RuntimeEvent:
        """Block (async) until an event matches ``predicate``.

        This is used by the offline demo scenarios to build deterministic
        rendezvous points (e.g. "wait until the leader observed its first
        dependency") without inventing fake events.
        """

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        with self._lock:
            self._waiters.append((predicate, future))
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            with self._lock:
                self._waiters = [item for item in self._waiters if item[1] is not future]

    def subscribe_all(self) -> Iterable[RuntimeEvent]:
        with self._lock:
            return list(self._history)
