"""Test fixture tools.

The tools of this module exist to verify the dependency tracking contract of
the runtime:

``raw_reader``
    reads the workspace **directly** (it bypasses the controlled
    ``WorkspaceReader``).  It declares a complete dependency tracking - the
    runtime has to detect the bypass and must not share its result.

``random_reader``
    reads the workspace like ``raw_reader`` and adds a number that it draws
    from the random source of the call.  It declares ``uses_random`` and is
    therefore never shared - a result that depends on randomness must not be
    reused (requirement 7.4).

They are only registered when ``AGENTFUSE_TEST_TOOLS=1`` is set, so the
production registry stays clean.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .base import ToolContext


def raw_reader(context: ToolContext) -> dict[str, Any]:
    """Read the workspace without the controlled reader (a bypass)."""

    root = Path(context.reader.root)
    files = sorted(str(path.relative_to(root)).replace("\\", "/") for path in root.rglob("*.py"))
    total = 0
    for relative in files:
        text = (root / relative).read_text(encoding="utf-8")
        total += text.count("def ")
    return {"files": len(files), "functions": total}


def random_reader(context: ToolContext) -> dict[str, Any]:
    """Read the workspace and draw a number from the random source of the call.

    The number is derived from the seed of the workflow **and** the id of the
    logical call: the same call always draws the same value, two different
    calls never share it.
    """

    payload = raw_reader(context)
    payload["random"] = context.random().random()
    return payload


def test_tools_enabled() -> bool:
    """True when the fixture tools have to be registered."""

    return os.environ.get("AGENTFUSE_TEST_TOOLS") == "1"
