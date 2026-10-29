"""Fault injection tool (test fixture).

This tool exists to exercise the failure, timeout and cancellation paths of
the runtime with *real* behaviour instead of mocks:

``raise``
    raises a regular tool error (business failure, no retry),
``exit``
    terminates the worker process immediately (abnormal exit / crash),
``hang``
    burns CPU for a number of seconds (used to trigger the execution timeout),
``sleep``
    sleeps for a number of seconds (used to trigger the execution timeout
    without consuming CPU).

The tool is explicitly marked as **non deterministic** and **not read only**,
therefore it can never enter the share coordinator.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .base import ToolContext, ToolError


def fault_injector(context: ToolContext) -> dict[str, Any]:
    mode = str(context.arg("mode", "raise"))
    seconds = float(context.arg("seconds", 5.0))

    if mode == "raise":
        raise ToolError(f"injected tool failure ({context.arg('message', 'boom')})")
    if mode == "exit":
        os._exit(int(context.arg("exit_code", 7)))
    if mode == "hang":
        deadline = time.time() + seconds
        value = 0
        while time.time() < deadline:
            for index in range(10_000):
                value = (value * 31 + index) % 1_000_003
        return {"mode": mode, "value": value}
    if mode == "sleep":
        time.sleep(seconds)
        return {"mode": mode, "slept": seconds}
    raise ToolError(f"unknown fault injection mode: {mode}")
