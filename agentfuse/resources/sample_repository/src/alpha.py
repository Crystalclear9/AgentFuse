"""Alpha module: basic numeric helpers."""

from __future__ import annotations


def add(left: int, right: int) -> int:
    """Return the sum of two integers."""

    return left + right


def multiply(left: int, right: int) -> int:
    """Return the product of two integers."""

    return left * right


def average(values: list[float]) -> float:
    """Return the arithmetic mean of ``values`` (0.0 for an empty list)."""

    if not values:
        return 0.0
    return sum(values) / len(values)
