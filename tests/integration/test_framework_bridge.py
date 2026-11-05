import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agentfuse.adapters.external_adapter import ExternalAdapter, ProtocolError


async def test_parallel_framework_calls_acknowledge_each_logical_result(app, snapshot):
    workflow = app.create_workflow('framework', default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    adapter = ExternalAdapter(workflow)
    tool = next(item for item in adapter.catalogue() if item['name'] == 'ast_analyzer')
    tool['input_schema'].clear()
    assert next(item for item in adapter.catalogue() if item['name'] == 'ast_analyzer')['input_schema']
    async with adapter.invocation('planner') as invocation:
        first, second = await asyncio.gather(*(
            adapter.call_tool(invocation.invocation_id, tool['name'], {'files': ['src/alpha.py']},
                              tool_version=tool['version']) for _ in range(2)
        ))
        assert first.result == second.result
        assert first.physical_execution_id == second.physical_execution_id
        assert first.logical_call_id != second.logical_call_id
    with pytest.raises(ProtocolError, match='unknown invocation'):
        await adapter.call_tool(invocation.invocation_id, tool['name'], {'files': ['src/alpha.py']})
    events = app.event_bus.history(workflow.workflow_id)
    received = [event.logical_call_id for event in events if event.event_type == 'FRAMEWORK_RESULT_RECEIVED']
    assert sorted(received) == sorted([first.logical_call_id, second.logical_call_id])
    assert invocation.status == 'COMPLETED'
    workflow.finish()


async def test_framework_activation_records_failure_cancellation_and_explicit_parent(app):
    workflow = app.create_workflow('framework')
    workflow.start()
    adapter = ExternalAdapter(workflow)
    with pytest.raises(ProtocolError, match='unknown parent invocation'):
        async with adapter.invocation('planner', parent_invocation_id='invented'):
            pytest.fail('an invalid parent must be rejected')
    async with adapter.invocation('planner') as parent:
        with pytest.raises(ValueError):
            async with adapter.invocation(parent_invocation_id=parent.invocation_id) as failed:
                raise ValueError('framework failed')
        with pytest.raises(asyncio.CancelledError):
            async with adapter.invocation(parent_invocation_id=parent.invocation_id) as cancelled:
                raise asyncio.CancelledError
        assert failed.parent_invocation_id == cancelled.parent_invocation_id == parent.invocation_id
        assert failed.agent_id != cancelled.agent_id
        assert failed.status == 'FAILED'
        assert cancelled.status == 'CANCELLED'
    workflow.finish()


def test_node_client_discovers_and_runs_tools_on_its_own_workspace(tmp_path):
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node.js is needed for the cross-language client example')
    root = Path(__file__).resolve().parents[2]
    workspace = tmp_path / 'source'
    workspace.mkdir()
    (workspace / '分析.py').write_text('answer = 42\n', encoding='utf-8')
    nested_home = workspace / 'runtime-output'
    rejected = subprocess.run([sys.executable, '-m', 'agentfuse.adapters.external_adapter',
                               '--home', str(nested_home), '--workspace', str(workspace)],
                              cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert rejected.returncode != 0 and '--home must be outside --workspace' in rejected.stderr
    assert not nested_home.exists()
    run = subprocess.run([node, str(root / 'examples/external_framework/client.mjs'),
                          sys.executable, str(tmp_path / 'home'), str(workspace)],
                         cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=40)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result['tool'] == 'source_index'
    assert result['output']['file_count'] == 1
    assert result['output']['files'][0]['path'] == '分析.py'
    assert result['call']['logical_call_id']
