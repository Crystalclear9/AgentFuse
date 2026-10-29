"""Resource monitoring of the tool worker processes.

The monitor samples the resident set size (RSS) of a worker **process tree**
and writes the samples into the ``resource_samples`` table.

Why the process tree?  On Windows the interpreter may be started through a
launcher stub, therefore the process that owns the real memory is a *child* of
the process we spawned.  Sampling only the direct child would report the
launcher (a few MiB) instead of the actual worker.
"""

from __future__ import annotations

import time
from typing import Any

try:  # pragma: no cover - psutil is a hard dependency of the runtime
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore[assignment]


class ResourceMonitor:
    """Samples the memory of a worker process tree."""

    def __init__(self, repository: Any = None, interval: float = 0.05) -> None:
        self.repository = repository
        self.interval = interval

    # ------------------------------------------------------------------
    def sample(self, pid: int) -> int | None:
        """Return the RSS (bytes) of the process tree of ``pid``."""

        if psutil is None or not pid:
            return None
        try:
            process = psutil.Process(int(pid))
        except Exception:
            return None
        rss = 0
        try:
            rss = int(process.memory_info().rss)
        except Exception:
            return None
        try:
            for child in process.children(recursive=True):
                try:
                    rss = max(rss, int(child.memory_info().rss))
                except Exception:
                    continue
        except Exception:
            pass
        return rss

    # ------------------------------------------------------------------
    def record(self, workflow_id: str | None, execution_id: str, rss: int) -> None:
        """Persist one resource sample."""

        if self.repository is None:
            return
        from ..storage.models import ResourceSample

        self.repository.save_resource_sample(
            ResourceSample(
                workflow_id=workflow_id,
                execution_id=execution_id,
                timestamp=time.time(),
                rss_bytes=rss,
            )
        )

    # ------------------------------------------------------------------
    def run(self, execution: Any, stop: Any) -> None:
        """Sample until the execution is finished or the runtime shuts down."""

        while not stop.is_set():
            rss = self.sample(execution.pid)
            if rss is None:
                break
            if rss > execution.peak_rss:
                execution.peak_rss = rss
            self.record(execution.workflow_id, execution.execution_id, rss)
            time.sleep(self.interval)
