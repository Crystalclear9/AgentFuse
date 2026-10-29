"""Command line benchmark runner.

    python benchmarks/runner.py --scenario shared --repeat 5
    python benchmarks/runner.py --scenario multi-agent --repeat 3 --json

The implementation is shared with the CLI (``agentfuse compare``) and with the
web API (``POST /api/benchmarks/run``) - see ``agentfuse.benchmarks.runner``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentfuse.app import AgentFuseApp  # noqa: E402
from agentfuse.benchmarks.runner import compare  # noqa: E402
from agentfuse.console import configure_console  # noqa: E402
from agentfuse.core.state import RuntimeMode  # noqa: E402
from agentfuse.scenarios import list_scenarios, resolve_scenario  # noqa: E402
from agentfuse.settings import load_settings  # noqa: E402


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))
    lines = [" | ".join(str(h).ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("-+-".join("-" * width for width in widths))
    for row in rows:
        lines.append(" | ".join(str(cell).ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> dict:
    settings = load_settings()
    app = AgentFuseApp(settings=settings, mode=RuntimeMode.ONLINE)
    try:
        return await compare(
            app,
            resolve_scenario(args.scenario),
            repeats=args.repeat,
            modes=tuple(args.modes),
            workers=args.workers,
        )
    finally:
        await app.close()


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="shared", help=f"{', '.join(list_scenarios())}")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument(
        "--modes", nargs="+", default=["baseline", "online", "deferred"]
    )
    parser.add_argument(
        "--workers", type=int, default=None, help="同时运行的 worker 数量"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
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
                f"{data['share_success_rate']['mean'] * 100:.1f}%",
                f"{data['detach_count']['mean']:.1f}",
            ]
        )
    print(
        _table(
            rows,
            ["mode", "runs", "makespan(s)", "stdev", "execs", "cpu(s)", "share%", "detach"],
        )
    )
    for mode, correctness in report.get("correctness", {}).items():
        print(
            f"correctness[{mode}]: compared={correctness['compared']} "
            f"identical={correctness['identical']} mismatched={correctness['mismatched']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
