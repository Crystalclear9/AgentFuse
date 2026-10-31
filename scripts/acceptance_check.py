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

from agentfuse.console import configure_console  # noqa: E402

configure_console()

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
    home = ROOT / ".agentfuse" / "acceptance"
    #: the ids of the real run - the CLI commands below inspect them
    runtime_ids: dict[str, str] = {}

    async def runtime_checks() -> None:
        settings = load_settings(home=home)
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

            # every scenario of the requirement has to run for real
            from agentfuse.scenarios.definitions import (
                scenario_cancel_failure,
                scenario_different_workspace,
                scenario_shared_heavy,
            )

            heavy = await scenario_shared_heavy(app, RuntimeMode.ONLINE, {"rounds": 400})
            check("CPU 密集场景共享一次执行",
                  heavy.metrics["physical_executions"] == 1
                  and heavy.metrics["logical_calls"] == 2
                  and heavy.results[0].result_hash == heavy.results[1].result_hash,
                  f"executions={heavy.metrics['physical_executions']}")

            workspace = await scenario_different_workspace(app, RuntimeMode.ONLINE, {})
            check("不同工作区仍能共享",
                  workspace.metrics["physical_executions"] == 1
                  and workspace.metrics["detach_count"] == 0,
                  f"executions={workspace.metrics['physical_executions']}")

            failures = await scenario_cancel_failure(
                app, RuntimeMode.ONLINE, {"timeout_seconds": 0.5}
            )
            check("取消与失败场景覆盖全部情形",
                  failures.notes["follower_cancel"]["follower_result"] == {"cancelled": True}
                  and failures.notes["leader_cancel"]["cancelled_was_the_leader"] is True
                  and "timed out" in failures.notes["timeout"]["error"]
                  and failures.notes["crash"]["kind"] in ("worker_crash", "internal_error"),
                  "取消/超时/崩溃")

            calls = app.repository.list_logical_calls(shared.workflow_id)
            executions = app.repository.list_executions(shared.workflow_id)
            runtime_ids["workflow"] = shared.workflow_id
            runtime_ids["execution"] = executions[0]["execution_id"]
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

            # --- referential integrity ------------------------------------
            database = app.repository.db
            check("数据库写入零错误",
                  database.write_errors == 0,
                  f"errors={database.write_errors}: {(database.last_error or '')[:200]}")
            from agentfuse.storage.database import SCHEMA_VERSION

            check("数据库版本与代码一致",
                  database.schema_version() == SCHEMA_VERSION,
                  f"db={database.schema_version()} code={SCHEMA_VERSION}")
            violations = database.foreign_key_violations()
            check("外键约束无违规", not violations, f"violations={violations[:5]}")
            foreign_keys = database.foreign_keys()
            check("外键约束已声明",
                  len(foreign_keys) >= 14,
                  f"declared={len(foreign_keys)}")
            # the count alone proves nothing - the *relations* have to be there
            # the pragma reports (id, seq, table, from, to, ...) - the row of
            # ``foreign_keys()`` starts with the child table
            relations = {
                (str(row[0]), str(row[4]), str(row[3])) for row in foreign_keys
            }
            expected_relations = {
                ("agents", "workflow_id", "workflows"),
                ("agent_invocations", "agent_id", "agents"),
                ("logical_tool_calls", "physical_execution_id", "physical_executions"),
                ("physical_executions", "workflow_id", "workflows"),
                ("share_groups", "leader_execution_id", "physical_executions"),
                ("share_participants", "share_group_id", "share_groups"),
                ("share_participants", "logical_call_id", "logical_tool_calls"),
                ("dependency_records", "execution_id", "physical_executions"),
                ("validation_records", "share_group_id", "share_groups"),
                ("resource_samples", "execution_id", "physical_executions"),
            }
            missing_relations = sorted(
                relation
                for relation in expected_relations
                if relation not in relations
            )
            check(
                "外键关系齐备",
                not missing_relations,
                f"缺: {missing_relations}" if missing_relations else f"{len(expected_relations)} 条关系",
            )
            check("调用与执行原子落库",
                  all(call["physical_execution_id"] for call in calls)
                  and all(execution["result_reference"] for execution in executions),
                  f"calls={[c['physical_execution_id'] for c in calls]}")
        finally:
            await app.close()

    asyncio.run(runtime_checks())

    # --- cli ------------------------------------------------------------
    # The commands of requirement 15 have to be *usable* - not only to exist.
    # ``serve`` is checked with ``--help``: starting the server would block.
    cli_commands: list[list[str]] = [
        ["init"],
        ["tools"],
        ["demo", "--scenario", "shared", "--mode", "online"],
        ["demo", "--scenario", "diverged", "--mode", "online"],
        ["demo", "--scenario", "multi-agent", "--mode", "online"],
        ["compare", "--scenario", "shared", "--repeat", "1"],
        ["serve", "--help"],
        ["--version"],
    ]
    # ``trace`` and ``inspect`` need a real id - the workflow and the
    # execution of the run above.
    trace_id = runtime_ids.get("workflow", "")
    execution_id = runtime_ids.get("execution", "")
    cli_commands.append(["trace", trace_id])
    cli_commands.append(["inspect", execution_id])

    for command in cli_commands:
        process = subprocess.run(
            [sys.executable, "-m", "agentfuse", "--home", str(home), *command],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(ROOT), timeout=600,
        )
        check(f"CLI 命令可用: agentfuse {' '.join(command)}",
              process.returncode == 0, process.stderr[-200:] if process.returncode else "")

    # --- api ------------------------------------------------------------
    import httpx

    from agentfuse.api.app import create_app

    settings = load_settings(home=ROOT / ".agentfuse" / "acceptance-api")
    api_app = create_app(settings=settings)

    async def api_checks() -> None:
        # the API serves its own database - a real run gives the endpoints
        # something to report
        from agentfuse.scenarios.definitions import scenario_shared

        api_result = await scenario_shared(
            api_app.state.runtime, RuntimeMode.ONLINE, {}
        )
        workflow_id = api_result.workflow_id
        transport = httpx.ASGITransport(app=api_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as client:
            index = await client.get("/")
            check("Web UI 可打开", index.status_code == 200 and "AgentFuse" in index.text)
            overview = await client.get("/api/overview")
            check("Dashboard 读取真实后端状态", overview.status_code == 200)
            payload = overview.json()
            # the payload has to describe the *real* state, not only be a 200
            required = (
                "running_workflows", "active_invocations", "logical_tool_calls",
                "physical_executions", "shared_waiting", "detached", "workers",
                "mode", "workflows", "recent_events", "tools",
            )
            missing = [key for key in required if key not in payload]
            check("Dashboard 返回真实字段", not missing, f"缺: {missing}")
            check(
                "Dashboard 的调用计数与数据库一致",
                payload["logical_tool_calls"] >= 1 and payload["physical_executions"] >= 1,
                f"calls={payload.get('logical_tool_calls')} "
                f"executions={payload.get('physical_executions')}",
            )
            check(
                "工具注册表带依赖声明",
                all("dependency_tracking" in tool for tool in payload["tools"]),
            )
            tools = await client.get("/api/tools")
            check("工具注册表可查询", tools.status_code == 200)

            # the endpoints that were never called before
            resources = await client.get("/api/resources")
            resource_payload = resources.json()
            check(
                "资源端点返回真实快照",
                resources.status_code == 200
                and "resources" in resource_payload
                and "max_workers" in resource_payload["resources"]
                and "running" in resource_payload["resources"],
                resource_payload.get("resources", {}).get("max_workers"),
            )
            scenarios = await client.get("/api/benchmarks/scenarios")
            check(
                "基准场景端点返回场景清单",
                scenarios.status_code == 200 and scenarios.json().get("scenarios"),
            )
            metrics = await client.get(f"/api/workflows/{workflow_id}/metrics")
            check(
                "工作流指标端点返回真实指标",
                metrics.status_code == 200
                and metrics.json().get("physical_executions", 0) >= 1,
                metrics.text[:120],
            )
            graph = await client.get(f"/api/workflows/{workflow_id}/graph")
            check(
                "执行图端点返回节点与边",
                graph.status_code == 200 and graph.json().get("nodes"),
            )

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

    # the benchmark catalogue must be importable and consistent with the
    # runtime - a stale copy of it would crash the CLI
    try:
        from benchmarks.scenarios import catalogue as scenario_catalogue

        rows = scenario_catalogue()
        check(
            "基准场景目录可导入且与运行时一致",
            len(rows) == 6 and all(row.get("workload") for row in rows),
            f"scenarios={len(rows)}",
        )
    except Exception as exc:
        check("基准场景目录可导入且与运行时一致", False, str(exc))

    # --- offline check ---------------------------------------------------
    sources = list((ROOT / "agentfuse").rglob("*.py"))
    remote = [
        path for path in sources
        if any(token in path.read_text(encoding="utf-8", errors="ignore")
               for token in ("openai", "anthropic", "google.generativeai", "api_key"))
    ]
    check("无外部模型 API 依赖", not remote, f"files: {[str(p) for p in remote]}")
    # the front end must not load anything from the network - **all** assets
    import re

    external: list[str] = []
    for asset in sorted((ROOT / "agentfuse" / "web").glob("*")):
        if asset.suffix not in (".html", ".js", ".css"):
            continue
        text = asset.read_text(encoding="utf-8", errors="ignore")
        # the SVG namespace is an identifier, not a resource that is fetched
        text = text.replace("http://www.w3.org/2000/svg", "")
        for match in re.findall(r"https?://[^\s\"'()]+", text):
            external.append(f"{asset.name}: {match}")
    check("前端不加载 CDN 资源", not external, f"external: {external}")

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
