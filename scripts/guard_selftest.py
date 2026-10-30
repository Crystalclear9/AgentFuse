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
        "old": "            _cancelled_outcome(reason)",
        "new": "            _timeout_outcome(reason)",
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
    path.write_text(original.replace(mutation["old"], mutation["new"], 1), encoding="utf-8")
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
