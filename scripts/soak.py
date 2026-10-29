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

import psutil  # noqa: E402

from agentfuse.app import AgentFuseApp  # noqa: E402
from agentfuse.core.state import RuntimeMode  # noqa: E402
from agentfuse.scenarios import run_scenario  # noqa: E402
from agentfuse.settings import load_settings  # noqa: E402

ME = os.getpid()


def worker_processes() -> list[int]:
    """The pids of the processes that really run a tool worker."""

    found: list[int] = []
    for pid in psutil.pids():
        if pid == ME:
            continue
        try:
            cmdline = psutil.Process(pid).cmdline()
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
        if len(cmdline) >= 2 and "python" in cmdline[0].lower() and cmdline[-1].endswith(
            "worker_child"
        ):
            found.append(pid)
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
    settings = load_settings(home=str(ROOT / ".agentfuse" / "soak"))
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    app.coordinator.worker_pool_size = args.workers
    samples: list[dict] = []
    executions = 0
    errors = 0
    started = time.time()
    deadline = started + args.minutes * 60
    try:
        samples.append(sample("start"))
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
    output = ROOT / ".agentfuse" / "reports" / f"soak-{args.scenario}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(f"  iterations           : {report['iterations']}")
    print(f"  physical executions  : {report['physical_executions']}")
    print(f"  errors               : {report['errors']}")
    print(f"  worker growth        : {report['worker_growth']}")
    print(f"  worker rss growth    : {report['rss_growth_mb']} MiB")
    print(f"  report               : {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--minutes", type=float, default=2.0)
    parser.add_argument("--scenario", default="shared")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--pause", type=float, default=0.2)
    parser.add_argument("--sample-every", type=int, default=5)
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
