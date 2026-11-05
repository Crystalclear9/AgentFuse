import pytest

pytest.importorskip('langchain_core.tools')
from langchain_core.tools import StructuredTool

from agentfuse.adapters.external_adapter import ProtocolError
from agentfuse.adapters.langchain_adapter import LangChainAdapter


async def test_langchain_structured_tool_runs_inside_observed_activation(app, snapshot):
    workflow = app.create_workflow('langchain', default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    adapter = LangChainAdapter(workflow)
    async with adapter.invocation('analyst', registration_source='langchain') as invocation:
        tool = adapter.structured_tool(invocation.invocation_id, 'ast_analyzer')
        assert isinstance(tool, StructuredTool)
        assert 'files' in tool.args
        output = await tool.ainvoke({'files': ['src/alpha.py']})
        assert output['files'][0]['path'] == 'src/alpha.py'
        calls = app.coordinator.active_calls(workflow.workflow_id)
        assert len(calls) == 1 and calls[0].invocation_id == invocation.invocation_id
    with pytest.raises(ProtocolError, match='unknown invocation'):
        await tool.ainvoke({'files': ['src/alpha.py']})
    workflow.finish()
