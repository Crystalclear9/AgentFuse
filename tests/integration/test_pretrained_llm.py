import json
import os
from pathlib import Path
import pytest

from agentfuse.runtime.llama_backend import LlamaBackend


@pytest.fixture
def backend(tmp_path):
    executable = os.environ.get('AGENTFUSE_LLAMA_SERVER')
    model = os.environ.get('AGENTFUSE_LLM_MODEL')
    if not executable or not model:
        pytest.skip('set local llama.cpp executable and pretrained GGUF model paths')
    device = os.environ.get('AGENTFUSE_LLM_DEVICE')
    with LlamaBackend(executable, model, context_size=1024, gpu_layers=99 if device else 0,
                      device=device, log_path=tmp_path / 'llama.log') as runtime:
        yield runtime


def test_pretrained_native_kv_reuse_and_erase_preserve_generated_tokens(backend):
    prompt = 'The capital of France is'
    cold = backend.generate(prompt, 8, use_cache=False)
    warm = backend.generate(prompt, 8)
    assert cold['text'] and cold['tokens'] == warm['tokens']
    assert cold['reused_tokens'] == 0
    assert warm['reused_tokens'] > 0
    assert warm['evaluated_tokens'] < cold['evaluated_tokens']
    assert backend.clear()['n_erased'] > 0
    cleared = backend.generate(prompt, 8)
    assert cleared['reused_tokens'] == 0
    assert cleared['tokens'] == cold['tokens']
    with pytest.raises(ValueError, match='budget'):
        backend.generate('one ' * 1100, 8)
    print(json.dumps({'cold': cold, 'warm': warm, 'cleared': cleared}))


async def test_pretrained_language_agent_executes_real_tools_with_cached_decision(app, snapshot, backend):
    from agentfuse.adapters.language_model_agent import LanguageModelAgent
    from agentfuse.adapters.scripted_adapter import ScriptedAdapter
    workflow = app.create_workflow('pretrained-agent', default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    agent = LanguageModelAgent('analyst', backend, [
        {'tool': 'ast_analyzer', 'description': 'Parse Python functions and classes',
         'arguments': {'files': ['src/alpha.py']}},
        {'tool': 'source_index', 'description': 'List source files', 'arguments': {'root': 'src'}}])
    adapter = ScriptedAdapter(workflow)
    cold = await agent.run(adapter, 'Analyze Python functions in alpha.py',
                           snapshot_id=snapshot.snapshot_id, use_cache=False)
    warm = await agent.run(adapter, 'Analyze Python functions in alpha.py',
                           snapshot_id=snapshot.snapshot_id)
    assert cold.result == warm.result
    events = [e for e in app.event_bus.history(workflow.workflow_id) if e.event_type == 'INFERENCE_COMPLETED']
    assert len(events) == 2
    assert events[-1].metadata['reused_tokens'] > 0
    assert events[-1].metadata['selected_tool'] == events[0].metadata['selected_tool']
    assert events[-1].metadata['backend'] == 'llama.cpp'
    workflow.finish()


async def test_configured_local_llm_api_reuses_and_erases_native_cache(settings, backend):
    import httpx
    from agentfuse.api.app import create_app
    settings = settings.with_overrides(raw={**settings.raw, 'inference': {
        'backend': 'llama.cpp', 'executable': str(backend.executable),
        'model_path': str(backend.model), 'context_size': 1024,
        'gpu_layers': backend.gpu_layers, 'device': backend.device}})
    api = create_app(settings)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://local') as client:
            prompt = {'prompt': 'The capital of France is', 'max_new_tokens': 8}
            first = await client.post('/api/extensions/llm', json={**prompt, 'use_cache': False})
            second = await client.post('/api/extensions/llm', json=prompt)
            assert first.status_code == second.status_code == 200
            assert first.json()['tokens'] == second.json()['tokens']
            assert second.json()['reused_tokens'] > 0
            assert (await client.delete('/api/extensions/llm/cache')).json()['n_erased'] > 0
            assert (await client.get('/api/extensions/capabilities')).json()['inference_backend'] == 'llama.cpp'
    finally:
        process = api.state.runtime.llm.process
        await api.state.runtime.close()
        if process is not None:
            assert process.poll() is not None
