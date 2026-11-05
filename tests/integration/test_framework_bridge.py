import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agentfuse.adapters.external_adapter import ExternalAdapter, ProtocolError
from agentfuse.scenarios.common import wait_for_running_execution
from agentfuse.sharing.result_delivery import compute_result_hash


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
        first.result["files"][0]["path"] = "modified.py"
        assert second.result["files"][0]["path"] == "src/alpha.py"
        assert compute_result_hash(second.result) == second.result_hash
    with pytest.raises(ProtocolError, match='unknown invocation'):
        await adapter.call_tool(invocation.invocation_id, tool['name'], {'files': ['src/alpha.py']})
    events = app.event_bus.history(workflow.workflow_id)
    received = [event.logical_call_id for event in events if event.event_type == 'FRAMEWORK_RESULT_RECEIVED']
    assert sorted(received) == sorted([first.logical_call_id, second.logical_call_id])
    assert invocation.status == 'COMPLETED'
    workflow.finish()


async def test_unconfirmed_labels_require_an_explicit_handle_for_reuse(app):
    workflow = app.create_workflow("framework")
    adapter = ExternalAdapter(workflow)
    ambiguous = await adapter.bind_agent("same-label", identity_confirmed=False)
    async with adapter.invocation("same-label") as first, adapter.invocation("same-label") as second:
        assert len({ambiguous.agent_id, first.agent_id, second.agent_id}) == 3
    async with adapter.invocation(ambiguous.agent_id) as explicit:
        assert explicit.agent_id == ambiguous.agent_id
    confirmed = await adapter.bind_agent("same-label")
    async with adapter.invocation("same-label") as first, adapter.invocation("same-label") as second:
        assert first.agent_id == second.agent_id == confirmed.agent_id


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


async def test_protocol_call_cancelled_by_runtime_gets_a_reply(app, snapshot):
    workflow = app.create_workflow("framework", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    adapter = ExternalAdapter(workflow)
    async with adapter.invocation() as invocation:
        call = asyncio.create_task(adapter._reply({
            "type": "call", "request_id": 7, "invocation_id": invocation.invocation_id,
            "tool": "fault_injector", "arguments": {"mode": "sleep", "seconds": 15},
        }))
        execution = await wait_for_running_execution(app.event_bus, workflow.workflow_id)
        assert await app.coordinator.cancel_logical_call(execution.logical_call_id, "test cancellation")
        assert await call == {"type": "error", "request_id": 7, "code": "tool_cancelled",
                              "message": "test cancellation"}
        assert (await adapter._reply({"type": "discover"}))["type"] == "catalogue"


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
    assert result['call']['logical_call_id'] != result['second_call']['logical_call_id']
    assert result['call']['physical_execution_id'] == result['second_call']['physical_execution_id']
