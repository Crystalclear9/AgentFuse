"""Entry point of the tool worker process.

The parent starts the worker with::

    python -m agentfuse.runtime.worker_child

The protocol is newline delimited JSON:

* the parent writes one payload line (tool, arguments, snapshot root),
* the worker answers with any number of ``dep`` messages (one per observed
  dependency) and exactly one terminal message (``result`` or ``error``).

The worker can serve **several** payloads in a row (the process stays alive and
waits for the next line on stdin).  That is what makes the warm worker pool
possible: the interpreter start up and the imports are paid once per worker
instead of once per execution.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
from typing import Any

from .protocol import ERROR_INTERNAL, dependency_message, error_message
from .worker import execute_tool


def _serve(payload: dict[str, Any]) -> None:
    """Run one tool and stream its messages to stdout."""

    messages: "queue.Queue[dict[str, Any]]" = queue.Queue()

    def reporter(dependency_type: str, relative_path: str, fingerprint: str) -> None:
        messages.put(
            dependency_message(dependency_type, relative_path, fingerprint, 0)
        )

    def runner() -> None:
        try:
            message = execute_tool(payload, reporter)
        except Exception as exc:  # pragma: no cover - defensive
            import traceback

            message = error_message(
                ERROR_INTERNAL, f"{type(exc).__name__}: {exc}", traceback.format_exc(), 0.0
            )
        messages.put(message)

    thread = threading.Thread(target=runner, name="agentfuse-tool", daemon=True)
    thread.start()

    stdout = sys.stdout
    while True:
        message = messages.get()
        stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
        stdout.flush()
        if message.get("t") in ("result", "error"):
            break
    thread.join(timeout=1.0)


def main(argv: list[str] | None = None) -> int:
    # The first message tells the parent which process really executes the
    # tool.  On some installations (a virtual environment on Windows) the
    # process that is started is a launcher that spawns the real interpreter
    # as a child - the parent has to know the real pid in order to terminate
    # the worker reliably.
    sys.stdout.write(
        json.dumps({"t": "hello", "pid": os.getpid()}, ensure_ascii=False) + "\n"
    )
    sys.stdout.flush()

    stdin = sys.stdin
    while True:
        line = stdin.readline()
        if not line:
            return 0
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:  # pragma: no cover - defensive
            sys.stderr.write(f"invalid worker payload: {exc}\n")
            return 2
        _serve(payload)


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
