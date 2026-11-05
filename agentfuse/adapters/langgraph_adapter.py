"""LangGraph nodes discovered from the runtime's versioned tool registry.

No model provider or LangSmith service is required. Node identity comes from
LangGraph's actual execution metadata, while parents must be explicitly given.
"""
import asyncio

from .external_adapter import ExternalAdapter


class LangGraphAdapter(ExternalAdapter):
    def catalogue(self):
        return [dict(name=spec.tool_name, version=spec.tool_version,
                     description=spec.description, input_schema=spec.input_schema)
                for spec in self.coordinator.registry.list_tools()]

    def tool_node(self, tool_name, *, arguments=None, result_key='result',
                  snapshot_id=None, node_name=None):
        # Import only when this optional integration is used.
        from langchain_core.runnables import RunnableConfig
        self.coordinator.registry.get(tool_name)

        async def node(state: dict, config: RunnableConfig):
            metadata = config.get('metadata', {})
            identity = node_name or metadata.get('langgraph_node') or tool_name
            handle = self._agents_by_external.get(identity)
            if handle is None:
                handle = await self.bind_agent(identity, registration_source='langgraph',
                                               identity_confirmed=False)
            parent = config.get('configurable', {}).get('agentfuse_parent_invocation_id')
            invocation = await self.begin_invocation(handle.agent_id, parent_invocation_id=parent,
                metadata={'framework': 'langgraph', 'node': identity,
                          'step': metadata.get('langgraph_step')})
            try:
                values = arguments(state) if callable(arguments) else arguments
                if values is None:
                    values = state.get('arguments', {})
                output = await self.submit_tool_call(invocation.invocation_id, tool_name,
                    dict(values), snapshot_id=snapshot_id)
                await self.deliver_result(invocation.invocation_id, output.logical_call_id, output)
                await self.end_invocation(invocation.invocation_id)
                return {result_key: output.result}
            except asyncio.CancelledError:
                await self.end_invocation(invocation.invocation_id, status='CANCELLED')
                raise
            except BaseException:
                await self.end_invocation(invocation.invocation_id, status='FAILED')
                raise
        return node

    def discover_nodes(self):
        """Build nodes without handwritten wrappers for the registered tools."""
        return {name: self.tool_node(name) for name in self.coordinator.registry.tool_names()}

    def close(self):
        self._unsubscribe()
