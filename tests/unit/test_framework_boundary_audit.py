import importlib.util
from pathlib import Path


def test_framework_audit_covers_runtime_and_allows_only_optional_adapter(tmp_path):
    source = Path(__file__).resolve().parents[2] / 'scripts/audit_requirements.py'
    spec = importlib.util.spec_from_file_location('framework_boundary_audit', source)
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    runtime = tmp_path / 'agentfuse/runtime'
    adapters = tmp_path / 'agentfuse/adapters'
    runtime.mkdir(parents=True)
    adapters.mkdir(parents=True)
    (runtime / 'bad.py').write_text('from langgraph.graph import StateGraph\n')
    (adapters / 'langgraph_adapter.py').write_text('from langchain_core.runnables import RunnableConfig\n')
    (runtime / 'comment.py').write_text('# langchain is not imported here\n')
    assert audit._framework_imports(tmp_path) == ['agentfuse/runtime/bad.py:langgraph.graph']
