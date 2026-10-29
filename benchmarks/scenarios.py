"""Benchmark scenario catalogue.

The scenario implementations live in ``agentfuse.scenarios`` so that the CLI,
the web API and this script all use exactly the same workload.  This module
adds the *description* of every scenario: what it measures, what the expected
result is and which workload parameters it uses.

Run a comparison::

    python benchmarks/runner.py --scenario shared --repeat 5
    python benchmarks/runner.py --scenario divergent --repeat 3 --modes baseline online deferred
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentfuse.scenarios import SCENARIOS, list_scenarios  # noqa: E402


@dataclass(frozen=True)
class ScenarioSpec:
    """The description of one benchmark scenario."""

    name: str
    title: str
    description: str
    expectation: str
    workload: dict[str, Any] = field(default_factory=dict)
    measures: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "expectation": self.expectation,
            "workload": self.workload,
            "measures": list(self.measures),
        }


CATALOGUE: dict[str, ScenarioSpec] = {
    "shared": ScenarioSpec(
        name="shared",
        title="完全共享",
        description="两个 Agent 从同一个快照发起完全相同的 AST 分析请求。",
        expectation="2 个逻辑调用只产生 1 次物理执行，两个调用得到相同结果。",
        workload={"tool": "ast_analyzer", "files": 5},
        measures=("makespan", "physical_executions", "share_success_rate"),
    ),
    "different-workspace": ScenarioSpec(
        name="different-workspace",
        title="不同工作区但依赖等价",
        description="两个分支来自同一快照，分支 A 只修改 README，工具只分析 src/*.py。",
        expectation="快照不同但被读取的依赖一致 -> 允许共享。",
        workload={"tool": "ast_analyzer", "files": 5, "branch_change": "README.md"},
        measures=("makespan", "physical_executions", "detach_count"),
    ),
    "divergent": ScenarioSpec(
        name="divergent",
        title="动态依赖冲突",
        description="两个分支在 src/zeta_variant.py 上内容不同，冲突文件按排序位于最后。",
        expectation="Follower 在 Leader 运行期间被分离，双方各自得到正确结果。",
        workload={"tool": "content_digest", "files": 5, "rounds": 1500},
        measures=(
            "makespan",
            "detach_count",
            "validation_count",
            "detach_lead_ms_max",
        ),
    ),
    "multi-agent": ScenarioSpec(
        name="multi-agent",
        title="并发多 Agent 与汇合",
        description="Supervisor 启动三个 Worker（A、B、C），Aggregator 在结果到齐后汇合。",
        expectation="4 个逻辑调用产生 3 次物理执行（A 与 B 共享）。",
        workload={"tools": ["ast_analyzer", "ast_analyzer", "dependency_analyzer", "source_index"]},
        measures=("makespan", "physical_executions", "invocations"),
    ),
    "cancel-failure": ScenarioSpec(
        name="cancel-failure",
        title="取消与失败语义",
        description="Follower 取消、Leader 逻辑调用取消、执行超时、worker 崩溃、共享失败。",
        expectation="不产生错误结果、不永久等待、不泄漏 worker 进程。",
        workload={"tool": "fault_injector", "modes": ["cancel", "timeout", "crash", "failure"]},
        measures=("failed_calls", "detach_count", "leaked_processes"),
    ),
}


def describe(scenario: str) -> dict[str, Any]:
    """Return the description of one scenario."""

    spec = CATALOGUE.get(scenario)
    if spec is None:
        raise KeyError(f"unknown scenario: {scenario}")
    return spec.to_dict()


def catalogue() -> list[dict[str, Any]]:
    """Return the description of every known scenario."""

    return [CATALOGUE[name].to_dict() for name in list_scenarios()]


__all__ = ["CATALOGUE", "SCENARIOS", "ScenarioSpec", "catalogue", "describe", "list_scenarios"]
