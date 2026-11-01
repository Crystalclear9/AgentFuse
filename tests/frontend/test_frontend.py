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
import re
import shutil
import subprocess
import sys
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


#: The keys of the maps that are keyed by a runtime id (the id changes with
#: every run - the **values** are compared instead).
_RUNTIME_ID = re.compile(r"^(exec|call|wf|sg|inv|agent|evt)-[0-9a-f]+$")


def _shape_differences(recorded, live, path: str = "") -> list[str]:
    """The places where the **shape** of the two payloads differs.

    Only the structure is compared - the values change with every run.  A
    list that is empty in one of the two runs carries no information about
    its shape, therefore it is skipped.  A map that is keyed by a runtime id
    is compared by its values, because the ids change with every run.
    """

    if isinstance(live, dict) and isinstance(recorded, dict):
        differences: list[str] = []
        recorded_keys = set(recorded)
        live_keys = set(live)
        if recorded_keys and live_keys and all(
            _RUNTIME_ID.match(key) for key in recorded_keys | live_keys
        ):
            # a map keyed by runtime ids - compare the values
            recorded_shapes = sorted(
                json.dumps(_shape_of(value), sort_keys=True)
                for value in recorded.values()
            )
            live_shapes = sorted(
                json.dumps(_shape_of(value), sort_keys=True) for value in live.values()
            )
            if recorded_shapes != live_shapes:
                differences.append(f"{path} is keyed by runtime ids and differs")
            return differences
        for key in sorted(live_keys | recorded_keys):
            if key not in recorded:
                differences.append(f"{path}.{key} is missing in the recording")
            elif key not in live:
                differences.append(f"{path}.{key} is not part of the API any more")
            else:
                differences.extend(
                    _shape_differences(recorded[key], live[key], f"{path}.{key}")
                )
        return differences
    if isinstance(live, list) and isinstance(recorded, list):
        if not live or not recorded:
            # an empty list says nothing about the shape of its elements
            return []
        return _shape_differences(recorded[0], live[0], f"{path}[0]")
    if type(live) is not type(recorded):
        return [f"{path} has another type in the recording"]
    return []


def _shape_of(value):
    """The shape of a value - the keys of the nested objects."""

    if isinstance(value, dict):
        return {key: _shape_of(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape_of(value[0])] if value else []
    return type(value).__name__


def test_the_recorded_payloads_have_the_shape_of_the_current_api():
    """The recording must not drift away from the API.

    The front end test renders the **recorded** responses.  If the API
    changes, the recording has to be refreshed - otherwise the test proves
    something that does not exist any more.  Only the **shape** is compared:
    the values change with every run.
    """

    import asyncio
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "capture_frontend_payloads",
        ROOT / "scripts" / "capture_frontend_payloads.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    live = asyncio.run(module.capture())
    recorded = json.loads(PAYLOADS.read_text(encoding="utf-8"))

    assert set(recorded) == set(live), (
        "the recorded payloads and the API disagree - run "
        "python scripts/capture_frontend_payloads.py"
    )
    for name, payload in live.items():
        if name == "_ids" or not isinstance(payload, dict):
            continue
        differences = _shape_differences(recorded[name], payload, name)
        assert not differences, (
            f"the recorded payload differs from the API: {differences[:5]} - run "
            "python scripts/capture_frontend_payloads.py"
        )


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
