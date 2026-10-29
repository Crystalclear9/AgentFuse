"""Integration tests: the local HTTP API."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from agentfuse.api.app import create_app
from agentfuse.core.state import RuntimeMode
from agentfuse.scenarios.definitions import scenario_shared


@pytest.fixture()
async def client(settings):
    """An httpx client bound to the ASGI application (same event loop)."""

    app = create_app(settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agentfuse") as test_client:
        test_client.agentfuse_app = app
        yield test_client
    await app.state.runtime.close()


async def test_health(client):
    response = await client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["database"].endswith(".sqlite3")


async def test_the_dashboard_is_served(client):
    response = await client.get("/")
    assert response.status_code == 200
    assert "AgentFuse" in response.text
    assert (await client.get("/static/styles.css")).status_code == 200
    assert (await client.get("/static/app.js")).status_code == 200


async def test_the_overview_endpoint(client):
    payload = (await client.get("/api/overview")).json()
    assert "logical_tool_calls" in payload
    assert "physical_executions" in payload
    assert "workers" in payload
    assert isinstance(payload["tools"], list)


async def test_the_tools_endpoint_lists_the_registry(client):
    payload = (await client.get("/api/tools")).json()
    names = {tool["tool_name"] for tool in payload["tools"]}
    assert {"source_index", "ast_analyzer", "dependency_analyzer", "content_digest"} <= names
    for tool in payload["tools"]:
        assert "implementation_reference" in tool


async def test_the_workflow_endpoints(client):
    app = client.agentfuse_app.state.runtime
    result = await scenario_shared(app, RuntimeMode.ONLINE, {})

    listing = (await client.get("/api/workflows")).json()["workflows"]
    assert any(row["workflow_id"] == result.workflow_id for row in listing)

    detail = (await client.get(f"/api/workflows/{result.workflow_id}")).json()
    assert detail["workflow"]["scenario"] == "shared"
    assert len(detail["logical_calls"]) == 2
    assert len(detail["physical_executions"]) == 1
    assert detail["metrics"]["share_success_rate"] > 0

    graph = (await client.get(f"/api/workflows/{result.workflow_id}/graph")).json()
    assert graph["nodes"]

    timeline = (await client.get(f"/api/workflows/{result.workflow_id}/timeline")).json()
    assert timeline["lanes"]

    events = (await client.get(f"/api/workflows/{result.workflow_id}/events")).json()["events"]
    assert any(event["event_type"] == "TOOL_READ_OBSERVED" for event in events)


async def test_the_execution_endpoints(client):
    app = client.agentfuse_app.state.runtime
    result = await scenario_shared(app, RuntimeMode.ONLINE, {})

    executions = (
        await client.get(f"/api/executions?workflow_id={result.workflow_id}")
    ).json()["executions"]
    assert len(executions) == 1
    execution_id = executions[0]["execution_id"]

    detail = (await client.get(f"/api/executions/{execution_id}")).json()
    assert detail["execution"]["tool_name"] == "ast_analyzer"

    inspection = (await client.get(f"/api/executions/{execution_id}/dependencies")).json()
    assert inspection["summary"]["dependency_count"] == 5
    assert len(inspection["participants"]) == 2


async def test_an_unknown_workflow_returns_404(client):
    assert (await client.get("/api/workflows/does-not-exist")).status_code == 404
    assert (await client.get("/api/executions/does-not-exist")).status_code == 404


async def test_the_benchmark_endpoint_runs_a_comparison(client):
    response = await client.post(
        "/api/benchmarks/run",
        json={
            "scenario": "shared",
            "repeat": 1,
            "modes": ["baseline", "online"],
            "workers": 2,
            "memory_budget_mb": 512,
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    for _ in range(120):
        job = (await client.get(f"/api/benchmarks/jobs/{job_id}")).json()
        if job["status"] != "RUNNING":
            break
        await asyncio.sleep(0.5)
    assert job["status"] == "COMPLETED", job.get("error")
    report = job["result"]
    assert set(report["aggregated"]) == {"baseline", "online"}
    assert report["aggregated"]["online"]["physical_executions"]["mean"] == 1
    assert report["aggregated"]["baseline"]["physical_executions"]["mean"] == 2
    assert report["correctness"]["online"]["mismatched"] == 0
    assert (await client.get("/api/benchmarks")).json()["runs"]
