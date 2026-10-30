"""The front end is executed against the real backend data.

The dashboard is plain HTML/CSS/JS without a build chain.  The test runs the
real script (``agentfuse/web/app.js``) inside a minimal DOM that Node
provides, feeds it the **recorded responses of the real backend** and
asserts that the interface renders the real values.

A JavaScript error, a wrong field or a broken renderer therefore fails the
test - the front end is not only parsed, it is executed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "frontend" / "dom_harness.mjs"
PAYLOADS = ROOT / "tests" / "frontend" / "payloads.json"


@pytest.fixture(scope="module")
def node() -> str:
    executable = shutil.which("node")
    if executable is None:
        pytest.skip("node is not installed - the front end test needs it")
    return executable


def test_the_recorded_payloads_are_the_real_ones():
    """The payloads of the front end test come from a real run."""

    data = json.loads(PAYLOADS.read_text(encoding="utf-8"))
    assert data["overview"]["logical_tool_calls"] >= 1
    assert data["overview"]["physical_executions"] >= 1
    assert data["tools"], "the tool registry has to be recorded"
    assert data["graph"]["nodes"], "the execution graph has to be recorded"
    assert data["timeline"]["lanes"], "the timeline has to be recorded"
    assert data["dependencies"]["dependencies"], "the dependency ledger has to be recorded"
    assert data["_ids"]["workflow"] and data["_ids"]["execution"]


def test_the_dashboard_renders_the_real_data(node: str):
    """The front end script runs and renders the real values."""

    process = subprocess.run(
        [node, str(HARNESS)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    assert "[FAIL]" not in process.stdout
    assert "前端在真实数据上渲染成功" in process.stdout


def test_the_front_end_does_not_load_anything_from_the_network():
    """Requirement 2.2 - no CDN, no external resources."""

    for asset in sorted((ROOT / "agentfuse" / "web").glob("*")):
        if asset.suffix not in (".html", ".js", ".css"):
            continue
        text = asset.read_text(encoding="utf-8", errors="ignore")
        # the SVG namespace is an identifier, not a resource that is fetched
        text = text.replace("http://www.w3.org/2000/svg", "")
        assert "http://" not in text, f"{asset.name} loads an external resource"
        assert "https://" not in text, f"{asset.name} loads an external resource"
