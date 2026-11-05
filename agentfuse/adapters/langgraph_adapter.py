"""LangGraph nodes discovered from the runtime's versioned tool registry.

No model provider or LangSmith service is required. Node identity comes from
LangGraph's actual execution metadata, while parents must be explicitly given.
"""
from .external_adapter import ExternalAdapter


class LangGraphAdapter(ExternalAdapter):
    def tool_node(self, tool_name, *, arguments=None, result_key='result',
                  snapshot_id=None, node_name=None):
        # Import only when this optional integration is used.
        from langchain_core.runnables import RunnableConfig
        self.coordinator.registry.get(tool_name)

        async def node(state: dict, config: RunnableConfig):
            metadata = config.get('metadata', {})
            identity = node_name or metadata.get('langgraph_node') or tool_name
            parent = config.get('configurable', {}).get('agentfuse_parent_invocation_id')
            async with self.invocation(
                identity, parent_invocation_id=parent, registration_source='langgraph',
                metadata={'framework': 'langgraph', 'node': identity,
                          'step': metadata.get('langgraph_step')},
            ) as invocation:
                values = arguments(state) if callable(arguments) else arguments
                if values is None:
                    values = state.get('arguments', {})
                output = await self.call_tool(invocation.invocation_id, tool_name, dict(values),
                                              snapshot_id=snapshot_id)
                return {result_key: output.result}
        return node

    def discover_nodes(self):
        """Build nodes without handwritten wrappers for the registered tools."""
        return {name: self.tool_node(name) for name in self.coordinator.registry.tool_names()}
