"""Guard self test.

A green test suite only proves that **nothing changed**.  This script
answers the other question: *would the guards notice if something broke?*

It deliberately breaks one behaviour at a time, runs the guard that is
supposed to catch it and asserts that the guard **fails**.  A guard that
cannot fail is a guard that does not protect anything.

    python scripts/guard_selftest.py            # human readable report
    python scripts/guard_selftest.py --json     # machine readable

The mutations are reverted immediately - the working copy is left untouched.
Exit code 0 = every guard caught its mutation, 1 = a guard stayed green.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentfuse.console import configure_console  # noqa: E402

configure_console()


#: The mutations.  Every entry breaks one behaviour of the runtime and names
#: the guards that have to notice.
MUTATIONS: list[dict[str, Any]] = [
    {
        "name": "最终提交验证门禁被删除",
        "clause": "8.7",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """                if (
                    participant.validated_prefix_length != len(ledger)
                    or participant.validation_status is not ValidationStatus.MATCH
                ):""",
        "new": """                if False:""",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_divergent.py::"
                "test_a_follower_that_fails_the_final_validation_gets_no_result",
                "-q",
            ],
        ],
    },
    {
        "name": "依赖完整性不再决定工具能否共享",
        "clause": "7.2 / 21.3",
        "file": "agentfuse/tools/registry.py",
        "old": (
            '            tool_name="ast_analyzer",\n'
            '            tool_version="1.0.0",\n'
            '            deterministic=True,\n'
            '            read_only=True,\n'
        ),
        "new": (
            '            tool_name="ast_analyzer",\n'
            '            tool_version="1.0.0",\n'
            '            deterministic=True,\n'
            '            read_only=True,\n'
            '            dependency_tracking=False,\n'
        ),
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_dependency_tracking.py",
                "-q",
            ],
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_shared_execution.py",
                "-q",
            ],
        ],
    },
    {
        "name": "调用与执行之间的外键被删除",
        "clause": "16",
        "file": "agentfuse/storage/database.py",
        "old": "        physical_execution_id TEXT REFERENCES physical_executions(execution_id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,",
        "new": "        physical_execution_id TEXT,",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "写入错误不再被计数",
        "clause": "16",
        "file": "agentfuse/storage/database.py",
        "old": (
            "            self.write_errors += 1\n"
            "            self.last_error = f\"transaction failed: {type(exc).__name__}: {exc}\""
        ),
        "new": (
            "            self.last_error = f\"transaction failed: {type(exc).__name__}: {exc}\""
        ),
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "分离后不再独立执行",
        "clause": "8.8",
        "file": "agentfuse/runtime/coordinator.py",
        "old": "        self._reassign_after_detach_locked(call, group)",
        "new": "        pass  # the follower would keep waiting forever",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_divergent.py",
                "-q",
            ],
        ],
    },
    {
        "name": "取消不再记录为取消",
        "clause": "10.4",
        "file": "agentfuse/runtime/cancellation.py",
        "old": (
            "        if kind == ERROR_TIMEOUT:\n"
            "            execution.outcome.set_result(_timeout_outcome(reason))\n"
            "        else:\n"
            "            execution.outcome.set_result(_cancelled_outcome(reason))"
        ),
        "new": (
            "        execution.outcome.set_result(_timeout_outcome(reason))"
        ),
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_concurrency.py::"
                "test_cancelling_every_participant_terminates_the_execution",
                "-q",
            ],
        ],
    },
    {
        "name": "前端不再渲染真实数据",
        "clause": "14",
        "file": "agentfuse/web/app.js",
        "old": '  $("tool-table").querySelector("tbody").innerHTML = rows;',
        "new": '  $("tool-table-broken").querySelector("tbody").innerHTML = rows;',
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/frontend/test_frontend.py",
                "-q",
            ],
        ],
    },
    {
        "name": "超时不再记录为超时",
        "clause": "10.1",
        "file": "agentfuse/runtime/cancellation.py",
        "old": """        if kind == ERROR_TIMEOUT:
            execution.outcome.set_result(_timeout_outcome(reason))
        else:
            execution.outcome.set_result(_cancelled_outcome(reason))""",
        "new": """        execution.outcome.set_result(_cancelled_outcome(reason))""",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "候选键包含 workspace snapshot",
        "clause": "8.1",
        "file": "agentfuse/sharing/candidate.py",
        "old": """            "arguments": arguments,
            "environment": spec.environment_signature(),""",
        "new": """            "arguments": arguments,
            "snapshot_id": "the-snapshot-must-not-be-part-of-the-key",
            "environment": spec.environment_signature(),""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_candidate.py",
                "-q",
            ],
        ],
    },
    {
        "name": "种子不再到达工具",
        "clause": "7.4",
        "file": "agentfuse/runtime/worker.py",
        "old": """    return random.Random(f"{seed}:{call_id}")""",
        "new": """    return random.Random()""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_tools.py",
                "-q",
            ],
        ],
    },
    {
        "name": "一个逻辑调用可以再拥有第二个执行",
        "clause": "8.2",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """            own_execution = self._running_execution_of(call)
            if own_execution is not None:
                continue""",
        "new": """            own_execution = None""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_concurrency.py::"
                "test_a_detached_follower_never_gets_a_second_execution",
                "-q",
            ],
        ],
    },
    {
        "name": "失败的参与者不再得到失败状态",
        "clause": "10.1",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """            self._fail_waiting_participants(group, outcome.message)""",
        "new": """            pass""",
        "extra": (
            "agentfuse/runtime/coordinator.py",
            """        self._fail_participant_of(call, message)""",
            """        pass""",
        ),
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_concurrency.py::"
                "test_the_participants_of_a_failed_group_are_failed",
                "-q",
            ],
        ],
    },
    {
        "name": "调度器错误又被报告成进程崩溃",
        "clause": "10.1",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """                    ExecutionOutcome(ok=False, kind=ERROR_INTERNAL, message=str(exc))
                )
            return""",
        "new": """                    ExecutionOutcome(ok=False, kind=ERROR_CRASH, message=str(exc))
                )
            return""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_concurrency.py::"
                "test_a_runtime_failure_is_not_reported_as_a_worker_crash",
                "-q",
            ],
        ],
    },
    {
        "name": "共享组不再关闭",
        "clause": "4.8",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """        # the group has served its purpose - it is closed (requirement 4.8)
        group.close()
        self._persist_group(group)""",
        "new": """        pass""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_shared_execution.py::"
                "test_a_finished_share_group_is_closed",
                "-q",
            ],
        ],
    },
    {
        "name": "身份未确定的状态被忽略",
        "clause": "5.2",
        "file": "agentfuse/core/workflow.py",
        "old": """            identity_status=(
                str(AgentIdentityStatus.CONFIRMED)
                if identity_confirmed
                else str(AgentIdentityStatus.UNDETERMINED)
            ),""",
        "new": """            identity_status=str(AgentIdentityStatus.CONFIRMED),""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_shared_execution.py::"
                "test_an_undetermined_agent_identity_is_recorded",
                "-q",
            ],
        ],
    },
    {
        "name": "内置工具开始依赖当前时间",
        "clause": "7.4",
        "file": "agentfuse/tools/digest_analyzer.py",
        "old": """    return {
        "file_count": len(per_file),""",
        "new": """    return {
        "generated_at": time.time(),
        "file_count": len(per_file),""",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
]


#: A guard that runs into the timeout has *caught* the mutation as well - a
#: runtime that hangs forever is the worst possible failure and the test
#: suite notices it.
GUARD_TIMEOUT = 240


def _run(command: list[str]) -> tuple[int, str]:
    try:
        process = subprocess.run(
            command,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GUARD_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return 124, str(output)[-400:]
    return process.returncode, (process.stdout or "") + (process.stderr or "")


def _check_mutation(mutation: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / mutation["file"]
    original = path.read_text(encoding="utf-8")
    if mutation["old"] not in original:
        return {
            "name": mutation["name"],
            "clause": mutation["clause"],
            "ok": False,
            "detail": f"变异点不存在于 {mutation['file']}（代码已经变了？）",
            "guards": [],
        }
    # a mutation may touch a second file as well
    extra = mutation.get("extra")
    extra_path = ROOT / extra[0] if extra else None
    extra_original = extra_path.read_text(encoding="utf-8") if extra_path else None
    if extra_path is not None and extra[1] not in extra_original:
        return {
            "name": mutation["name"],
            "clause": mutation["clause"],
            "ok": False,
            "detail": f"变异点不存在于 {extra[0]}（代码已经变了？）",
            "guards": [],
        }
    path.write_text(original.replace(mutation["old"], mutation["new"], 1), encoding="utf-8")
    if extra_path is not None:
        extra_path.write_text(
            extra_original.replace(extra[1], extra[2], 1), encoding="utf-8"
        )
    guards: list[dict[str, Any]] = []
    try:
        for command in mutation["guards"]:
            code, output = _run(command)
            label = " ".join(command[1:3])
            guards.append(
                {
                    "guard": label,
                    "exit_code": code,
                    "caught": code != 0,
                    "output": output[-400:],
                }
            )
    finally:
        path.write_text(original, encoding="utf-8")
        if extra_path is not None and extra_original is not None:
            extra_path.write_text(extra_original, encoding="utf-8")
    caught = all(guard["caught"] for guard in guards) and guards
    return {
        "name": mutation["name"],
        "clause": mutation["clause"],
        "ok": bool(caught),
        "detail": "所有守卫都发现了变异" if caught else "有一个守卫没有发现变异",
        "guards": guards,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = [_check_mutation(mutation) for mutation in MUTATIONS]
    failed = [item for item in results if not item["ok"]]

    if args.json:
        print(json.dumps(
            {
                "total": len(results),
                "failed": len(failed),
                "mutations": results,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        for item in results:
            mark = "PASS" if item["ok"] else "FAIL"
            print(f"[{mark}] {item['name']}  (§{item['clause']})")
            for guard in item["guards"]:
                state = "发现" if guard["caught"] else "未发现"
                print(f"        {state}: {guard['guard']} (exit={guard['exit_code']})")
            if not item["ok"]:
                print(f"        {item['detail']}")
        print(f"\n{len(results) - len(failed)}/{len(results)} 个守卫证明了它们能发现问题")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
