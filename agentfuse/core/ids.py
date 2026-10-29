"""Identifier helpers.

Every runtime entity receives a globally unique identifier.  The helpers keep
the identifier space namespaced so that logs and database rows stay readable.
"""

from __future__ import annotations

import itertools
import threading
import uuid

_counter = itertools.count(1)
_counter_lock = threading.Lock()


def new_id(prefix: str) -> str:
    """Return a globally unique identifier with a readable prefix."""

    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def sequence_id(prefix: str) -> str:
    """Return a short monotonically increasing identifier (process local)."""

    with _counter_lock:
        value = next(_counter)
    return f"{prefix}-{value:06d}"
