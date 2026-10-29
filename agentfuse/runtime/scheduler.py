"""Local resource controller.

Responsibilities:

* limit the number of concurrently running tool worker processes,
* apply an admission budget based on the *declared* memory estimate of a tool,
* track the *observed* RSS of the running workers (psutil),
* expose a live snapshot for the dashboard.

Important semantic distinction (documented in ``docs/KNOWN_LIMITATIONS.md``):

``reserved_estimate_mb``
    Declared, used for **admission control**.

``observed_rss_mb``
    Measured with psutil, used for **reporting and warnings**.

Neither of them is an operating system level hard limit.  A worker that
misbehaves can still exceed the budget; in that case the controller refuses
*new* admissions and raises a warning, it does not kill the offender.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .strategy import (
    AdmissionDecision,
    PendingRequest,
    SchedulingStrategy,
    build_strategy,
)

try:  # pragma: no cover - psutil is a hard dependency of the runtime
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


@dataclass
class _Slot:
    execution_id: str
    estimated_memory_mb: float
    future: asyncio.Future
    requested_at: float = field(default_factory=time.time)
    blocked_reason: str | None = None
    estimated_cpu_seconds: float = 0.0

    def to_request(self) -> PendingRequest:
        return PendingRequest(
            execution_id=self.execution_id,
            requested_at=self.requested_at,
            estimated_memory_mb=self.estimated_memory_mb,
            estimated_cpu_seconds=self.estimated_cpu_seconds,
        )


class ResourceScheduler:
    """FIFO scheduler with a concurrency limit and a memory admission budget."""

    def __init__(
        self,
        max_workers: int = 2,
        max_memory_budget_mb: int = 1024,
        max_rss_soft_limit_mb: int = 0,
        repository: Any = None,
        strategy: SchedulingStrategy | str | None = None,
    ) -> None:
        self.max_workers = max(1, int(max_workers))
        self.max_memory_budget_mb = int(max_memory_budget_mb)
        self.strategy = build_strategy(strategy) if isinstance(strategy, str) else (
            strategy or build_strategy("fifo")
        )
        #: soft limit for the *observed* RSS; 0 disables it (the admission
        #: budget is then used as the soft limit as well)
        self.max_rss_soft_limit_mb = int(max_rss_soft_limit_mb)
        self.repository = repository

        self._running: dict[str, _Slot] = {}
        self._pending: list[_Slot] = []
        self._observed_rss_mb: dict[str, float] = {}
        self._peak_rss_mb: float = 0.0
        self._total_admitted = 0
        self._system_cpu_percent = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._sampler: threading.Thread | None = None

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._sampler is None:
            self._stop.clear()
            self._sampler = threading.Thread(
                target=self._sample_system_cpu, name="agentfuse-cpu-sampler", daemon=True
            )
            self._sampler.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sampler is not None:
            self._sampler.join(timeout=2.0)
            self._sampler = None

    def _sample_system_cpu(self) -> None:
        if psutil is None:
            return
        psutil.cpu_percent(interval=None)
        while not self._stop.wait(0.5):
            try:
                self._system_cpu_percent = float(psutil.cpu_percent(interval=None))
            except Exception:  # pragma: no cover
                pass

    # ------------------------------------------------------------------
    # admission control
    # ------------------------------------------------------------------
    @property
    def reserved_estimate_mb(self) -> float:
        return sum(slot.estimated_memory_mb for slot in self._running.values())

    @property
    def observed_rss_mb(self) -> float:
        return sum(self._observed_rss_mb.values())

    @property
    def peak_rss_mb(self) -> float:
        return self._peak_rss_mb

    @property
    def effective_rss_limit_mb(self) -> int:
        """The limit that the observed RSS is compared with."""

        return self.max_rss_soft_limit_mb or self.max_memory_budget_mb

    def rss_limit_exceeded(self) -> bool:
        """True when the observed RSS is above the soft limit."""

        limit = self.effective_rss_limit_mb
        return bool(limit) and self.observed_rss_mb > limit

    def admission_check(self, estimated_memory_mb: float) -> tuple[bool, str]:
        """Return ``(allowed, reason)`` for a new execution."""

        with self._lock:
            if len(self._running) >= self.max_workers:
                return False, f"worker limit reached ({self.max_workers})"

            limit = self.effective_rss_limit_mb
            if limit and self.observed_rss_mb > limit:
                return False, (
                    f"observed RSS {self.observed_rss_mb:.1f} MiB exceeds the limit "
                    f"({limit} MiB)"
                )

            projected = self.reserved_estimate_mb + estimated_memory_mb
            if projected > self.max_memory_budget_mb:
                if not self._running:
                    # Never deadlock: a single oversized tool still has to run.
                    return True, "single execution exceeds the memory budget (allowed to avoid deadlock)"
                return False, (
                    f"memory admission refused: projected {projected:.1f} MiB > "
                    f"{self.max_memory_budget_mb} MiB"
                )
            return True, "admitted"

    async def acquire(
        self,
        execution_id: str,
        estimated_memory_mb: float,
        estimated_cpu_seconds: float = 0.0,
    ) -> str:
        """Wait until the execution may start.  Returns the admission reason."""

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        slot = _Slot(
            execution_id=execution_id,
            estimated_memory_mb=float(estimated_memory_mb),
            estimated_cpu_seconds=float(estimated_cpu_seconds),
            future=future,
        )
        with self._lock:
            self._pending.append(slot)
        self._pump()
        return await future

    def release(self, execution_id: str) -> None:
        with self._lock:
            self._running.pop(execution_id, None)
            self._observed_rss_mb.pop(execution_id, None)
        self._pump()

    def _pump(self) -> None:
        """Grant execution slots according to the configured strategy."""

        while True:
            with self._lock:
                if not self._pending:
                    return
                decision = self.strategy.select(
                    pending=[slot.to_request() for slot in self._pending],
                    running=len(self._running),
                    max_workers=self.max_workers,
                    reserved_mb=self.reserved_estimate_mb,
                    observed_rss_mb=self.observed_rss_mb,
                    budget_mb=self.max_memory_budget_mb,
                    rss_limit_mb=self.effective_rss_limit_mb,
                )
                if not decision.allowed:
                    # The reason is kept so that the UI can explain *why* an
                    # execution is waiting (worker limit vs. memory budget).
                    for slot in self._pending:
                        slot.blocked_reason = decision.reason
                    return
                index = min(decision.index, len(self._pending) - 1)
                slot = self._pending.pop(index)
                self._running[slot.execution_id] = slot
                self._total_admitted += 1
                reason = decision.reason
            if not slot.future.done():
                slot.future.set_result(reason)

    def _admission_check_locked(self, estimated_memory_mb: float) -> tuple[bool, str]:
        if len(self._running) >= self.max_workers:
            return False, "worker limit reached"
        limit = self.effective_rss_limit_mb
        if limit and self.observed_rss_mb > limit:
            return False, "observed RSS exceeds the limit"
        projected = self.reserved_estimate_mb + estimated_memory_mb
        if projected > self.max_memory_budget_mb:
            if not self._running:
                return True, "single execution exceeds the memory budget"
            return False, "memory admission refused"
        return True, "admitted"

    # ------------------------------------------------------------------
    # monitoring
    # ------------------------------------------------------------------
    def record_rss(self, execution_id: str, rss_bytes: int) -> None:
        rss_mb = rss_bytes / (1024 * 1024)
        with self._lock:
            self._observed_rss_mb[execution_id] = rss_mb
            if rss_mb > self._peak_rss_mb:
                self._peak_rss_mb = rss_mb

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            pending = [
                {
                    "execution_id": slot.execution_id,
                    "waiting_seconds": round(max(0.0, time.time() - slot.requested_at), 3),
                    "estimated_memory_mb": slot.estimated_memory_mb,
                    "reason": slot.blocked_reason or "waiting for a free worker slot",
                }
                for slot in self._pending
            ]
            return {
                "max_workers": self.max_workers,
                "running": len(self._running),
                "waiting": len(self._pending),
                "max_memory_budget_mb": self.max_memory_budget_mb,
                "max_rss_soft_limit_mb": self.max_rss_soft_limit_mb,
                "effective_rss_limit_mb": self.effective_rss_limit_mb,
                "rss_limit_exceeded": self.rss_limit_exceeded(),
                "reserved_estimate_mb": round(self.reserved_estimate_mb, 2),
                "observed_rss_mb": round(self.observed_rss_mb, 2),
                "peak_rss_mb": round(self._peak_rss_mb, 2),
                "system_cpu_percent": round(self._system_cpu_percent, 2),
                "admitted_total": self._total_admitted,
                "strategy": self.strategy.name,
                "pending": pending,
            }
