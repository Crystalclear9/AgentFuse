"""Shared helpers for the offline demo scenarios."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable

from ..core.events import EventBus
from ..core.state import EventType
from ..resources import sample_repository_path
from ..workspace.snapshots import SnapshotInfo

#: The seed that every scenario uses when the caller does not specify one.
#: The benchmark compares the modes with **the same** seed - that is one of
#: the fairness rules of requirement 13.2.
DEFAULT_SEED = 20240917

#: The files that the demo scenarios analyse.
DEMO_FILES: list[str] = [
    "src/alpha.py",
    "src/beta.py",
    "src/gamma.py",
    "src/pkg/helper.py",
    "src/zeta_variant.py",
]

#: The file that the divergent scenario mutates in one branch.
#:
#: It is the **third** file the digest tool reads (the files are processed in
#: sorted order).  The conflict must be found while the leader still has work
#: left - otherwise the separation would happen at the very end of the
#: execution and the scenario would no longer prove what requirement 12.4
#: asks for ("the follower is separated while the leader has not finished its
#: computation").
#:
#: The first file cannot be used: the follower joins after the first
#: dependency was observed, so a conflict there would be found by the join
#: time validation instead of the online one.  The second file would leave
#: the follower only the crunch of the first file as a window - the third
#: file doubles that window and still leaves the leader with work to do.
DIVERGENT_FILE = "src/gamma.py"

#: The workload of the scenarios.  The catalogue (which the API serves to the
#: dashboard) uses the very same constants - a description cannot drift away
#: from the runtime any more.
SHARED_HEAVY_ROUNDS = 3000
DIVERGENT_ROUNDS = 5000


def default_source_repository() -> Path:
    """Return the path of the packaged sample repository."""

    return sample_repository_path()


def prepare_base_snapshot(app: Any, source: Path | None = None) -> SnapshotInfo:
    """Create the first immutable snapshot of the sample repository."""

    source = source or default_source_repository()
    return app.snapshots.create_workspace(
        source_dir=source,
        source_reference=f"sample-repository:{source.name}",
        metadata={"kind": "base"},
    )


def create_branch(
    app: Any,
    base: SnapshotInfo,
    mutate: Callable[[Path], None] | None = None,
    label: str = "branch",
) -> SnapshotInfo:
    """Fork a snapshot, apply an edit in a writable copy and snapshot the result.

    The edit never touches the immutable snapshot itself - the mutation is
    applied to a temporary working directory which is then turned into a new
    immutable snapshot.
    """

    working = app.settings.home / "branches" / f"{base.snapshot_id}-{label}"
    if working.exists():
        shutil.rmtree(working)
    app.snapshots.materialise(base.snapshot_id, working)
    if mutate is not None:
        mutate(working)
    snapshot = app.snapshots.create_snapshot(
        workspace_id=base.workspace_id,
        source_dir=working,
        parent_snapshot_id=base.snapshot_id,
        source_reference=f"branch:{label}",
        metadata={"kind": "branch", "label": label},
    )
    shutil.rmtree(working, ignore_errors=True)
    return snapshot


def mutate_readme(working: Path) -> None:
    """Branch A of the 'different workspace' scenario touches the README only."""

    readme = working / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8")
        + "\nBranch A added this note; the analysed sources are untouched.\n",
        encoding="utf-8",
    )


def mutate_variant_source(working: Path) -> None:
    """Branch B of the divergent scenario changes the content of one module."""

    target = working / DIVERGENT_FILE
    source = target.read_text(encoding="utf-8")
    # the mutation must change the content but keep the module valid
    mutated = source.replace(
        "return add(left, right) + 0",
        "return add(left, right) + 1  # branch specific behaviour",
    )
    if mutated == source:
        # the sample module does not contain the expected expression - the
        # content is changed in a way that does not depend on the sample
        mutated = source + "\n# branch specific behaviour\n"
    target.write_text(mutated, encoding="utf-8")


async def wait_for_event(
    event_bus: EventBus,
    event_type: str,
    workflow_id: str | None = None,
    timeout: float = 30.0,
    predicate: Callable[[Any], bool] | None = None,
) -> Any:
    """Wait for a real runtime event (used to build deterministic rendezvous)."""

    def matcher(event: Any) -> bool:
        if event.event_type != event_type:
            return False
        if workflow_id is not None and event.workflow_id != workflow_id:
            return False
        if predicate is not None and not predicate(event):
            return False
        return True

    return await event_bus.wait_for(matcher, timeout=timeout)


async def wait_for_any_event(
    event_bus: EventBus,
    event_types: tuple[str, ...],
    workflow_id: str | None = None,
    timeout: float = 30.0,
    predicate: Callable[[Any], bool] | None = None,
) -> Any:
    """Wait for the first of several event types."""

    def matcher(event: Any) -> bool:
        if event.event_type not in event_types:
            return False
        if workflow_id is not None and event.workflow_id != workflow_id:
            return False
        if predicate is not None and not predicate(event):
            return False
        return True

    return await event_bus.wait_for(matcher, timeout=timeout)


async def wait_for_first_dependency(
    event_bus: EventBus, workflow_id: str, timeout: float = 30.0
) -> Any:
    return await wait_for_event(
        event_bus, EventType.TOOL_READ_OBSERVED, workflow_id=workflow_id, timeout=timeout
    )


async def wait_for_running_execution(
    event_bus: EventBus,
    workflow_id: str,
    tool_name: str | None = None,
    timeout: float = 30.0,
) -> Any:
    """Wait until a physical execution is really running.

    The barrier makes the rendezvous deterministic: a follower that submits
    its call after this barrier is guaranteed to find a running group that it
    can join - the scenario does not depend on the scheduling of the two
    agents.
    """

    def predicate(event: Any) -> bool:
        if tool_name is None:
            return True
        metadata = getattr(event, "metadata", None) or {}
        return metadata.get("tool_name") == tool_name

    return await wait_for_event(
        event_bus,
        EventType.TOOL_EXECUTION_STARTED,
        workflow_id=workflow_id,
        timeout=timeout,
        predicate=predicate,
    )
