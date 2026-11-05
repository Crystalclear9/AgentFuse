from typing import TypedDict
import pytest

pytest.importorskip('langgraph')
from langgraph.graph import StateGraph, START, END
from agentfuse.adapters.langgraph_adapter import LangGraphAdapter


class State(TypedDict, total=False):
    left: dict
    right: dict


async def test_real_langgraph_parallel_nodes_share_registered_computation(app, snapshot):
    workflow = app.create_workflow('langgraph', default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    adapter = LangGraphAdapter(workflow)
    try:
        assert 'ast_analyzer' in adapter.discover_nodes()
        assert any(t['name'] == 'ast_analyzer' and t['input_schema'] for t in adapter.catalogue())
        graph = StateGraph(State)
        for name in ('left', 'right'):
            graph.add_node(name, adapter.tool_node('ast_analyzer',
                arguments={'files': ['src/alpha.py']}, result_key=name))
            graph.add_edge(START, name)
            graph.add_edge(name, END)
        result = await graph.compile().ainvoke({})
        assert result['left'] == result['right']
        calls = app.coordinator.active_calls(workflow.workflow_id)
        assert len(calls) == 2
        assert len({c.physical_execution_id for c in calls}) == 1
        assert len({c.agent_id for c in calls}) == 2
        assert all(c.invocation_id for c in calls)
    finally:
        adapter.close()
        workflow.finish()


async def test_langgraph_rejects_unknown_tool_before_running(app, snapshot):
    workflow = app.create_workflow('langgraph', default_snapshot_id=snapshot.snapshot_id)
    adapter = LangGraphAdapter(workflow)
    try:
        with pytest.raises(KeyError):
            adapter.tool_node('not-registered')
    finally:
        adapter.close()
