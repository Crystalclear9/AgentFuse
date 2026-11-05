"""Expose registered AgentFuse tools as optional LangChain StructuredTools."""
from copy import deepcopy

from .external_adapter import ExternalAdapter


class LangChainAdapter(ExternalAdapter):
    def structured_tool(self, invocation_id: str, tool_name: str, *,
                        tool_version: str | None = None, snapshot_id: str | None = None,
                        timeout_seconds: float | None = None):
        """Export one tool bound to an observed invocation and registry version."""
        from langchain_core.tools import StructuredTool

        spec = self.coordinator.registry.get(tool_name, tool_version)

        async def execute(**arguments):
            result = await self.call_tool(invocation_id, spec.tool_name, arguments,
                tool_version=spec.tool_version, snapshot_id=snapshot_id,
                timeout_seconds=timeout_seconds)
            return result.result

        return StructuredTool.from_function(
            coroutine=execute, name=spec.tool_name, description=spec.description or spec.tool_name,
            args_schema=deepcopy(spec.input_schema), infer_schema=False,
        )
