"""The dashboard in a **real browser**.

The front end test that runs `app.js` in a minimal DOM proves that the script
renders the real values - but it is not a browser: there is no layout, no
real event dispatch and no CSS.

This test starts the **real** server, runs a real scenario, opens the page in
the browser that is installed on the machine (Edge or Chrome) and drives it
through the Chrome DevTools Protocol.  Node has a global `WebSocket` since
v22, so no dependency is installed and no browser is downloaded.

The test is skipped when no browser is available - the delivery still has to
work on a machine without one.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "frontend" / "browser_e2e.mjs"

#: the browsers that speak the Chrome DevTools Protocol
BROWSERS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/microsoft-edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def _browser() -> str | None:
    for candidate in BROWSERS:
        if Path(candidate).exists():
            return candidate
    found = shutil.which("msedge") or shutil.which("google-chrome") or shutil.which("chromium")
    return found


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as probe:
            probe.settimeout(1.0)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def test_the_dashboard_works_in_a_real_browser(tmp_path: Path):
    """The real page in a real browser: the tabs, the graph and the ledger."""

    browser = _browser()
    if browser is None:
        pytest.skip("no browser with the Chrome DevTools Protocol is installed")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed - the browser test needs it")

    import uvicorn

    from agentfuse.api.app import create_app
    from agentfuse.core.state import RuntimeMode
    from agentfuse.scenarios.definitions import scenario_shared
    from agentfuse.settings import load_settings

    home = tmp_path / "home"
    settings = load_settings(home=str(home))
    settings.ensure_directories()
    app = create_app(settings=settings)

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    import threading

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert _wait_for_port(port), "the server did not start"

    # a real run - the page has to show real numbers
    import asyncio

    runtime = app.state.runtime

    async def run_scenario() -> None:
        await scenario_shared(runtime, RuntimeMode.ONLINE, {})

    asyncio.run(run_scenario())
    time.sleep(1.0)

    debugging_port = _free_port()
    profile = tmp_path / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    browser_process = subprocess.Popen(
        [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--remote-debugging-port={debugging_port}",
            f"--user-data-dir={profile}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    try:
        assert _wait_for_port(debugging_port, timeout=60), "the browser did not start"
        screenshot = tmp_path / "dashboard.png"
        if os.environ.get("AGENTFUSE_BROWSER_SCREENSHOT"):
            screenshot = Path(os.environ["AGENTFUSE_BROWSER_SCREENSHOT"])
            screenshot.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                node,
                str(HARNESS),
                "--port",
                str(debugging_port),
                "--base",
                f"http://127.0.0.1:{port}",
                "--screenshot",
                str(screenshot),
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=environment,
        )
        assert result.returncode == 0, (
            "the browser test failed:\n" + result.stdout + result.stderr
        )
        assert "[FAIL]" not in result.stdout, result.stdout
        assert screenshot.exists() and screenshot.stat().st_size > 1000, (
            "the browser has to produce a screenshot of the rendered page"
        )
    finally:
        browser_process.terminate()
        try:
            browser_process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            browser_process.kill()
        server.should_exit = True
        thread.join(timeout=30)
