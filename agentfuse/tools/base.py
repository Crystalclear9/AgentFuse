"""Tool contract.

A tool is only eligible for shared execution when it is registered, declares
itself deterministic and read only, and reads the workspace exclusively through
the controlled :class:`~agentfuse.workspace.reader.WorkspaceReader`.

The registry entry records the *implementation reference* (module + qualname)
so that the executed code can always be traced back to a versioned source
location.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..workspace.reader import WorkspaceReader

LOGGER = logging.getLogger("agentfuse.tools")


class ToolError(RuntimeError):
    """A business level failure raised by a tool implementation.

    Tool errors are *not* retried: re-running the same deterministic tool with
    the same inputs would fail again.
    """


class ToolInputError(ToolError):
    """The tool arguments did not satisfy the declared input schema."""


@dataclass
class ToolContext:
    """Everything a tool implementation is allowed to touch."""

    reader: WorkspaceReader
    arguments: dict[str, Any]
    logger: logging.Logger = LOGGER
    extra: dict[str, Any] = field(default_factory=dict)

    def arg(self, name: str, default: Any = None, required: bool = False) -> Any:
        if name in self.arguments:
            return self.arguments[name]
        if required:
            raise ToolInputError(f"missing required argument: {name}")
        return default

    def arg_list(self, name: str, default: list[Any] | None = None) -> list[Any]:
        value = self.arg(name, default if default is not None else [])
        if not isinstance(value, (list, tuple)):
            raise ToolInputError(f"argument {name} must be a list")
        return list(value)


@dataclass
class ToolSpec:
    """Registration record of a controlled tool."""

    tool_name: str
    tool_version: str
    deterministic: bool
    read_only: bool
    resource_estimate: dict[str, float]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    implementation: Callable[[ToolContext], dict[str, Any]]
    implementation_reference: str = ""
    env_signature: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    argument_normalizer: Callable[[dict[str, Any]], dict[str, Any]] | None = None

    @property
    def shareable(self) -> bool:
        """Only deterministic, read-only tools may enter the share coordinator."""

        return bool(self.deterministic and self.read_only)

    @property
    def estimated_memory_mb(self) -> float:
        return float(self.resource_estimate.get("memory_mb", 64.0))

    @property
    def estimated_cpu_seconds(self) -> float:
        return float(self.resource_estimate.get("cpu_seconds", 1.0))

    def environment_signature(self) -> dict[str, Any]:
        """The tool scoped part of the candidate key."""

        import platform
        import sys

        return {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "impl": platform.python_implementation(),
            "tool_env": dict(self.env_signature),
        }


def describe_implementation(func: Callable[..., Any]) -> str:
    module = getattr(func, "__module__", "?")
    qualname = getattr(func, "__qualname__", getattr(func, "__name__", "?"))
    return f"{module}:{qualname}"


def sort_file_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Canonicalise a ``files`` list so that order does not change the candidate key.

    The file based tools read the files in sorted order anyway, so sorting the
    argument list only makes the candidate key more stable without changing the
    result of the tool.
    """

    normalised = dict(arguments)
    files = normalised.get("files")
    if isinstance(files, (list, tuple)):
        normalised["files"] = sorted(str(item) for item in files)
    return normalised


def validate_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> None:
    """Very small schema validation (the registered schema is intentionally simple)."""

    schema = spec.input_schema or {}
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    for name in required:
        if name not in arguments:
            raise ToolInputError(f"{spec.tool_name}: missing required argument {name!r}")
    for name, definition in properties.items():
        if name not in arguments:
            continue
        expected = definition.get("type")
        value = arguments[name]
        if expected == "array" and not isinstance(value, (list, tuple)):
            raise ToolInputError(f"{spec.tool_name}: argument {name!r} must be an array")
        if expected == "integer" and not isinstance(value, int):
            raise ToolInputError(f"{spec.tool_name}: argument {name!r} must be an integer")
        if expected == "number" and not isinstance(value, (int, float)):
            raise ToolInputError(f"{spec.tool_name}: argument {name!r} must be a number")
        if expected == "string" and not isinstance(value, str):
            raise ToolInputError(f"{spec.tool_name}: argument {name!r} must be a string")
        if expected == "boolean" and not isinstance(value, bool):
            raise ToolInputError(f"{spec.tool_name}: argument {name!r} must be a boolean")
