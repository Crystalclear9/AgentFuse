"""Tool 3 - SourceDependencyAnalyzer.

Scans a set of Python files and reports the import relations between the
scanned modules plus the local (intra set) dependency edges.

Supported scope (documented in ``docs/KNOWN_LIMITATIONS.md``): only *top level*
``import`` / ``from ... import`` statements are considered, only modules of the
scanned set can be resolved to a local file, and no package resolution, no
conditional import evaluation and no dynamic import handling is performed.
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


def source_dependency_analyzer(context: ToolContext) -> dict[str, Any]:
    files = context.arg_list("files")
    if not files:
        raise ToolError("source_dependency_analyzer requires a non empty 'files' list")

    module_by_path = {_module_name(path): path for path in files}
    imports_by_path: dict[str, list[str]] = {}
    local_edges: list[dict[str, str]] = []
    external_modules: set[str] = set()

    for relative in sorted(files):
        source = context.reader.read_text(relative)
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            raise ToolError(f"cannot parse {relative}: {exc}") from exc

        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    # relative import: resolve against the package of the file
                    package = _module_name(relative).rsplit(".", 1)[0] if "." in _module_name(relative) else ""
                    base = ".".join(filter(None, [package] + [node.module or ""]))
                    if node.module:
                        imported.append(base)
                    for alias in node.names:
                        imported.append(f"{base}.{alias.name}" if base else alias.name)
                elif node.module:
                    imported.append(node.module)

        imports_by_path[relative] = sorted(set(imported))
        for target in imports_by_path[relative]:
            if target in module_by_path and module_by_path[target] != relative:
                local_edges.append({"from": relative, "to": module_by_path[target], "module": target})
            elif target not in module_by_path:
                external_modules.add(target.split(".")[0])

    unique_edges = sorted({(edge["from"], edge["to"]) for edge in local_edges})
    return {
        "file_count": len(files),
        "modules": [
            {"path": path, "module": _module_name(path), "imports": imports_by_path.get(path, [])}
            for path in sorted(files)
        ],
        "local_dependencies": [
            {"from": source, "to": target} for source, target in unique_edges
        ],
        "import_edges": sorted(local_edges, key=lambda edge: (edge["from"], edge["to"], edge["module"])),
        "external_modules": sorted(external_modules),
        "stats": {
            "local_edge_count": len(unique_edges),
            "external_module_count": len(external_modules),
            "average_imports": (
                round(sum(len(v) for v in imports_by_path.values()) / len(files), 2) if files else 0.0
            ),
        },
    }
