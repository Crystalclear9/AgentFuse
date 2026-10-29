"""Tool 2 - PythonASTAnalyzer.

Really parses the requested Python files with :mod:`ast` and summarises the
code structure.  Each file is read through the controlled reader, therefore
every analysed file becomes a ``FILE_READ`` dependency.
"""

from __future__ import annotations

import ast
from typing import Any

from .base import ToolContext, ToolError


def python_ast_analyzer(context: ToolContext) -> dict[str, Any]:
    files = context.arg_list("files")
    if not files:
        raise ToolError("python_ast_analyzer requires a non empty 'files' list")

    per_file: list[dict[str, Any]] = []
    total_functions = 0
    total_classes = 0
    total_imports = 0

    for relative in sorted(files):
        source = context.reader.read_text(relative)
        try:
            tree = ast.parse(source, filename=relative)
        except SyntaxError as exc:
            raise ToolError(f"cannot parse {relative}: {exc}") from exc

        functions = 0
        classes = 0
        imports = 0
        top_level: list[str] = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions += 1
                top_level.append(f"function:{node.name}")
            elif isinstance(node, ast.ClassDef):
                classes += 1
                top_level.append(f"class:{node.name}")
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                imports += 1
                top_level.append("import")

        nested_functions = sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ) - functions
        nested_classes = sum(1 for node in ast.walk(tree) if isinstance(node, ast.ClassDef)) - classes

        per_file.append(
            {
                "path": relative,
                "functions": functions,
                "classes": classes,
                "imports": imports,
                "nested_functions": nested_functions,
                "nested_classes": nested_classes,
                "line_count": len(source.splitlines()),
                "top_level": top_level,
            }
        )
        total_functions += functions
        total_classes += classes
        total_imports += imports

    return {
        "file_count": len(per_file),
        "files": per_file,
        "totals": {
            "functions": total_functions,
            "classes": total_classes,
            "imports": total_imports,
        },
        "structure_summary": {
            "has_classes": total_classes > 0,
            "average_functions": round(total_functions / len(per_file), 2) if per_file else 0.0,
            "average_imports": round(total_imports / len(per_file), 2) if per_file else 0.0,
        },
    }
