"""Long running observation of the runtime.

The script starts a runtime, runs a workflow in a loop and reports how the
process count and the memory of the worker pool develop.  It is the tool for
the question "does a long running AgentFuse leak processes?".

    python scripts/soak.py --minutes 5
    python scripts/soak.py --minutes 1 --scenario shared --workers 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentfuse.console import configure_console  # noqa: E402

configure_console()

import psutil  # noqa: E402

from agentfuse.app import AgentFuseApp  # noqa: E402
from agentfuse.core.state import RuntimeMode  # noqa: E402
from agentfuse.scenarios import run_scenario  # noqa: E402
from agentfuse.settings import load_settings  # noqa: E402

ME = os.getpid()
_OWNED_WORKERS: dict[int, float] = {}


def worker_processes() -> list[int]:
    """The pids of the processes that really run a tool worker."""

    found: list[int] = []
    for process in psutil.process_iter(["pid", "name"]):
        pid = process.pid
        if pid == ME:
            continue
        if "python" not in (process.info["name"] or "").lower():
            continue
        try:
            cmdline = process.cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if len(cmdline) >= 2 and "python" in cmdline[0].lower() and cmdline[-1].endswith(
            "worker_child"
        ):
            try:
                created = process.create_time()
                owned = _OWNED_WORKERS.get(pid) == created
                if not owned:
                    owned = any(parent.pid == ME for parent in process.parents())
                if owned:
                    _OWNED_WORKERS[pid] = created
                    found.append(pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    for pid in list(_OWNED_WORKERS):
        if pid not in found:
            _OWNED_WORKERS.pop(pid)
    return found


def sample(label: str) -> dict:
    workers = worker_processes()
    rss = 0
    for pid in workers:
        try:
            rss += psutil.Process(pid).memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    entry = {
        "label": label,
        "timestamp": time.time(),
        "workers": len(workers),
        "worker_rss_mb": round(rss / 1048576, 2),
        "processes": len(psutil.pids()),
    }
    return entry


async def main_async(args: argparse.Namespace) -> int:
    settings = load_settings(home=str(ROOT / ".agentfuse" / "soak" / f"{sys.platform}-{os.getpid()}"))
    # Baseline before prewarming, otherwise initial warm workers could mask a
    # smaller leak after close. Foreign runtimes never count toward this run.
    samples: list[dict] = [sample("start")]
    app = AgentFuseApp(settings=settings.with_overrides(worker_pool_size=args.workers), mode=RuntimeMode.ONLINE)
    executions = 0
    errors = 0
    started = time.time()
    deadline = started + args.minutes * 60
    try:
        iteration = 0
        while time.time() < deadline:
            iteration += 1
            try:
                result = await run_scenario(app, args.scenario, RuntimeMode.ONLINE, {})
                executions += result.metrics["physical_executions"]
            except Exception as exc:  # pragma: no cover - reported in the summary
                errors += 1
                print(f"  iteration {iteration} failed: {type(exc).__name__}: {exc}")
            if iteration % args.sample_every == 0:
                entry = sample(f"iteration {iteration}")
                samples.append(entry)
                print(
                    "  %-14s workers=%d worker_rss=%.1f MiB processes=%d"
                    % (
                        entry["label"],
                        entry["workers"],
                        entry["worker_rss_mb"],
                        entry["processes"],
                    )
                )
            await asyncio.sleep(args.pause)
    finally:
        await app.close()

    final = sample("end")
    samples.append(final)

    report = {
        "scenario": args.scenario,
        "minutes": args.minutes,
        "workers": args.workers,
        "iterations": iteration,
        "physical_executions": executions,
        "errors": errors,
        "samples": samples,
        "worker_growth": final["workers"] - samples[0]["workers"],
        "rss_growth_mb": round(
            final["worker_rss_mb"] - samples[0]["worker_rss_mb"], 2
        ),
    }

    # The gate: the soak is only useful if it can **fail**.  It used to
    # return 0 in every case - a leak or a growing memory would have been
    # printed and then ignored.  The thresholds are command line arguments so
    # that a slow machine can be given more room, but the default is the one
    # the CI uses.
    problems: list[str] = []
    if errors:
        problems.append(f"{errors} 次执行失败")
    if report["worker_growth"] > args.max_worker_growth:
        problems.append(
            f"worker 数增长 {report['worker_growth']} "
            f"(>阈值 {args.max_worker_growth})：残留了无效进程"
        )
    if report["rss_growth_mb"] > args.max_rss_growth_mb:
        problems.append(
            f"worker RSS 增长 {report['rss_growth_mb']} MiB "
            f"(>阈值 {args.max_rss_growth_mb} MiB)"
        )
    if args.minutes > 0 and iteration < 1:
        problems.append("没有完成任何一轮执行")
    report["problems"] = problems

    output = ROOT / ".agentfuse" / "reports" / f"soak-{args.scenario}-{sys.platform}-{os.getpid()}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"  iterations           : {report['iterations']}")
    print(f"  physical executions  : {report['physical_executions']}")
    print(f"  errors               : {report['errors']}")
    print(f"  worker growth        : {report['worker_growth']}")
    print(f"  worker rss growth    : {report['rss_growth_mb']} MiB")
    print(f"  report               : {output}")
    if problems:
        print()
        print("  长跑观测未通过：")
        for problem in problems:
            print(f"    - {problem}")
        return 1
    print("  长跑观测通过：没有残留进程，内存稳定")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=2.0)
    parser.add_argument("--scenario", default="shared")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--sample-every", type=int, default=5)
    parser.add_argument(
        "--max-worker-growth",
        type=int,
        default=0,
        help="允许的 worker 进程数增长（默认 0）",
    )
    parser.add_argument(
        "--max-rss-growth-mb",
        type=float,
        default=64.0,
        help="允许的 worker RSS 增长（MiB，默认 64）",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
