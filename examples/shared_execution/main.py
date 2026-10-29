"""Runnable example: two agents share one physical execution.

    python examples/shared_execution/main.py
    python examples/shared_execution/main.py --mode baseline

The example uses the same adapter interface as a real agent framework would
use - there is no demo specific code path inside the runtime.
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
from agentfuse.scenarios.common import DEMO_FILES  # noqa: E402
from agentfuse.scenarios.definitions import scenario_shared  # noqa: E402
from agentfuse.settings import load_settings  # noqa: E402


async def run(mode: RuntimeMode) -> None:
    settings = load_settings()
    app = AgentFuseApp(settings=settings, mode=mode)
    try:
        result = await scenario_shared(app, mode, {})
        print(f"workflow: {result.workflow_id}")
        for key, value in result.metrics.items():
            if isinstance(value, (int, float, str)) and key != "collected_at":
                print(f"  {key}: {value}")
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
