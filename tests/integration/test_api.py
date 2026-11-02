"""Integration tests: the local HTTP API."""

from __future__ import annotations

import asyncio
import contextlib

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


async def test_the_api_does_not_block_the_event_loop(client):
    """§16.5 - the HTTP API must not freeze the event loop.

    A read waits for the writer to commit the pending statements.  That wait
    belongs into a worker thread - done directly in the route it would stop
    the whole runtime (the dashboard, the benchmark polling, the coordinator)
    for the duration of the commit.
    """

    app = client.agentfuse_app.state.runtime
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.005)
            ticks += 1

    # the writer gets a lot of work - the read has to wait for it.  The
    # number of writes stays moderate: the flush has to finish within its
    # timeout even on a loaded machine.
    for index in range(500):
        app.database.write(
            "INSERT INTO runtime_events "
            "(event_id, workflow_id, sequence, event_type, timestamp, metadata) "
            "VALUES (?, NULL, ?, 'WORKFLOW_STARTED', 1.0, '{}')",
            (f"evt-busy-{index}", index),
        )

    task = asyncio.create_task(ticker())
    try:
        response = await client.get("/api/overview")
        assert response.status_code == 200
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert ticks > 0, (
        "the event loop was blocked while the API answered - the database "
        "reads have to run in a worker thread"
    )
    app.database.flush(timeout=30)


async def test_the_api_refuses_a_public_address(settings):
    """§2.2 - the dashboard has no authentication.

    It may therefore only be served on a loopback address - unless the user
    confirmed it explicitly.  The check lives in the application factory, so
    that every entry point enforces it.
    """

    from dataclasses import replace

    public = replace(settings, api_host="0.0.0.0")
    with pytest.raises(PermissionError):
        create_app(settings=public)
    # the explicit confirmation works
    app = create_app(settings=public, allow_remote=True)
    await app.state.runtime.close()
