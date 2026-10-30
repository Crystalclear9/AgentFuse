"""Test fixture tools.

The tools of this module exist to verify the dependency tracking contract of
the runtime:

``raw_reader``
    reads the workspace **directly** (it bypasses the controlled
    ``WorkspaceReader``).  It declares a complete dependency tracking - the
    runtime has to detect the bypass and must not share its result.

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


def test_tools_enabled() -> bool:
    """True when the fixture tools have to be registered."""

    return os.environ.get("AGENTFUSE_TEST_TOOLS") == "1"
