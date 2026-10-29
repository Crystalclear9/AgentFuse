"""Runnable example: a conflicting dependency detaches the follower online.

    python examples/divergent_execution/main.py
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
from agentfuse.scenarios.definitions import scenario_divergent  # noqa: E402
from agentfuse.settings import load_settings  # noqa: E402


async def run(mode: RuntimeMode) -> None:
    settings = load_settings()
    app = AgentFuseApp(settings=settings, mode=mode)
    try:
        result = await scenario_divergent(app, mode, {})
        print(f"workflow: {result.workflow_id}")
        for key in (
            "logical_calls",
            "physical_executions",
            "detach_count",
            "validation_count",
            "validation_failures",
            "detach_lead_ms_max",
            "online_detach_before_finish",
        ):
            print(f"  {key}: {result.metrics.get(key)}")
        print("\n参与者状态:")
        for participant in app.repository.list_participants(result.workflow_id):
            print(
                f"  {participant['logical_call_id']} {participant['status']} "
                f"validated_prefix={participant['validated_prefix_length']}"
            )
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
