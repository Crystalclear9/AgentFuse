"""Unit tests: the accelerator slots of the scheduler.

Requirement section 19 lists the GPU scheduling as a P2 capability.  The
runtime does not need a GPU to schedule for one: a tool **declares** that it
needs an accelerator, and the scheduler reserves one of the slots of the
machine for it.

The machine of the test has one slot (the tests configure it explicitly), so
the behaviour can be proven without a GPU.
"""

from __future__ import annotations

import asyncio

import pytest

from agentfuse.runtime.scheduler import LocalResourceController
from agentfuse.runtime.strategy import PendingRequest


async def test_a_gpu_request_waits_for_a_free_slot():
    """The second accelerator request waits until the first one is done."""

    scheduler = LocalResourceController(max_workers=4, gpu_slots=1)
    assert scheduler.gpu_slots == 1

    first = asyncio.create_task(scheduler.acquire("exec-gpu-1", 64, 0.1, requires_gpu=True))
    await asyncio.wait_for(first, timeout=5)
    assert scheduler.gpu_slots_used == 1

    second = asyncio.create_task(scheduler.acquire("exec-gpu-2", 64, 0.1, requires_gpu=True))
    # it has to wait - the only accelerator is busy
    await asyncio.sleep(0.2)
    assert not second.done(), "the second accelerator request has to wait"
    assert "accelerator" in scheduler.snapshot()["pending"][0]["reason"]

    scheduler.release("exec-gpu-1")
    reason = await asyncio.wait_for(second, timeout=5)
    assert "accelerator" not in reason


async def test_a_cpu_request_is_not_blocked_by_an_accelerator():
    """A request without an accelerator does not wait for one."""

    scheduler = LocalResourceController(max_workers=4, gpu_slots=1)
    await asyncio.wait_for(
        scheduler.acquire("exec-gpu-1", 64, 0.1, requires_gpu=True), timeout=5
    )
    reason = await asyncio.wait_for(scheduler.acquire("exec-cpu", 64, 0.1), timeout=5)
    assert reason


async def test_without_an_accelerator_the_tool_runs_anyway():
    """A machine without a GPU does not block a tool that declares one.

    The declaration of a tool is a hint - the runtime cannot check whether the
    machine really has an accelerator, so it must not refuse the execution.
    """

    scheduler = LocalResourceController(max_workers=4, gpu_slots=0)
    reason = await asyncio.wait_for(
        scheduler.acquire("exec-gpu", 64, 0.1, requires_gpu=True), timeout=5
    )
    assert reason


def test_the_detection_does_not_fail_without_nvidia_smi(monkeypatch):
    """The detection is a hint as well - it never raises."""

    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    from agentfuse.runtime.scheduler import detect_gpu_slots

    assert detect_gpu_slots() == 0


def test_the_strategies_respect_the_accelerator():
    """Both strategies block a request that needs a busy accelerator."""

    from agentfuse.runtime.strategy import build_strategy

    request = PendingRequest(execution_id="exec-1", requires_gpu=True)
    for name in ("fifo", "sjf"):
        strategy = build_strategy(name)
        decision = strategy.select(
            pending=[request],
            running=1,
            max_workers=4,
            reserved_mb=0.0,
            observed_rss_mb=0.0,
            budget_mb=1024,
            rss_limit_mb=0,
            gpu_slots_total=1,
            gpu_slots_used=1,
        )
        assert not decision.allowed, name
        assert "accelerator" in decision.reason, name

        decision = strategy.select(
            pending=[request],
            running=1,
            max_workers=4,
            reserved_mb=0.0,
            observed_rss_mb=0.0,
            budget_mb=1024,
            rss_limit_mb=0,
            gpu_slots_total=1,
            gpu_slots_used=0,
        )
        assert decision.allowed, name
