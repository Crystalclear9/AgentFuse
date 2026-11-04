"""A multi agent framework that knows **nothing** about AgentFuse.

This program is the proof of requirement 3.1 and 21.5: an external framework
that cannot be imported by AgentFuse takes part in the shared execution.  It

* runs in its **own** process,
* has its own agent objects, its own loop and its own idea of what a tool
  call is,
* knows nothing about ``AgentAdapter``, ``RuntimeCoordinator`` or the event
  protocol of AgentFuse - it only writes lines of JSON to its standard output
  and reads lines of JSON from its standard input.

The protocol is the one of ``agentfuse/adapters/external_adapter.py``:

.. code-block:: text

    -> {"type": "bind", "external_identity": "planner-1"}
    <- {"type": "bound", "agent_id": "agent-..."}
    -> {"type": "invoke", "agent_id": "planner-1"}
    <- {"type": "invoked", "invocation_id": "inv-..."}
    -> {"type": "call", "invocation_id": "inv-...", "tool": "ast_analyzer",
        "arguments": {"files": ["src/alpha.py"]}, "snapshot_id": "snap-..."}
    <- {"type": "result", "logical_call_id": "call-...", "result": {...}}
    -> {"type": "end", "invocation_id": "inv-...", "status": "COMPLETED"}

Run it against a runtime::

    python -m agentfuse.adapters.external_adapter --home .agentfuse/example \
        < examples/external_framework/agent_fwk.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class Bridge:
    """The minimal client of the protocol."""

    def __init__(self) -> None:
        self.agent_id: str | None = None
        self.invocation_id: str | None = None
        self.snapshot_id: str | None = None
        #: the streams of the protocol - they are the pipes of the runtime
        self.stdin: Any = sys.stdin
        self.stdout: Any = sys.stdout

    def send(self, message: dict[str, Any]) -> dict[str, Any]:
        """Write one message and read the answer."""

        self.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.stdout.flush()
        line = self.stdin.readline()
        if not line:
            raise SystemExit("the runtime closed the connection")
        answer = json.loads(line)
        if answer.get("type") == "error":
            raise SystemExit(f"the runtime refused: {answer.get('message')}")
        return answer

    # -- the steps of the framework ---------------------------------------
    def ask_for_the_snapshot(self) -> str:
        """Ask the runtime which workspace the calls may use."""

        answer = self.send({"type": "snapshot"})
        self.snapshot_id = str(answer["snapshot_id"])
        return self.snapshot_id

    def bind(self, external_identity: str) -> str:
        answer = self.send({"type": "bind", "external_identity": external_identity})
        self.agent_id = str(answer["agent_id"])
        return self.agent_id

    def invoke(self) -> str:
        answer = self.send({"type": "invoke", "agent_id": self.agent_id})
        self.invocation_id = str(answer["invocation_id"])
        return self.invocation_id

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        answer = self.send(
            {
                "type": "call",
                "invocation_id": self.invocation_id,
                "tool": tool,
                "arguments": arguments,
                "snapshot_id": self.snapshot_id,
            }
        )
        return dict(answer.get("result") or {})

    def end(self) -> None:
        self.send({"type": "end", "invocation_id": self.invocation_id, "status": "COMPLETED"})


def start_the_runtime(home: str) -> subprocess.Popen:
    """Start the runtime of AgentFuse as a child process.

    The framework **integrates** the runtime: it starts it, talks to it over
    the protocol and stops it again.  The framework does not import anything of
    the runtime.
    """

    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return subprocess.Popen(
        [sys.executable, "-m", "agentfuse.adapters.external_adapter", "--home", home],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        bufsize=1,
        env=environment,
    )


def main() -> int:
    """The framework: it binds an agent, calls a tool and gets the result."""

    home = sys.argv[1] if len(sys.argv) > 1 else ".agentfuse/example-framework"
    runtime = start_the_runtime(home)
    assert runtime.stdin is not None and runtime.stdout is not None
    bridge = Bridge()
    bridge.stdout = runtime.stdin
    bridge.stdin = runtime.stdout
    try:
        # the framework asks the runtime for the snapshot of the current workspace
        bridge.ask_for_the_snapshot()
        bridge.bind("framework-planner")
        bridge.invoke()
        result = bridge.call("ast_analyzer", {"files": ["src/alpha.py"]})
        bridge.end()
    finally:
        # EOF asks the bridge to finish the workflow and flush SQLite. Killing
        # the process immediately after a result can discard queued writes.
        runtime.stdin.close()
        try:
            runtime.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            runtime.kill()
            runtime.wait(timeout=10)
        runtime.stdout.close()
    if runtime.returncode != 0:
        raise SystemExit(f"the runtime did not shut down cleanly: {runtime.returncode}")

    totals = result.get("totals") or {}
    print(
        f"  the framework received the result: "
        f"{totals.get('functions', 0)} functions in {totals.get('files', 0)} files",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
