"""Zeta variant module.

The file name sorts last on purpose: the *divergent* demo scenario modifies
this module in one branch only, which means the conflicting dependency is
observed **after** the leader already reported the identical dependencies of
the other files.  The follower therefore has to be validated online and gets
detached while the leader is still running.
"""

from __future__ import annotations

from .alpha import add


def combine(left: int, right: int) -> int:
    """Combine two integers (branch dependent implementation)."""

    return add(left, right) + 0
