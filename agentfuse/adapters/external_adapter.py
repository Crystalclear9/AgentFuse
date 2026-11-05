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
from contextlib import asynccontextmanager
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any

from ..core.agent_context import AgentHandle, InvocationHandle
from ..core.state import EventType, InvocationStatus
from ..runtime.coordinator import ToolCallResult, ToolExecutionError
from ..tools.base import ToolInputError
from ..workspace.snapshots import SnapshotError
from .base import AgentAdapter


class ProtocolError(RuntimeError):
    """The external framework sent something the adapter cannot use."""


def _string(message, key, *, optional=False):
    value = message.get(key)
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{key} must be a nonempty string")
    return value


def _object(message, key):
    value = message.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProtocolError(f"{key} must be an object")
    return value


class ExternalAdapter(AgentAdapter):
    """Bridge framework-controlled activations and tools over Python or JSON Lines."""

    def __init__(self, workflow: Any, auto_bind: bool = True) -> None:
        super().__init__(workflow)
        #: The adapter may bind the agents **by itself**: a framework that
        #: does not know the internal ids only sends the identity it happens
        #: to use.  The identity of such an agent stays ``UNDETERMINED`` -
        #: the runtime must never merge it with another one.
        self.auto_bind = bool(auto_bind)
        #: the external framework may not know the internal ids - they are
        #: mapped here.  The key is the identity the framework uses.
        self._agents_by_external: dict[str, AgentHandle] = {}
        self._invocations: dict[str, InvocationHandle] = {}

    def catalogue(self) -> list[dict[str, Any]]:
        """Return independent schema copies suitable for framework tool registration."""
        return [dict(name=spec.tool_name, version=spec.tool_version,
                     description=spec.description, input_schema=deepcopy(spec.input_schema),
                     output_schema=deepcopy(spec.output_schema))
                for spec in self.coordinator.registry.list_tools()]

    @asynccontextmanager
    async def invocation(self, agent_id: str | None = None, *,
                         parent_invocation_id: str | None = None,
                         metadata: dict[str, Any] | None = None,
                         registration_source: str = "external-framework",
                         identity_confirmed: bool = False):
        """Scope a framework activation and record its terminal status on exit."""
        if agent_id is None:
            handle = await self.bind_agent(registration_source=registration_source,
                                           identity_confirmed=False)
            agent_id = handle.agent_id
        else:
            agent_id = await self._resolve_agent(agent_id, registration_source,
                                                 identity_confirmed=identity_confirmed)
        handle = await self.begin_invocation(agent_id, parent_invocation_id, metadata)
        try:
            yield handle
        except asyncio.CancelledError:
            await self.end_invocation(handle.invocation_id, InvocationStatus.CANCELLED)
            raise
        except BaseException:
            await self.end_invocation(handle.invocation_id, InvocationStatus.FAILED)
            raise
        else:
            await self.end_invocation(handle.invocation_id)

    async def call_tool(self, invocation_id: str, tool_name: str, arguments: dict[str, Any], *,
                        snapshot_id: str | None = None, timeout_seconds: float | None = None,
                        tool_version: str | None = None) -> ToolCallResult:
        """Execute a registered tool and acknowledge its exact logical result."""
        result = await self.submit_tool_call(invocation_id, tool_name, arguments, snapshot_id,
                                             timeout_seconds, tool_version=tool_version)
        await self.deliver_result(invocation_id, result.logical_call_id, result)
        return result

    def close(self) -> None:
        """Record any abandoned activations after the client has stopped making calls."""
        for invocation_id in tuple(self._invocations):
            self.workflow.end_invocation(invocation_id, status=InvocationStatus.CANCELLED)
            del self._invocations[invocation_id]

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
        if external_identity and identity_confirmed:
            self._agents_by_external[external_identity] = handle
        return handle

    async def begin_invocation(
        self,
        agent_id: str,
        parent_invocation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> InvocationHandle:
        if agent_id not in self.workflow.agents:
            raise ProtocolError(f"unknown agent: {agent_id}")
        if parent_invocation_id is not None and parent_invocation_id not in self.workflow.invocations:
            raise ProtocolError(f"unknown parent invocation: {parent_invocation_id}")
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
        timeout_seconds: float | None = None,
        *,
        tool_version: str | None = None,
    ) -> ToolCallResult:
        invocation = self._invocations.get(invocation_id)
        if invocation is None:
            raise ProtocolError(f"unknown invocation: {invocation_id}")
        if timeout_seconds is not None and (type(timeout_seconds) not in (int, float)
                                            or not 0 < timeout_seconds <= sys.float_info.max):
            raise ProtocolError("timeout_seconds must be a finite positive number")
        workflow = self.workflow
        resolved_snapshot = snapshot_id or workflow.default_snapshot_id
        if resolved_snapshot is None:
            raise ValueError("no workspace snapshot available for the tool call")
        if resolved_snapshot.endswith((".", " ")) or any(char in resolved_snapshot for char in "/\\:*?[]\x00"):
            raise ProtocolError("snapshot_id must be a literal snapshot identifier")
        self.coordinator.snapshots.get(resolved_snapshot)
        return await self.coordinator.submit_tool_call(
            workflow_id=workflow.workflow_id,
            invocation_id=invocation_id,
            agent_id=invocation.agent_id,
            tool_name=tool_name,
            tool_version=tool_version,
            arguments=arguments,
            snapshot_id=resolved_snapshot,
            timeout_seconds=timeout_seconds,
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
        if status not in (InvocationStatus.COMPLETED, InvocationStatus.FAILED, InvocationStatus.CANCELLED):
            raise ProtocolError(f"invalid terminal invocation status: {status}")
        if invocation_id not in self._invocations:
            raise ProtocolError(f"unknown invocation: {invocation_id}")
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

        if not isinstance(message, dict):
            raise ProtocolError("request must be a JSON object")
        version = message.get("protocol_version", 1)
        if type(version) is not int or version != 1:
            raise ProtocolError("unsupported protocol_version; use 1")
        kind = _string(message, "type")
        if kind == "discover":
            return {"type": "catalogue", "protocol_version": 1, "tools": self.catalogue()}

        if kind == "bind":
            confirmed = message.get("identity_confirmed", True)
            if type(confirmed) is not bool:
                raise ProtocolError("identity_confirmed must be a boolean")
            handle = await self.bind_agent(
                external_identity=_string(message, "external_identity", optional=True),
                agent_description=_string(message, "description", optional=True),
                registration_source=_string(message, "source", optional=True) or "external-framework",
                metadata=_object(message, "metadata"),
                identity_confirmed=confirmed,
            )
            return {"type": "bound", "agent_id": handle.agent_id}

        if kind == "snapshot":
            # the framework asks which workspace it may use - the runtime
            # owns the snapshots, the framework only names them
            snapshot_id = self.workflow.default_snapshot_id
            if not snapshot_id:
                raise ProtocolError("the workflow has no workspace snapshot")
            return {"type": "snapshot", "snapshot_id": snapshot_id}

        if kind == "invoke":
            agent_id = await self._resolve_agent(_string(message, "agent_id"))
            invocation = await self.begin_invocation(
                agent_id,
                parent_invocation_id=_string(message, "parent_invocation_id", optional=True),
                metadata=_object(message, "metadata"),
            )
            return {"type": "invoked", "invocation_id": invocation.invocation_id,
                    "agent_id": invocation.agent_id}

        if kind == "call":
            result = await self.call_tool(
                _string(message, "invocation_id"), _string(message, "tool"),
                _object(message, "arguments"),
                snapshot_id=_string(message, "snapshot_id", optional=True),
                tool_version=_string(message, "tool_version", optional=True),
                timeout_seconds=message.get("timeout_seconds"),
            )
            return {
                "type": "result",
                "logical_call_id": result.logical_call_id,
                "result": result.to_dict(),
                "output": result.result,
            }

        if kind == "end":
            invocation_id = _string(message, "invocation_id")
            await self.end_invocation(invocation_id, status=message.get("status", "COMPLETED"))
            return {"type": "ended", "invocation_id": invocation_id}

        raise ProtocolError(f"unknown message type: {kind!r}")

    async def _resolve_agent(self, agent_id: str | None, registration_source="external-framework",
                             *, identity_confirmed=False) -> str:
        """The internal agent id for an external one.

        The framework may use the external identity or the internal id - both
        have to work, because a framework that only learns the internal id
        later must not be rejected.

        With ``auto_bind`` an unknown label establishes a new identity.
        Only identities confirmed by the framework are indexed by label.
        """

        if not agent_id:
            raise ProtocolError("the message has no agent")
        if agent_id in self.workflow.agents:
            return agent_id
        handle = self._agents_by_external.get(agent_id)
        if handle is not None:
            return handle.agent_id
        if not self.auto_bind:
            raise ProtocolError(f"unknown agent: {agent_id}")
        bound = await self.bind_agent(
            external_identity=agent_id,
            agent_description="bound automatically by the external adapter",
            registration_source=registration_source,
            identity_confirmed=identity_confirmed,
        )
        return bound.agent_id

    async def _reply(self, message: Any) -> dict[str, Any]:
        request_id = None
        try:
            if isinstance(message, dict):
                request_id = message.get("request_id")
                if request_id is not None and type(request_id) not in (str, int):
                    request_id = None
                    raise ProtocolError("request_id must be a string or integer")
            answer = await self.handle(message)
        except (ProtocolError, SnapshotError, ToolInputError, KeyError, ValueError) as exc:
            answer = {"type": "error", "code": "invalid_request", "message": str(exc)}
        except ToolExecutionError as exc:
            answer = {"type": "error", "code": "tool_failed", "kind": exc.kind,
                      "execution_id": exc.execution_id, "message": str(exc)}
        except asyncio.CancelledError as exc:
            if asyncio.current_task().cancelling():
                raise
            answer = {"type": "error", "code": "tool_cancelled", "message": str(exc)}
        if request_id is not None:
            answer["request_id"] = request_id
        return answer

    async def _serve_requests(self, receive, send) -> None:
        """Multiplex ID-bearing calls while keeping lifecycle and legacy requests ordered."""

        pending: dict[str | int, asyncio.Task] = {}
        write_lock = asyncio.Lock()

        async def write(answer):
            async with write_lock:
                await send((json.dumps(answer, ensure_ascii=False) + "\n").encode("utf-8"))

        async def run_call(request_id, message):
            try:
                await write(await self._reply(message))
            finally:
                pending.pop(request_id, None)

        async with asyncio.TaskGroup() as tasks:
            while line := await receive():
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    await write({"type": "error", "code": "invalid_json", "message": "invalid UTF-8 JSON"})
                    continue
                request_id = message.get("request_id") if isinstance(message, dict) else None
                if (isinstance(message, dict) and message.get("type") == "call"
                        and type(request_id) in (str, int)):
                    if request_id in pending:
                        await write({"type": "error", "code": "duplicate_request_id",
                                     "message": "request_id is already in flight"})
                        continue
                    if len(pending) >= 32:
                        await asyncio.wait(tuple(pending.values()), return_when=asyncio.FIRST_COMPLETED)
                    pending[request_id] = tasks.create_task(run_call(request_id, message))
                else:
                    # Calls submitted before an end (or an ID-less request)
                    # finish first, even when all lines were sent together.
                    if pending:
                        await asyncio.wait(tuple(pending.values()))
                    await write(await self._reply(message))

    # ------------------------------------------------------------------
    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Serve the protocol until the framework closes the stream."""

        async def send(data):
            writer.write(data)
            await writer.drain()

        try:
            await self._serve_requests(reader.readline, send)
        finally:
            self.close()


async def _serve_stdio(adapter: ExternalAdapter) -> None:
    """Run the protocol on the standard input and output.

    The reader runs in a thread: the event loop of Windows cannot attach to
    a pipe of the standard input (``connect_read_pipe`` is missing on the
    proactor loop), and the protocol has to work on every platform.
    """

    import threading

    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[bytes] = asyncio.Queue()
    capacity = threading.BoundedSemaphore(32)
    stopped = threading.Event()

    def read_lines() -> None:
        while not stopped.is_set():
            if not capacity.acquire(timeout=0.1):
                continue
            try:
                line = sys.stdin.buffer.readline()
            except OSError:
                line = b""
            if stopped.is_set():
                return
            loop.call_soon_threadsafe(lines.put_nowait, line)
            if not line:
                return

    async def receive():
        line = await lines.get()
        capacity.release()
        return line

    def write(data):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    async def send(data):
        await asyncio.to_thread(write, data)

    threading.Thread(target=read_lines, daemon=True).start()
    try:
        await adapter._serve_requests(receive, send)
    finally:
        stopped.set()


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
    parser.add_argument("--workspace", help="directory to snapshot for the framework's tools")
    arguments = parser.parse_args(argv)

    settings = load_settings(home=arguments.home)
    source = Path(arguments.workspace).resolve() if arguments.workspace else None
    if source is not None:
        if not source.is_dir():
            parser.error('--workspace must point to an existing directory')
        if settings.home.resolve().is_relative_to(source):
            parser.error('--home must be outside --workspace')
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    async def run():
        try:
            if source is not None:
                base = app.snapshots.create_workspace(source)
            else:
                from ..scenarios.common import prepare_base_snapshot
                base = prepare_base_snapshot(app)
            workflow = app.create_workflow(arguments.workflow, default_snapshot_id=base.snapshot_id)
            workflow.start()
            adapter = ExternalAdapter(workflow)
            try:
                await _serve_stdio(adapter)
            finally:
                adapter.close()
                workflow.finish()
        finally:
            await app.close()
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
