"""Exercise optional extensions with real computation and real worker processes."""
import asyncio
import os
from pathlib import Path

import pytest

from agentfuse.runtime.inference import AttentionModel, InferenceRuntime, reference_weights
from agentfuse.runtime.prediction import PathPredictor
from agentfuse.runtime.remote import forward, pack_tree, start_server, unpack_tree


def test_kv_reuse_matches_independent_inference_and_reduces_computation():
    model = AttentionModel(reference_weights())
    runtime = InferenceRuntime()
    cold = runtime.generate(model, [1, 2, 3], 4, use_cache=False)
    runtime.generate(model, [1, 2, 3], 0)
    warm = runtime.generate(model, [1, 2, 3], 4)
    assert warm["tokens"] == cold["tokens"]
    assert warm["logits"] == cold["logits"]
    assert warm["reused_tokens"] == 3
    assert warm["evaluated_tokens"] == cold["evaluated_tokens"] - 3


def test_kv_cache_is_bounded_and_model_scoped():
    runtime = InferenceRuntime(max_cache_bytes=10000)
    model = AttentionModel(reference_weights())
    for index in range(30):
        runtime.generate(model, [index % 16, 2, 3], 1)
        assert runtime.cache_bytes <= runtime.budget
    weights = reference_weights()
    weights["query"][0][0] += 1
    other = AttentionModel(weights)
    assert runtime.generate(other, [1, 2, 3], 0)["reused_tokens"] == 0
    runtime.clear()
    assert runtime.cache_bytes == 0


def test_prediction_learns_observed_paths_without_crossing_streams():
    predictor = PathPredictor(4)
    predictor.observe("a", "index")
    predictor.observe("b", "digest")
    predictor.observe("a", "ast")
    assert predictor.predict("index") == [dict(tool="ast", probability=1.0, observations=1)]
    assert predictor.predict("digest") == []
    predictor.finish("a")
    for index in range(20):
        predictor.observe(str(index), str(index))
    assert len(predictor.previous) <= 4


def test_agent_forecast_uses_explicit_parent_edges_and_keeps_workflows_separate():
    predictor = PathPredictor()
    predictor.observe_invocation("wf", "root", "Supervisor")
    predictor.observe_invocation("wf", "child", "Analyst", "root")
    predictor.observe_invocation("wf", "last", "Aggregator", "child")
    predictor.observe_invocation("other", "unknown", "Wrong", "root")
    assert predictor.forecast("Supervisor")[0]["path"] == ["Supervisor", "Analyst", "Aggregator"]
    assert predictor.predict("Aggregator") == []
    predictor.finish_workflow("wf")
    assert all(key[0] != "wf" for key in predictor.invocations)


async def test_local_inference_policy_drives_real_tool_calls_with_identical_outputs(app, snapshot):
    from agentfuse.adapters.inference_agent import InferenceAgent
    from agentfuse.adapters.scripted_adapter import ScriptedAdapter
    workflow = app.create_workflow("inference-agent", default_snapshot_id=snapshot.snapshot_id)
    workflow.start()
    agent = InferenceAgent("policy", app.inference_model, app.inference,
                           [dict(tool="ast_analyzer", arguments={"files": ["src/alpha.py"]}),
                            dict(tool="source_index", arguments={"root": "src"})])
    adapter = ScriptedAdapter(workflow)
    first = await agent.run(adapter, [1, 2, 3], snapshot_id=snapshot.snapshot_id, use_cache=False)
    await agent.run(adapter, [1, 2, 3], snapshot_id=snapshot.snapshot_id)
    third = await agent.run(adapter, [1, 2, 3], snapshot_id=snapshot.snapshot_id)
    assert first.result == third.result
    events = app.event_bus.history(workflow.workflow_id)
    inferences = [e for e in events if e.event_type == "INFERENCE_COMPLETED"]
    assert inferences[-1].metadata["reused_tokens"] == 3
    assert inferences[-1].metadata["evaluated_tokens"] < inferences[0].metadata["evaluated_tokens"]


async def test_remote_timeout_releases_the_server_for_next_request(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTFUSE_WORKER_TOKEN", "x" * 40)
    server = await start_server(port=0, token="x" * 40, workers=1)
    endpoint = dict(host="127.0.0.1", port=server.sockets[0].getsockname()[1], timeout=0.2)
    try:
        messages = []
        await asyncio.to_thread(forward, dict(snapshot_root=str(tmp_path), tool_name="fault_injector",
                                             arguments=dict(mode="sleep", seconds=10)), endpoint, messages.append)
        assert messages[-1]["t"] == "error"
        assert "timed out" in messages[-1]["message"]
        (tmp_path / "a.py").write_text("x = 1\n")
        endpoint["timeout"] = 10
        messages = []
        await asyncio.to_thread(forward, dict(snapshot_root=str(tmp_path), tool_name="ast_analyzer",
                                             arguments=dict(files=["a.py"])), endpoint, messages.append)
        assert messages[-1]["t"] == "result"
    finally:
        server.close()
        await server.wait_closed()


def test_remote_snapshot_rejects_traversal(tmp_path):
    with pytest.raises(ValueError, match="unsafe"):
        unpack_tree(dict(files={"../escape": "eA=="}, directories=[]), tmp_path)
    assert not (tmp_path.parent / "escape").exists()


async def test_distributed_runtime_streams_dependencies_and_shares(settings, monkeypatch):
    from agentfuse.scenarios.definitions import scenario_shared
    monkeypatch.setenv("AGENTFUSE_WORKER_TOKEN", "x" * 40)
    server = await start_server(port=0, token="x" * 40)
    port = server.sockets[0].getsockname()[1]
    from agentfuse.app import AgentFuseApp
    app = AgentFuseApp(settings=settings.with_overrides(raw={**settings.raw, "distributed": {
        "workers": [dict(host="127.0.0.1", port=port)]}}))
    try:
        result = await scenario_shared(app, app.mode, {})
        assert result.metrics["physical_executions"] == 1
        assert result.metrics["logical_calls"] == 2
        events = app.repository.list_events(result.workflow_id)
        reads = [e for e in events if e["event_type"] == "TOOL_READ_OBSERVED"]
        finished = [e for e in events if e["event_type"] == "TOOL_EXECUTION_FINISHED"]
        assert reads and finished
        assert reads[0]["sequence"] < finished[0]["sequence"]
        started = [e for e in app.event_bus.history(result.workflow_id) if e.event_type == "TOOL_EXECUTION_STARTED"]
        assert started[0].metadata["worker_node"] == f"127.0.0.1:{port}"
        execution = app.coordinator.get_execution(started[0].physical_execution_id)
        assert execution.peak_rss > 0, "remote tool RSS must be returned, not bridge RSS"
    finally:
        await app.close()
        server.close()
        await server.wait_closed()


async def test_remote_worker_rejects_invalid_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("BAD_TOKEN", "wrong")
    server = await start_server(port=0, token="x" * 40)
    port = server.sockets[0].getsockname()[1]
    try:
        messages = []
        await asyncio.to_thread(forward, dict(snapshot_root=str(tmp_path), tool_name="ast_analyzer"),
                                dict(host="127.0.0.1", port=port, token_env="BAD_TOKEN"), messages.append)
        assert messages[-1]["t"] == "error"
        assert "authentication" in messages[-1]["message"]
    finally:
        server.close()
        await server.wait_closed()


async def test_tls_worker_verifies_certificate_and_streams_remote_memory(tmp_path, monkeypatch):
    import datetime
    import ipaddress
    import ssl
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AgentFuse test worker")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(minutes=1))
                   .not_valid_after(now + datetime.timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                   .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("def actual(): return 1\n")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    monkeypatch.setenv("AGENTFUSE_WORKER_TOKEN", "t" * 40)
    server = await start_server(port=0, token="t" * 40, tls=context)
    endpoint = dict(host="127.0.0.1", port=server.sockets[0].getsockname()[1], tls=True)
    payload = dict(snapshot_root=str(workspace), tool_name="ast_analyzer", arguments=dict(files=["a.py"]))
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            await asyncio.to_thread(forward, payload, endpoint, lambda _: None)
        endpoint["ca_file"] = str(cert_path)
        messages = []
        await asyncio.to_thread(forward, payload, endpoint, messages.append)
        assert messages[-1]["t"] == "result"
        samples = [message["rss"] for message in messages if message["t"] == "resource"]
        assert samples and min(samples) > 0
        assert messages[-1]["remote_peak_rss"] >= max(samples)
    finally:
        server.close()
        await server.wait_closed()


async def test_distinct_physical_executions_use_two_worker_nodes(settings, monkeypatch):
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.definitions import scenario_shared
    monkeypatch.setenv("AGENTFUSE_WORKER_TOKEN", "x" * 40)
    servers = [await start_server(port=0, token="x" * 40) for _ in range(2)]
    endpoints = [dict(host="127.0.0.1", port=server.sockets[0].getsockname()[1]) for server in servers]
    from agentfuse.app import AgentFuseApp
    app = AgentFuseApp(settings=settings.with_overrides(raw={**settings.raw, "distributed": {"workers": endpoints}}))
    try:
        result = await scenario_shared(app, RuntimeMode.BASELINE, {})
        nodes = {event.metadata["worker_node"] for event in app.event_bus.history(result.workflow_id)
                 if event.event_type == "TOOL_EXECUTION_STARTED"}
        assert nodes == {f"127.0.0.1:{endpoint['port']}" for endpoint in endpoints}
        assert result.metrics["physical_executions"] == 2
    finally:
        await app.close()
        for server in servers:
            server.close()
            await server.wait_closed()


async def test_extension_api_runs_real_inference(settings):
    import httpx
    from agentfuse.api.app import create_app
    api = create_app(settings)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://test") as client:
            request = dict(tokens=[1, 2, 3], max_new_tokens=4)
            first = (await client.post("/api/extensions/inference", json=request)).json()
            second = (await client.post("/api/extensions/inference", json=request)).json()
            assert second["tokens"] == first["tokens"]
            assert second["reused_tokens"] == 3
            assert (await client.post("/api/extensions/inference", json=dict(tokens=[999]))).status_code == 422
            assert (await client.delete("/api/extensions/inference/cache")).json()["cleared"]
    finally:
        await api.state.runtime.close()


def test_sandbox_requires_immutable_images_and_never_falls_back(tmp_path, monkeypatch):
    from agentfuse.runtime.sandbox import SandboxUnavailable, run_isolated
    with pytest.raises(ValueError, match="pinned"):
        run_isolated(tmp_path, ["python", "-V"], image="python:latest")
    monkeypatch.setenv("PATH", "")
    with pytest.raises(SandboxUnavailable):
        run_isolated(tmp_path, ["python", "-V"], image="sha256:" + "0" * 64)


def test_sandbox_enforces_read_only_inputs_and_no_network(tmp_path):
    import subprocess
    from agentfuse.runtime.sandbox import run_isolated
    image = os.environ.get("AGENTFUSE_SANDBOX_TEST_IMAGE")
    if not image:
        pytest.skip("set AGENTFUSE_SANDBOX_TEST_IMAGE to a local immutable Linux image")
    (tmp_path / "input.txt").write_text("secret", encoding="utf-8")
    script = '''
import socket
from pathlib import Path
assert Path('/workspace/input.txt').read_text() == 'secret'
try:
    Path('/workspace/input.txt').write_text('changed')
except OSError:
    pass
else:
    raise AssertionError('input writable')
try:
    socket.create_connection(('1.1.1.1', 80), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('network available')
print('isolated')
'''
    result = run_isolated(tmp_path, ["python", "-c", script], image=image)
    assert result["exit_code"] == 0, result["stderr"]
    assert result["stdout"].strip() == "isolated"
    assert result["shareable"] is False
    assert (tmp_path / "input.txt").read_text() == "secret"
