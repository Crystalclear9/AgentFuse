"""Candidate key computation.

The candidate key answers a *different* question than the share coordinator:

    "could these two calls possibly be executed by the same physical run?"

It is deliberately **not** a proof of result equivalence - only the observed
dependencies can prove that (see :mod:`agentfuse.sharing.validator`).

The workspace snapshot id is **not** part of the key.  Two branches of the same
repository have different snapshots but may still produce identical results,
and it is exactly the online dependency validation that decides this.
"""

from __future__ import annotations

from typing import Any

from ..tools.base import ToolSpec
from ..workspace.fingerprint import canonical_json, sha256_hex


def compute_candidate_key(spec: ToolSpec, arguments: dict[str, Any]) -> str:
    """Return the candidate key for a tool invocation."""

    payload = canonical_json(
        {
            "tool_name": spec.tool_name,
            "tool_version": spec.tool_version,
            "arguments": arguments,
            "environment": spec.environment_signature(),
        }
    )
    return sha256_hex(payload.encode("utf-8"))
