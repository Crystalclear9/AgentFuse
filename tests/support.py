"""Helpers that make the tests independent of the speed of the machine.

A fixed ``sleep`` is a guess: on a loaded runner the guess is too short and
the test fails although nothing is broken.  The helpers below wait for a
**condition** with a deadline instead - the test then takes as long as it
needs and fails with a clear message when the condition never happens.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable


async def wait_until(
    predicate: Callable[[], Any],
    timeout: float = 30.0,
    interval: float = 0.02,
    message: str = "",
) -> bool:
    """Wait until the predicate is true.

    Returns True when the condition happened and False when the deadline was
    reached.  The caller decides what a timeout means - usually a failure
    with a message.
    """

    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return bool(predicate())
        await asyncio.sleep(interval)


async def wait_for_process_exit(pids: list[int], timeout: float = 30.0) -> bool:
    """Wait until the given processes are gone."""

    def gone() -> bool:
        return not _alive(pids)

    return await wait_until(gone, timeout=timeout, message="the processes are still running")


def _alive(pids: list[int]) -> list[int]:
    import psutil

    alive: list[int] = []
    for pid in pids:
        try:
            process = psutil.Process(pid)
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                alive.append(pid)
        except Exception:
            continue
    return alive
