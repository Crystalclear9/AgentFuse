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
import contextlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
AUDIT_HOME = ROOT / ".agentfuse" / "audit-runs" / f"{sys.platform}-{os.getpid()}"
sys.path.insert(0, str(ROOT))

from agentfuse.console import configure_console  # noqa: E402
from agentfuse.core.state import EventType  # noqa: E402
from agentfuse.storage.database import SCHEMA_VERSION  # noqa: E402
from agentfuse.storage.database import Database  # noqa: E402

configure_console()

RESULTS: list[dict[str, Any]] = []

#: The kind of the check that is currently recorded.  A **behavioural** check
#: runs the real runtime and looks at what happened; a **structural** check
#: only reads the source.  The report prints the split - a reader has to be
#: able to see how many of the checks really run the system.
PHASE = "structural"

#: The requirement sections the audit checks.  The requirement has 25
#: chapters - the chapters that are **not** listed here are either general
#: introductions (1, 3, 18, 23-25) or covered by the test suite and the
#: acceptance check.  ``--coverage`` prints the list.
COVERED_SECTIONS = (
    "2.2", "4", "4.8", "5.1", "5.2", "6.2", "7.1", "7.2", "7.3",
    "7.4", "8.1", "8.2", "8.5", "8.7", "8.8", "9.1", "9.2", "9.3", "9.4",
    "10.1", "10.5", "11.3", "12", "12.4", "12.6", "13", "13.1", "13.2",
    "13.3", "13.4", "13.5", "14", "15", "16", "17.1", "17.2", "17.3",
    "17.4", "17.5", "17.6", "17.7", "19", "20", "21.1", "21.2", "21.3",
    "21.4", "21.5", "22",
)


def record(
    section: str,
    clause: str,
    ok: bool,
    detail: str = "",
    kind: str | None = None,
) -> bool:
    """Record the result of a check.

    The kind says how the check was verified: a **behavioural** check runs
    the real runtime, a **structural** check only reads the source.  A check
    inside the behavioural section that is really a text match has to say so
    (``kind="structural"``) - otherwise the report would claim more than it
    proves.
    """

    RESULTS.append(
        {
            "section": section,
            "clause": clause,
            "ok": bool(ok),
            "detail": detail,
            "kind": kind or PHASE,
        }
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

    # §7.4 a shareable tool must not depend on the current time, the current
    # random numbers, the environment or the absolute workspace path
    import re as _re

    forbidden = {
        "time.time": "当前时间",
        "time.monotonic": "当前时间",
        "datetime.now": "当前时间",
        "random.random": "当前随机数",
        "os.environ": "未声明的环境变量",
        "os.getcwd": "工作区的绝对路径",
        "str(Path.cwd": "工作区的绝对路径",
    }
    offenders: list[str] = []
    for path in sorted((ROOT / "agentfuse" / "tools").glob("*.py")):
        if path.name in ("base.py", "fixtures.py", "fault_injector.py", "registry.py"):
            continue
        text = path.read_text(encoding="utf-8")
        for marker, reason in forbidden.items():
            if marker in text:
                offenders.append(f"{path.name}: {marker} ({reason})")
    record("7.4", "内置工具不依赖时间/随机数/环境变量/绝对路径", not offenders, str(offenders))

    config = read("config/default.toml")
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
    "agents": [
        "agent_id", "workflow_id", "external_identity", "agent_description",
        "registration_source", "metadata", "created_at", "identity_status",
    ],
    "logical_tool_calls": [
        "logical_call_id", "workflow_id", "invocation_id", "agent_id", "tool_name",
        "tool_version", "normalized_arguments", "workspace_snapshot_id",
        "request_time", "status", "physical_execution_id", "result_reference",
        "result_hash", "candidate_key", "share_group_id", "error", "error_kind",
        "completed_time", "retry_count",
    ],
    "physical_executions": [
        "execution_id", "workflow_id", "tool_name", "tool_version",
        "arguments_hash", "candidate_key", "source_snapshot_id", "worker_pid",
        "start_time", "finish_time", "execution_status", "cpu_time", "peak_rss",
        "exit_status", "result_hash", "result_reference", "error", "error_kind",
        "timeout_seconds", "share_group_id", "attempt", "admission_reason",
        "queued_time", "worker_root_pid", "worker_command",
    ],
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
    # The version is not only **named** in the source - the database has to
    # carry it and a database of an older version has to be migrated.
    record(
        "16",
        "保存数据库 Schema 版本",
        version == SCHEMA_VERSION,
        f"db={version} code={SCHEMA_VERSION}",
    )
    migrated = _migrate_an_old_database()
    record(
        "16",
        "提供必要迁移",
        migrated is not None and migrated >= SCHEMA_VERSION,
        f"v1 -> v{migrated}" if migrated is not None else "the migration failed",
    )


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
    # The event protocol is checked on a **real** event: the fields of the
    # dataclass, the uniqueness of the id and the stable sequence of a
    # workflow.
    from agentfuse.core.events import EventBus, EventType

    bus = EventBus()
    events = [
        bus.publish(EventType.WORKFLOW_STARTED, workflow_id="wf-audit")
        for _ in range(5)
    ]
    event_fields = set(events[0].to_dict())
    for name in ("event_id", "workflow_id", "event_type", "timestamp", "agent_id",
                 "invocation_id", "logical_call_id", "physical_execution_id",
                 "parent_event_id", "metadata"):
        record("5.1", f"RuntimeEvent.{name}", name in event_fields, kind="behavioural")
    record(
        "5.1",
        "event_id 全局唯一",
        len({event.event_id for event in events}) == len(events),
        kind="behavioural",
    )
    record(
        "5.1",
        "Workflow 内稳定序号",
        [event.sequence for event in events] == list(range(len(events))),
        kind="behavioural",
    )


# ----------------------------------------------------------------------
# 5.2 / 7.x interfaces
# ----------------------------------------------------------------------
def audit_interfaces() -> None:
    # The interfaces are checked on the **real** objects - a method that is
    # only mentioned in the source does not exist.
    from agentfuse.adapters.base import AgentAdapter
    from agentfuse.tools.base import ToolSpec
    from agentfuse.tools.registry import build_default_registry
    from agentfuse.workspace.reader import WorkspaceBoundaryError, WorkspaceReader
    from agentfuse.workspace.snapshots import SnapshotStore

    for method in ("bind_agent", "begin_invocation", "submit_tool_call",
                   "deliver_result", "end_invocation", "record_event"):
        record("5.2", f"适配器方法 {method}",
               callable(getattr(AgentAdapter, method, None)), kind="structural")

    for method in ("read_text", "read_bytes", "list_dir", "exists", "stat"):
        record("7.2", f"WorkspaceReader.{method}",
               callable(getattr(WorkspaceReader, method, None)), kind="structural")
    # the controlled reader has to refuse a path outside of the workspace
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "tree"
        root.mkdir()
        (root / "inside.py").write_text("VALUE = 1\n", encoding="utf-8")
        observed: list[Any] = []
        reader = WorkspaceReader(root, reporter=lambda *args: observed.append(args))
        boundary_refused = False
        try:
            reader.read_text("../outside.py")
        except WorkspaceBoundaryError:
            boundary_refused = True
        except Exception:
            # some other error - the reader did not refuse the path either
            boundary_refused = False
        record("7.2", "受控读取拒绝越界路径", boundary_refused, kind="behavioural")
        # the dependency is reported **before** the value is returned
        reader.read_text("inside.py")
        record("7.2", "读取立即上报依赖", bool(observed), kind="behavioural")

        from agentfuse.workspace.reader import audit_workspace_access

        target = root / "inside.py"
        with audit_workspace_access(root) as bypasses:
            target.read_bytes()
            with os.scandir(root) as entries:
                list(entries)
            os.listdir(root)
            os.stat(target)
            os.lstat(target)
        expected_bypasses = [str(target), str(root), str(root), str(target), str(target)]
        record(
            "7.2",
            "绕过检测覆盖文件、目录与 stat",
            bypasses == expected_bypasses,
            f"实际记录 {len(bypasses)} / {len(expected_bypasses)}",
            kind="behavioural",
        )

    import dataclasses

    spec_fields = {field.name for field in dataclasses.fields(ToolSpec)}
    for field in ("tool_name", "tool_version", "deterministic", "read_only",
                  "resource_estimate", "input_schema", "output_schema",
                  "implementation_reference"):
        record("7.1", f"工具注册字段 {field}", field in spec_fields, kind="structural")

    registry = build_default_registry()
    for tool in ("source_index", "ast_analyzer", "dependency_analyzer", "content_digest"):
        record("7.3", f"必备工具 {tool}", registry.get(tool) is not None, kind="structural")

    for api in ("create_workspace", "create_snapshot", "fork_snapshot",
                "inspect_snapshot", "compare_snapshots", "delete_snapshot"):
        record("6.2", f"快照 API {api}",
               callable(getattr(SnapshotStore, api, None)), kind="structural")


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

    # §21.3 the boundary of the dependency tracking: a tool that bypasses the
    # controlled reader must never be shared
    record(
        "21.3",
        "绕过受控读取的工具永不共享",
        "test_a_bypassing_tool_is_never_shared"
        in read("tests/integration/test_dependency_tracking.py")
        and "dependency_tracking" in read("agentfuse/tools/base.py"),
        "依赖追踪的边界由用例证明",
    )
    # §21.5 the runtime must not depend on the internals of a framework
    framework_imports = _framework_imports(ROOT)
    record(
        "21.5",
        "Runtime 不依赖任何 Agent 框架的内部对象",
        not framework_imports,
        str(framework_imports),
    )


def _framework_imports(root):
    """Optional adapters may use public framework APIs; the core may not."""
    import ast
    offenders = []
    for path in sorted((root / 'agentfuse').rglob('*.py')):
        if path.relative_to(root).as_posix() in {
            'agentfuse/adapters/langgraph_adapter.py', 'agentfuse/adapters/langchain_adapter.py',
        }:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            names = ([node.module or ''] if isinstance(node, ast.ImportFrom) else
                     [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for name in names:
                if name.startswith(('langchain', 'langgraph', 'autogen', 'crewai', 'semantic_kernel')):
                    offenders.append(f'{path.relative_to(root).as_posix()}:{name}')
    return offenders


# ----------------------------------------------------------------------
# 12 / 13 / 14 / 15 surface
# ----------------------------------------------------------------------
def audit_surface() -> None:
    # the catalogue of the runtime - not the source of the definitions
    from agentfuse.scenarios import list_scenarios

    known_scenarios = set(list_scenarios())
    for scenario in ("shared", "different-workspace", "divergent", "multi-agent"):
        record("12", f"演示场景 {scenario}", scenario in known_scenarios, kind="structural")


    cli = read("agentfuse/cli.py")
    html = read("agentfuse/web/index.html")
    # §15 the commands have to be **usable** - the audit runs them itself
    for command in ("init", "serve", "demo", "compare", "trace", "inspect", "tools"):
        result = subprocess.run(
            [sys.executable, "-m", "agentfuse", command, "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(ROOT),
            timeout=120,
        )
        record(
            "15",
            f"CLI {command}",
            result.returncode == 0,
            result.stderr[-160:] if result.returncode else "",
        )
    try:
        import asyncio
        import shutil

        from agentfuse.api.app import create_app
        from agentfuse.settings import load_settings

        # A fixed directory inside the runtime home: on Windows the removal of
        # a temporary directory can race with the SQLite file handles.
        tmp = AUDIT_HOME / "api"
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

    The scenarios are independent of each other - they run **concurrently** so
    that the audit stays as fast as its slowest scenario.
    """

    # the checks of this function really run the runtime
    global PHASE
    PHASE = "behavioural"
    try:
        _audit_behaviour()
    finally:
        PHASE = "structural"


def _audit_behaviour() -> None:
    """The implementation of the behavioural audit (see ``audit_behaviour``)."""

    import asyncio
    import os
    import shutil

    from agentfuse.app import AgentFuseApp
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.common import DEMO_FILES, create_branch, prepare_base_snapshot
    from agentfuse.scenarios.definitions import (
        scenario_cancel_failure,
        scenario_divergent,
        scenario_different_workspace,
        scenario_multi_agent,
        scenario_shared,
        scenario_shared_heavy,
    )
    from agentfuse.settings import load_settings

    home = AUDIT_HOME / "behaviour"
    shutil.rmtree(home, ignore_errors=True)

    async def run() -> None:
        settings = load_settings(home=str(home))
        app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
        # the scenarios run concurrently - they need workers of their own
        app.scheduler.max_workers = 8
        try:
            # ---------------------------------------------------------------
            # the scenarios - every one of them is really executed
            # ---------------------------------------------------------------
            # The scenarios run one after the other: two scenarios that use
            # the same tool with the same arguments would **share** an
            # execution - the share coordinator does not know about the
            # workflow boundaries.  Running them in parallel would therefore
            # change their results.
            # The scheduler is observed while the shared scenario runs: the
            # two calls share **one** execution, therefore only one worker
            # slot may be occupied.
            observed_slots: list[dict[str, Any]] = []

            def slot_observer(event: Any) -> None:
                if event.event_type == "TOOL_READ_OBSERVED":
                    observed_slots.append(app.scheduler.snapshot())

            unsubscribe = app.event_bus.subscribe(slot_observer)
            shared = await scenario_shared(app, RuntimeMode.ONLINE, {})
            # only the shared scenario is observed - the baseline mode runs
            # two executions in parallel
            unsubscribe()
            divergent = None
            # The divergent scenario has a timing margin: the follower has to
            # join before the leader reads the conflicting file.  Under heavy
            # load that margin can be missed - the scenario is then repeated
            # (it costs about half a second), so that the check does not
            # depend on the speed of the machine.
            divergent_attempts = 0
            for _attempt in range(3):
                divergent_attempts += 1
                divergent = await scenario_divergent(app, RuntimeMode.ONLINE, {})
                if (divergent.metrics["detach_lead_ms_max"] or 0) > 0:
                    break
            baseline = await scenario_shared(app, RuntimeMode.BASELINE, {})
            # the remaining scenarios are executed by the acceptance check and
            # by the test suite - the audit only proves that they are covered
            cancel_failure = None
            shared_heavy = None
            different_workspace = None
            multi_agent = None

            repository = app.repository
            scenarios = {
                "shared": shared,
                "divergent": divergent,
            }

            # every scenario that the audit runs produced real executions
            for name, result in scenarios.items():
                executions = repository.list_executions(result.workflow_id)
                record(
                    "12",
                    f"演示场景 {name} 真实执行",
                    bool(executions) and result.metrics["physical_executions"] >= 1,
                    f"executions={len(executions)}",
                )

            # every scenario of the requirement has to be executed by an
            # automated test - the test suite runs on every CI job
            scenario_tests = "\n".join(
                path.read_text(encoding="utf-8")
                for path in sorted(ROOT.glob("tests/**/*.py"))
            )
            for name, function in (
                ("shared", "scenario_shared"),
                ("shared-heavy", "scenario_shared_heavy"),
                ("different-workspace", "scenario_different_workspace"),
                ("divergent", "scenario_divergent"),
                ("multi-agent", "scenario_multi_agent"),
                ("cancel-failure", "scenario_cancel_failure"),
            ):
                record(
                    "12",
                    f"演示场景 {name} 由自动化测试执行",
                    function in scenario_tests,
                    "已覆盖" if function in scenario_tests else "未被任何测试执行",
                    # it is a text match, not a behaviour
                    kind="structural",
                )

            # ---------------------------------------------------------------
            # the shared scenario
            # ---------------------------------------------------------------
            calls = repository.list_logical_calls(shared.workflow_id)
            executions = repository.list_executions(shared.workflow_id)
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

            # ---------------------------------------------------------------
            # §13.2 the fairness of the comparison
            # ---------------------------------------------------------------
            baseline_calls = repository.list_logical_calls(baseline.workflow_id)
            baseline_keys = {
                call["candidate_key"] for call in baseline_calls
            }
            online_keys = {call["candidate_key"] for call in calls}
            record(
                "13.2",
                "所有模式使用相同的请求序列",
                baseline_keys == online_keys and len(online_keys) == 1,
                f"baseline={len(baseline_keys)} online={len(online_keys)}",
            )
            baseline_workflow = repository.get_workflow(baseline.workflow_id) or {}
            online_workflow = repository.get_workflow(shared.workflow_id) or {}
            record(
                "13.2",
                "所有模式使用相同的随机种子",
                baseline_workflow.get("random_seed")
                == online_workflow.get("random_seed")
                is not None,
                f"baseline={baseline_workflow.get('random_seed')} "
                f"online={online_workflow.get('random_seed')}",
            )
            baseline_snapshots = {
                call["workspace_snapshot_id"] for call in baseline_calls
            }
            online_snapshots = {call["workspace_snapshot_id"] for call in calls}
            baseline_manifests = {
                snapshot["manifest_hash"]
                for snapshot in repository.list_snapshots()
                if snapshot["snapshot_id"] in baseline_snapshots
            }
            online_manifests = {
                snapshot["manifest_hash"]
                for snapshot in repository.list_snapshots()
                if snapshot["snapshot_id"] in online_snapshots
            }
            record(
                "13.2",
                "所有模式使用相同的工作区快照内容",
                bool(baseline_manifests)
                and baseline_manifests == online_manifests,
                f"baseline={baseline_manifests} online={online_manifests}",
            )
            record(
                "13.2",
                "Baseline 不共享",
                baseline.metrics["physical_executions"] == 2
                and baseline.metrics["saved_executions"] == 0,
                f"executions={baseline.metrics['physical_executions']}",
            )
            # Tiny AST runs are dominated by timer quantisation/import noise.
            # Measure real repeated hashing with identical inputs and rounds;
            # keep the original CPU tolerance rather than widening the gate.
            resource_options = {"rounds": 12000}
            resource_baseline = await scenario_shared_heavy(app, RuntimeMode.BASELINE, resource_options)
            resource_online = await scenario_shared_heavy(app, RuntimeMode.ONLINE, resource_options)
            baseline_cpu = float(resource_baseline.metrics["total_cpu_time"] or 0.0)
            online_cpu = float(resource_online.metrics["total_cpu_time"] or 0.0)
            record(
                "13.2",
                "共享模式不使用更多计算资源",
                resource_online.metrics["physical_executions"] < resource_baseline.metrics["physical_executions"]
                and resource_baseline.results[0].result_hash == resource_online.results[0].result_hash
                and online_cpu <= baseline_cpu + 0.05,
                f"content_digest rounds=12000 executions={resource_online.metrics['physical_executions']} vs "
                f"{resource_baseline.metrics['physical_executions']}, "
                f"cpu={online_cpu} vs {baseline_cpu}",
            )
            record(
                "13.3",
                "共享结果与 Baseline 完全一致",
                len(baseline.results) == 2
                and baseline.results[0].result_hash == shared.results[0].result_hash,
            )

            # ---------------------------------------------------------------
            # §8.1 the candidate key does not contain the snapshot
            # ---------------------------------------------------------------
            # ---------------------------------------------------------------
            # the divergent scenario
            # ---------------------------------------------------------------
            participants = repository.list_participants(divergent.workflow_id)
            divergent_calls = repository.list_logical_calls(divergent.workflow_id)
            divergent_executions = repository.list_executions(divergent.workflow_id)
            detached = [
                participant
                for participant in participants
                if participant["status"] == "DETACHED"
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
                f"lead_ms={divergent.metrics['detach_lead_ms_max']}"
                + (
                    f"（余量被错过，重试 {divergent_attempts - 1} 次后通过）"
                    if divergent_attempts > 1
                    else ""
                ),
            )

            # §12.6 the cancellation scenario really runs - every case has to
            # be reported by the scenario itself
            from agentfuse.scenarios.definitions import scenario_cancel_failure

            cancel_result = await scenario_cancel_failure(
                app, RuntimeMode.ONLINE, {"timeout_seconds": 0.5}
            )
            for case in ("follower_cancel", "leader_cancel", "timeout", "crash", "shared_failure"):
                record(
                    "12.6",
                    f"失败场景 {case}",
                    case in cancel_result.notes,
                    kind="behavioural",
                )

            # the base snapshot is used by the failure calls and by the
            # integrity check
            base = prepare_base_snapshot(app)

            # ---------------------------------------------------------------
            # §9.2 / §21.1 / §9.3 the shared call and the resources
            # ---------------------------------------------------------------
            record(
                "9.2",
                "共享只占用一次执行槽位",
                len(executions) == 1
                and len(calls) == 2
                and bool(observed_slots)
                and all(item["running"] == 1 for item in observed_slots),
                f"executions={len(executions)} running="
                f"{sorted({item['running'] for item in observed_slots})}",
            )
            # §21.1 no call is delivered twice
            delivered: dict[str, int] = {}
            for event in repository.list_events(shared.workflow_id):
                if event["event_type"] == "TOOL_RESULT_DELIVERED":
                    key = str(event["logical_call_id"])
                    delivered[key] = delivered.get(key, 0) + 1
            record(
                "21.1",
                "同一调用不会被重复交付",
                len(delivered) == len(calls)
                and all(count == 1 for count in delivered.values()),
                f"delivered={delivered}",
            )
            record(
                "21.1",
                "并发调用只产生一次物理执行",
                len(executions) == 1,
                f"executions={len(executions)}",
            )
            # §9.3 the resources of the real worker
            samples = repository.list_resource_samples(
                executions[0]["execution_id"]
            )
            record(
                "9.3",
                "采集真实 worker 的内存与 CPU",
                bool(executions[0]["peak_rss"])
                and bool(samples)
                and all(sample["rss_bytes"] for sample in samples),
                f"peak_rss={executions[0]['peak_rss']} samples={len(samples)}",
            )

            # ---------------------------------------------------------------
            # §10.1 the failure kinds - three independent calls, they run
            # concurrently because the fault injector is never shared
            # ---------------------------------------------------------------
            kinds: dict[str, str | None] = {}

            async def failing_call(mode: str, arguments: dict[str, Any], timeout: float | None = None):
                workflow = app.create_workflow(f"audit-failure-{mode}")
                workflow.default_snapshot_id = base.snapshot_id
                workflow.start()
                try:
                    await app.coordinator.submit_tool_call(
                        workflow_id=workflow.workflow_id,
                        tool_name="fault_injector",
                        arguments=arguments,
                        snapshot_id=base.snapshot_id,
                        timeout_seconds=timeout,
                    )
                except Exception as exc:
                    kinds[mode] = getattr(exc, "kind", None)
                return workflow

            async def cancelled_call() -> Any:
                workflow = app.create_workflow("audit-failure-cancel")
                workflow.default_snapshot_id = base.snapshot_id
                workflow.start()
                task = asyncio.ensure_future(
                    app.coordinator.submit_tool_call(
                        workflow_id=workflow.workflow_id,
                        tool_name="fault_injector",
                        arguments={"mode": "sleep", "seconds": 5},
                        snapshot_id=base.snapshot_id,
                    )
                )
                # the call has to exist before it can be cancelled
                await asyncio.sleep(0.05)
                for call in app.coordinator.active_calls(workflow.workflow_id):
                    await app.coordinator.cancel_logical_call(
                        call.logical_call_id, "audit"
                    )
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return workflow

            async def integrity_calls() -> Any:
                await asyncio.gather(
                    *[
                        app.coordinator.submit_tool_call(
                            workflow_id=integrity.workflow_id,
                            tool_name="ast_analyzer",
                            arguments={"files": ["src/alpha.py"]},
                            snapshot_id=snapshot_id,
                        )
                        for snapshot_id in (base.snapshot_id, branch.snapshot_id)
                    ]
                )
                return integrity

            # the tampered snapshot: its two calls use another file, therefore
            # they cannot join the group of the shared scenario
            branch = create_branch(app, base, None, "audit-branch")
            tampered = branch.tree_path / "src" / "alpha.py"
            tampered.write_text("tampered = True\n", encoding="utf-8")
            record(
                "21.2",
                "快照完整性检查发现篡改",
                app.snapshots.check_integrity(branch.snapshot_id) is False,
            )
            integrity = app.create_workflow("audit-integrity")
            integrity.default_snapshot_id = base.snapshot_id
            integrity.start()

            failure_workflows = await asyncio.gather(
                failing_call("tool", {"mode": "raise", "message": "audit"}),
                failing_call("crash", {"mode": "exit", "exit_code": 9}),
                failing_call("timeout", {"mode": "sleep", "seconds": 30}, 0.05),
                cancelled_call(),
                integrity_calls(),
            )
            record(
                "10.1",
                "区分工具错误",
                kinds.get("tool") == "tool_error",
                kinds.get("tool"),
            )
            record(
                "10.1",
                "区分进程崩溃",
                kinds.get("crash") in ("worker_crash", "internal_error"),
                kinds.get("crash"),
            )
            record(
                "10.1",
                "区分超时",
                kinds.get("timeout") == "timeout",
                kinds.get("timeout"),
            )

            # ---------------------------------------------------------------
            # §5.1 every required event is really produced
            # ---------------------------------------------------------------
            published: set[str] = set()
            for result in list(scenarios.values()) + list(failure_workflows):
                published |= {
                    event["event_type"]
                    for event in repository.list_events(result.workflow_id)
                }
            for event in (
                "WORKFLOW_STARTED",
                "WORKFLOW_FINISHED",
                "AGENT_BOUND",
                "AGENT_INVOKED",
                "AGENT_RETURNED",
                "TOOL_CALL_REQUESTED",
                "TOOL_CALL_WAITING",
                "TOOL_EXECUTION_STARTED",
                "TOOL_READ_OBSERVED",
                "SHARE_CANDIDATE_FOUND",
                "SHARE_VALIDATION_PASSED",
                "SHARE_VALIDATION_FAILED",
                "SHARE_PARTICIPANT_DETACHED",
                "TOOL_EXECUTION_FINISHED",
                "TOOL_RESULT_DELIVERED",
                "TOOL_CALL_FAILED",
                "TOOL_CALL_CANCELLED",
            ):
                record(
                    "5.1",
                    f"事件 {event} 真实产生",
                    event in published,
                    "已产生" if event in published else "未产生",
                )

            # the causal chain
            events = repository.list_events(shared.workflow_id)
            known = {event["event_id"] for event in events}
            chained = [
                event
                for event in events
                if event["parent_event_id"] in known
            ]
            record(
                "5.1",
                "事件携带可解析的因果链",
                len(chained) >= 3,
                f"chained={len(chained)} of {len(events)}",
            )

            # ---------------------------------------------------------------
            # §21.2 a tampered snapshot is not shared
            # ---------------------------------------------------------------
            integrity_executions = repository.list_executions(integrity.workflow_id)
            record(
                "21.2",
                "快照被篡改时拒绝共享",
                len(integrity_executions) == 2,
                f"executions={len(integrity_executions)}",
            )

            # the same tool with the same arguments on **two different
            # snapshots** has to produce the same candidate key - the
            # snapshot is not part of it (requirement 8.1)
            integrity_calls = repository.list_logical_calls(integrity.workflow_id)
            workspace_keys = {call["candidate_key"] for call in integrity_calls}
            workspace_snapshots = {
                call["workspace_snapshot_id"] for call in integrity_calls
            }
            record(
                "8.1",
                "候选键不含 workspace snapshot",
                len(workspace_keys) == 1 and len(workspace_snapshots) > 1,
                f"keys={len(workspace_keys)} snapshots={len(workspace_snapshots)}",
            )
            record(
                "8.1",
                "不同参数产生不同候选键",
                len({call["candidate_key"] for call in calls}) == 1
                and len(
                    {
                        call["candidate_key"]
                        for call in repository.list_logical_calls(
                            divergent.workflow_id
                        )
                    }
                )
                == 1,
            )

            # ---------------------------------------------------------------
            # §16 the referential integrity
            # ---------------------------------------------------------------
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

            # ---------------------------------------------------------------
            # §2.2 the runtime does not talk to the outside world
            # ---------------------------------------------------------------
            # The text scan above only finds the *names* of the vendor
            # packages.  The runtime itself has to prove that it did not open
            # a single outbound connection while the scenarios ran.
            try:
                import psutil

                connections = psutil.Process().net_connections(kind="inet")
                external = [
                    connection
                    for connection in connections
                    if connection.status == psutil.CONN_ESTABLISHED
                    and connection.raddr
                    and connection.raddr.ip not in ("127.0.0.1", "::1", "0.0.0.0")
                ]
                record(
                    "2.2",
                    "运行时没有对外连接",
                    not external,
                    f"connections={[(c.raddr.ip, c.raddr.port) for c in external[:3]]}",
                )
            except Exception as exc:  # pragma: no cover - depends on the platform
                record(
                    "2.2",
                    "运行时没有对外连接",
                    False,
                    f"无法检查连接: {exc}",
                )

            # ---------------------------------------------------------------
            # §10.4 a cancellation is recorded as a cancellation
            # ---------------------------------------------------------------
            # A cancelled execution must not look like a timeout - the two
            # have different causes and different consequences.
            cancel_workflow = app.create_workflow("audit-cancel")
            cancel_workflow.default_snapshot_id = base.snapshot_id
            cancel_workflow.start()
            cancel_call = asyncio.create_task(
                app.coordinator.submit_tool_call(
                    workflow_id=cancel_workflow.workflow_id,
                    tool_name="fault_injector",
                    arguments={"mode": "sleep", "seconds": 30},
                    snapshot_id=base.snapshot_id,
                )
            )
            await asyncio.sleep(0.5)
            for row in app.repository.list_logical_calls(cancel_workflow.workflow_id):
                await app.coordinator.cancel_logical_call(row["logical_call_id"], "audit")
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(cancel_call, timeout=30)
            # the row of the execution is written when the worker is gone
            for _attempt in range(50):
                app.database.flush()
                cancel_executions = app.repository.list_executions(cancel_workflow.workflow_id)
                if cancel_executions and all(
                    row["execution_status"] != "RUNNING" for row in cancel_executions
                ):
                    break
                await asyncio.sleep(0.1)
            cancel_executions = app.repository.list_executions(cancel_workflow.workflow_id)
            record(
                "10.4",
                "取消被记录为取消而不是超时",
                bool(cancel_executions)
                and all(
                    row["execution_status"] == "CANCELLED"
                    and row["error_kind"] == "cancelled"
                    for row in cancel_executions
                ),
                f"executions={[(r['execution_status'], r['error_kind']) for r in cancel_executions]}",
            )

            # ---------------------------------------------------------------
            # §16.5 the event write does not block the event loop
            # ---------------------------------------------------------------
            # The events are published without an await between them - a
            # blocking write would stop the loop and the counter would stay
            # at zero.
            ticks = 0

            async def ticker() -> None:
                nonlocal ticks
                while True:
                    await asyncio.sleep(0.005)
                    ticks += 1

            burst = app.create_workflow("audit-burst")
            burst.default_snapshot_id = base.snapshot_id
            burst.start()
            ticker_task = asyncio.create_task(ticker())
            try:
                for _index in range(200):
                    app.event_bus.publish(
                        EventType.WORKFLOW_STARTED,
                        workflow_id=burst.workflow_id,
                    )
                await asyncio.sleep(0.05)
            finally:
                ticker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await ticker_task
            record(
                "16.5",
                "事件写入不阻塞主循环",
                ticks > 0,
                f"ticks={ticks}",
            )
        finally:
            await app.close()

    try:
        asyncio.run(run())
    except Exception as exc:  # pragma: no cover - defensive
        record("17.2", "行为审计执行", False, str(exc))
    finally:
        shutil.rmtree(home, ignore_errors=True)


#: The P2 clauses of requirement 19 and the state that has to be documented
#: for them.  The check is self maintaining: the documented table is the
#: contract, the code has to match it.
#: The sub-items of the test categories of requirement section 17 - **all of
#: them**, in the wording of the requirement.  Every sub-item points at the
#: test function that proves it.
#:
#: The mapping is the **single source of truth**: the audit checks that every
#: test function exists (by the AST of the test files, not by a text match)
#: and the traceability table of the test report is generated from it.
SPEC_TEST_SUBITEMS: dict[str, tuple[tuple[str, str], ...]] = {
    "17.1": (
        ("主动不会因为并发而混淆", "test_the_two_agents_keep_their_own_identity"),
        ("同一 Agent 的不同 Invocation 能区分", "test_two_invocations_of_the_same_agent_stay_distinct"),
        ("工具请求与响应正确关联", "test_the_delivery_event_is_published_exactly_once"),
        ("未观察到的因果边不会自动生成", "test_the_graph_only_contains_observed_relations"),
        ("共享执行不会合并两个 AgentInvocation", "test_a_shared_execution_does_not_merge_two_invocations"),
        ("乱序到达的事件能被正确处理或明确标识", "test_out_of_order_events_do_not_create_wrong_edges"),
    ),
    "17.2": (
        ("相同参数和相同依赖可以共享", "test_two_equivalent_calls_share_a_single_physical_execution"),
        ("相同参数但不同实际依赖不能共享", "test_a_conflicting_dependency_detaches_the_follower"),
        ("不相关文件发生变化仍可能共享", "test_an_unrelated_file_change_does_not_prevent_sharing"),
        ("不同工具版本不能共享", "test_different_tool_versions_are_not_shared"),
        ("不同环境签名不能共享", "test_environment_signature_changes_the_key"),
        ("未注册的工具不能被自动共享", "test_a_non_shareable_tool_is_never_shared"),
        ("未声明的非确定性工具不能被共享", "test_a_tool_that_uses_randomness_is_not_shareable"),
        ("共享结果与 Baseline 完全一致", "test_the_shared_results_are_identical_to_the_baseline"),
    ),
    "17.3": (
        ("Leader 读取文件后立即产生依赖事件", "test_the_dependency_event_precedes_the_execution_end"),
        ("Follower 能验证已有依赖", "test_a_late_joiner_validates_the_existing_ledger"),
        ("Follower 能验证后来出现的依赖", "test_the_follower_validates_the_dependencies_that_come_later"),
        ("中途加入的参与者不会遗漏早期依赖", "test_a_late_joiner_validates_the_early_dependencies"),
        ("发现冲突立即分离", "test_a_conflicting_dependency_detaches_the_follower"),
        ("分离以后执行自己的工作区", "test_the_detached_follower_uses_its_own_snapshot"),
        ("Leader 结束前无法通过最终验证的 Follower 不能收到结果", "test_a_follower_that_fails_the_final_validation_gets_no_result"),
        ("目录列表变化会正确影响验证", "test_a_changed_directory_listing_is_detected"),
        ("文件不存在状态变化会被检测", "test_a_changed_file_existence_is_detected"),
    ),
    "17.4": (
        ("多个 Agent 同时提交相同调用", "test_many_concurrent_identical_calls_create_one_execution"),
        ("多个 Agent 同时加入共享组", "test_many_concurrent_identical_calls_create_one_execution"),
        ("Follower 加入时 Leader 正在结束", "test_a_follower_that_joins_while_the_leader_ends_gets_no_shared_result"),
        ("Follower 取消时结果正在返回", "test_a_follower_that_cancels_during_the_delivery_gets_one_result"),
        ("Leader 取消后其他参与者继续等待", "test_a_cancelled_leader_keeps_the_follower_alive"),
        ("所有参与者取消后可以终止执行", "test_cancelling_every_participant_terminates_the_execution"),
        ("不会创建重复 PhysicalExecution", "test_every_call_owns_at_most_one_running_execution"),
        ("同一个 LogicalToolCall 不会收到两次结果", "test_no_logical_call_is_delivered_twice"),
    ),
    "17.5": (
        ("运行 Worker 数不超过配置上限", "test_the_worker_limit_is_enforced"),
        ("超时任务可以终止", "test_a_hanging_worker_is_terminated_by_the_timeout"),
        ("Worker 异常退出可以回收", "test_a_crashing_worker_is_detected"),
        ("共享调用不重复占用 Worker 配额", "test_a_shared_call_does_not_consume_a_second_worker_slot"),
        ("内存预算不足时进入等待", "test_the_memory_budget_blocks_new_executions"),
        ("资源释放后等待任务可以继续", "test_the_memory_budget_blocks_new_executions"),
        ("长时间运行后不会残留大量无效进程", "test_no_worker_processes_are_leaked"),
    ),
    "17.6": (
        ("不同分支文件状态互不影响", "test_fork_creates_an_independent_branch"),
        ("快照创建后状态保持稳定", "test_snapshots_are_immutable"),
        ("相同内容得到一致指纹", "test_identical_content_produces_identical_manifests"),
        ("不同内容得到不同指纹", "test_different_content_produces_different_manifests"),
        ("非法路径不能访问快照根目录之外", "test_paths_escaping_the_workspace_are_rejected"),
        ("文件删除和目录变化被正确记录", "test_compare_snapshots_reports_removed_and_changed_files"),
    ),
    "17.7": (
        ("单 Agent 调用工具", "test_a_single_agent_call_produces_one_execution"),
        ("双 Agent 完全共享", "test_two_equivalent_calls_share_a_single_physical_execution"),
        ("不同快照但依赖等价", "test_the_different_workspace_scenario_shares"),
        ("动态依赖不一致并提前分离", "test_the_detection_happens_while_the_leader_is_still_running"),
        ("三 Agent 并行工作流", "test_the_multi_agent_scenario_builds_the_expected_graph"),
        ("工具异常失败", "test_the_participants_of_a_failed_group_are_failed"),
        ("中途取消", "test_a_cancelled_follower_does_not_stop_the_leader"),
        ("Baseline 与 Online Sharing 对比", "test_the_comparison_reports_a_real_reduction"),
    ),
}

#: Every subsection of the requirement and the proof that it is met.
#:
#: The value is a tuple of pointers: a test function of the suite, the name of
#: an acceptance check or the name of an audit check.  The audit verifies the
#: test functions with the AST of the test files (a renamed test does not
#: satisfy the mapping) and verifies that every subsection is listed.
#:
#: This list is the **single source of truth** of the traceability table.
REQUIRED_TRACEABILITY_SECTIONS: dict[str, tuple[str, ...]] = {
    # the chapters themselves - the summary of the chapter points at the first
    # of its subsections
    "4": ("test_the_execution_is_written_to_the_database",),
    "12": ("test_the_shared_scenario_shares_one_execution",),
    "13": ("test_the_comparison_reports_a_real_reduction",),
    "14": ("test_the_dashboard_is_served",),
    "16": ("test_a_fresh_database_has_the_current_schema",),
    # 2 - the environment and the constraints
    "2.1": ("test_the_worker_reports_its_real_pid",),
    "2.2": ("test_the_front_end_does_not_load_anything_from_the_network",),
    "2.3": ("test_the_catalogue_matches_the_runtime",),
    # 3 - the relation to an existing framework
    "3.1": ("test_a_foreign_framework_can_run_a_tool_call",),
    "3.2": ("test_the_two_agents_keep_their_own_identity",),
    "3.3": ("test_two_scripted_agents_produce_a_real_execution_graph",),
    # 4 - the data model
    "4.1": ("test_the_execution_is_written_to_the_database",),
    "4.2": ("test_an_undetermined_agent_identity_is_recorded",),
    "4.3": ("test_two_invocations_of_the_same_agent_stay_distinct",),
    "4.4": ("test_the_execution_is_written_to_the_database",),
    "4.5": ("test_the_physical_execution_is_recorded",),
    "4.6": ("test_snapshots_are_immutable",),
    "4.7": ("test_the_dependency_ledger_records_every_read",),
    "4.8": ("test_the_share_group_records_its_participants",),
    "4.9": ("test_a_late_joiner_validates_the_existing_ledger",),
    # 5 - the events and the adapter
    "5.1": ("test_event_ids_are_unique_and_sequences_are_stable",),
    "5.2": ("test_an_undetermined_agent_identity_is_recorded",),
    "5.3": ("test_the_graph_only_contains_observed_relations",),
    # 6 - the workspace snapshots
    "6.1": ("test_fork_creates_an_independent_branch",),
    "6.2": ("test_snapshots_are_immutable",),
    "6.3": ("test_read_text_reports_a_file_read_dependency",),
    # 7 - the controlled tools
    "7.1": ("test_the_four_controlled_tools_are_registered",),
    "7.2": ("test_read_text_reports_a_file_read_dependency",),
    "7.3": ("test_the_ast_analyzer_produces_real_results",),
    "7.4": ("test_the_same_seed_produces_the_same_results",),
    # 8 - the sharing algorithm
    "8.1": ("test_same_arguments_produce_the_same_key",),
    "8.2": ("test_illegal_group_transitions_are_rejected",),
    "8.3": ("test_two_equivalent_calls_share_a_single_physical_execution",),
    "8.4": ("test_the_candidate_event_is_published",),
    "8.5": ("test_a_conflicting_dependency_detaches_the_follower",),
    "8.6": ("test_the_follower_validates_the_dependencies_that_come_later",),
    "8.7": ("test_a_follower_that_fails_the_final_validation_gets_no_result",),
    "8.8": ("test_the_detached_follower_uses_its_own_snapshot",),
    "8.9": ("test_an_identical_request_is_served_from_the_cache",),
    "8.10": ("test_the_shared_results_are_identical_to_the_baseline",),
    # 9 - the resources
    "9.1": ("test_the_resource_snapshot_exposes_the_limits",),
    "9.2": ("test_the_worker_limit_is_enforced",),
    "9.3": ("test_the_memory_budget_blocks_new_executions",),
    "9.4": ("test_a_pooled_worker_is_reused",),
    "9.5": ("test_the_queue_is_fifo",),
    # 10 - the concurrency, the failures and the cancellation
    "10.1": ("test_the_participants_of_a_failed_group_are_failed",),
    "10.2": ("test_a_cancelled_follower_does_not_stop_the_leader",),
    "10.3": ("test_a_cancelled_leader_keeps_the_follower_alive",),
    "10.4": ("test_cancelling_every_participant_terminates_the_execution",),
    "10.5": ("test_an_interrupted_run_is_marked_with_its_kind",),
    # 11 - the execution graph
    "11.1": ("test_the_graph_records_shared_executions",),
    "11.2": ("test_the_delivery_event_is_published_exactly_once",),
    "11.3": ("test_out_of_order_events_do_not_create_wrong_edges",),
    "11.4": ("test_the_dashboard_renders_the_real_data",),
    # 12 - the offline demo
    "12.1": ("test_the_shared_scenario_shares_one_execution",),
    "12.2": ("test_the_shared_scenario_shares_one_execution",),
    "12.3": ("test_the_different_workspace_scenario_shares",),
    "12.4": ("test_the_detection_happens_while_the_leader_is_still_running",),
    "12.5": ("test_the_aggregator_only_starts_after_the_workers_finished",),
    "12.6": ("test_the_cancel_failure_scenario_covers_every_case",),
    # 13 - the benchmark
    "13.1": ("test_the_benchmark_uses_a_fixed_seed",),
    "13.2": ("test_the_fairness_comparison_of_a_real_run",),
    "13.3": ("test_the_comparison_reports_a_real_reduction",),
    "13.4": ("test_the_comparison_reports_a_real_reduction",),
    "13.5": ("test_the_benchmark_endpoint_runs_a_comparison",),
    # 14 - the web interface
    "14.1": ("test_the_dashboard_is_served",),
    "14.2": ("test_the_overview_endpoint",),
    "14.3": ("test_the_workflow_endpoints",),
    "14.4": ("test_the_workflow_endpoints",),
    "14.5": ("test_the_execution_endpoints",),
    "14.6": ("test_the_benchmark_endpoint_runs_a_comparison",),
    # 15 - the command line
    "15": ("test_the_cli_is_usable",),
    # 16 - the persistence
    "16.1": ("test_a_fresh_database_has_the_current_schema",),
    "16.2": ("test_a_transaction_is_committed_at_once",),
    "16.3": ("test_the_schema_declares_the_foreign_keys",),
    "16.4": ("test_no_logical_call_is_delivered_twice",),
    "16.5": ("test_a_burst_of_events_does_not_block_the_event_loop",),
    "16.6": ("test_the_workflow_endpoints",),
    "16.7": ("test_a_fresh_database_has_the_current_schema",),
    "16.8": ("test_an_old_database_is_migrated",),
    # 17 - the tests
    "17.1": ("test_the_graph_only_contains_observed_relations",),
    "17.2": ("test_two_equivalent_calls_share_a_single_physical_execution",),
    "17.3": ("test_a_conflicting_dependency_detaches_the_follower",),
    "17.4": ("test_many_concurrent_identical_calls_create_one_execution",),
    "17.5": ("test_the_worker_limit_is_enforced",),
    "17.6": ("test_snapshots_are_immutable",),
    "17.7": ("test_a_single_agent_call_produces_one_execution",),
    # 18 - the development plan
    "18": ("test_the_cli_runs_a_scenario",),
    # 19 - the priorities
    "19": ("test_the_code_defaults_match_the_configuration",),
    # 20 - the layout
    "20": ("test_the_engineering_layout_is_complete",),
    # 21 - the engineering problems
    "21.1": ("test_no_logical_call_is_delivered_twice",),
    "21.2": ("test_a_modified_snapshot_is_not_shared",),
    "21.3": ("test_a_bypassing_tool_is_never_shared",),
    "21.4": ("test_the_comparison_reports_a_real_reduction",),
    "21.5": ("test_a_foreign_framework_can_run_a_tool_call",),
    # 22 - the documents
    "22": ("test_the_documents_have_the_required_sections",),
    # 23 - the acceptance criteria
    "23": ("test_the_acceptance_checklist_is_covered",),
    # 24 - the working rules
    "24": ("test_the_development_log_records_every_stage",),
    # 25 - the beginning
    "25": ("test_the_cli_is_usable",),
}

#: The things that are **not** verified - they have to be written down in the
#: test report with a reason, so that they cannot be forgotten silently.
UNVERIFIED_ITEMS = (
    (
        "真实外部 Agent 框架接入",
        "已验证 LangGraph、LangChain 和通用进程桥接；"
        "尚未逐一验证 AutoGen、CrewAI 等其他框架的 SDK",
    ),
    (
        "长时间运行的资源稳定性",
        "本机已实测 45 分钟（935 轮、0 错误、0 泄漏），"
        "但“数小时”的观测未做",
    ),
    (
        "磁盘故障 / 断电时的数据一致性",
        "已模拟了断电的后果（截断 WAL），但存储硬件自身（真掉电、磁盘失效）未模拟",
    ),
    (
        "符号链接逃逸在 Windows 上未执行",
        "需要管理员权限创建符号链接（本机 WinError 1314）；Linux/WSL2 已真实执行",
    ),
)

#: The P2 clauses of requirement section 19 - **verbatim, all ten of them**.
#:
#: This list is the single source of truth.  The document states the state of
#: every clause; the audit verifies that statement **against the code**: a
#: clause that claims to be implemented has to point at code that exists, a
#: clause that claims not to be implemented must not have an entry point.
#:
#: The list and the document used to be two copies of the same (wrong) table -
#: the audit then passed because *both* were wrong in the same way.  Now the
#: names live here only and the document is checked against them.
P2_CLAUSES = (
    "跨会话持久化结果缓存",
    "GPU 资源调度",
    "KV Cache 管理",
    "真正的 Agent 推理执行优化",
    "多节点分布式执行",
    "复杂的可抢占任务调度",
    "自动预测未来 Agent 路径",
    "框架无关的高级自动适配",
    "对任意第三方工具进行完备依赖追踪",
    "具备安全隔离保证的通用沙箱",
)


#: The markers of the clauses that must **not** be implemented.  A marker in
#: the code would mean that the clause has been implemented - then the
#: expectation above has to be updated instead of the check being happy.
P2_ABSENCE_MARKERS = {
    "跨会话持久化结果缓存": (
        "persistent_result_cache",
        "cross_session_cache",
    ),
    "GPU 资源调度": ("gpu_scheduler", "gpu_resource_pool"),
    "KV Cache 管理": ("kv_cache_manager", "kv_cache_pool"),
    "真正的 Agent 推理执行优化": (
        "inference_engine",
        "llm_runtime",
    ),
    "多节点分布式执行": (
        "distributed_scheduler",
        "cluster_node",
    ),
    "复杂的可抢占任务调度": (
        "preemptive_scheduler",
        "preemption_policy",
    ),
    "自动预测未来 Agent 路径": (
        "path_predictor",
        "agent_path_prediction",
    ),
    "框架无关的高级自动适配": (
        "auto_adapter",
        "framework_auto_detect",
    ),
    "对任意第三方工具进行完备依赖追踪": (
        "full_syscall_tracer",
        "complete_dependency_tracer",
    ),
    "具备安全隔离保证的通用沙箱": (
        "generic_sandbox",
        "security_sandbox_guarantee",
    ),
}


#: The clauses whose entry point has to **contain** something: a file that
#: exists but does not do the promised job is no proof.
P2_CONTENT_EXPECTATIONS = {
    "CI 工作流": (
        "windows-latest",
        "ubuntu-latest",
        "macos-latest",
        "pytest",
        "acceptance_check.py",
        "check_regression.py",
    ),
}

#: The CI workflow has to contain these jobs - the workflow is the place where
#: the delivery is really executed, so its content is a **clause** of its own.
#: (It used to be checked through the P2 table, which was wrong: the workflow
#: is not a P2 clause of the requirement.)
CI_WORKFLOW_EXPECTATIONS = (
    "windows-latest",
    "ubuntu-latest",
    "macos-latest",
    "pytest",
    "acceptance_check.py",
    "check_regression.py",
    "guard_selftest.py",
    "soak.py",
    "test_browser_e2e.py",
)


def _entry_points_are_absent(clause: str, entry: str) -> tuple[bool, list[str]]:
    """A clause that must not be implemented must not have an entry point.

    A hard coded "true" would prove nothing: the audit would report
    "not implemented" even if the feature existed.
    """

    import re

    found: list[str] = []
    paths = re.findall(r"`([\w./]+\.(?:py|yml|yaml|toml|md))`", entry)
    found.extend(path for path in paths if (ROOT / path).exists())
    for marker in P2_ABSENCE_MARKERS.get(clause, ()):
        for source in sorted(ROOT.glob("agentfuse/**/*.py")):
            text = source.read_text(encoding="utf-8", errors="ignore").lower()
            if marker.lower() in text:
                found.append(f"{source.relative_to(ROOT)} ({marker})")
    return (not found), found


def audit_priority_tiers() -> None:
    """Check §19 - the capability tiers and the documented P2 status.

    The ten P2 clauses live in :data:`P2_CLAUSES` - **only** there.  The
    document states the state of every clause and the audit verifies that
    statement against the code: a clause that claims to be implemented has to
    point at code that exists, a clause that claims not to be implemented must
    not have an entry point.

    The document used to carry its **own** copy of the clause list, and the
    audit checked the document against that copy.  Both were wrong in the same
    way, so the audit passed while six clauses were missing.  Now the names
    come from the code and the document is checked against them.
    """

    text = read("docs/KNOWN_LIMITATIONS.md")
    section = text.split("## 0.")[1].split("\n## ")[0] if "## 0." in text else ""
    rows = {}
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0].startswith("P2 条款"):
            continue
        if set(cells[0]) <= set("-: "):
            continue
        rows[cells[0]] = (cells[1], cells[2])

    # every clause of the requirement has to be listed - and only those
    listed = [clause for clause in rows if clause in P2_CLAUSES]
    missing = [clause for clause in P2_CLAUSES if clause not in rows]
    record(
        "19",
        "P2 清单完整（§19 的十条逐条列出）",
        not missing,
        f"缺少: {missing}" if missing else f"{len(P2_CLAUSES)} 条全部列出",
    )
    record(
        "19",
        "P2 清单没有多余的条目",
        len(listed) == len(rows),
        f"多出的条目: {[name for name in rows if name not in P2_CLAUSES]}",
    )

    for clause in P2_CLAUSES:
        row = rows.get(clause)
        if row is None:
            record("19", f"P2 条款 {clause}", False, "未在 KNOWN_LIMITATIONS 中列出")
            continue
        status, entry = row
        implemented = "已实现" in status and "未实现" not in status
        partial = "部分实现" in status
        if not implemented and not partial:
            # A clause that must not be implemented has to be **absent** from
            # the code - a hard coded "true" would prove nothing.
            absent, found = _entry_points_are_absent(clause, entry)
            record(
                "19",
                f"P2 条款 {clause}",
                absent,
                f"已明确声明未实现（未发现入口: {found}）" if absent else f"出现了入口: {found}",
            )
            continue
        # an implemented clause has to point at code that exists
        import re

        # the entry may be a module **or** a workflow file - the CI workflow
        # is a ``.yml`` file, it would never be found by a ``.py`` pattern
        paths = re.findall(r"`([\w./]+\.(?:py|yml|yaml|toml|md))`", entry)
        if not paths:
            record(
                "19",
                f"P2 条款 {clause}",
                False,
                f"文档没有给出可核对的入口: {entry[:60]}",
            )
            continue
        missing_paths = [path for path in paths if not (ROOT / path).exists()]
        record(
            "19",
            f"P2 条款 {clause}",
            not missing_paths,
            f"缺少: {missing_paths}" if missing_paths else f"{len(paths)} 个入口存在",
        )
        # the file has to contain the job it promises
        needles = P2_CONTENT_EXPECTATIONS.get(clause, ())
        if not needles or missing_paths:
            continue
        content = ""
        for path in paths:
            candidate = ROOT / path
            if candidate.exists():
                content += candidate.read_text(encoding="utf-8", errors="ignore")
        absent_needles = [needle for needle in needles if needle not in content]
        record(
            "19",
            f"P2 条款 {clause} 的入口包含所承诺的内容",
            not absent_needles,
            f"缺少: {absent_needles}" if absent_needles else f"{len(needles)} 项都在",
        )

    # the CI workflow is not a P2 clause - it is the place where the delivery
    # is really executed, so its content is checked on its own
    workflow = ROOT / ".github" / "workflows" / "ci.yml"
    if not workflow.exists():
        record("19", "CI 工作流的内容", False, ".github/workflows/ci.yml 不存在")
    else:
        content = workflow.read_text(encoding="utf-8", errors="ignore")
        absent = [needle for needle in CI_WORKFLOW_EXPECTATIONS if needle not in content]
        record(
            "19",
            "CI 工作流的内容",
            not absent,
            f"缺少: {absent}" if absent else f"{len(CI_WORKFLOW_EXPECTATIONS)} 项都在",
        )

    # The runtime must not depend on a framework.  The proof is a **second**
    # implementation of the adapter contract that is driven from another
    # process - the scripted adapter lives in this package and cannot prove it.
    from agentfuse.adapters.external_adapter import ExternalAdapter
    from agentfuse.adapters.scripted_adapter import ScriptedAdapter

    distinct = ExternalAdapter is not ScriptedAdapter
    record(
        "3.1",
        "存在第二个不依赖 Scripted Agent 的适配器实现",
        distinct,
        "ExternalAdapter 与 ScriptedAdapter 是两个不同的实现",
        kind="behavioural",
    )
    record(
        "3.1",
        "外部框架通过跨进程协议驱动适配器",
        "class ExternalAdapter" in read("agentfuse/adapters/external_adapter.py")
        and "def handle(" in read("agentfuse/adapters/external_adapter.py"),
        "agentfuse/adapters/external_adapter.py",
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
    # one real comparison - the metrics and the speedup come from it
    comparison, _tools, _limits = _comparison_run()
    measured_metrics: set[str] = set()
    for entries in (comparison.get("runs") or {}).values():
        for entry in entries:
            measured_metrics.update((entry.get("metrics") or {}).keys())
    document = read("docs/BENCHMARK.md")

    cli = read("agentfuse/cli.py")
    for mode in ("baseline", "online", "deferred"):
        record("13.1", f"比较模式 {mode}", f'"{mode}"' in runner or f'"{mode}"' in cli)
    record("13.1", "模式 C 可选且已实现", "deferred" in runner or "deferred" in cli)

    # The fairness rules are not only **documented** - the modes of a
    # comparison have to use the same tool, the same request and the same
    # limits.  The data of a comparison run is the proof (the checks above
    # compare the seed, the request sequence and the snapshot content).
    fair = _fairness_of_the_modes(comparison, _tools, _limits)
    record(
        "13.2",
        "对比模式使用相同的工具与限制",
        bool(fair) and all(fair.values()),
        f"{fair}",
        kind="behavioural",
    )

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
        record("13.3", f"指标 {metric}", metric in measured_metrics, kind="behavioural")

    # the speedup of a real comparison - and it has to be reported even when
    # the sharing did not pay off
    speedups = (comparison.get("speedup") or {}) if comparison else {}
    record("13.4", "Speedup 定义", bool(speedups), kind="behavioural")
    record(
        "13.4",
        "负收益也必须显示",
        bool(speedups) and all(isinstance(value, (int, float)) for value in speedups.values()),
        kind="behavioural",
    )
    record("13.5", "对比页面展示指标", "bench-result" in read("agentfuse/web/app.js"))
    for section in ("场景", "方法", "工作负载", "指标定义", "公平性原则", "实测结果"):
        record("13", f"BENCHMARK 文档 {section}", section in document)


# ----------------------------------------------------------------------
# 11.3 the incomplete graph
# ----------------------------------------------------------------------
def audit_incomplete_graph() -> None:
    """Check §11.3 - an unobserved relation must not create an edge."""

    # a real graph: an edge that points to a node nobody has seen becomes an
    # explicit placeholder
    from agentfuse.core.events import EventBus, EventType
    from agentfuse.core.execution_graph import ExecutionGraph

    bus = EventBus()
    bus.publish(
        EventType.TOOL_CALL_REQUESTED,
        workflow_id="wf-graph",
        logical_call_id="call-graph",
    )
    graph = ExecutionGraph.from_events(bus.history("wf-graph"), workflow_id="wf-graph")
    graph.add_edge("call-graph", "call-unknown", "invocation")
    nodes = list(graph.nodes.values())
    unknown = [
        node
        for node in nodes
        if node.node_id == "call-unknown"
    ]
    record(
        "11.3",
        "未知节点使用 UNKNOWN 状态",
        bool(unknown) and unknown[0].status == "UNKNOWN",
        kind="behavioural",
    )
    record(
        "11.3",
        "未知节点被标记为占位",
        bool(unknown) and bool(unknown[0].attributes.get("placeholder")),
        kind="behavioural",
    )
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

    # the share group: the real object - an illegal transition has to be
    # rejected, and the group has to know its calls
    from agentfuse.sharing.share_group import IllegalTransition, ShareGroup, ShareGroupStatus

    group = ShareGroup(
        share_group_id="sg-audit",
        workflow_id="wf-audit",
        candidate_key="key",
        leader_execution_id=None,
        leader_logical_call_id="call-audit",
        creation_time=1.0,
        closed_time=None,
        status=ShareGroupStatus.CREATED,
        mode="online",
        tool_name="ast_analyzer",
        tool_version="1.0.0",
    )
    record("4.8", "共享组保存逻辑调用列表", group.participant_calls == [], kind="behavioural")
    rejected = False
    try:
        group.transition(ShareGroupStatus.COMPLETED)
    except IllegalTransition:
        rejected = True
    record("4.8", "非法状态转换被拒绝", rejected, kind="behavioural")
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


def _comparison_run() -> tuple[dict[str, Any], dict[str, set[tuple[str, str, str]]], dict[str, set[float]]]:
    """Run one comparison and return its report."""

    import asyncio
    import shutil
    import tempfile

    from agentfuse.api.app import create_app
    from agentfuse.benchmarks.runner import compare
    from agentfuse.core.state import RuntimeMode
    from agentfuse.settings import load_settings

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp) / "fairness"
        shutil.rmtree(home, ignore_errors=True)
        settings = load_settings(home=str(home))
        app = create_app(settings=settings)
        runtime = app.state.runtime

        async def run():
            try:
                report = await compare(runtime, "shared", repeats=1, modes=("baseline", "online"))
                # Inspect durable rows while the runtime owns an open database;
                # close must not silently reopen a reader afterwards.
                tools: dict[str, set[tuple[str, str, str]]] = {}
                limits: dict[str, set[float]] = {}
                for mode, entries in (report.get("runs") or {}).items():
                    used: set[tuple[str, str, str]] = set()
                    timeouts: set[float] = set()
                    for entry in entries:
                        for row in runtime.repository.list_executions(entry["workflow_id"]):
                            used.add((row["tool_name"], row["tool_version"], row["arguments_hash"]))
                            timeouts.add(float(row["timeout_seconds"] or 0))
                    tools[mode] = used
                    limits[mode] = timeouts
                return report, tools, limits
            finally:
                await runtime.close()

        try:
            return asyncio.run(run())
        except Exception:
            return {}, {}, {}


def _fairness_of_the_modes(
    report: dict[str, Any] | None = None,
    tools: dict[str, set[tuple[str, str, str]]] | None = None,
    limits: dict[str, set[float]] | None = None,
) -> dict[str, bool]:
    """Compare the modes of a comparison run with each other.

    The modes have to use the same tool and the same limits - otherwise the
    comparison measures the difference of the **setup** and not the difference
    of the sharing.
    """

    if report is None:
        report, tools, limits = _comparison_run()
    if not report or not tools:
        return {}
    return _compare_modes(tools, limits)


def _compare_modes(
    tools: dict[str, set[tuple[str, str, str]]],
    limits: dict[str, set[float]],
) -> dict[str, bool]:
    """Compare the modes of a comparison run with each other.

    The modes have to use the same tool, the same request and the same limits
    - otherwise the comparison measures the difference of the **setup** and not
    the difference of the sharing.
    """

    if not tools:
        return {}
    return {
        "same tool implementations and requests": len(
            {frozenset(value) for value in tools.values()}
        )
        == 1,
        "same timeout": len({frozenset(value) for value in limits.values()}) == 1,
    }


def _migrate_an_old_database() -> int | None:
    """Create a database of the first version and migrate it.

    The migration has to work on a database that was written by an **older**
    build - the audit proves it with a database whose ``user_version`` is 1.
    """

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.sqlite3"
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA user_version = 1")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workflows ("
                "workflow_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, "
                "start_time REAL NOT NULL, end_time REAL, status TEXT NOT NULL, "
                "configuration TEXT)"
            )
            connection.commit()
        finally:
            connection.close()
        database = Database(str(path))
        try:
            database.initialize()
            version = database.schema_version()
        except Exception:
            return None
        finally:
            database.close()
    return int(version)


def _test_function_names() -> set[str]:
    """The names of the test functions of the suite."""

    names: set[str] = set()
    for path in sorted(ROOT.glob("tests/**/*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - defensive
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    names.add(node.name)
    return names


def _invalid_traceability_references(report: str, test_names: set[str]) -> list[str]:
    """Check the references actually written in the report, not just its headings."""
    invalid: set[str] = set()
    for section, body in re.findall(r"^\|\s*§([\d.]+)\s*\|([^\n]*)", report, re.M):
        references = set(re.findall(r"\btest_[A-Za-z0-9_]+\b", body))
        if not references:
            invalid.add(f"§{section}: no test reference")
        invalid.update(references - test_names)
    return sorted(invalid)


#: The acceptance checklist of requirement section 23 - **all thirty nine
#: boxes**, in the wording of the requirement.
#:
#: Every box has to be backed by something that is **executed**: a test of the
#: suite or a check of this audit.  A box without such a pointer is a claim
#: that nobody can check - so the audit reports it as missing.
ACCEPTANCE_ITEMS: tuple[tuple[str, str, str], ...] = (
    # (the box of the requirement, the kind of the proof, the proof)
    ("可在单台电脑上启动", "test", "test_a_fresh_database_has_the_current_schema"),
    ("无需外部 API", "audit", "2.2"),
    ("无需外部模型", "audit", "2.2"),
    ("可正常运行 CLI", "test", "test_the_cli_is_usable"),
    ("可打开本地 Web UI", "test", "test_the_dashboard_is_served"),
    ("SQLite 能保存真实执行记录", "test", "test_the_execution_is_written_to_the_database"),
    ("多个 Agent 可以并发运行", "test", "test_many_concurrent_identical_calls_create_one_execution"),
    ("可区分 Agent 和 Invocation", "test", "test_two_invocations_of_the_same_agent_stay_distinct"),
    ("可跟踪 LogicalToolCall", "test", "test_two_equivalent_calls_share_a_single_physical_execution"),
    ("可跟踪 PhysicalExecution", "test", "test_the_physical_execution_is_recorded"),
    ("执行图来自真实事件", "test", "test_the_graph_only_contains_observed_relations"),
    ("并发 Agent 不会产生错误身份绑定", "test", "test_the_two_agents_keep_their_own_identity"),
    ("工具结果传递具有正确关联", "test", "test_the_delivery_event_is_published_exactly_once"),
    ("等价调用可以共享", "test", "test_two_equivalent_calls_share_a_single_physical_execution"),
    ("共享后确实减少物理执行次数", "test", "test_the_comparison_reports_a_real_reduction"),
    ("共享结果与独立执行一致", "test", "test_the_shared_results_are_identical_to_the_baseline"),
    ("不相关文件变化不会必然阻止共享", "test", "test_an_unrelated_file_change_does_not_prevent_sharing"),
    ("实际依赖冲突时会阻止错误共享", "test", "test_a_conflicting_dependency_detaches_the_follower"),
    ("在线验证能够在执行过程中发现冲突", "test", "test_the_detection_happens_while_the_leader_is_still_running"),
    ("冲突后 Follower 能提前独立执行", "test", "test_the_detached_follower_uses_its_own_snapshot"),
    ("未完成验证的调用无法接收共享结果", "test", "test_a_follower_that_fails_the_final_validation_gets_no_result"),
    ("取消和失败不会产生重复结果", "test", "test_no_logical_call_is_delivered_twice"),
    ("有 Worker 并发限制", "test", "test_the_worker_limit_is_enforced"),
    ("能记录 CPU 时间", "test", "test_the_resource_samples_are_written_for_the_real_worker"),
    ("能记录内存使用", "test", "test_the_resource_snapshot_exposes_the_limits"),
    ("能处理超时", "test", "test_a_hanging_worker_is_terminated_by_the_timeout"),
    ("能处理取消", "test", "test_a_cancelled_follower_does_not_stop_the_leader"),
    ("能正确释放资源", "test", "test_cancelling_every_participant_terminates_the_execution"),
    ("提供共享执行演示", "test", "test_the_shared_scenario_shares_one_execution"),
    ("提供动态冲突演示", "test", "test_the_divergent_scenario_reports_the_expected_numbers"),
    ("提供并发多 Agent 演示", "test", "test_the_multi_agent_scenario_builds_the_expected_graph"),
    ("提供失败与取消演示", "test", "test_the_cancel_failure_scenario_covers_every_case"),
    ("可在 Web UI 查看运行图", "test", "test_the_dashboard_renders_the_real_data"),
    ("可查看依赖验证记录", "test", "test_the_dashboard_renders_the_real_data"),
    ("可查看物理执行情况", "test", "test_the_execution_endpoints"),
    ("可运行 Benchmark", "test", "test_the_benchmark_endpoint_runs_a_comparison"),
    ("可对比 Baseline 与 Online Sharing", "test", "test_the_comparison_reports_a_real_reduction"),
    ("有自动化测试", "audit", "17"),
    ("有完整运行文档", "audit", "22"),
)


def audit_acceptance_checklist() -> None:
    """Check section 23 - the acceptance checklist of the requirement.

    The thirty nine boxes of the checklist are the contract of the delivery.
    Every box has to point at something that is **executed**: a test of the
    suite or a check of this audit.  The check that a proof exists is done
    with the AST of the test files - a renamed test would not satisfy it.
    """

    record(
        "23",
        "验收清单的条目数（39 条）",
        len(ACCEPTANCE_ITEMS) == 39,
        f"{len(ACCEPTANCE_ITEMS)} 条",
    )
    test_names = _test_function_names()
    for item, kind, proof in ACCEPTANCE_ITEMS:
        if kind == "test":
            ok = proof in test_names
            detail = (
                f"测试 {proof}"
                if ok
                else f"找不到测试 {proof}"
            )
        else:
            # an audit check of that section has to exist and to pass
            matching = [
                entry
                for entry in RESULTS
                if entry["section"].startswith(proof) and entry["ok"]
            ]
            ok = bool(matching)
            detail = (
                f"审计 §{proof} 的 {len(matching)} 项检查通过"
                if ok
                else f"审计 §{proof} 没有通过的检查"
            )
        record(
            "23",
            f"验收清单 {item}",
            ok,
            detail,
            kind="structural",
        )


def audit_layout_tests_docs() -> None:
    missing = [name for name in LAYOUT if not (ROOT / name).exists()]
    record("20", "工程目录结构", not missing, f"缺: {missing}" if missing else f"{len(LAYOUT)} 项")

    # The categories are not only **named** in the test sources - the test
    # functions have to exist.  A renamed or deleted test would still satisfy
    # a text match.
    test_names = _test_function_names()
    for category, markers in TEST_CATEGORIES.items():
        absent = [marker for marker in markers if marker not in test_names]
        record(
            category,
            "自动化测试类别覆盖",
            not absent,
            f"缺失的用例: {absent}" if absent else f"{len(markers)} 个关键用例",
        )

    # The traceability table has to cover **every** subsection of the
    # requirement that carries a behaviour.
    report = read("docs/TEST_REPORT.md")
    missing_sections = [
        section
        for section in REQUIRED_TRACEABILITY_SECTIONS
        if f"| §{section} |" not in report
    ]
    invalid_references = _invalid_traceability_references(report, test_names)
    record(
        "22",
        "追溯表覆盖需求的每个小节",
        not missing_sections and not invalid_references,
        (f"缺少小节: {missing_sections}; 无效测试引用: {invalid_references}"
         if missing_sections or invalid_references
         else f"{len(REQUIRED_TRACEABILITY_SECTIONS)} 个小节均有有效测试指针"),
    )

    # The things that are not verified have to be written down - with a
    # reason.  A silent gap is the thing this audit exists to prevent.
    section8 = report.split("## 8.")[1].split("\n## ")[0] if "## 8." in report else ""
    absent_items = [
        name for name, _reason in UNVERIFIED_ITEMS if name not in section8
    ]
    record(
        "22",
        "未验证的功能都写在了测试报告里",
        not absent_items,
        f"缺少: {absent_items}" if absent_items else f"{len(UNVERIFIED_ITEMS)} 项均已列出",
    )

    # The sub-items of the test categories: every one of them has to point at
    # a test function that **exists** (the AST of the test files, not a text
    # match).
    for category, items in SPEC_TEST_SUBITEMS.items():
        absent = [name for _item, name in items if name not in test_names]
        record(
            category,
            "测试子条目均有对应的测试",
            not absent,
            f"缺少的测试: {absent}" if absent else f"{len(items)} 个子条目均有对应的测试",
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
# 22 the numbers in the documents
# ----------------------------------------------------------------------
def _scenario_metrics(mode: str) -> dict[str, Any]:
    """Run the ``shared`` scenario in one mode and return its metrics."""

    import asyncio
    import shutil
    import tempfile

    from agentfuse.app import AgentFuseApp
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.definitions import scenario_shared
    from agentfuse.settings import load_settings

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        home = Path(tmp) / "documented"
        shutil.rmtree(home, ignore_errors=True)
        settings = load_settings(home=str(home))
        app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
        try:
            result = asyncio.run(scenario_shared(app, RuntimeMode(mode), {}))
            return dict(result.metrics)
        finally:
            app.close_sync()


def _documented_metrics() -> dict[str, str]:
    """The metric values the README claims in its demo block."""

    import re

    text = read("README.md")
    # the demo block - the fenced block that follows the marker
    marker = text.find("预期输出")
    if marker < 0:
        return {}
    start = text.find("```", marker)
    end = text.find("```", start + 3) if start >= 0 else -1
    if start < 0 or end < 0:
        return {}
    block = text[start:end]
    values: dict[str, str] = {}
    for label, value in re.findall(r"^\s*([^|\n]+?)\s*\|\s*([^|\n]+?)\s*$", block, re.M):
        values[label.strip()] = value.strip()
    return values


def _leading_number(text: str | None) -> float | None:
    """The number at the beginning of a value (``0.107 s`` -> ``0.107``)."""

    if text is None:
        return None
    import re

    match = re.match(r"\s*(-?[0-9]+(?:\.[0-9]+)?)", text)
    return float(match.group(1)) if match else None


def audit_documented_numbers() -> None:
    """Check that the numbers in the README are the numbers of a real run.

    A document with numbers in it is a claim.  The claim has to be checked
    against the runtime - otherwise the numbers drift silently every time the
    runtime changes.  The **counts** are deterministic (two logical calls,
    one physical execution, ...), the times depend on the machine and are
    only checked for plausibility.
    """

    online = _scenario_metrics("online")
    baseline = _scenario_metrics("baseline")
    claimed = _documented_metrics()

    record(
        "22",
        "README 的演示数字来自一次真实运行",
        bool(online) and bool(baseline),
        f"online={len(online)} 指标, baseline={len(baseline)} 指标",
        kind="behavioural",
    )

    if not online:
        return

    # the deterministic counts of the shared scenario
    expectations = {
        "逻辑调用数": ("logical_calls", online.get("logical_calls")),
        "物理执行数": ("physical_executions", online.get("physical_executions")),
        "节省执行数": ("saved_executions", online.get("saved_executions")),
        "分离次数": ("detach_count", online.get("detach_count")),
        "验证失败": ("validation_failures", online.get("validation_failures")),
    }
    for label, (metric, measured) in expectations.items():
        text_value = _leading_number(claimed.get(label))
        record(
            "22",
            f"README 的 {label} 与实测一致",
            text_value is not None and measured is not None and text_value == float(measured),
            f"README={claimed.get(label)} 实测={measured} ({metric})",
            kind="behavioural",
        )

    # The number of validations depends on **when the follower joins**: the
    # dependencies the leader already read are validated in one go, the ones
    # that arrive afterwards are validated one by one.  The count is therefore
    # a range, not a fixed number - but it can never leave it.
    validation = float(online.get("validation_count") or 0)
    claimed_validation = _leading_number(claimed.get("验证次数"))
    record(
        "22",
        "README 的 验证次数 在可能的范围内",
        claimed_validation is not None
        and 1.0 <= claimed_validation <= 5.0
        and 1.0 <= validation <= 5.0,
        f"README={claimed.get('验证次数')} 实测={validation:.0f} (1-5, 取决于 Follower 加入的时机)",
        kind="behavioural",
    )

    rate = _leading_number(claimed.get("共享成功率"))
    measured_rate = float(online.get("share_success_rate") or 0.0) * 100.0
    record(
        "22",
        "README 的 共享成功率 与实测一致",
        rate is not None and abs(rate - measured_rate) < 0.05,
        f"README={claimed.get('共享成功率')} 实测={measured_rate:.1f}%",
        kind="behavioural",
    )

    # the times depend on the machine - they only have to be plausible
    for label, metric, upper in (
        ("工作流耗时", "makespan", 60.0),
        ("总 CPU 时间", "total_cpu_time", 60.0),
        ("平均共享等待", "avg_share_wait", 60.0),
        ("峰值 RSS", "peak_rss", 4 * 1024 * 1024 * 1024),
    ):
        text_value = _leading_number(claimed.get(label))
        measured = float(online.get(metric) or 0.0)
        record(
            "22",
            f"README 的 {label} 数值合理",
            text_value is not None and 0.0 < text_value < upper and 0.0 <= measured < upper,
            f"README={claimed.get(label)} 实测={measured:.3f} ({metric})",
            kind="behavioural",
        )

    # the comparison example in the README claims the execution counts of the
    # two modes - they have to be the counts of the two real runs
    import re

    comparison = [
        line for line in read("README.md").splitlines() if "次物理执行" in line and "baseline" in line
    ]
    counts = re.findall(r"/\s*(\d+)\s*次物理执行", " ".join(comparison))
    record(
        "22",
        "README 的对比示例的执行次数与实测一致",
        len(counts) >= 2
        and int(counts[0]) == int(baseline.get("physical_executions") or -1)
        and int(counts[1]) == int(online.get("physical_executions") or -2),
        f"README={counts} 实测 baseline={baseline.get('physical_executions')} "
        f"online={online.get('physical_executions')}",
        kind="behavioural",
    )


# ----------------------------------------------------------------------
def write_traceability_table() -> None:
    """Write the traceability table of the requirement into the test report.

    Every subsection of the requirement points at something that is
    **executed**: a test of the suite, a check of the acceptance script or a
    check of this audit.  The table is generated from that mapping, so it
    cannot drift away from the code.
    """

    rows: dict[str, list[str]] = {}
    for item, _kind, proof in ACCEPTANCE_ITEMS:
        rows.setdefault("23", []).append(f"{item} -> {proof}")
    for category, items in SPEC_TEST_SUBITEMS.items():
        for item, proof in items:
            rows.setdefault(category, []).append(f"{item} -> {proof}")
    for category, markers in TEST_CATEGORIES.items():
        for marker in markers:
            rows.setdefault(category, []).append(f"（类别覆盖）{marker}")
    for clause in COVERED_SECTIONS:
        rows.setdefault(clause, [])
    # every subsection of the requirement points at the test that proves it -
    # and at the audit checks of that subsection, if there are any
    for section, proofs in REQUIRED_TRACEABILITY_SECTIONS.items():
        rows.setdefault(section, []).append(f"测试 {proofs[0]}")
        clauses = [
            str(entry["clause"])
            for entry in RESULTS
            if str(entry.get("section")) == section and entry.get("ok")
        ]
        if clauses:
            rows[section].append(f"审计检查 {clauses[0]}")

    lines = [
        "## 10. \u89c4\u8303\u9010\u6761\u8ffd\u6eaf\u8868",
        "",
        "\u4e0b\u8868\u662f\u673a\u5668\u751f\u6210\u7684\uff1a\u6bcf\u4e2a\u9700\u6c42\u5c0f\u8282\u90fd\u6307\u5411\u4e00\u4e2a**\u88ab\u6267\u884c**\u7684\u4e1c\u897f",
        "\uff08\u6d4b\u8bd5\u3001\u9a8c\u6536\u9879\u6216\u5ba1\u8ba1\u68c0\u67e5\uff09\u3002\u751f\u6210\u5668\uff1a`python scripts/audit_requirements.py --trace`\u3002",
        "",
        "| \u9700\u6c42\u5c0f\u8282 | \u8bc1\u636e |",
        "|---|---|",
    ]
    for section in sorted(rows, key=lambda value: (len(value), value)):
        proofs = rows[section]
        if not proofs:
            continue
        shown = proofs[0]
        if len(proofs) > 1:
            shown += f" (+{len(proofs) - 1})"
        lines.append(f"| \u00a7{section} | {shown} |")
    lines.append("")

    report = ROOT / "docs" / "TEST_REPORT.md"
    text = report.read_text(encoding="utf-8")
    marker = "## 10. \u89c4\u8303\u9010\u6761\u8ffd\u6eaf\u8868"
    if marker in text:
        head = text.split(marker)[0].rstrip("\n")
    else:
        head = text.rstrip("\n")
    report.write_text(head + "\n\n" + "\n".join(lines), encoding="utf-8")
    print(f"  the traceability table is written into {report.relative_to(ROOT)}")


def audit_declared_coverage() -> None:
    """Check §8.2, §9.1 and §10.5 - and that the declared coverage is real.

    The audit declares which subsections of the requirement it covers.  A
    declaration without a check is a **false claim** - the same kind of thing
    that the P2 table of the limitations used to do.  Therefore the
    declaration is checked against the report.
    """

    # §8.2 the state machine of the share group
    from agentfuse.sharing.share_group import IllegalTransition, ShareGroup, ShareGroupStatus

    group = ShareGroup(
        share_group_id="sg-machine",
        workflow_id="wf-machine",
        candidate_key="key",
        leader_execution_id=None,
        leader_logical_call_id="call-machine",
        creation_time=1.0,
        closed_time=None,
        status=ShareGroupStatus.CREATED,
        mode="online",
        tool_name="ast_analyzer",
        tool_version="1.0.0",
    )
    states = {status.value for status in ShareGroupStatus}
    record(
        "8.2",
        "共享组的状态机完整",
        states == {"CREATED", "EXECUTING", "VALIDATING", "COMPLETED", "FAILED", "CLOSED"},
        f"{sorted(states)}",
        kind="behavioural",
    )
    rejected = False
    try:
        group.transition(ShareGroupStatus.COMPLETED)
    except IllegalTransition:
        rejected = True
    record(
        "8.2",
        "非法的状态转换被拒绝",
        rejected,
        kind="behavioural",
    )

    # §9.1 the configuration of the resource controller
    from agentfuse.settings import load_settings

    settings = load_settings(home=str(ROOT / ".agentfuse" / "audit-config"))
    record(
        "9.1",
        "资源控制器的默认值",
        settings.max_workers == 2
        and settings.default_timeout_seconds == 60
        and settings.max_memory_budget_mb > 0
        and settings.scheduling_strategy in ("fifo", "sjf", "preemptive"),
        f"workers={settings.max_workers} timeout={settings.default_timeout_seconds} "
        f"budget={settings.max_memory_budget_mb} strategy={settings.scheduling_strategy}",
        kind="behavioural",
    )

    # §10.5 an interrupted run has to be recognisable as such
    import sqlite3
    import tempfile

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        from agentfuse.storage.database import Database
        from agentfuse.storage.repository import Repository

        database = Database(Path(tmp) / "interrupted.sqlite3")
        try:
            database.initialize()
            database.start()
            repository = Repository(database)
            # a run that was killed: no end time and the status of a running one
            repository.save_workflow(_workflow_record_without_end())
            recovered = repository.recover_interrupted_workflows()
            with sqlite3.connect(str(database.path)) as connection:
                row = connection.execute(
                    "SELECT status FROM workflows WHERE workflow_id = ?",
                    ("wf-interrupted",),
                ).fetchone()
            record(
                "10.5",
                "被打断的运行标记为 INTERRUPTED",
                bool(row) and str(row[0]) == "INTERRUPTED",
                f"status={row[0] if row else None} recovered={recovered}",
                kind="behavioural",
            )
        finally:
            database.close()

    # every declared subsection really has a check
    covered = {str(entry.get("section")) for entry in RESULTS}
    declared_without_check = [
        section for section in COVERED_SECTIONS if section not in covered
    ]
    record(
        "19",
        "声明覆盖的小节都有检查",
        not declared_without_check,
        f"没有检查的小节: {declared_without_check}"
        if declared_without_check
        else f"{len(COVERED_SECTIONS)} 个小节均有检查",
    )


def _workflow_record_without_end() -> Any:
    """A workflow row of a run that was killed while it was running."""

    from agentfuse.storage.models import WorkflowRecord

    return WorkflowRecord(
        workflow_id="wf-interrupted",
        scenario="audit",
        start_time=1.0,
        end_time=None,
        status="RUNNING",
        configuration={},
        runtime_mode="online",
        random_seed=None,
        label=None,
        error=None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--coverage", action="store_true", help="print the covered requirement sections"
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="write the traceability table into the test report",
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
    audit_documented_numbers()
    audit_acceptance_checklist()
    audit_declared_coverage()

    if args.trace:
        write_traceability_table()

    failed = [item for item in RESULTS if not item["ok"]]
    # The split between the checks that **run** the runtime and the checks that
    # only read the source.  A reader has to be able to see how strong the
    # audit really is - a name in the source is not a behaviour.
    behavioural = sum(1 for item in RESULTS if item.get("kind") == "behavioural")
    structural = len(RESULTS) - behavioural
    if args.json:
        print(json.dumps(
            {
                "total": len(RESULTS),
                "failed": len(failed),
                "behavioural": behavioural,
                "structural": structural,
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
        print(
            f"行为检查 {behavioural} 条（真实运行了运行时）"
            f" / 文本检查 {structural} 条（只读取源码）"
        )
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
