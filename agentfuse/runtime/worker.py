"""Tool worker implementation.

The worker runs in a *separate operating system process* so that CPU heavy
tool code never blocks the runtime event loop.  It receives the tool
description, the snapshot root and the arguments, executes the tool through
the controlled :class:`~agentfuse.workspace.reader.WorkspaceReader` and
reports every dependency observation **immediately** through the reporter
callback.

The process is started by the parent as::

    python -m agentfuse.runtime.worker_child

and communicates over stdin/stdout using newline delimited JSON.  Using a
plain subprocess (instead of ``multiprocessing``) has two advantages: the
worker never re-imports the ``__main__`` module of the parent and the
protocol stays human readable.
"""

from __future__ import annotations

import os
import time
import traceback
from typing import Any, Callable

from ..tools.base import ToolContext, ToolError
from ..tools.registry import build_default_registry
from ..workspace.reader import WorkspaceReader
from .protocol import (
    ERROR_INTERNAL,
    ERROR_TOOL,
    error_message,
    result_message,
)

DependencyReporter = Callable[[str, str, str], None]


def _current_rss() -> int:
    try:
        import psutil

        return int(psutil.Process(os.getpid()).memory_info().rss)
    except Exception:  # pragma: no cover - psutil is a hard dependency in practice
        return 0


def execute_tool(payload: dict[str, Any], reporter: DependencyReporter) -> dict[str, Any]:
    """Execute one tool and return the terminal protocol message.

    Every dependency observed by the controlled reader is passed to
    ``reporter`` *before* the tool continues, which is what makes the online
    dependency validation of the share coordinator possible.
    """

    started = time.time()
    cpu_start = time.process_time()

    try:
        registry = build_default_registry()
        spec = registry.get(payload["tool_name"], payload.get("tool_version"))
    except Exception as exc:  # pragma: no cover - configuration problem
        return error_message(
            ERROR_INTERNAL,
            f"cannot prepare tool {payload.get('tool_name')!r}: {exc}",
            traceback.format_exc(),
            time.process_time() - cpu_start,
        )

    reader = WorkspaceReader(
        payload["snapshot_root"],
        reporter=reporter,
        snapshot_id=payload.get("snapshot_id"),
    )
    context = ToolContext(reader=reader, arguments=payload.get("arguments") or {})

    try:
        result = spec.implementation(context)
    except ToolError as exc:
        return error_message(
            ERROR_TOOL, str(exc), traceback.format_exc(), time.process_time() - cpu_start
        )
    except Exception as exc:  # a failing tool implementation is a business error
        return error_message(
            ERROR_TOOL,
            f"{type(exc).__name__}: {exc}",
            traceback.format_exc(),
            time.process_time() - cpu_start,
        )

    from ..sharing.result_delivery import compute_result_hash

    return result_message(
        payload=result,
        result_hash=compute_result_hash(result),
        cpu_time=time.process_time() - cpu_start,
        elapsed=time.time() - started,
        rss_end=_current_rss(),
    )
