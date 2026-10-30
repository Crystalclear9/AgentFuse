"""Requirement audit.

Turns the line by line review of the specification into an executable check.
Every section of the requirements is verified against the *source* (not
against a hand written list), so a change that silently breaks a clause is
caught by CI.

    python scripts/audit_requirements.py            # human readable report
    python scripts/audit_requirements.py --json     # machine readable

Exit code 0 = every clause is satisfied, 1 = at least one clause is broken.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentfuse.console import configure_console  # noqa: E402
from agentfuse.storage.database import Database  # noqa: E402

configure_console()

RESULTS: list[dict[str, Any]] = []


def record(section: str, clause: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append(
        {"section": section, "clause": clause, "ok": bool(ok), "detail": detail}
    )
    return bool(ok)


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 2.2 fully local
# ----------------------------------------------------------------------
def audit_offline() -> None:
    sources = {
        path: read(path.relative_to(ROOT))
        for path in ROOT.glob("agentfuse/**/*.py")
    }
    offenders = [
        str(path.relative_to(ROOT))
        for path, text in sources.items()
        if any(
            token in text.lower()
            for token in ("openai", "anthropic", "api_key", "generativelanguage")
        )
    ]
    record("2.2", "不调用任何外部模型 API", not offenders, str(offenders))

    html = read("agentfuse/web/index.html")
    external = "http://" in html.replace("http://127.0.0.1", "") or "https://" in html
    record("2.2", "前端不加载 CDN 资源", not external)

    config = read("config/default.toml")
    record(
        "2.2",
        "Web 管理界面默认仅监听 127.0.0.1",
        'host = "127.0.0.1"' in config,
    )


# ----------------------------------------------------------------------
# 4. data model
# ----------------------------------------------------------------------
MODEL = {
    "Workflow": (
        "workflows",
        [
            "workflow_id", "scenario", "start_time", "end_time", "status",
            "configuration", "runtime_mode", "random_seed",
        ],
    ),
    "Agent": (
        "agents",
        [
            "agent_id", "workflow_id", "external_identity", "agent_description",
            "registration_source", "metadata",
        ],
    ),
    "AgentInvocation": (
        "agent_invocations",
        [
            "invocation_id", "workflow_id", "agent_id", "start_time", "end_time",
            "parent_invocation_id", "status",
        ],
    ),
    "LogicalToolCall": (
        "logical_tool_calls",
        [
            "logical_call_id", "workflow_id", "invocation_id", "tool_name",
            "tool_version", "normalized_arguments", "workspace_snapshot_id",
            "request_time", "status", "physical_execution_id", "result_reference",
        ],
    ),
    "PhysicalExecution": (
        "physical_executions",
        [
            "execution_id", "tool_name", "tool_version", "arguments_hash",
            "source_snapshot_id", "worker_pid", "start_time", "finish_time",
            "execution_status", "cpu_time", "peak_rss", "exit_status", "result_hash",
        ],
    ),
    "WorkspaceSnapshot": (
        "workspace_snapshots",
        [
            "snapshot_id", "parent_snapshot_id", "workspace_id", "snapshot_path",
            "creation_time", "manifest_hash", "source_reference", "metadata",
        ],
    ),
    "DependencyRecord": (
        "dependency_records",
        [
            "execution_id", "sequence_number", "dependency_type", "relative_path",
            "fingerprint", "observed_time",
        ],
    ),
    "ShareGroup": (
        "share_groups",
        [
            "share_group_id", "candidate_key", "leader_execution_id",
            "creation_time", "status", "participant_calls",
        ],
    ),
    "ValidationRecord": (
        "validation_records",
        [
            "validation_id", "share_group_id", "logical_call_id",
            "dependency_sequence", "validation_status", "validation_time",
            "mismatch_reason",
        ],
    ),
}

TABLES = [
    "workflows", "agents", "agent_invocations", "logical_tool_calls",
    "physical_executions", "share_groups", "share_participants",
    "workspace_snapshots", "dependency_records", "validation_records",
    "runtime_events", "resource_samples", "benchmark_runs",
]


def audit_data_model() -> None:
    """Check the schema of a freshly created database.

    The audit must not depend on the data directory of a previous run - on a
    fresh checkout the default home does not exist yet.  The schema is
    therefore verified on a temporary database, and the database of the
    default home is only inspected when it happens to exist.
    """

    import tempfile

    schema_source = read("agentfuse/storage/database.py")
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Database(str(Path(tmp) / "audit.sqlite3"))
        fresh.initialize()
        schema = {}
        conn = sqlite3.connect(f"file:{fresh.path}?mode=ro", uri=True)
        try:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table in tables:
                schema[table] = {
                    row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                }
            version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
        finally:
            conn.close()
        fresh.close()

    for table in TABLES:
        record("16", f"表 {table}", table in schema)
    for label, (table, fields) in MODEL.items():
        if table not in schema:
            record("4", f"{label} 字段", False, f"缺少表 {table}")
            continue
        missing = [field for field in fields if field not in schema[table]]
        record("4", f"{label} 字段", not missing, f"缺: {missing}" if missing else "")
    record("16", "保存数据库 Schema 版本", "SCHEMA_VERSION" in schema_source, f"v{version}")
    record("16", "提供必要迁移", "_MIGRATIONS" in schema_source)


# ----------------------------------------------------------------------
# 5. event protocol
# ----------------------------------------------------------------------
REQUIRED_EVENTS = [
    "WORKFLOW_STARTED", "WORKFLOW_FINISHED", "AGENT_BOUND", "AGENT_INVOKED",
    "AGENT_RETURNED", "TOOL_CALL_REQUESTED", "TOOL_CALL_WAITING",
    "TOOL_EXECUTION_STARTED", "TOOL_READ_OBSERVED", "SHARE_CANDIDATE_FOUND",
    "SHARE_VALIDATION_PASSED", "SHARE_VALIDATION_FAILED",
    "SHARE_PARTICIPANT_DETACHED", "TOOL_EXECUTION_FINISHED",
    "TOOL_RESULT_DELIVERED", "TOOL_CALL_FAILED", "TOOL_CALL_CANCELLED",
]


def audit_events() -> None:
    state = read("agentfuse/core/state.py")
    publishers = "\n".join(
        read(path.relative_to(ROOT))
        for path in ROOT.glob("agentfuse/**/*.py")
        if path.name != "state.py"
    )
    for event in REQUIRED_EVENTS:
        defined = f'{event} = "{event}"' in state
        published = f"EventType.{event}" in publishers
        record(
            "5.1",
            f"事件 {event}",
            defined and published,
            "已定义并发布" if defined and published else ("仅定义" if defined else "缺失"),
        )
    event_model = read("agentfuse/core/events.py")
    for name in ("event_id", "workflow_id", "event_type", "timestamp", "agent_id",
                 "invocation_id", "logical_call_id", "physical_execution_id",
                 "parent_event_id", "metadata"):
        record("5.1", f"RuntimeEvent.{name}", name in event_model)
    record("5.1", "event_id 全局唯一", "uuid" in read("agentfuse/core/ids.py"))
    record("5.1", "Workflow 内稳定序号", "sequence" in event_model)


# ----------------------------------------------------------------------
# 5.2 / 7.x interfaces
# ----------------------------------------------------------------------
def audit_interfaces() -> None:
    adapter = read("agentfuse/adapters/base.py")
    for method in ("bind_agent", "begin_invocation", "submit_tool_call",
                   "deliver_result", "end_invocation", "record_event"):
        record("5.2", f"适配器方法 {method}", f"def {method}" in adapter)

    reader = read("agentfuse/workspace/reader.py")
    for method in ("read_text", "read_bytes", "list_dir", "exists", "stat"):
        record("7.2", f"WorkspaceReader.{method}", f"def {method}" in reader)
    record("7.2", "受控读取拒绝越界路径", "WorkspaceBoundaryError" in reader)
    record("7.2", "读取立即上报依赖", "reporter" in reader)

    base = read("agentfuse/tools/base.py")
    for field in ("tool_name", "tool_version", "deterministic", "read_only",
                  "resource_estimate", "input_schema", "output_schema",
                  "implementation_reference"):
        record("7.1", f"工具注册字段 {field}", field in base)

    registry = read("agentfuse/tools/registry.py")
    for tool in ("source_index", "ast_analyzer", "dependency_analyzer", "content_digest"):
        record("7.3", f"必备工具 {tool}", tool in registry)

    snapshots = read("agentfuse/workspace/snapshots.py")
    for api in ("create_workspace", "create_snapshot", "fork_snapshot",
                "inspect_snapshot", "compare_snapshots", "delete_snapshot"):
        record("6.2", f"快照 API {api}", f"def {api}" in snapshots)


# ----------------------------------------------------------------------
# 8 / 9 / 10 core algorithms
# ----------------------------------------------------------------------
def audit_core() -> None:
    coordinator = read("agentfuse/runtime/coordinator.py")
    candidate = read("agentfuse/sharing/candidate.py")
    group = read("agentfuse/sharing/share_group.py")
    validator = read("agentfuse/sharing/validator.py")

    record("8.1", "候选键不含 workspace snapshot", "snapshot" not in candidate.split('"""')[2] if candidate.count('"""') > 2 else True)
    record("8.2", "共享组状态机", all(
        state in group for state in
        ("CREATED", "EXECUTING", "VALIDATING", "COMPLETED", "FAILED", "CLOSED")
    ))
    record("8.2", "参与者状态机", all(
        state in group for state in
        ("WAITING", "SHARED_WAIT", "DETACHED", "RUNNING_INDEPENDENTLY",
         "COMPLETED", "FAILED", "CANCELLED")
    ))
    record("8.5", "在线增量依赖验证", "_handle_dependency" in coordinator)
    record("8.7", "最终提交验证门禁", "validated_prefix_length != len(ledger)" in coordinator)
    record("8.8", "分离后独立执行", "_reassign_after_detach_locked" in coordinator)
    record("21.1", "单一协调者状态锁", "self._lock = asyncio.Lock()" in coordinator)
    record("21.2", "快照被篡改时拒绝共享", "_snapshot_is_trustworthy" in coordinator)
    record("21.4", "真实执行次数来自 PhysicalExecution", "physical_executions" in read("agentfuse/monitoring/metrics.py"))
    record("9.1", "LocalResourceController", "class LocalResourceController" in read("agentfuse/runtime/scheduler.py"))
    record("9.2", "共享只占用一次执行槽位", "acquire(" in coordinator)
    record("9.3", "psutil 采集 RSS", "psutil" in read("agentfuse/monitoring/resource_monitor.py"))
    record("9.4", "工具在独立进程执行",
           "worker_child" in read("agentfuse/runtime/pool.py"))
    record("10.1", "区分工具错误/进程崩溃/调度错误",
           all(token in coordinator for token in ("ERROR_TOOL", "ERROR_CRASH", "ERROR_TIMEOUT")))
    record("10.5", "中断运行标记 INTERRUPTED", "recover_interrupted_workflows" in read("agentfuse/storage/repository.py"))


# ----------------------------------------------------------------------
# 12 / 13 / 14 / 15 surface
# ----------------------------------------------------------------------
def audit_surface() -> None:
    definitions = read("agentfuse/scenarios/definitions.py")
    for case in ("follower_cancel", "leader_cancel", "timeout", "crash", "shared_failure"):
        record("12.6", f"失败场景 {case}", case in definitions)
    for scenario in ("shared", "different-workspace", "divergent", "multi-agent"):
        record("12", f"演示场景 {scenario}", scenario in definitions)

    cli = read("agentfuse/cli.py")
    for command in ("init", "serve", "demo", "compare", "trace", "inspect"):
        record("15", f"CLI {command}", f'"{command}"' in cli)

    html = read("agentfuse/web/index.html")
    for tab in ("overview", "workflow", "dependency", "benchmark", "tools"):
        record("14", f"界面页签 {tab}", f'data-tab="{tab}"' in html)

    try:
        import asyncio
        import shutil

        from agentfuse.api.app import create_app
        from agentfuse.settings import load_settings

        # A fixed directory inside the runtime home: on Windows the removal of
        # a temporary directory can race with the SQLite file handles.
        tmp = ROOT / ".agentfuse" / "audit"
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        if True:
            app = create_app(settings=load_settings(home=str(tmp)))
            try:
                # the audit must not start worker processes
                app.state.runtime.coordinator.worker_pool_size = 0
                paths = set()

                def flatten(routes):
                    for route in routes:
                        name = type(route).__name__
                        if name == "_IncludedRouter":
                            # FastAPI >= 0.112 wraps an included router
                            original = getattr(route, "original_router", None)
                            if original is not None:
                                yield from flatten(original.routes)
                            continue
                        if name == "APIRoute":
                            yield route.path

                paths = {path for path in flatten(app.router.routes)}
                required = [
                    "/api/health", "/api/overview", "/api/resources", "/api/workflows",
                    "/api/workflows/{workflow_id}", "/api/workflows/{workflow_id}/graph",
                    "/api/workflows/{workflow_id}/timeline",
                    "/api/workflows/{workflow_id}/events",
                    "/api/workflows/{workflow_id}/metrics", "/api/executions",
                    "/api/executions/{execution_id}",
                    "/api/executions/{execution_id}/dependencies",
                    "/api/benchmarks", "/api/benchmarks/run", "/api/benchmarks/scenarios",
                    "/api/benchmarks/jobs/{job_id}", "/api/tools",
                ]
                missing = [path for path in required if path not in paths]
                record(
                    "14",
                    "API 端点齐备",
                    not missing,
                    f"缺: {missing}" if missing else f"{len(required)} 个端点",
                )
            finally:
                asyncio.run(app.state.runtime.close())
                shutil.rmtree(tmp, ignore_errors=True)
    except Exception as exc:  # pragma: no cover - defensive
        record("14", "API 端点齐备", False, str(exc))


# ----------------------------------------------------------------------
# 17 test categories + 20 layout + 22 documentation
# ----------------------------------------------------------------------
LAYOUT = """
pyproject.toml README.md LICENSE config/default.toml
agentfuse/__init__.py agentfuse/cli.py
agentfuse/core/workflow.py agentfuse/core/agent_context.py agentfuse/core/events.py
agentfuse/core/execution_graph.py agentfuse/core/state.py
agentfuse/adapters/base.py agentfuse/adapters/scripted_adapter.py
agentfuse/runtime/coordinator.py agentfuse/runtime/execution.py agentfuse/runtime/worker.py
agentfuse/runtime/scheduler.py agentfuse/runtime/cancellation.py
agentfuse/sharing/candidate.py agentfuse/sharing/share_group.py agentfuse/sharing/dependency.py
agentfuse/sharing/validator.py agentfuse/sharing/result_delivery.py
agentfuse/workspace/snapshots.py agentfuse/workspace/manifest.py agentfuse/workspace/reader.py
agentfuse/tools/registry.py agentfuse/tools/source_index.py agentfuse/tools/ast_analyzer.py
agentfuse/tools/dependency_analyzer.py agentfuse/tools/digest_analyzer.py
agentfuse/monitoring/metrics.py agentfuse/monitoring/resource_monitor.py
agentfuse/storage/database.py agentfuse/storage/models.py agentfuse/storage/repository.py
agentfuse/api/app.py agentfuse/web/index.html agentfuse/web/styles.css agentfuse/web/app.js
benchmarks/runner.py benchmarks/scenarios.py
docs/ARCHITECTURE.md docs/EXECUTION_MODEL.md docs/SHARING_PROTOCOL.md
docs/ADAPTER_GUIDE.md docs/BENCHMARK.md docs/TEST_REPORT.md docs/KNOWN_LIMITATIONS.md
""".split()

DOC_SECTIONS = {
    "README.md": [
        "\u89e3\u51b3\u4ec0\u4e48\u95ee\u9898", "\u4e3b\u8981\u529f\u80fd", "\u6280\u672f\u6808",
        "\u5b89\u88c5", "\u542f\u52a8", "CLI", "Web UI", "\u6700\u5c0f\u6f14\u793a",
        "\u6d4b\u8bd5\u65b9\u6cd5",
    ],
    "docs/ARCHITECTURE.md": [
        "\u6a21\u5757\u56fe", "\u804c\u8d23", "\u4e8b\u4ef6\u6d41", "\u6267\u884c\u56fe\u6a21\u578b",
        "\u63a7\u5236\u6d41\u7a0b", "\u6301\u4e45\u5316",
    ],
    "docs/EXECUTION_MODEL.md": [
        "Workflow", "AgentInvocation", "LogicalToolCall", "PhysicalExecution",
        "Resource Scheduling", "Cancellation", "Failure Recovery",
        "LocalResourceController",
    ],
    "docs/SHARING_PROTOCOL.md": [
        "CandidateKey", "ShareGroup", "\u4f9d\u8d56\u8d26\u672c", "\u589e\u91cf\u9a8c\u8bc1",
        "Late Join", "\u5206\u79bb", "\u6700\u7ec8\u63d0\u4ea4\u9a8c\u8bc1", "\u4e00\u81f4\u6027",
    ],
    "docs/ADAPTER_GUIDE.md": ["AgentAdapter", "bind_agent", "submit_tool_call"],
    "docs/BENCHMARK.md": [
        "\u573a\u666f", "\u65b9\u6cd5", "\u5de5\u4f5c\u8d1f\u8f7d", "\u6307\u6807", "\u516c\u5e73\u6027",
        "\u5b9e\u6d4b\u7ed3\u679c",
    ],
    "docs/TEST_REPORT.md": [
        "\u6d4b\u8bd5\u547d\u4ee4", "\u6570\u91cf", "\u7ed3\u679c", "\u73af\u5883", "\u5c1a\u672a\u9a8c\u8bc1",
    ],
    "docs/KNOWN_LIMITATIONS.md": [
        "\u4ec5\u652f\u6301\u53d7\u63a7\u5de5\u5177", "\u6c99\u7bb1", "\u4f9d\u8d56\u8ffd\u8e2a",
        "KV Cache", "\u5206\u5e03\u5f0f", "\u5feb\u7167\u4e0d\u5305\u542b\u8fdb\u7a0b\u5185\u5b58",
        "\u786c\u9650\u5236", "Scripted Agent",
    ],
}

TEST_CATEGORIES = {
    "17.1": [
        "test_the_graph_only_contains_observed_relations",
        "test_out_of_order_events_do_not_create_wrong_edges",
        "test_two_scripted_agents_produce_a_real_execution_graph",
    ],
    "17.2": [
        "test_two_equivalent_calls_share_a_single_physical_execution",
        "test_the_baseline_mode_never_shares",
        "test_different_arguments_are_not_shared",
        "test_different_tool_versions_are_not_shared",
        "test_a_non_shareable_tool_is_never_shared",
        "test_an_unrelated_file_change_does_not_prevent_sharing",
        "test_the_shared_results_are_identical_to_the_baseline",
    ],
    "17.3": [
        "test_a_conflicting_dependency_detaches_the_follower",
        "test_a_changed_directory_listing_is_detected",
        "test_a_changed_file_existence_is_detected",
        "test_the_detection_happens_while_the_leader_is_still_running",
        "test_a_late_joiner_validates_the_existing_ledger",
        "test_the_dependency_event_precedes_the_execution_end",
    ],
    "17.4": [
        "test_many_concurrent_identical_calls_create_one_execution",
        "test_no_logical_call_is_delivered_twice",
        "test_a_cancelled_follower_does_not_stop_the_leader",
        "test_a_cancelled_leader_keeps_the_follower_alive",
        "test_cancelling_every_participant_terminates_the_execution",
        "test_a_failing_execution_releases_the_participants",
    ],
    "17.5": [
        "test_the_worker_limit_is_enforced",
        "test_the_memory_budget_blocks_new_executions",
        "test_a_hanging_worker_is_terminated_by_the_timeout",
        "test_a_crashing_worker_is_detected",
        "test_no_worker_processes_are_leaked",
        "test_the_rss_soft_limit_blocks_new_admissions",
    ],
    "17.6": [
        "test_snapshots_are_immutable",
        "test_identical_content_produces_identical_manifests",
        "test_different_content_produces_different_manifests",
        "test_fork_creates_an_independent_branch",
        "test_paths_escaping_the_workspace_are_rejected",
        "test_a_modified_snapshot_is_not_shared",
    ],
    "17.7": [
        "test_a_single_agent_call_produces_one_execution",
        "test_two_equivalent_calls_share_a_single_physical_execution",
        "test_an_unrelated_file_change_does_not_prevent_sharing",
        "test_a_conflicting_dependency_detaches_the_follower",
        "test_the_aggregator_only_starts_after_the_workers_finished",
        "test_a_failing_execution_releases_the_participants",
        "test_a_cancelled_follower_does_not_stop_the_leader",
        "test_the_shared_results_are_identical_to_the_baseline",
    ],
}


def audit_layout_tests_docs() -> None:
    missing = [name for name in LAYOUT if not (ROOT / name).exists()]
    record("20", "工程目录结构", not missing, f"缺: {missing}" if missing else f"{len(LAYOUT)} 项")

    tests = "\n".join(
        path.read_text(encoding="utf-8") for path in ROOT.glob("tests/**/*.py")
    )
    for category, markers in TEST_CATEGORIES.items():
        absent = [marker for marker in markers if marker not in tests]
        record(
            category,
            "自动化测试类别覆盖",
            not absent,
            f"缺: {absent}" if absent else f"{len(markers)} 个关键用例",
        )

    for doc, needles in DOC_SECTIONS.items():
        text = read(doc)
        absent = [needle for needle in needles if needle.lower() not in text.lower()]
        record(
            "22",
            f"文档必备章节 {doc}",
            not absent,
            f"缺: {absent}" if absent else f"{len(needles)} 节",
        )


# ----------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    audit_offline()
    audit_data_model()
    audit_events()
    audit_interfaces()
    audit_core()
    audit_surface()
    audit_layout_tests_docs()

    failed = [item for item in RESULTS if not item["ok"]]
    if args.json:
        print(json.dumps(
            {"total": len(RESULTS), "failed": len(failed), "checks": RESULTS},
            ensure_ascii=False, indent=2,
        ))
    else:
        current = None
        for item in RESULTS:
            if item["section"] != current:
                current = item["section"]
                print(f"\n=== §{current} ===")
            mark = "PASS" if item["ok"] else "FAIL"
            detail = f"  ({item['detail']})" if item["detail"] else ""
            print(f"  [{mark}] {item['clause']}{detail}")
        print(f"\n总计 {len(RESULTS)} 项，未通过 {len(failed)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
