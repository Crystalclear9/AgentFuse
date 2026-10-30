"""Unit tests for the tool registry and the built-in tools."""

from __future__ import annotations

import pytest

from agentfuse.tools.base import ToolError, ToolInputError, validate_arguments
from agentfuse.tools.registry import ToolRegistry, build_default_registry
from agentfuse.workspace.reader import WorkspaceReader


def test_the_four_controlled_tools_are_registered():
    registry = build_default_registry()
    assert {
        "source_index",
        "ast_analyzer",
        "dependency_analyzer",
        "content_digest",
    } <= set(registry.tool_names())


def test_the_fault_injector_is_never_shareable():
    registry = build_default_registry()
    spec = registry.get("fault_injector")
    assert spec.shareable is False


def test_unknown_tools_cannot_be_resolved():
    registry = build_default_registry()
    with pytest.raises(KeyError):
        registry.get("does_not_exist")


def test_unknown_versions_cannot_be_resolved():
    registry = build_default_registry()
    with pytest.raises(KeyError):
        registry.get("ast_analyzer", "9.9.9")


def test_registering_the_same_version_twice_fails():
    registry = ToolRegistry()
    spec = build_default_registry().get("ast_analyzer")
    registry.register(spec)
    with pytest.raises(ValueError):
        registry.register(spec)


def test_the_ast_analyzer_produces_real_results(snapshot):
    registry = build_default_registry()
    reader = WorkspaceReader(snapshot.tree_path)
    result = registry.execute(
        registry.get("ast_analyzer"),
        {"files": ["src/alpha.py", "src/beta.py"]},
        reader,
    )
    assert result["file_count"] == 2
    assert result["totals"]["functions"] >= 3
    assert result["totals"]["imports"] >= 1


def test_the_dependency_analyzer_finds_local_dependencies(snapshot):
    registry = build_default_registry()
    reader = WorkspaceReader(snapshot.tree_path)
    result = registry.execute(
        registry.get("dependency_analyzer"),
        {"files": ["src/alpha.py", "src/beta.py", "src/gamma.py"]},
        reader,
    )
    edges = {(edge["from"], edge["to"]) for edge in result["local_dependencies"]}
    assert ("src/beta.py", "src/alpha.py") in edges


def test_the_source_index_walks_the_tree(snapshot):
    registry = build_default_registry()
    reader = WorkspaceReader(snapshot.tree_path)
    result = registry.execute(registry.get("source_index"), {"root": "src"}, reader)
    assert result["file_count"] >= 5
    assert result["extension_stats"].get("py", 0) >= 5
    assert result["size_stats"]["total_bytes"] > 0


def test_the_content_digest_is_deterministic(snapshot):
    registry = build_default_registry()
    arguments = {"files": ["src/alpha.py"], "rounds": 20}
    first = registry.execute(registry.get("content_digest"), arguments, WorkspaceReader(snapshot.tree_path))
    second = registry.execute(registry.get("content_digest"), arguments, WorkspaceReader(snapshot.tree_path))
    assert first["combined_digest"] == second["combined_digest"]


def test_the_content_digest_depends_on_the_content(app, snapshot):
    from agentfuse.scenarios.common import DIVERGENT_FILE, create_branch, mutate_variant_source

    branch = create_branch(app, snapshot, mutate_variant_source, "branch")
    registry = build_default_registry()
    # the digest must change when the content of the analysed file changes
    arguments = {"files": [DIVERGENT_FILE], "rounds": 5}
    base = registry.execute(registry.get("content_digest"), arguments, WorkspaceReader(snapshot.tree_path))
    other = registry.execute(registry.get("content_digest"), arguments, WorkspaceReader(branch.tree_path))
    assert base["combined_digest"] != other["combined_digest"]


def test_the_argument_schema_is_validated():
    spec = build_default_registry().get("ast_analyzer")
    with pytest.raises(ToolInputError):
        validate_arguments(spec, {})
    with pytest.raises(ToolInputError):
        validate_arguments(spec, {"files": "not-a-list"})


def test_a_missing_file_produces_a_tool_error(snapshot):
    registry = build_default_registry()
    with pytest.raises(FileNotFoundError):
        registry.execute(
            registry.get("ast_analyzer"),
            {"files": ["src/missing.py"]},
            WorkspaceReader(snapshot.tree_path),
        )


def test_the_fault_injector_can_raise(snapshot):
    registry = build_default_registry()
    with pytest.raises(ToolError):
        registry.execute(
            registry.get("fault_injector"), {"mode": "raise"}, WorkspaceReader(snapshot.tree_path)
        )


def test_the_import_graph_builder_resolves_local_imports(snapshot):
    registry = build_default_registry()
    reader = WorkspaceReader(snapshot.tree_path)
    result = registry.execute(registry.get("import_graph"), {"root": "src"}, reader)
    edges = {(edge["from"], edge["to"]) for edge in result["edges"]}
    assert ("src/beta.py", "src/alpha.py") in edges
    assert ("src/gamma.py", "src/pkg/helper.py") in edges
    assert result["stats"]["edge_count"] == len(edges)
    assert "hashlib" in result["external_modules"]


def test_the_import_graph_builder_detects_cycles(snapshot):
    registry = build_default_registry()
    reader = WorkspaceReader(snapshot.tree_path)
    result = registry.execute(registry.get("import_graph"), {"root": "src"}, reader)
    assert isinstance(result["cycles"], list)


def test_the_duplicate_block_finder_reports_missing_files(snapshot):
    registry = build_default_registry()
    reader = WorkspaceReader(snapshot.tree_path)
    result = registry.execute(
        registry.get("duplicate_blocks"),
        {"files": ["src/alpha.py", "src/missing.py"], "block_size": 3},
        reader,
    )
    assert result["missing_files"] == ["src/missing.py"]
    assert result["analyzed_file_count"] == 1


def test_the_duplicate_block_finder_is_deterministic(snapshot):
    registry = build_default_registry()
    arguments = {"files": ["src/alpha.py", "src/beta.py"], "block_size": 3}
    first = registry.execute(registry.get("duplicate_blocks"), arguments, WorkspaceReader(snapshot.tree_path))
    second = registry.execute(registry.get("duplicate_blocks"), arguments, WorkspaceReader(snapshot.tree_path))
    assert first["stats"] == second["stats"]
    assert first["duplicates"] == second["duplicates"]


def test_the_new_tools_are_shareable():
    registry = build_default_registry()
    for name in ("import_graph", "duplicate_blocks"):
        spec = registry.get(name)
        assert spec.shareable is True
        assert spec.deterministic is True
        assert spec.read_only is True
