"""Unit tests for the resource scheduler."""

from __future__ import annotations

import asyncio

from agentfuse.runtime.scheduler import ResourceScheduler


async def test_the_worker_limit_is_enforced():
    scheduler = ResourceScheduler(max_workers=1, max_memory_budget_mb=1024)
    await scheduler.acquire("exec-1", 64)
    pending = asyncio.create_task(scheduler.acquire("exec-2", 64))
    await asyncio.sleep(0.05)
    assert not pending.done()
    scheduler.release("exec-1")
    reason = await asyncio.wait_for(pending, timeout=1.0)
    assert reason
    assert scheduler.snapshot()["running"] == 1


async def test_the_memory_budget_blocks_new_executions():
    scheduler = ResourceScheduler(max_workers=4, max_memory_budget_mb=128)
    await scheduler.acquire("exec-1", 100)
    pending = asyncio.create_task(scheduler.acquire("exec-2", 100))
    await asyncio.sleep(0.05)
    assert not pending.done()
    scheduler.release("exec-1")
    await asyncio.wait_for(pending, timeout=1.0)


async def test_a_single_oversized_execution_is_allowed():
    """The scheduler must never deadlock on an oversized tool."""

    scheduler = ResourceScheduler(max_workers=2, max_memory_budget_mb=16)
    reason = await scheduler.acquire("exec-1", 512)
    assert "deadlock" in reason or "budget" in reason


async def test_the_queue_is_fifo():
    scheduler = ResourceScheduler(max_workers=1, max_memory_budget_mb=1024)
    order: list[str] = []

    async def worker(name: str):
        await scheduler.acquire(name, 8)
        order.append(name)

    await scheduler.acquire("first", 8)
    tasks = [asyncio.create_task(worker(name)) for name in ("second", "third")]
    await asyncio.sleep(0.05)
    scheduler.release("first")
    await asyncio.sleep(0.05)
    scheduler.release("second")
    await asyncio.gather(*tasks)
    assert order == ["second", "third"]


async def test_the_snapshot_reports_the_usage():
    scheduler = ResourceScheduler(max_workers=3, max_memory_budget_mb=256)
    await scheduler.acquire("exec-1", 32)
    scheduler.record_rss("exec-1", 64 * 1024 * 1024)
    snapshot = scheduler.snapshot()
    assert snapshot["running"] == 1
    assert snapshot["reserved_estimate_mb"] == 32
    assert snapshot["observed_rss_mb"] == 64
    assert snapshot["peak_rss_mb"] == 64


async def test_the_fifo_strategy_admits_the_oldest_request():
    scheduler = ResourceScheduler(max_workers=1, max_memory_budget_mb=1024, strategy="fifo")
    await scheduler.acquire("exec-1", 8)
    first = asyncio.create_task(scheduler.acquire("exec-old", 8, estimated_cpu_seconds=100.0))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(scheduler.acquire("exec-new", 8, estimated_cpu_seconds=1.0))
    await asyncio.sleep(0.05)
    scheduler.release("exec-1")
    await asyncio.wait_for(first, timeout=1.0)
    scheduler.release("exec-old")
    await asyncio.wait_for(second, timeout=1.0)
    assert scheduler.snapshot()["strategy"] == "fifo"


async def test_the_sjf_strategy_admits_the_shortest_job_first():
    scheduler = ResourceScheduler(max_workers=1, max_memory_budget_mb=1024, strategy="sjf")
    await scheduler.acquire("exec-1", 8)
    long_task = asyncio.create_task(scheduler.acquire("exec-long", 8, estimated_cpu_seconds=100.0))
    await asyncio.sleep(0.05)
    short_task = asyncio.create_task(scheduler.acquire("exec-short", 8, estimated_cpu_seconds=1.0))
    await asyncio.sleep(0.05)
    scheduler.release("exec-1")
    # the short job is admitted first
    await asyncio.wait_for(short_task, timeout=1.0)
    assert not long_task.done()
    scheduler.release("exec-short")
    await asyncio.wait_for(long_task, timeout=1.0)
    assert scheduler.snapshot()["strategy"] == "sjf"


async def test_an_unknown_strategy_falls_back_to_fifo():
    from agentfuse.runtime.strategy import build_strategy

    assert build_strategy("does-not-exist").name == "fifo"
    assert build_strategy(None).name == "fifo"
    assert build_strategy("sjf").name == "sjf"
