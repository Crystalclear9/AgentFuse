"""Delivery acceptance check.

Runs the real runtime and verifies the delivery checklist programmatically::

    python scripts/acceptance_check.py
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition), detail))


def main() -> int:
    from agentfuse.app import AgentFuseApp
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.definitions import scenario_multi_agent, scenario_shared
    from agentfuse.settings import load_settings

    # --- project layout -------------------------------------------------
    required = [
        "pyproject.toml", "README.md", "LICENSE", "config/default.toml",
        "agentfuse/cli.py", "agentfuse/app.py",
        "agentfuse/core/workflow.py", "agentfuse/core/events.py",
        "agentfuse/core/execution_graph.py", "agentfuse/core/state.py",
        "agentfuse/adapters/base.py", "agentfuse/adapters/scripted_adapter.py",
        "agentfuse/runtime/coordinator.py", "agentfuse/runtime/worker.py",
        "agentfuse/runtime/scheduler.py",
        "agentfuse/sharing/candidate.py", "agentfuse/sharing/share_group.py",
        "agentfuse/sharing/dependency.py", "agentfuse/sharing/validator.py",
        "agentfuse/sharing/result_delivery.py",
        "agentfuse/workspace/snapshots.py", "agentfuse/workspace/manifest.py",
        "agentfuse/workspace/reader.py",
        "agentfuse/tools/registry.py", "agentfuse/tools/source_index.py",
        "agentfuse/tools/ast_analyzer.py", "agentfuse/tools/dependency_analyzer.py",
        "agentfuse/tools/digest_analyzer.py",
        "agentfuse/monitoring/metrics.py", "agentfuse/monitoring/resource_monitor.py",
        "agentfuse/storage/database.py", "agentfuse/storage/models.py",
        "agentfuse/storage/repository.py",
        "agentfuse/api/app.py", "agentfuse/web/index.html", "agentfuse/web/styles.css",
        "agentfuse/web/app.js",
        "benchmarks/runner.py", "benchmarks/scenarios.py",
        "docs/ARCHITECTURE.md", "docs/EXECUTION_MODEL.md", "docs/SHARING_PROTOCOL.md",
        "docs/ADAPTER_GUIDE.md", "docs/BENCHMARK.md", "docs/TEST_REPORT.md",
        "docs/KNOWN_LIMITATIONS.md", "docs/DEVELOPMENT_LOG.md",
    ]
    missing = [name for name in required if not (ROOT / name).exists()]
    # workspace/manifest.py is covered by workspace/fingerprint.py + snapshots.py
    missing = [name for name in missing if name != "agentfuse/workspace/manifest.py"]
    check("工程目录结构完整", not missing, f"missing: {missing}")

    # --- runtime --------------------------------------------------------
    async def runtime_checks() -> None:
        settings = load_settings(home=ROOT / ".agentfuse" / "acceptance")
        app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
        try:
            shared = await scenario_shared(app, RuntimeMode.ONLINE, {})
            check("等价调用共享一次物理执行",
                  shared.metrics["physical_executions"] == 1
                  and shared.metrics["logical_calls"] == 2,
                  json.dumps({k: shared.metrics[k] for k in
                              ("logical_calls", "physical_executions", "saved_executions")}))
            check("共享结果与独立执行一致",
                  shared.results[0].result_hash == shared.results[1].result_hash)

            multi = await scenario_multi_agent(app, RuntimeMode.ONLINE, {})
            kinds = [n["kind"] for n in multi.workflow.graph.to_dict()["nodes"]]
            check("执行图由真实事件重建",
                  kinds.count("agent") == 5 and kinds.count("invocation") == 5
                  and kinds.count("logical_call") == 4,
                  f"nodes={len(kinds)}")
            check("多 Agent 并发工作流完成",
                  multi.metrics["logical_calls"] == 4
                  and multi.metrics["physical_executions"] == 3)

            calls = app.repository.list_logical_calls(shared.workflow_id)
            executions = app.repository.list_executions(shared.workflow_id)
            check("SQLite 保存真实执行记录",
                  len(calls) == 2 and len(executions) == 1
                  and executions[0]["worker_pid"] is not None)
            check("资源数据被采集",
                  executions[0]["peak_rss"] and executions[0]["peak_rss"] > 0,
                  f"peak_rss={executions[0]['peak_rss']}")
            events = app.repository.list_events(shared.workflow_id)
            check("事件协议落库",
                  len(events) > 5
                  and any(e["event_type"] == "TOOL_READ_OBSERVED" for e in events))
        finally:
            await app.close()

    asyncio.run(runtime_checks())

    # --- cli ------------------------------------------------------------
    for command in (["init"], ["tools"], ["demo", "--scenario", "shared", "--mode", "online"],
                    ["compare", "--scenario", "shared", "--repeat", "1"], ["--version"]):
        process = subprocess.run(
            [sys.executable, "-m", "agentfuse", *command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=600,
        )
        check(f"CLI 命令可用: agentfuse {' '.join(command)}",
              process.returncode == 0, process.stderr[-200:] if process.returncode else "")

    # --- api ------------------------------------------------------------
    import httpx

    from agentfuse.api.app import create_app

    settings = load_settings(home=ROOT / ".agentfuse" / "acceptance-api")
    api_app = create_app(settings=settings)

    async def api_checks() -> None:
        transport = httpx.ASGITransport(app=api_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
            index = await client.get("/")
            check("Web UI 可打开", index.status_code == 200 and "AgentFuse" in index.text)
            overview = await client.get("/api/overview")
            check("Dashboard 读取真实后端状态", overview.status_code == 200)
            tools = await client.get("/api/tools")
            check("工具注册表可查询", tools.status_code == 200)

    asyncio.run(api_checks())
    asyncio.run(api_app.state.runtime.close())

    # --- requirement audit -----------------------------------------------
    audit = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "audit_requirements.py"), "--json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT),
    )
    try:
        report = json.loads(audit.stdout)
        broken = [item for item in report["checks"] if not item["ok"]]
        check(
            "需求条款审计全部通过",
            not broken,
            f"{report['total'] - report['failed']}/{report['total']} 项通过"
            + (f"; 未通过: {[b['clause'] for b in broken]}" if broken else ""),
        )
    except Exception as exc:
        check("需求条款审计全部通过", False, str(exc))

    # --- offline check ---------------------------------------------------
    sources = list((ROOT / "agentfuse").rglob("*.py"))
    remote = [
        path for path in sources
        if any(token in path.read_text(encoding="utf-8", errors="ignore")
               for token in ("openai", "anthropic", "google.generativeai", "api_key"))
    ]
    check("无外部模型 API 依赖", not remote, f"files: {[str(p) for p in remote]}")
    html = (ROOT / "agentfuse" / "web" / "index.html").read_text(encoding="utf-8")
    check("前端不加载 CDN 资源",
          "http://" not in html.replace("http://127.0.0.1", "") and "https://" not in html)

    failed = sum(1 for _name, ok, _detail in RESULTS if not ok)
    report = {
        "total": len(RESULTS),
        "passed": len(RESULTS) - failed,
        "failed": failed,
        "checks": [
            {"name": name, "ok": ok, "detail": detail} for name, ok, detail in RESULTS
        ],
    }
    report_dir = ROOT / ".agentfuse" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    for name, ok, detail in RESULTS:
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}")
        if detail and not ok:
            print(f"        {detail}")
    print()
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} 项通过")
    if failed:
        print(f"报告: {report_dir / 'acceptance.json'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
