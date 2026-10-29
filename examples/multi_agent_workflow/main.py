"""Runnable example: supervisor, three workers and an aggregator.

    python examples/multi_agent_workflow/main.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentfuse.app import AgentFuseApp  # noqa: E402
from agentfuse.console import configure_console  # noqa: E402
from agentfuse.core.state import RuntimeMode  # noqa: E402
from agentfuse.scenarios.definitions import scenario_multi_agent  # noqa: E402
from agentfuse.settings import load_settings  # noqa: E402


async def run(mode: RuntimeMode) -> None:
    settings = load_settings()
    app = AgentFuseApp(settings=settings, mode=mode)
    try:
        result = await scenario_multi_agent(app, mode, {})
        print(f"workflow: {result.workflow_id}")
        print(f"  logical calls   : {result.metrics['logical_calls']}")
        print(f"  physical execs  : {result.metrics['physical_executions']}")
        print(f"  shared calls    : {result.metrics['shared_calls']}")
        print(f"  makespan (s)    : {result.metrics['makespan']:.3f}")
        print("\n由事件重建的执行图:")
        graph = result.workflow.graph.to_dict()
        for node in graph["nodes"]:
            print(f"  {node['kind']:<20} {node['label']}")
        print("\n边:")
        for edge in graph["edges"]:
            print(f"  {edge['kind']:<32} {edge['source']} -> {edge['target']}")
    finally:
        await app.close()


def main() -> int:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", default="online", choices=["baseline", "online", "deferred"]
    )
    args = parser.parse_args()
    asyncio.run(run(RuntimeMode(args.mode)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
