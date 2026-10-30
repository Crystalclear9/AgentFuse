"""AgentFuse command line interface.

Commands::

    agentfuse init
    agentfuse serve
    agentfuse demo --scenario shared --mode baseline
    agentfuse demo --scenario diverged --mode online
    agentfuse compare --scenario shared --repeat 5
    agentfuse trace <workflow_id>
    agentfuse inspect <execution_id>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from . import __version__
from .app import AgentFuseApp
from .console import configure_console as _configure_console
from .core.state import RuntimeMode
from .scenarios import list_scenarios, resolve_scenario, run_scenario
from .settings import load_settings

MODE_ALIASES = {
    "baseline": RuntimeMode.BASELINE,
    "base": RuntimeMode.BASELINE,
    "independent": RuntimeMode.BASELINE,
    "online": RuntimeMode.ONLINE,
    "share": RuntimeMode.ONLINE,
    "deferred": RuntimeMode.DEFERRED,
    "defer": RuntimeMode.DEFERRED,
}


def _resolve_mode(name: str) -> RuntimeMode:
    key = (name or "online").strip().lower()
    if key not in MODE_ALIASES:
        raise SystemExit(f"未知模式: {name} (可选: baseline / online / deferred)")
    return MODE_ALIASES[key]


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    lines = [" | ".join(str(header).ljust(widths[index]) for index, header in enumerate(headers))]
    lines.append("-+-".join("-" * width for width in widths))
    for row in rows:
        lines.append(" | ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row)))
    return "\n".join(lines)


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------
def cmd_init(args: argparse.Namespace) -> int:
    settings = load_settings(config_path=args.config, home=args.home)
    settings.ensure_directories()
    app = AgentFuseApp(settings=settings)
    app.database.flush()
    app.database.close()
    print("AgentFuse 本地数据目录已初始化:")
    print(f"  home : {settings.home}")
    print(f"  db   : {settings.db_path}")
    print(f"  ws   : {settings.workspaces_dir}")
    print(f"  日志 : {settings.logs_dir}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = load_settings(config_path=args.config, home=args.home)
    settings.ensure_directories()
    host = args.host or settings.api_host
    port = args.port or settings.api_port
    print(f"AgentFuse 管理界面: http://{host}:{port}")
    uvicorn.run(
        "agentfuse.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=settings.api_log_level,
    )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    settings = load_settings(config_path=args.config, home=args.home)
    mode = _resolve_mode(args.mode)
    scenario = resolve_scenario(args.scenario)

    async def runner() -> Any:
        app = AgentFuseApp(settings=settings, mode=mode)
        try:
            return await run_scenario(app, scenario, mode, {"seed": args.seed})
        finally:
            await app.close()

    result = asyncio.run(runner())
    if args.json:
        _print(result.to_dict())
        return 0

    metrics = result.metrics
    print(f"场景: {result.scenario}   模式: {result.mode}   workflow: {result.workflow_id}")
    print(
        _table(
            [
                ["工作流耗时", f"{metrics.get('makespan', 0):.3f} s"],
                ["逻辑调用数", str(metrics.get("logical_calls"))],
                ["物理执行数", str(metrics.get("physical_executions"))],
                ["节省执行数", str(metrics.get("saved_executions"))],
                ["共享成功率", f"{metrics.get('share_success_rate', 0) * 100:.1f}%"],
                ["分离次数", str(metrics.get("detach_count"))],
                ["验证次数", str(metrics.get("validation_count"))],
                ["验证失败", str(metrics.get("validation_failures"))],
                ["总 CPU 时间", f"{metrics.get('total_cpu_time', 0):.3f} s"],
                ["峰值 RSS", f"{(metrics.get('peak_rss') or 0) / 1048576:.1f} MiB"],
                ["平均共享等待", f"{metrics.get('avg_share_wait', 0):.3f} s"],
            ],
            ["指标", "数值"],
        )
    )
    if result.notes:
        print("\n备注:")
        for key, value in result.notes.items():
            print(f"  {key}: {value}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from .benchmarks.runner import compare

    settings = load_settings(config_path=args.config, home=args.home)
    modes = tuple(args.modes)
    scenario = resolve_scenario(args.scenario)

    async def runner() -> Any:
        app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
        try:
            return await compare(
                app, scenario, repeats=args.repeat, modes=modes, workers=args.workers
            )
        finally:
            await app.close()

    report = asyncio.run(runner())
    if args.json:
        _print(report)
        return 0

    rows = []
    for mode, data in report["aggregated"].items():
        rows.append(
            [
                mode,
                str(data["runs"]),
                f"{data['makespan']['mean']:.3f}",
                f"{data['makespan']['stdev']:.3f}",
                f"{data['physical_executions']['mean']:.1f}",
                f"{data['total_cpu_time']['mean']:.3f}",
                f"{data['peak_rss']['mean'] / 1048576:.1f}",
                f"{data['share_success_rate']['mean'] * 100:.1f}%",
                f"{data['detach_count']['mean']:.1f}",
                f"{data['validation_count']['mean']:.1f}",
                f"{data['detach_lead_ms_max']['mean']:.1f}",
                f"{data['online_detach_before_finish_rate']['mean'] * 100:.0f}%",
            ]
        )
    print(
        f"场景 {report['scenario']}  重复 {report['repeats']} 次  "
        f"worker={report.get('workers')}  seed={report.get('seed')}"
    )
    print(
        _table(
            rows,
            [
                "模式",
                "次数",
                "耗时均值(s)",
                "耗时标准差",
                "物理执行",
                "CPU(s)",
                "峰值RSS(MiB)",
                "共享成功率",
                "分离",
                "验证",
                "提前分离(ms)",
                "在线提前分离率",
            ],
        )
    )
    if report.get("speedup"):
        print("\n加速比 (baseline / online):")
        for mode, value in report["speedup"].items():
            print(f"  {mode}: {value:.3f}x")
    for mode, correctness in report.get("correctness", {}).items():
        print(
            f"\n正确性({mode}): 对比 {correctness['compared']} 次, 一致 {correctness['identical']} 次, "
            f"不一致 {correctness['mismatched']} 次"
        )
    stability = report.get("resource_stability") or {}
    if stability:
        print(
            f"\n资源稳定性: {'稳定' if stability.get('stable') else '不稳定'} "
            f"(worker 进程序列 {stability.get('worker_series')}, "
            f"运行时内存增长 {stability.get('runtime_rss_growth_mib', 0.0):.1f} MiB)"
        )
    reproducibility = report.get("reproducibility") or {}
    if reproducibility:
        print(
            f"\n可复现性: {'通过' if reproducibility.get('reproducible') else '未通过'} "
            f"(相同种子 {report.get('seed')})"
        )
    if not stability.get("stable", True) or not reproducibility.get("reproducible", True):
        return 1
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    settings = load_settings(config_path=args.config, home=args.home)
    app = AgentFuseApp(settings=settings)
    try:
        workflow = app.repository.get_workflow(args.workflow_id)
        if workflow is None:
            print(f"未找到 workflow: {args.workflow_id}")
            return 1
        events = app.repository.list_events(args.workflow_id)
        calls = app.repository.list_logical_calls(args.workflow_id)
        executions = app.repository.list_executions(args.workflow_id)
        groups = app.repository.list_share_groups(args.workflow_id)
        participants = app.repository.list_participants(args.workflow_id)
        if args.json:
            _print(
                {
                    "workflow": workflow,
                    "events": events,
                    "logical_calls": calls,
                    "physical_executions": executions,
                    "share_groups": groups,
                    "share_participants": participants,
                }
            )
            return 0
        print(f"workflow {workflow['workflow_id']}  场景={workflow['scenario']}  模式={workflow['runtime_mode']}")
        print(f"状态: {workflow['status']}   耗时: {(workflow['end_time'] or 0) - (workflow['start_time'] or 0):.3f}s")
        print("\n事件序列:")
        for event in events:
            extra = " ".join(
                f"{key}={value}"
                for key, value in (event.get("metadata") or {}).items()
                if key in ("tool_name", "relative_path", "status", "reason", "dependency_type")
            )
            print(
                f"  [{event['sequence']:>4}] {event['event_type']:<28} {extra}"
            )
        print("\n逻辑调用 -> 物理执行:")
        for call in calls:
            print(
                f"  {call['logical_call_id']} {call['tool_name']:<20} {call['status']:<22} "
                f"exec={call['physical_execution_id']} snapshot={call['workspace_snapshot_id']}"
            )
        print("\n物理执行:")
        for execution in executions:
            print(
                f"  {execution['execution_id']} {execution['tool_name']:<20} {execution['execution_status']:<12} "
                f"cpu={execution['cpu_time']} rss={execution['peak_rss']} group={execution['share_group_id']}"
            )
        if groups:
            print("\n共享组:")
            for group in groups:
                print(
                    f"  {group['share_group_id']} status={group['status']} "
                    f"participants={group['participant_count']} leader_exec={group['leader_execution_id']}"
                )
            for participant in participants:
                print(
                    f"    - {participant['logical_call_id']} {participant['status']:<22} "
                    f"validated_prefix={participant['validated_prefix_length']} "
                    f"reason={participant['detach_reason']}"
                )
        return 0
    finally:
        app.close_sync()


def cmd_inspect(args: argparse.Namespace) -> int:
    settings = load_settings(config_path=args.config, home=args.home)
    app = AgentFuseApp(settings=settings)
    try:
        execution = app.repository.get_execution(args.execution_id)
        if execution is None:
            print(f"未找到物理执行: {args.execution_id}")
            return 1
        dependencies = app.repository.list_dependencies(args.execution_id)
        samples = app.repository.list_resource_samples(args.execution_id)
        if args.json:
            _print(
                {
                    "execution": execution,
                    "dependencies": dependencies,
                    "resource_samples": samples,
                }
            )
            return 0
        print(f"物理执行 {execution['execution_id']}")
        print(f"  工具        : {execution['tool_name']}@{execution['tool_version']}")
        print(f"  状态        : {execution['execution_status']}")
        print(f"  参数哈希    : {execution['arguments_hash']}")
        print(f"  候选键      : {execution['candidate_key']}")
        print(f"  快照        : {execution['source_snapshot_id']}")
        print(f"  worker pid  : {execution['worker_pid']}")
        print(f"  CPU 时间    : {execution['cpu_time']}")
        print(f"  峰值 RSS    : {(execution['peak_rss'] or 0) / 1048576:.2f} MiB")
        print(f"  退出状态    : {execution['exit_status']}")
        print(f"  错误        : {execution['error']}")
        print(f"  共享组      : {execution['share_group_id']}")
        print(f"\n依赖账本 ({len(dependencies)} 条):")
        rows = [
            [
                str(dep["sequence_number"]),
                dep["dependency_type"],
                dep["relative_path"],
                (dep["fingerprint"] or "")[:16],
            ]
            for dep in dependencies
        ]
        print(_table(rows, ["#", "类型", "路径", "指纹"]))
        if samples:
            peak = max((sample["rss_bytes"] or 0) for sample in samples)
            print(f"\n资源采样: {len(samples)} 次, 峰值 {peak / 1048576:.2f} MiB")
        return 0
    finally:
        app.close_sync()


def cmd_tools(args: argparse.Namespace) -> int:
    from .tools.registry import build_default_registry

    registry = build_default_registry()
    rows = []
    for spec in registry.list_tools():
        rows.append(
            [
                spec.tool_name,
                spec.tool_version,
                "是" if spec.deterministic else "否",
                "是" if spec.read_only else "否",
                "是" if spec.shareable else "否",
                f"{spec.estimated_memory_mb:.0f}",
                spec.implementation_reference,
            ]
        )
    print(
        _table(
            rows,
            ["工具", "版本", "确定性", "只读", "可共享", "预估内存(MiB)", "实现"],
        )
    )
    return 0


# ----------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentfuse",
        description="AgentFuse - 面向多智能体工作流的共享执行与资源管理 Runtime",
    )
    parser.add_argument("--version", action="version", version=f"AgentFuse {__version__}")
    parser.add_argument("--config", help="配置文件路径 (TOML)")
    parser.add_argument("--home", help="数据目录 (默认 <repo>/.agentfuse)")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化本地数据目录").set_defaults(func=cmd_init)

    serve = sub.add_parser("serve", help="启动本地 Web 管理服务")
    serve.add_argument("--host", help="监听地址 (默认 127.0.0.1)")
    serve.add_argument("--port", type=int, help="监听端口 (默认 8765)")
    serve.set_defaults(func=cmd_serve)

    demo = sub.add_parser("demo", help="运行离线多智能体演示场景")
    demo.add_argument(
        "--scenario",
        default="shared",
        help=f"场景: {', '.join(list_scenarios())}",
    )
    demo.add_argument("--mode", default="online", help="模式: baseline / online / deferred")
    demo.add_argument("--seed", type=int, default=1234, help="随机种子")
    demo.add_argument("--json", action="store_true", help="以 JSON 输出")
    demo.set_defaults(func=cmd_demo)

    compare = sub.add_parser("compare", help="运行 Baseline / Online Sharing 对比实验")
    compare.add_argument("--scenario", default="shared", help=f"场景: {', '.join(list_scenarios())}")
    compare.add_argument("--repeat", type=int, default=3, help="重复次数")
    compare.add_argument(
        "--modes",
        nargs="+",
        default=["baseline", "online"],
        help="参与对比的模式",
    )
    compare.add_argument(
        "--workers",
        type=int,
        default=None,
        help="同时运行的 worker 数量 (1 = 资源受限场景)",
    )
    compare.add_argument("--json", action="store_true", help="以 JSON 输出")
    compare.set_defaults(func=cmd_compare)

    trace = sub.add_parser("trace", help="查看 workflow 的真实事件与运行关系")
    trace.add_argument("workflow_id")
    trace.add_argument("--json", action="store_true")
    trace.set_defaults(func=cmd_trace)

    inspect = sub.add_parser("inspect", help="查看物理执行的状态、依赖与资源")
    inspect.add_argument("execution_id")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect)

    sub.add_parser("tools", help="列出已注册的可控工具").set_defaults(func=cmd_tools)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:  # pragma: no cover
        print("\n已中断")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
