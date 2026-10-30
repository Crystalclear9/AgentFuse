"""Scheduling strategies.

The resource controller does not hard code one admission policy - it asks a
:class:`SchedulingStrategy` which pending execution may start next.  This keeps
the door open for research policies (SLO aware, goodput aware, preemptive …)
without touching the runtime core.

Two strategies ship with AgentFuse:

``fifo``
    The default.  Strict first in, first out - the oldest waiting request is
    admitted first.  Fair and predictable.

``sjf``
    *Shortest job first*: the waiting request with the smallest declared CPU
    cost is admitted first.  It maximises the number of finished executions per
    unit of time (goodput) at the cost of fairness: a long running request can
    be postponed indefinitely.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PendingRequest:
    """One execution waiting for an execution slot."""

    execution_id: str
    requested_at: float = field(default_factory=time.time)
    estimated_memory_mb: float = 0.0
    estimated_cpu_seconds: float = 0.0

    @property
    def waiting_seconds(self) -> float:
        return max(0.0, time.time() - self.requested_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "waiting_seconds": round(self.waiting_seconds, 3),
            "estimated_memory_mb": self.estimated_memory_mb,
            "estimated_cpu_seconds": self.estimated_cpu_seconds,
        }


@dataclass
class AdmissionDecision:
    """The answer of a strategy: may an execution start, and which one?"""

    allowed: bool
    reason: str
    index: int = 0

    @property
    def allowed_reason(self) -> str:
        return self.reason


class SchedulingStrategy:
    """Base class of all scheduling strategies."""

    name = "base"

    def select(
        self,
        *,
        pending: list[PendingRequest],
        running: int,
        max_workers: int,
        reserved_mb: float,
        observed_rss_mb: float,
        budget_mb: int,
        rss_limit_mb: int,
    ) -> AdmissionDecision:
        """Return the decision for the current scheduler state."""

        raise NotImplementedError

    # -- shared helpers -------------------------------------------------
    @staticmethod
    def _resource_block(
        *,
        running: int,
        max_workers: int,
        reserved_mb: float,
        observed_rss_mb: float,
        budget_mb: int,
        rss_limit_mb: int,
        estimate_mb: float,
    ) -> tuple[str | None, str | None]:
        """Return ``(blocking_reason, admission_note)``."""

        if running >= max_workers:
            return f"worker limit reached ({max_workers})", None
        if rss_limit_mb and observed_rss_mb > rss_limit_mb:
            return (
                f"observed RSS {observed_rss_mb:.1f} MiB exceeds the limit ({rss_limit_mb} MiB)"
            ), None
        projected = reserved_mb + estimate_mb
        if projected > budget_mb:
            if running == 0:
                # never deadlock on a single oversized tool
                return None, "single execution exceeds the memory budget (allowed to avoid deadlock)"
            return (
                f"memory admission refused: projected {projected:.1f} MiB > {budget_mb} MiB"
            ), None
        return None, None

    def describe(self) -> dict[str, Any]:
        return {"name": self.name}


class FifoStrategy(SchedulingStrategy):
    """Strict first in, first out."""

    name = "fifo"

    def select(
        self,
        *,
        pending: list[PendingRequest],
        running: int,
        max_workers: int,
        reserved_mb: float,
        observed_rss_mb: float,
        budget_mb: int,
        rss_limit_mb: int,
    ) -> AdmissionDecision:
        if not pending:
            return AdmissionDecision(False, "no pending execution")
        head = pending[0]
        blocked, note = self._resource_block(
            running=running,
            max_workers=max_workers,
            reserved_mb=reserved_mb,
            observed_rss_mb=observed_rss_mb,
            budget_mb=budget_mb,
            rss_limit_mb=rss_limit_mb,
            estimate_mb=head.estimated_memory_mb,
        )
        if blocked:
            return AdmissionDecision(False, blocked)
        return AdmissionDecision(True, note or "admitted", index=0)


class ShortestJobFirstStrategy(SchedulingStrategy):
    """Admit the waiting execution with the smallest declared CPU cost."""

    name = "sjf"

    def select(
        self,
        *,
        pending: list[PendingRequest],
        running: int,
        max_workers: int,
        reserved_mb: float,
        observed_rss_mb: float,
        budget_mb: int,
        rss_limit_mb: int,
    ) -> AdmissionDecision:
        if not pending:
            return AdmissionDecision(False, "no pending execution")
        order = sorted(
            range(len(pending)),
            key=lambda index: (
                pending[index].estimated_cpu_seconds,
                pending[index].requested_at,
            ),
        )
        reason = "no pending execution"
        for index in order:
            request = pending[index]
            blocked, note = self._resource_block(
                running=running,
                max_workers=max_workers,
                reserved_mb=reserved_mb,
                observed_rss_mb=observed_rss_mb,
                budget_mb=budget_mb,
                rss_limit_mb=rss_limit_mb,
                estimate_mb=request.estimated_memory_mb,
            )
            if blocked:
                # try the next candidate, but remember the reason
                reason = blocked
                continue
            detail = note or f"admitted (sjf, cpu={request.estimated_cpu_seconds}s)"
            return AdmissionDecision(True, detail, index)
        return AdmissionDecision(False, reason)


STRATEGIES: dict[str, type[SchedulingStrategy]] = {
    "fifo": FifoStrategy,
    "sjf": ShortestJobFirstStrategy,
}


def build_strategy(name: str | None) -> SchedulingStrategy:
    """Instantiate a strategy by name (falls back to FIFO)."""

    key = (name or "fifo").strip().lower()
    factory = STRATEGIES.get(key, FifoStrategy)
    return factory()

