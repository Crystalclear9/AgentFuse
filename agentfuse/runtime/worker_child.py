"""Entry point of the tool worker process.

The parent starts the worker with::

    python -m agentfuse.runtime.worker_child

The payload is read from stdin (JSON), the protocol messages are written to
stdout (newline delimited JSON).  Because the tool runs in a worker *thread*
of this process, the dependency events can be streamed to the parent while the
tool is still computing.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
from .protocol import ERROR_INTERNAL, error_message
from .worker import execute_tool


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive
        sys.stderr.write(f"invalid worker payload: {exc}\n")
        return 2

    messages: "queue.Queue[dict[str, Any]]" = queue.Queue()

    def reporter(dependency_type: str, relative_path: str, fingerprint: str) -> None:
        from .protocol import dependency_message

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
        try:
            stdout.write(json.dumps(message, ensure_ascii=False, default=str) + "\n")
            stdout.flush()
        except BrokenPipeError:  # pragma: no cover - the parent vanished
            return 1
        if message.get("t") in ("result", "error"):
            break
    thread.join(timeout=1.0)
    return 0


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
