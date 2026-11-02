"""An adapter for a **foreign** multi-agent framework.

The scripted adapter of this package is a convenience for the demo.  This
module is the second implementation of the adapter contract: it binds an agent
that lives in **another process** and speaks a line based JSON protocol over
its standard input and output.

Why a second implementation matters (requirement 3.1 and 21.5):

* the runtime must not depend on the internals of a framework.  A framework
  that cannot be imported at all still has to be able to take part in the
  sharing;
* the identity of an agent is established at an **observed boundary**, not by
  a convention: the external framework only sends the external identity it
  happens to know, the runtime creates the internal identity;
* every relation in the execution graph comes from an event that really
  happened - the adapter never invents an edge.

The protocol is newline delimited JSON on ``stdin`` and ``stdout``:

.. code-block:: text

    -> {"type": "bind", "external_identity": "planner-1"}
    <- {"type": "bound", "agent_id": "agent-..."}
    -> {"type": "invoke", "agent_id": "agent-...", "parent_invocation_id": null}
    <- {"type": "invoked", "invocation_id": "inv-..."}
    -> {"type": "call", "invocation_id": "inv-...", "tool": "ast_analyzer",
        "arguments": {...}, "snapshot_id": "snap-..."}
    <- {"type": "result", "logical_call_id": "call-...", "result": {...}}
    -> {"type": "end", "invocation_id": "inv-...", "status": "COMPLETED"}

The protocol is deliberately small - it is the **minimum** an external
framework has to implement.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from ..core.agent_context import AgentHandle, InvocationHandle
from ..core.state import EventType
from .base import AgentAdapter


class ProtocolError(RuntimeError):
    """The external framework sent something the adapter cannot use."""


class ExternalAdapter(AgentAdapter):
    """An adapter that is driven from **outside** the process.

    The adapter does not run any agent itself: it exposes the boundary and
    waits for the events of the external framework.  A test (or a real
    framework) can therefore drive it either directly through the methods or
    through :meth:`serve`, which reads the protocol from a stream.
    """

    def __init__(self, workflow: Any) -> None:
        super().__init__(workflow)
        #: the external framework may not know the internal ids - they are
        #: mapped here.  The key is the identity the framework uses.
        self._agents_by_external: dict[str, AgentHandle] = {}
        self._invocations: dict[str, InvocationHandle] = {}
        self._calls: dict[str, str] = {}
        #: the calls that were really observed - the events are the only
        #: source of the relations between the framework and the runtime
        self._observed_calls: list[dict[str, Any]] = []
        self._unsubscribe = self.event_bus.subscribe(self._observe)

    def _observe(self, event: Any) -> None:
        """Remember the calls that the runtime really published."""

        if getattr(event, "event_type", None) == EventType.TOOL_CALL_REQUESTED:
            self._observed_calls.append(
                {
                    "logical_call_id": getattr(event, "logical_call_id", None),
                    "invocation_id": getattr(event, "invocation_id", None),
                    "agent_id": getattr(event, "agent_id", None),
                }
            )

    # ------------------------------------------------------------------
    # the adapter contract
    # ------------------------------------------------------------------
    async def bind_agent(
        self,
        external_identity: str | None = None,
        agent_description: str | None = None,
        registration_source: str | None = None,
        metadata: dict[str, Any] | None = None,
        identity_confirmed: bool = True,
    ) -> AgentHandle:
        """Bind the agent of the external framework.

        The framework does not need a stable id: the internal identity is
        created here and the external identity is only a **label**.  If the
        framework cannot confirm the identity, the handle stays in the
        ``UNDETERMINED`` state and the runtime never merges it with another
        agent.
        """

        handle = self.workflow.bind_agent(
            external_identity=external_identity,
            agent_description=agent_description,
            registration_source=registration_source,
            metadata=metadata,
            identity_confirmed=identity_confirmed,
        )
        if external_identity:
            self._agents_by_external[external_identity] = handle
        return handle

    async def begin_invocation(
        self,
        agent_id: str,
        parent_invocation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InvocationHandle:
        handle = self.workflow.begin_invocation(
            agent_id, parent_invocation_id=parent_invocation_id, metadata=metadata
        )
        self._invocations[handle.invocation_id] = handle
        return handle

    async def submit_tool_call(
        self,
        invocation_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        snapshot_id: str | None = None,
    ) -> Any:
        invocation = self.workflow.invocations.get(invocation_id)
        agent_id = invocation.agent_id if invocation else None
        workflow = self.workflow
        resolved_snapshot = snapshot_id or workflow.default_snapshot_id
        if resolved_snapshot is None:
            raise ValueError("no workspace snapshot available for the tool call")
        return await self.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            invocation_id=invocation_id,
            agent_id=agent_id,
            tool_name=tool_name,
            arguments=arguments,
            snapshot_id=resolved_snapshot,
        )

    async def deliver_result(self, invocation_id: str, logical_call_id: str, result: Any) -> None:
        """Record that the *framework* received the result.

        The authoritative delivery event is published by the runtime; the
        adapter only records the framework side boundary with its own event
        type, so the delivery can never be counted twice.
        """

        invocation = self.workflow.invocations.get(invocation_id)
        self.workflow.event_bus.publish(
            EventType.FRAMEWORK_RESULT_RECEIVED,
            workflow_id=self.workflow.workflow_id,
            agent_id=invocation.agent_id if invocation else None,
            invocation_id=invocation_id,
            logical_call_id=logical_call_id,
            metadata={"delivered_by": "external-adapter"},
        )

    async def end_invocation(
        self, invocation_id: str, status: str = "COMPLETED"
    ) -> None:
        self.workflow.end_invocation(invocation_id, status=status)
        self._invocations.pop(invocation_id, None)

    async def record_event(self, event_type: str | EventType, **fields: Any) -> Any:
        workflow = self.workflow
        return workflow.event_bus.publish(
            event_type,
            workflow_id=fields.pop("workflow_id", workflow.workflow_id),
            agent_id=fields.pop("agent_id", None),
            invocation_id=fields.pop("invocation_id", None),
            logical_call_id=fields.pop("logical_call_id", None),
            physical_execution_id=fields.pop("physical_execution_id", None),
            parent_event_id=fields.pop("parent_event_id", None),
            metadata=fields.pop("metadata", None) or fields,
        )

    # ------------------------------------------------------------------
    # the boundary of a foreign framework
    # ------------------------------------------------------------------
    async def handle(self, message: dict[str, Any]) -> dict[str, Any]:
        """Handle one message of the external framework.

        The return value is the answer that is written back to the framework.
        Every message produces **observed** events - nothing is invented.
        """

        kind = str(message.get("type", ""))
        if kind == "bind":
            handle = await self.bind_agent(
                external_identity=message.get("external_identity"),
                agent_description=message.get("description"),
                registration_source=message.get("source", "external-framework"),
                metadata=message.get("metadata"),
                identity_confirmed=bool(message.get("identity_confirmed", True)),
            )
            return {"type": "bound", "agent_id": handle.agent_id}

        if kind == "invoke":
            agent_id = self._resolve_agent(message.get("agent_id"))
            invocation = await self.begin_invocation(
                agent_id,
                parent_invocation_id=message.get("parent_invocation_id"),
                metadata=message.get("metadata"),
            )
            return {"type": "invoked", "invocation_id": invocation.invocation_id}

        if kind == "call":
            invocation_id = str(message.get("invocation_id", ""))
            if invocation_id not in self._invocations:
                raise ProtocolError(f"unknown invocation: {invocation_id}")
            result = await self.submit_tool_call(
                invocation_id,
                str(message.get("tool", "")),
                dict(message.get("arguments") or {}),
                message.get("snapshot_id"),
            )
            logical_call_id = self._last_logical_call_id(invocation_id)
            await self.deliver_result(invocation_id, logical_call_id, result)
            return {
                "type": "result",
                "logical_call_id": logical_call_id,
                "result": result.to_dict() if hasattr(result, "to_dict") else result,
            }

        if kind == "end":
            invocation_id = str(message.get("invocation_id", ""))
            await self.end_invocation(invocation_id, status=str(message.get("status", "COMPLETED")))
            return {"type": "ended", "invocation_id": invocation_id}

        raise ProtocolError(f"unknown message type: {kind!r}")

    def _resolve_agent(self, agent_id: str | None) -> str:
        """The internal agent id for an external one.

        The framework may use the external identity or the internal id - both
        have to work, because a framework that only learns the internal id
        later must not be rejected.
        """

        if not agent_id:
            raise ProtocolError("the message has no agent")
        handle = self._agents_by_external.get(agent_id)
        if handle is not None:
            return handle.agent_id
        if agent_id in self.workflow.agents:
            return agent_id
        raise ProtocolError(f"unknown agent: {agent_id}")

    def _last_logical_call_id(self, invocation_id: str) -> str:
        """The id of the call that the runtime has just bound.

        The adapter does not get the id from the coordinator - it reads it
        from the **events**, because the events are the boundary that both
        sides agree on (requirement 5.3: a relation is only real when it was
        observed).
        """

        for event in reversed(self._observed_calls):
            if event.get("invocation_id") == invocation_id:
                return str(event.get("logical_call_id"))
        raise ProtocolError(f"no call was recorded for the invocation {invocation_id}")

    # ------------------------------------------------------------------
    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve the protocol until the framework closes the stream."""

        while True:
            line = await reader.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError as exc:
                answer = {"type": "error", "message": f"invalid json: {exc}"}
            else:
                try:
                    answer = await self.handle(message)
                except ProtocolError as exc:
                    answer = {"type": "error", "message": str(exc)}
            writer.write((json.dumps(answer, ensure_ascii=False) + "\n").encode("utf-8"))
            await writer.drain()


def _serve_stdio(adapter: ExternalAdapter) -> None:
    """Run the protocol on the standard input and output.

    The reader runs in a thread: the event loop of Windows cannot attach to
    a pipe of the standard input (``connect_read_pipe`` is missing on the
    proactor loop), and the protocol has to work on every platform.
    """

    import queue
    import threading

    async def main() -> None:
        loop = asyncio.get_running_loop()
        incoming: "queue.Queue[str | None]" = queue.Queue()
        pending: asyncio.Queue = asyncio.Queue()

        def read_lines() -> None:
            try:
                for line in sys.stdin:
                    incoming.put(line)
            except Exception:  # pragma: no cover - defensive
                pass
            finally:
                incoming.put(None)

        threading.Thread(target=read_lines, daemon=True).start()

        def pump() -> None:
            while True:
                line = incoming.get()
                loop.call_soon_threadsafe(pending.put_nowait, line)
                if line is None:
                    return

        threading.Thread(target=pump, daemon=True).start()

        while True:
            line = await pending.get()
            if line is None:
                break
            text = line.strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError as exc:
                answer = {"type": "error", "message": f"invalid json: {exc}"}
            else:
                try:
                    answer = await adapter.handle(message)
                except ProtocolError as exc:
                    answer = {"type": "error", "message": str(exc)}
            sys.stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    asyncio.run(main())


def main(argv: list[str] | None = None) -> int:
    """Entry point of the external framework bridge.

    ``python -m agentfuse.adapters.external_adapter --home <dir>`` starts the
    protocol on stdin/stdout.  The framework writes its messages as lines of
    JSON and reads the answers the same way.
    """

    import argparse

    from ..app import AgentFuseApp
    from ..core.state import RuntimeMode
    from ..settings import load_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default=None, help="the AgentFuse home directory")
    parser.add_argument("--workflow", default="external", help="the name of the workflow")
    arguments = parser.parse_args(argv)

    settings = load_settings(home=arguments.home)
    settings.ensure_directories()
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    workflow = app.create_workflow(arguments.workflow)
    # The external framework does not manage the workspace - the runtime
    # offers it a snapshot of the current state.  Without it the framework
    # could not call a tool at all.
    from ..scenarios.common import prepare_base_snapshot

    base = prepare_base_snapshot(app)
    workflow.default_snapshot_id = base.snapshot_id
    workflow.start()
    adapter = ExternalAdapter(workflow)
    try:
        _serve_stdio(adapter)
    finally:
        workflow.finish()
        app.close_sync()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
