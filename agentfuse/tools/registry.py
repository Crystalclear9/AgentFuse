"""Tool registry.

The registry is the gatekeeper of the share coordinator: a tool that is not
registered here can never be shared.  ``build_default_registry`` is imported
by the worker process (which runs with the ``spawn`` start method and therefore
has to rebuild the registry from scratch).
"""

from __future__ import annotations

from typing import Any, Iterable

from .base import (
    ToolContext,
    ToolSpec,
    describe_implementation,
    sort_file_arguments,
    validate_arguments,
)
from .ast_analyzer import python_ast_analyzer
from .dependency_analyzer import source_dependency_analyzer
from .duplicate_blocks import duplicate_block_finder
from .fixtures import random_reader, raw_reader, test_tools_enabled
from .digest_analyzer import content_digest_analyzer
from .fault_injector import fault_injector
from .import_graph import import_graph_builder
from .source_index import source_file_index


class ToolRegistry:
    """Versioned registry of controlled tools."""

    def __init__(self) -> None:
        self._tools: dict[str, dict[str, ToolSpec]] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        versions = self._tools.setdefault(spec.tool_name, {})
        if spec.tool_version in versions:
            raise ValueError(
                f"tool {spec.tool_name} version {spec.tool_version} is already registered"
            )
        versions[spec.tool_version] = spec
        return spec

    def get(self, tool_name: str, tool_version: str | None = None) -> ToolSpec:
        versions = self._tools.get(tool_name)
        if not versions:
            raise KeyError(f"unknown tool: {tool_name}")
        if tool_version is None:
            return next(reversed(list(versions.values())))
        spec = versions.get(tool_version)
        if spec is None:
            raise KeyError(f"unknown tool version: {tool_name}@{tool_version}")
        return spec

    def has(self, tool_name: str, tool_version: str | None = None) -> bool:
        try:
            self.get(tool_name, tool_version)
        except KeyError:
            return False
        return True

    def list_tools(self) -> list[ToolSpec]:
        result: list[ToolSpec] = []
        for versions in self._tools.values():
            for spec in versions.values():
                result.append(spec)
        return sorted(result, key=lambda spec: (spec.tool_name, spec.tool_version))

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def versions(self, tool_name: str) -> list[str]:
        return sorted(self._tools.get(tool_name, {}))

    def execute(self, spec: ToolSpec, arguments: dict[str, Any], reader: Any) -> dict[str, Any]:
        validate_arguments(spec, arguments)
        context = ToolContext(reader=reader, arguments=arguments)
        return spec.implementation(context)

    def __contains__(self, tool_name: object) -> bool:
        return isinstance(tool_name, str) and tool_name in self._tools


def build_default_registry() -> ToolRegistry:
    """Build the registry of the four built-in controlled tools."""

    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            tool_name="source_index",
            tool_version="1.0.0",
            deterministic=True,
            read_only=True,
            resource_estimate={"cpu_seconds": 0.2, "memory_mb": 48.0},
            input_schema={
                "type": "object",
                "properties": {
                    "root": {"type": "string"},
                    "extensions": {"type": "array"},
                    "max_files": {"type": "integer"},
                },
                "required": [],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "file_count": {"type": "integer"},
                    "extension_stats": {"type": "object"},
                    "size_stats": {"type": "object"},
                },
            },
            implementation=source_file_index,
            implementation_reference=describe_implementation(source_file_index),
            dependency_tracking=True,
            env_signature={"scope": "workspace-filesystem"},
            description="Index the source files of a directory (read only).",
        )
    )

    registry.register(
        ToolSpec(
            tool_name="ast_analyzer",
            tool_version="1.0.0",
            deterministic=True,
            read_only=True,
            resource_estimate={"cpu_seconds": 0.3, "memory_mb": 64.0},
            input_schema={
                "type": "object",
                "properties": {"files": {"type": "array"}},
                "required": ["files"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "file_count": {"type": "integer"},
                    "totals": {"type": "object"},
                },
            },
            implementation=python_ast_analyzer,
            implementation_reference=describe_implementation(python_ast_analyzer),
            dependency_tracking=True,
            argument_normalizer=sort_file_arguments,
            env_signature={"scope": "workspace-filesystem", "parser": "ast"},
            description="Parse Python sources and summarise their structure.",
        )
    )

    registry.register(
        ToolSpec(
            tool_name="dependency_analyzer",
            tool_version="1.0.0",
            deterministic=True,
            read_only=True,
            resource_estimate={"cpu_seconds": 0.3, "memory_mb": 64.0},
            input_schema={
                "type": "object",
                "properties": {"files": {"type": "array"}},
                "required": ["files"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "local_dependencies": {"type": "array"},
                    "stats": {"type": "object"},
                },
            },
            implementation=source_dependency_analyzer,
            implementation_reference=describe_implementation(source_dependency_analyzer),
            dependency_tracking=True,
            argument_normalizer=sort_file_arguments,
            env_signature={"scope": "workspace-filesystem", "parser": "ast"},
            description="Extract module import relations from Python sources.",
        )
    )

    registry.register(
        ToolSpec(
            tool_name="content_digest",
            tool_version="1.0.0",
            deterministic=True,
            read_only=True,
            resource_estimate={"cpu_seconds": 1.0, "memory_mb": 64.0},
            input_schema={
                "type": "object",
                "properties": {"files": {"type": "array"}, "rounds": {"type": "integer"}},
                "required": ["files"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "combined_digest": {"type": "string"},
                    "total_rounds": {"type": "integer"},
                },
            },
            implementation=content_digest_analyzer,
            implementation_reference=describe_implementation(content_digest_analyzer),
            dependency_tracking=True,
            argument_normalizer=sort_file_arguments,
            env_signature={"scope": "workspace-filesystem", "algorithm": "sha256"},
            description="Hash files and perform deterministic CPU work.",
        )
    )

    registry.register(
        ToolSpec(
            tool_name="import_graph",
            tool_version="1.0.0",
            deterministic=True,
            read_only=True,
            resource_estimate={"cpu_seconds": 0.4, "memory_mb": 72.0},
            input_schema={
                "type": "object",
                "properties": {
                    "root": {"type": "string"},
                    "extensions": {"type": "array"},
                },
                "required": [],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "modules": {"type": "array"},
                    "edges": {"type": "array"},
                    "cycles": {"type": "array"},
                },
            },
            implementation=import_graph_builder,
            implementation_reference=describe_implementation(import_graph_builder),
            dependency_tracking=True,
            env_signature={"scope": "workspace-filesystem", "parser": "ast"},
            description="Build the module import graph of a source tree.",
        )
    )

    registry.register(
        ToolSpec(
            tool_name="duplicate_blocks",
            tool_version="1.0.0",
            deterministic=True,
            read_only=True,
            resource_estimate={"cpu_seconds": 0.5, "memory_mb": 64.0},
            input_schema={
                "type": "object",
                "properties": {
                    "files": {"type": "array"},
                    "block_size": {"type": "integer"},
                },
                "required": ["files"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "duplicates": {"type": "array"},
                    "duplicate_group_count": {"type": "integer"},
                },
            },
            implementation=duplicate_block_finder,
            implementation_reference=describe_implementation(duplicate_block_finder),
            dependency_tracking=True,
            argument_normalizer=sort_file_arguments,
            env_signature={"scope": "workspace-filesystem", "algorithm": "sha256"},
            description="Find duplicated code blocks inside a set of source files.",
        )
    )

    # Test fixture - never shareable (not deterministic, not read only).
    registry.register(
        ToolSpec(
            tool_name="fault_injector",
            tool_version="1.0.0",
            deterministic=False,
            read_only=False,
            resource_estimate={"cpu_seconds": 1.0, "memory_mb": 32.0},
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {"type": "string"},
                    "seconds": {"type": "number"},
                },
                "required": [],
            },
            output_schema={"type": "object"},
            implementation=fault_injector,
            implementation_reference=describe_implementation(fault_injector),
            dependency_tracking=False,
            env_signature={"scope": "fault-injection"},
            description="Injects a failure, a crash or a hang (test fixture).",
        )
    )

    if test_tools_enabled():
        # the fixture tools verify the dependency tracking contract - they are
        # only available when they are explicitly requested
        registry.register(
            ToolSpec(
                tool_name="raw_reader",
                tool_version="1.0.0",
                deterministic=True,
                read_only=True,
                dependency_tracking=True,  # the tool *claims* a complete ledger
                resource_estimate={"cpu_seconds": 0.1, "memory_mb": 32.0},
                input_schema={"type": "object", "properties": {}, "required": []},
                output_schema={"type": "object"},
                implementation=raw_reader,
                implementation_reference="agentfuse.tools.fixtures:raw_reader",
                env_signature={"scope": "workspace-filesystem"},
                description="Test fixture: reads the workspace without the reader.",
            )
        )
        # a tool that draws random numbers: it declares it and is therefore
        # never shared (requirement 7.4)
        registry.register(
            ToolSpec(
                tool_name="random_reader",
                tool_version="1.0.0",
                deterministic=True,
                read_only=True,
                dependency_tracking=True,
                uses_random=True,
                resource_estimate={"cpu_seconds": 0.1, "memory_mb": 32.0},
                input_schema={"type": "object", "properties": {}, "required": []},
                output_schema={"type": "object"},
                implementation=random_reader,
                implementation_reference="agentfuse.tools.fixtures:random_reader",
                env_signature={"scope": "workspace-filesystem"},
                description="Test fixture: reads the workspace and draws a number.",
            )
        )
        # a second version of the same tool: the version is part of the
        # candidate key, therefore two versions never share an execution
        registry.register(
            ToolSpec(
                tool_name="raw_reader",
                tool_version="2.0.0",
                deterministic=True,
                read_only=True,
                dependency_tracking=True,
                resource_estimate={"cpu_seconds": 0.1, "memory_mb": 32.0},
                input_schema={"type": "object", "properties": {}, "required": []},
                output_schema={"type": "object"},
                implementation=raw_reader,
                implementation_reference="agentfuse.tools.fixtures:raw_reader",
                env_signature={"scope": "workspace-filesystem"},
                description="Test fixture: the second version of the raw reader.",
            )
        )

    return registry


def shareable_tools(registry: ToolRegistry) -> Iterable[ToolSpec]:
    return [spec for spec in registry.list_tools() if spec.shareable]
