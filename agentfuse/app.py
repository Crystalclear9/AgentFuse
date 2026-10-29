"""Composition root of the AgentFuse runtime.

``AgentFuseApp`` wires the pieces together:

    settings -> database -> repository
             -> snapshot store
             -> tool registry
             -> event bus
             -> resource scheduler
             -> runtime coordinator

It is used by the CLI, the demo scripts, the benchmark runner and the web
API, so that all entry points share exactly the same runtime semantics.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .core.events import EventBus
from .core.state import RuntimeMode
from .core.workflow import WorkflowRuntime
from .runtime.coordinator import RuntimeCoordinator
from .runtime.scheduler import ResourceScheduler
from .settings import RuntimeSettings, load_settings
from .sharing.result_delivery import ResultStore
from .storage.database import Database
from .storage.repository import Repository
from .tools.registry import ToolRegistry, build_default_registry
from .workspace.snapshots import SnapshotStore


class AgentFuseApp:
    """The runtime application object."""

    def __init__(
        self,
        settings: RuntimeSettings | None = None,
        mode: RuntimeMode = RuntimeMode.ONLINE,
        registry: ToolRegistry | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self.settings.ensure_directories()
        self.mode = mode

        self.database = Database(self.settings.db_path)
        self.database.initialize()
        self.database.start()
        self.repository = Repository(self.database)
        #: workflow runs that were interrupted by a previous runtime crash
        self.recovered_workflows: list[str] = self.repository.recover_interrupted_workflows()
        if self.recovered_workflows:
            import logging

            logging.getLogger("agentfuse.app").warning(
                "recovered %d interrupted workflow run(s): %s",
                len(self.recovered_workflows),
                ", ".join(self.recovered_workflows),
            )

        self.registry = registry or build_default_registry()
        self.snapshots = SnapshotStore(
            root=self.settings.workspaces_dir,
            max_files=self.settings.snapshot_max_files,
            max_total_bytes=self.settings.snapshot_max_total_bytes,
            exclude=self.settings.snapshot_exclude,
            repository=self.repository,
        )
        self.event_bus = EventBus()
        self.event_bus.subscribe(self._persist_event)
        self.result_store = ResultStore(self.settings.results_dir, repository=self.repository)
        self.scheduler = ResourceScheduler(
            max_workers=self.settings.max_workers,
            max_memory_budget_mb=self.settings.max_memory_budget_mb,
            max_rss_soft_limit_mb=self.settings.max_rss_soft_limit_mb,
            strategy=self.settings.scheduling_strategy,
            repository=self.repository,
        )
        self.scheduler.start()
        self.coordinator = RuntimeCoordinator(
            repository=self.repository,
            registry=self.registry,
            snapshots=self.snapshots,
            event_bus=self.event_bus,
            result_store=self.result_store,
            scheduler=self.scheduler,
            mode=mode,
            default_timeout_seconds=self.settings.default_timeout_seconds,
            result_cache_entries=self.settings.result_cache_entries,
            worker_pool_size=self.settings.worker_pool_size,
        )
        self._workflows: list[WorkflowRuntime] = []
        # Start the pool workers while the runtime is idle.
        self.coordinator.prewarm_workers()

    # ------------------------------------------------------------------
    @property
    def workflows(self) -> list[WorkflowRuntime]:
        return list(self._workflows)

    def _persist_event(self, event: Any) -> None:
        """Persist every runtime event (the event bus is the single source)."""

        try:
            self.database.insert_event(event.to_dict())
        except Exception:  # pragma: no cover - never break the runtime
            import logging

            logging.getLogger("agentfuse.app").exception("failed to persist event")

    def create_workflow(
        self,
        scenario: str,
        mode: RuntimeMode | None = None,
        workflow_id: str | None = None,
        random_seed: int | None = None,
        label: str | None = None,
        configuration: dict[str, Any] | None = None,
        default_snapshot_id: str | None = None,
    ) -> WorkflowRuntime:
        workflow = WorkflowRuntime(
            coordinator=self.coordinator,
            repository=self.repository,
            event_bus=self.event_bus,
            scenario=scenario,
            mode=mode or self.mode,
            workflow_id=workflow_id,
            random_seed=random_seed,
            label=label,
            configuration=configuration,
        )
        workflow.default_snapshot_id = default_snapshot_id
        self.coordinator.register_workflow_mode(workflow.workflow_id, workflow.mode)
        self._workflows.append(workflow)
        return workflow

    # ------------------------------------------------------------------
    async def close(self) -> None:
        try:
            await self.coordinator.shutdown()
        finally:
            self.scheduler.stop()
            for workflow in self._workflows:
                workflow.close()
            self.database.flush()
            self.database.close()

    def close_sync(self) -> None:
        """Close the application from synchronous code (CLI entry points)."""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is None:
            asyncio.run(self.close())
        else:  # pragma: no cover - only reachable from inside a running loop
            loop.create_task(self.close())
