"""Unit tests for the candidate key and the argument canonicalisation."""

from __future__ import annotations

from agentfuse.sharing.candidate import compute_candidate_key
from agentfuse.tools.registry import build_default_registry
from agentfuse.workspace.fingerprint import canonical_json


def test_same_arguments_produce_the_same_key():
    registry = build_default_registry()
    spec = registry.get("ast_analyzer")
    first = compute_candidate_key(spec, {"files": ["a.py", "b.py"]})
    second = compute_candidate_key(spec, {"files": ["a.py", "b.py"]})
    assert first == second


def test_different_arguments_produce_different_keys():
    registry = build_default_registry()
    spec = registry.get("ast_analyzer")
    first = compute_candidate_key(spec, {"files": ["a.py"]})
    second = compute_candidate_key(spec, {"files": ["b.py"]})
    assert first != second


def test_different_tool_versions_produce_different_keys():
    registry = build_default_registry()
    spec = registry.get("ast_analyzer")
    arguments = {"files": ["a.py"]}
    original = spec.tool_version
    try:
        spec.tool_version = "1.0.1"
        changed = compute_candidate_key(spec, arguments)
    finally:
        spec.tool_version = original
    assert changed != compute_candidate_key(spec, arguments)


def test_environment_signature_changes_the_key():
    registry = build_default_registry()
    spec = registry.get("ast_analyzer")
    arguments = {"files": ["a.py"]}
    baseline = compute_candidate_key(spec, arguments)
    spec.env_signature = {"scope": "other"}
    try:
        assert compute_candidate_key(spec, arguments) != baseline
    finally:
        spec.env_signature = {"scope": "workspace-filesystem", "parser": "ast"}


def test_candidate_key_does_not_contain_the_snapshot():
    """Two branches of the same repository must be able to share."""

    registry = build_default_registry()
    spec = registry.get("ast_analyzer")
    key = compute_candidate_key(spec, {"files": ["a.py"]})
    assert "snap" not in key
    assert len(key) == 64


def test_canonical_json_is_order_independent_for_mappings():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_json_preserves_list_order():
    assert canonical_json({"files": ["a", "b"]}) != canonical_json({"files": ["b", "a"]})
