"""Run the repository reads outside of the event loop.

The database is written by its own thread.  A read has to wait for the writer
to commit the pending statements - that is the *read your writes* guarantee
the storage layer documents.  The wait is a **blocking** one.

Done directly inside an ``async def`` route it freezes the whole event loop:
the dashboard stops answering, the benchmark job cannot be polled and the
runtime itself is delayed.  The reads therefore run in a worker thread - the
SQLite connection of that thread is opened on demand and closed with the
thread.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from fastapi.concurrency import run_in_threadpool

T = TypeVar("T")


async def read(read: Callable[[], T]) -> T:
    """Run a repository read in a worker thread."""

    return await run_in_threadpool(read)
