"""Tool 5 - ImportGraphBuilder.

Walks a source tree through the controlled reader, parses the import
statements of every Python file and builds the *module import graph* of the
project.

Compared to :mod:`agentfuse.tools.dependency_analyzer` (which analyses an
explicit list of files) this tool discovers the files itself, resolves imports
across directories and reports import cycles.
"""

from __future__ import annotations

import ast
from typing import Any

from .base import ToolContext, ToolError


def _module_name(path: str) -> str:
    text = path.replace("\\", "/")
    if text.endswith(".py"):
        text = text[:-3]
    text = text.replace("/", ".")
    if text.endswith(".__init__"):
        text = text[: -len(".__init__")]
    return text


def _resolve_relative(current_module: str, level: int, module: str | None) -> str:
    package = current_module.rsplit(".", 1)[0] if "." in current_module else ""
    base = ".".join(part for part in [package, module or ""] if part)
    for _ in range(max(0, level - 1)):
        base = base.rsplit(".", 1)[0] if "." in base else ""
    return base


def import_graph_builder(context: ToolContext) -> dict[str, Any]:
    root = context.arg("root", "src")
    extensions = tuple(context.arg("extensions", [".py"]))

    paths = [
        path
        for path in sorted(context.reader.iter_files(root))
        if not extensions or path.endswith(extensions)
    ]
    if not paths:
        raise ToolError(f"import_graph_builder found no source files below {root!r}")

    module_by_path = {_module_name(path): path for path in paths}
    edges: list[dict[str, str]] = []
    external: set[str] = set()
    imports_by_path: dict[str, list[str]] = {}

    for path in paths:
        source = context.reader.read_text(path)
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            raise ToolError(f"cannot parse {path}: {exc}") from exc
        current = _module_name(path)
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    resolved = _resolve_relative(current, node.level, node.module)
                    imported.append(resolved)
                    for alias in node.names:
                        imported.append(f"{resolved}.{alias.name}" if resolved else alias.name)
                elif node.module:
                    imported.append(node.module)
        imports_by_path[path] = sorted(set(imported))
        for target in imports_by_path[path]:
            if target in module_by_path and module_by_path[target] != path:
                edges.append({"from": path, "to": module_by_path[target], "module": target})
            elif not target.startswith(current):
                external.add(target.split(".")[0])

    unique_edges = sorted({(edge["from"], edge["to"]) for edge in edges})
    cycles = _find_cycles(module_by_path, unique_edges)

    return {
        "root": root,
        "file_count": len(paths),
        "modules": [
            {
                "path": path,
                "module": _module_name(path),
                "imports": imports_by_path.get(path, []),
                "outgoing": sorted(
                    target for source, target in unique_edges if source == path
                ),
                "incoming": sorted(
                    source for source, target in unique_edges if target == path
                ),
            }
            for path in paths
        ],
        "edges": [{"from": source, "to": target} for source, target in unique_edges],
        "external_modules": sorted(external),
        "cycles": cycles,
        "stats": {
            "edge_count": len(unique_edges),
            "external_module_count": len(external),
            "cycle_count": len(cycles),
            "isolated_module_count": sum(
                1
                for path in paths
                if not any(edge[0] == path or edge[1] == path for edge in unique_edges)
            ),
        },
    }


def _find_cycles(
    module_by_path: dict[str, str], edges: list[tuple[str, str]]
) -> list[list[str]]:
    """Detect import cycles (deterministic, depth first search)."""

    adjacency: dict[str, list[str]] = {path: [] for path in module_by_path.values()}
    for source, target in edges:
        adjacency[source].append(target)

    cycles: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    state: dict[str, int] = {}

    def visit(node: str, stack: list[str]) -> None:
        state[node] = 1
        stack.append(node)
        for neighbour in sorted(adjacency.get(node, ())):
            if state.get(neighbour) == 1:
                index = stack.index(neighbour)
                cycle = stack[index:] + [neighbour]
                key = tuple(sorted(cycle))
                if key not in seen:
                    seen.add(key)
                    cycles.append(cycle)
            elif neighbour not in state:
                visit(neighbour, stack)
        stack.pop()
        state[node] = 2

    for node in sorted(adjacency):
        if node not in state:
            visit(node, [])
    return sorted(cycles)
