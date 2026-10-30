"""Capture the real API payloads of a run for the front end test.

The front end harness (``tests/frontend/dom_harness.mjs``) renders the
dashboard against **recorded** responses of the real backend.  The payloads
are captured here so that the test does not need a running server.

    python scripts/capture_frontend_payloads.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentfuse.console import configure_console  # noqa: E402

configure_console()

TARGET = ROOT / "tests" / "frontend" / "payloads.json"


async def capture() -> dict:
    import httpx

    from agentfuse.api.app import create_app
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.definitions import scenario_shared
    from agentfuse.settings import load_settings

    home = ROOT / ".agentfuse" / "frontend-capture"
    shutil.rmtree(home, ignore_errors=True)
    settings = load_settings(home=str(home))
    app = create_app(settings=settings)
    runtime = app.state.runtime

    result = await scenario_shared(runtime, RuntimeMode.ONLINE, {})
    workflow_id = result.workflow_id
    executions = runtime.repository.list_executions(workflow_id)
    execution_id = executions[0]["execution_id"]

    payloads: dict[str, object] = {}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
        for name, path in (
            ("overview", "/api/overview"),
            ("workflows", "/api/workflows?limit=100"),
            ("workflow", f"/api/workflows/{workflow_id}"),
            ("metrics", f"/api/workflows/{workflow_id}/metrics"),
            ("graph", f"/api/workflows/{workflow_id}/graph"),
            ("timeline", f"/api/workflows/{workflow_id}/timeline"),
            ("executions", "/api/executions"),
            ("execution", f"/api/executions/{execution_id}"),
            ("dependencies", f"/api/executions/{execution_id}/dependencies"),
            ("tools", "/api/tools"),
            ("scenarios", "/api/benchmarks/scenarios"),
            ("benchmarks", "/api/benchmarks"),
        ):
            response = await client.get(path)
            response.raise_for_status()
            payloads[name] = response.json()

    await runtime.close()
    payloads["_ids"] = {"workflow": workflow_id, "execution": execution_id}
    return payloads


def main() -> int:
    payloads = asyncio.run(capture())
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(
        json.dumps(payloads, ensure_ascii=False, indent=1, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"  {len(payloads)} payloads written to {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
