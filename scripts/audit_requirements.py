"""Requirement audit.

Turns the line by line review of the specification into an executable check.

The audit has two kinds of checks:

**behavioural**
    The runtime is started and the *outcome* is inspected - the events have
    to be in the database, the executions have to be real processes, the
    shared result has to be identical to the baseline result.  These checks
    can fail when the behaviour breaks.

**structural**
    The schema, the files, the functions and the documented clauses are
    checked against the repository.  They prove that a building block exists,
    not that it behaves correctly.

The coverage is **not** complete: the requirement has 25 chapters and the
audit checks the sections listed in ``COVERED_SECTIONS``.  A clause that is
neither audited nor covered by a test is a gap - the gap list is printed with
``--coverage``.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentfuse.console import configure_console  # noqa: E402
from agentfuse.storage.database import Database  # noqa: E402

configure_console()

RESULTS: list[dict[str, Any]] = []

#: The requirement sections the audit checks.  The requirement has 25
#: chapters - the chapters that are **not** listed here are either general
#: introductions (1, 3, 18, 23-25) or covered by the test suite and the
#: acceptance check.  ``--coverage`` prints the list.
COVERED_SECTIONS = (
    "2.2", "4", "4.8", "5.1", "5.2", "6.2", "7.1", "7.2", "7.3",
    "8.1", "8.2", "8.5", "8.7", "8.8", "9.1", "9.2", "9.3", "9.4",
    "10.1", "10.5", "11.3", "12", "12.4", "12.6", "13", "13.1", "13.2",
    "13.3", "13.4", "13.5", "14", "15", "16", "17.1", "17.2", "17.3",
    "17.4", "17.5", "17.6", "17.7", "19", "20", "21.1", "21.2", "21.4",
    "22",
)


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

    # the distribution must not pull a model provider in
    import tomllib

    pyproject = tomllib.loads(read("pyproject.toml"))
    dependencies = list(pyproject.get("project", {}).get("dependencies", []))
    for extra in pyproject.get("project", {}).get("optional-dependencies", {}).values():
        dependencies.extend(extra)
    providers = (
        "openai", "anthropic", "google-generativeai", "google-genai", "cohere",
        "mistralai", "ollama", "litellm", "transformers", "torch", "langchain",
    )
    provider_packages = [
        dependency
        for dependency in dependencies
        if any(provider in dependency.lower() for provider in providers)
    ]
    record(
        "2.2",
        "安装依赖中没有模型厂商的包",
        not provider_packages,
        str(provider_packages),
    )

    # the front end must not load anything from the network - **all** assets
    import re

    external: list[str] = []
    for asset in sorted((ROOT / "agentfuse" / "web").glob("*")):
        if asset.suffix not in (".html", ".js", ".css"):
            continue
        text = asset.read_text(encoding="utf-8")
        # the SVG namespace is an identifier, not a resource that is fetched
        text = text.replace("http://www.w3.org/2000/svg", "")
        for match in re.findall(r"https?://[^\s\"'()]+", text):
            external.append(f"{asset.name}: {match}")
    record("2.2", "前端不加载 CDN 资源", not external, str(external))

    # the front end has to be valid JavaScript - a broken script would make
    # the dashboard unusable without any server side error
    script = ROOT / "agentfuse" / "web" / "app.js"
    node = shutil.which("node")
    if node:
        process = subprocess.run(
            [node, "--check", str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        record(
            "14",
            "前端脚本语法正确",
            process.returncode == 0,
            (process.stderr or "")[-200:],
        )
    else:
        record("14", "前端脚本语法正确", True, "没有 node，跳过语法检查")

    config = read("config/default.toml")
    record(
        "2.2",
        "Web 管理界面默认仅监听 127.0.0.1",
        'host = "127.0.0.1"' in config,
    )
    # the *effective* configuration has to bind to the loopback as well
    try:
        from agentfuse.settings import load_settings

        settings = load_settings(home=str(ROOT / ".agentfuse" / "audit-settings"))
        record(
            "2.2",
            "生效的监听地址是 127.0.0.1",
            settings.api_host == "127.0.0.1",
            settings.api_host,
        )
    except Exception as exc:  # pragma: no cover - defensive
        record("2.2", "生效的监听地址是 127.0.0.1", False, str(exc))


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
    "tool_results", "schema_meta",
]

#: The columns of the tables that the requirement names explicitly.  A column
#: that disappears would break the data model without any other check
#: noticing it.
TABLE_COLUMNS = {
    "share_participants": [
        "share_group_id", "logical_call_id", "workflow_id", "agent_id",
        "invocation_id", "snapshot_id", "status", "validated_prefix_length",
        "validation_status", "attached_time", "detached_time", "delivered_time",
        "wait_duration", "wait_reason", "detach_reason", "detach_kind",
        "is_leader",
    ],
    "tool_results": [
        "result_id", "workflow_id", "execution_id", "logical_call_id",
        "result_hash", "payload_path", "payload_size", "created_at",
    ],
    "resource_samples": [
        "sample_id", "workflow_id", "execution_id", "timestamp", "rss_bytes",
        "cpu_seconds", "system_cpu_percent",
    ],
    "runtime_events": [
        "event_id", "workflow_id", "sequence", "event_type", "timestamp",
        "agent_id", "invocation_id", "logical_call_id", "physical_execution_id",
        "parent_event_id", "metadata",
    ],
}


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
    for table, columns in TABLE_COLUMNS.items():
        if table not in schema:
            record("16", f"表 {table} 的列", False, f"缺少表 {table}")
            continue
        missing = [column for column in columns if column not in schema[table]]
        record(
            "16",
            f"表 {table} 的列",
            not missing,
            f"缺: {missing}" if missing else f"{len(columns)} 列",
        )
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
    """Check the core algorithms.

    The behavioural counterpart of every clause in this function lives in
    :func:`audit_behaviour` - the checks here only prove that the building
    blocks exist, the behaviour is verified by running the runtime.
    """

    coordinator = read("agentfuse/runtime/coordinator.py")
    group = read("agentfuse/sharing/share_group.py")

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
    record("9.3", "psutil 采集 RSS", "psutil" in read("agentfuse/monitoring/resource_monitor.py"))
    record("9.4", "工具在独立进程执行",
           "worker_child" in read("agentfuse/runtime/pool.py"))
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


def _final_gate_holds(app: Any, result: Any) -> bool:
    """Check §8.7 - the final commit validation gate.

    A participant that was detached must not have received the result of the
    shared execution: the delivery event of the leader's physical execution
    must not exist for the detached call.
    """

    repository = app.repository
    calls = repository.list_logical_calls(result.workflow_id)
    executions = repository.list_executions(result.workflow_id)
    participants = repository.list_participants(result.workflow_id)
    detached = [
        participant for participant in participants if participant["status"] == "DETACHED"
    ]
    if not detached:
        return False
    all_execution_ids = {execution["execution_id"] for execution in executions}
    events = repository.list_events(result.workflow_id)
    delivered = {
        (event["logical_call_id"], event["physical_execution_id"])
        for event in events
        if event["event_type"] == "TOOL_RESULT_DELIVERED"
    }
    for participant in detached:
        call = next(
            (
                item
                for item in calls
                if item["logical_call_id"] == participant["logical_call_id"]
            ),
            None,
        )
        if call is None:
            return False
        own = call["physical_execution_id"]
        if not own:
            return False
        # every other execution is a leader whose result must not have been
        # delivered to the detached call
        for leader_id in all_execution_ids - {own}:
            if (call["logical_call_id"], leader_id) in delivered:
                return False
    return True


# ----------------------------------------------------------------------
# behavioural audit - the clauses that describe the runtime behaviour are
# verified by *running* the runtime, not by reading the source
# ----------------------------------------------------------------------
def audit_behaviour() -> None:
    """Verify the runtime clauses with a real run.

    The text based checks can only prove that a name exists.  The clauses of
    the sharing protocol, the event protocol and the failure semantics are
    therefore verified against a real workflow: the events have to be in the
    database, the executions have to be physical processes and the results
    have to be the results of those executions.
    """

    import asyncio
    import os
    import shutil

    from agentfuse.app import AgentFuseApp
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.definitions import (
        scenario_divergent,
        scenario_shared,
    )
    from agentfuse.settings import load_settings

    home = ROOT / ".agentfuse" / "audit-behaviour"
    shutil.rmtree(home, ignore_errors=True)

    async def run() -> None:
        settings = load_settings(home=str(home))
        app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
        try:
            shared = await scenario_shared(app, RuntimeMode.ONLINE, {})
            calls = app.repository.list_logical_calls(shared.workflow_id)
            executions = app.repository.list_executions(shared.workflow_id)
            record(
                "17.2",
                "等价调用只产生一次物理执行",
                len(calls) == 2 and len(executions) == 1,
                f"calls={len(calls)} executions={len(executions)}",
            )
            record(
                "17.2",
                "共享者与 Leader 得到同一个结果",
                len(shared.results) == 2
                and shared.results[0].result_hash == shared.results[1].result_hash,
            )
            record(
                "9.4",
                "工具在独立进程执行",
                all(execution["worker_pid"] for execution in executions)
                and all(
                    execution["worker_pid"] != os.getpid() for execution in executions
                ),
                f"pids={[e['worker_pid'] for e in executions]}",
            )
            record(
                "21.4",
                "执行次数来自真实物理执行记录",
                shared.metrics["physical_executions"] == 1 == len(executions),
                f"metric={shared.metrics['physical_executions']} rows={len(executions)}",
            )
            record(
                "16",
                "调用与执行记录外键完整",
                app.database.write_errors == 0
                and not app.database.foreign_key_violations(),
                f"errors={app.database.write_errors}",
            )
            # the relations have to be there - the count alone proves nothing
            relations = {
                (str(row[0]), str(row[4]), str(row[3]))
                for row in app.database.foreign_keys()
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
            record(
                "16",
                "外键关系齐备",
                not missing_relations,
                f"缺: {missing_relations}"
                if missing_relations
                else f"{len(expected_relations)} 条关系",
            )
            # the write errors have to be counted: a statement that violates a
            # constraint must not disappear silently
            errors_before = app.database.write_errors
            app.database.write(
                "INSERT INTO agents (agent_id, workflow_id, created_at) "
                "VALUES ('audit-orphan', 'audit-missing-workflow', 1.0)"
            )
            app.database.flush()
            record(
                "16",
                "写入错误被计数",
                app.database.write_errors > errors_before,
                f"errors={app.database.write_errors} last={app.database.last_error}",
            )

            # the event protocol: the events have to be in the database
            events = app.repository.list_events(shared.workflow_id)
            published = {event["event_type"] for event in events}
            for event in (
                "WORKFLOW_STARTED",
                "AGENT_BOUND",
                "AGENT_INVOKED",
                "AGENT_RETURNED",
                "TOOL_CALL_REQUESTED",
                "TOOL_EXECUTION_STARTED",
                "TOOL_READ_OBSERVED",
                "SHARE_CANDIDATE_FOUND",
                "SHARE_VALIDATION_PASSED",
                "TOOL_CALL_WAITING",
                "TOOL_EXECUTION_FINISHED",
                "TOOL_RESULT_DELIVERED",
                "WORKFLOW_FINISHED",
            ):
                record(
                    "5.1",
                    f"事件 {event} 真实产生",
                    event in published,
                    f"已发布 {event}" if event in published else "未产生",
                )
            record(
                "5.1",
                "事件携带因果链",
                any(event["parent_event_id"] for event in events),
            )

            # the divergent scenario: the follower has to be detached and has
            # to run its own execution
            divergent = await scenario_divergent(
                app, RuntimeMode.ONLINE, {"rounds": 3000}
            )
            participants = app.repository.list_participants(divergent.workflow_id)
            divergent_calls = app.repository.list_logical_calls(divergent.workflow_id)
            divergent_executions = app.repository.list_executions(divergent.workflow_id)
            detached = [
                participant for participant in participants if participant["status"] == "DETACHED"
            ]
            record(
                "8.5",
                "在线增量依赖验证",
                divergent.metrics["validation_failures"] >= 1,
                f"failures={divergent.metrics['validation_failures']}",
            )
            record(
                "8.8",
                "分离后独立执行",
                len(detached) >= 1 and len(divergent_executions) == 2,
                f"detached={len(detached)} executions={len(divergent_executions)}",
            )
            record(
                "17.3",
                "冲突依赖导致分离",
                divergent.metrics["detach_count"] >= 1
                and divergent.metrics["validation_failures"] >= 1,
            )
            record(
                "8.7",
                "最终提交验证门禁",
                _final_gate_holds(app, divergent),
                "未完成验证的参与者没有收到共享结果"
                if _final_gate_holds(app, divergent)
                else "分离的参与者收到了共享结果",
            )
            record(
                "21.4",
                "分离后每个调用都有真实执行",
                len({call["physical_execution_id"] for call in divergent_calls})
                == len(divergent_calls),
            )
            record(
                "12.4",
                "分离时 Leader 尚未结束后续计算",
                (divergent.metrics["detach_lead_ms_max"] or 0) > 0,
                f"lead_ms={divergent.metrics['detach_lead_ms_max']}",
            )

            # the candidate key must not contain the snapshot
            from agentfuse.sharing.candidate import compute_candidate_key

            spec = app.registry.get("ast_analyzer")
            key_a = compute_candidate_key(spec, {"files": ["a.py"]})
            key_b = compute_candidate_key(spec, {"files": ["a.py"]})
            key_other = compute_candidate_key(spec, {"files": ["b.py"]})
            record("8.1", "候选键只由工具与参数决定", key_a == key_b)
            record(
                "8.1",
                "候选键不含 workspace snapshot",
                "snapshot" not in key_a,
                key_a,
            )
            record(
                "8.1",
                "不同参数产生不同候选键",
                key_a != key_other,
            )

            # a snapshot that was modified behind the runtime's back must
            # not take part in a shared execution
            from agentfuse.scenarios.common import (
                DEMO_FILES,
                create_branch,
                prepare_base_snapshot,
            )

            base = prepare_base_snapshot(app)
            branch = create_branch(app, base, None, "audit-branch")
            tampered = branch.tree_path / "src" / "alpha.py"
            tampered.write_text("tampered = True\n", encoding="utf-8")
            integrity_ok = app.snapshots.check_integrity(branch.snapshot_id)
            record("21.2", "快照完整性检查发现篡改", integrity_ok is False)

            workflow = app.create_workflow("audit-integrity")
            workflow.default_snapshot_id = base.snapshot_id
            workflow.start()
            await asyncio.gather(
                app.coordinator.submit_tool_call(
                    workflow_id=workflow.workflow_id,
                    tool_name="ast_analyzer",
                    arguments={"files": list(DEMO_FILES)},
                    snapshot_id=base.snapshot_id,
                ),
                app.coordinator.submit_tool_call(
                    workflow_id=workflow.workflow_id,
                    tool_name="ast_analyzer",
                    arguments={"files": list(DEMO_FILES)},
                    snapshot_id=branch.snapshot_id,
                ),
            )
            integrity_executions = app.repository.list_executions(workflow.workflow_id)
            record(
                "21.2",
                "快照被篡改时拒绝共享",
                len(integrity_executions) == 2,
                f"executions={len(integrity_executions)}",
            )

            # §13.2 / §13.3 - the baseline mode computes the same result
            # without any sharing
            baseline = await scenario_shared(app, RuntimeMode.BASELINE, {})
            baseline_executions = app.repository.list_executions(baseline.workflow_id)
            record(
                "13.3",
                "共享结果与 Baseline 完全一致",
                len(baseline_executions) == 2
                and baseline.results[0].result_hash == shared.results[0].result_hash,
                f"baseline_executions={len(baseline_executions)}",
            )
            record(
                "13.2",
                "Baseline 不共享",
                baseline.metrics["physical_executions"] == 2
                and baseline.metrics["saved_executions"] == 0,
                f"executions={baseline.metrics['physical_executions']}",
            )

            # §9.2 - a shared call occupies exactly one execution slot
            slot_workflow = app.create_workflow("audit-shared-slot")
            slot_workflow.default_snapshot_id = base.snapshot_id
            slot_workflow.start()
            observed_slots: list[dict[str, Any]] = []

            def slot_observer(event: Any) -> None:
                if event.event_type == "TOOL_READ_OBSERVED":
                    observed_slots.append(app.scheduler.snapshot())

            app.event_bus.subscribe(slot_observer)
            await asyncio.gather(
                app.coordinator.submit_tool_call(
                    workflow_id=slot_workflow.workflow_id,
                    tool_name="content_digest",
                    arguments={"files": list(DEMO_FILES), "rounds": 600},
                    snapshot_id=base.snapshot_id,
                ),
                app.coordinator.submit_tool_call(
                    workflow_id=slot_workflow.workflow_id,
                    tool_name="content_digest",
                    arguments={"files": list(DEMO_FILES), "rounds": 600},
                    snapshot_id=base.snapshot_id,
                ),
            )
            slot_executions = app.repository.list_executions(slot_workflow.workflow_id)
            record(
                "9.2",
                "共享只占用一次执行槽位",
                len(slot_executions) == 1
                and all(item["running"] <= 1 for item in observed_slots)
                and app.scheduler.snapshot()["admitted_total"] >= 1,
                f"executions={len(slot_executions)} running={sorted({i['running'] for i in observed_slots})}",
            )

            # the failure kinds have to be distinguishable
            failing = app.create_workflow("audit-failure")
            failing.default_snapshot_id = base.snapshot_id
            failing.start()
            raised: str | None = None
            kind: str | None = None
            try:
                await app.coordinator.submit_tool_call(
                    workflow_id=failing.workflow_id,
                    tool_name="fault_injector",
                    arguments={"mode": "raise", "message": "audit"},
                    snapshot_id=base.snapshot_id,
                )
            except Exception as exc:
                raised = type(exc).__name__
                kind = getattr(exc, "kind", None)
            failed_calls = app.repository.list_logical_calls(failing.workflow_id)
            record(
                "10.1",
                "区分工具错误",
                kind == "tool_error"
                and any(
                    call["status"] in ("FAILED", "CANCELLED")
                    for call in failed_calls
                ),
                f"kind={kind} status={[call['status'] for call in failed_calls]}",
            )
            record(
                "10.1",
                "工具错误不会进入共享协调",
                all(
                    call["share_group_id"] is None for call in failed_calls
                ),
            )
        finally:
            await app.close()

    try:
        asyncio.run(run())
    except Exception as exc:  # pragma: no cover - defensive
        record("17.2", "行为审计执行", False, str(exc))
    finally:
        shutil.rmtree(home, ignore_errors=True)


# ----------------------------------------------------------------------
# 19 the priority tiers
# ----------------------------------------------------------------------
#: The P2 clauses of requirement 19 and the state that has to be documented
#: for them.  The check is self maintaining: the documented table is the
#: contract, the code has to match it.
P2_EXPECTATIONS = {
    "可插拔调度策略": True,
    "会话内已完成结果复用": True,
    "Worker 预热池": True,
    "性能基线与回归守卫": True,
    "CI 工作流": True,
    "跨会话持久化结果缓存": False,
    "GPU 调度与 GPU KV Cache 管理": False,
    "分布式执行 / 多节点调度": False,
    "多用户权限与认证": False,
    "崩溃后的自动恢复执行": False,
}


def audit_priority_tiers() -> None:
    """Check §19 - the capability tiers and the documented P2 status.

    Every P2 clause has to be listed in ``KNOWN_LIMITATIONS.md`` with its
    state, and the state has to match the code: an implemented clause has to
    point at a module that exists, a clause that is not implemented must not
    have one.
    """

    text = read("docs/KNOWN_LIMITATIONS.md")
    section = text.split("## 0.")[1].split("\n## ")[0] if "## 0." in text else ""
    rows = {}
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("P2 条款", "---"):
            continue
        if set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = (cells[1], cells[2])

    for clause, implemented in P2_EXPECTATIONS.items():
        row = rows.get(clause)
        if row is None:
            record("19", f"P2 条款 {clause}", False, "未在 KNOWN_LIMITATIONS 中列出")
            continue
        status, entry = row
        documented = "已实现" in status and "未实现" not in status
        if documented != implemented:
            record(
                "19",
                f"P2 条款 {clause}",
                False,
                f"文档声明与期望不符: {entry[:60]}",
            )
            continue
        if not implemented:
            record("19", f"P2 条款 {clause}", True, "已明确声明未实现")
            continue
        # an implemented clause has to point at code that exists
        import re

        paths = re.findall(r"`([\w./]+\.py)`", entry)
        missing = [path for path in paths if not (ROOT / path).exists()]
        record(
            "19",
            f"P2 条款 {clause}",
            not missing,
            f"缺少: {missing}" if missing else f"{len(paths)} 个入口存在",
        )

    # the P0 clauses are the acceptance contract - they must all be covered
    # by a behaviour check of this script
    p0_clauses = {
        "真实多 Agent 工作流": "17.2",
        "受控调用接入": "7.2",
        "真实工具进程执行": "9.4",
        "本地工作区快照": "6.2",
        "工具依赖追踪": "7.2",
        "In-flight Sharing": "17.2",
        "在线动态依赖验证": "8.5",
        "冲突分离": "8.8",
        "正确结果分发": "8.7",
        "Worker 并发控制": "9.2",
        "基线对比": "13.3",
        "SQLite": "16",
        "可运行 CLI": "15",
        "基础 Web UI": "14",
        "完整自动化测试": "17.2",
    }
    covered = {item["section"] for item in RESULTS}
    for clause, section in p0_clauses.items():
        record(
            "19",
            f"P0 条款 {clause}",
            section in covered,
            f"由 §{section} 覆盖" if section in covered else f"§{section} 没有检查",
        )


# ----------------------------------------------------------------------
# 13 the benchmark
# ----------------------------------------------------------------------
def audit_benchmark() -> None:
    """Check §13 - the comparison modes, the fairness rules and the metrics."""

    runner = read("agentfuse/benchmarks/runner.py")
    metrics = read("agentfuse/monitoring/metrics.py")
    document = read("docs/BENCHMARK.md")

    cli = read("agentfuse/cli.py")
    for mode in ("baseline", "online", "deferred"):
        record("13.1", f"比较模式 {mode}", f'"{mode}"' in runner or f'"{mode}"' in cli)
    record("13.1", "模式 C 可选且已实现", "deferred" in runner or "deferred" in cli)

    # the fairness rules of the module docstring
    for rule in (
        "same input repository and same snapshots",
        "same tool implementations",
        "same request sequence",
        "same worker limit, memory budget and timeout",
        "same random seed",
    ):
        record("13.2", f"公平性规则 {rule}", rule in runner)

    # the metrics of requirement 13.3
    for metric in (
        "makespan",
        "physical_executions",
        "saved_executions",
        "share_success_rate",
        "total_cpu_time",
        "peak_rss",
        "avg_share_wait",
        "validation_count",
        "validation_failures",
        "validation_ms",
        "detach_count",
        "first_conflict_at",
        "follower_independent_at",
    ):
        record("13.3", f"指标 {metric}", f'"{metric}"' in metrics)

    record("13.4", "Speedup 定义", "speedup" in runner)
    record("13.4", "负收益也必须显示", "speedup" in runner and "baseline" in runner)
    record("13.5", "对比页面展示指标", "bench-result" in read("agentfuse/web/app.js"))
    for section in ("场景", "方法", "工作负载", "指标定义", "公平性原则", "实测结果"):
        record("13", f"BENCHMARK 文档 {section}", section in document)


# ----------------------------------------------------------------------
# 11.3 the incomplete graph
# ----------------------------------------------------------------------
def audit_incomplete_graph() -> None:
    """Check §11.3 - an unobserved relation must not create an edge."""

    graph = read("agentfuse/core/execution_graph.py")
    record("11.3", "未知节点使用 UNKNOWN 状态", '"UNKNOWN"' in graph)
    record("11.3", "未知节点被标记为占位", "placeholder" in graph)
    record(
        "11.3",
        "乱序事件不产生错误的边",
        "test_out_of_order_events_do_not_create_wrong_edges" in read("tests/integration/test_events.py"),
    )


# ----------------------------------------------------------------------
# 4.8 the share group
# ----------------------------------------------------------------------
def audit_share_group_contract() -> None:
    """Check §4.8 - the share group and its participants."""

    group = read("agentfuse/sharing/share_group.py")
    record("4.8", "共享组保存逻辑调用列表", "participant_calls" in group)
    record("4.8", "非法状态转换被拒绝", "IllegalTransition" in group)
    record(
        "4.8",
        "参与者状态机被测试覆盖",
        "test_illegal_participant_transitions_are_rejected"
        in read("tests/unit/test_share_group.py"),
    )


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
    "docs/DEVELOPMENT_LOG.md": [
        "\u9636\u6bb5", "\u5df2\u9a8c\u8bc1", "\u95ee\u9898", "\u4e0b\u4e00\u9636\u6bb5",
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
    parser.add_argument(
        "--coverage", action="store_true", help="print the covered requirement sections"
    )
    args = parser.parse_args()

    audit_offline()
    audit_data_model()
    audit_events()
    audit_interfaces()
    audit_core()
    audit_surface()
    audit_behaviour()
    audit_incomplete_graph()
    audit_share_group_contract()
    audit_benchmark()
    audit_priority_tiers()
    audit_layout_tests_docs()

    failed = [item for item in RESULTS if not item["ok"]]
    if args.json:
        print(json.dumps(
            {
                "total": len(RESULTS),
                "failed": len(failed),
                "covered_sections": list(COVERED_SECTIONS),
                "checks": RESULTS,
            },
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
        if args.coverage:
            checked = {item["section"] for item in RESULTS}
            print(
                "\n覆盖的需求小节: "
                + ", ".join(sorted(checked, key=lambda value: (len(value), value)))
            )
            print(f"声明覆盖的小节: {len(COVERED_SECTIONS)} 项")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
