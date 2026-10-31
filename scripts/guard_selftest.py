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
import atexit
import json
import signal
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
                "tests/integration/test_divergent.py::"
                "test_the_detection_happens_while_the_leader_is_still_running",
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
        "old": """            if self._running_execution_of(call) is not None:
                continue
            if retryable and call.retry_count == 0:""",
        "new": """            if False:
                continue
            if retryable and call.retry_count == 0:""",
        # the participants that already left the group are skipped as well -
        # without that second guard the mutation has no effect
        "edits": [
            (
                """                    ParticipantStatus.DETACHED,
                ):
                    continue""",
                """                ):
                    continue""",
            ),
        ],
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
        "old": """            self._fail_waiting_participants(group, outcome.message, retried)""",
        "new": """            pass  # the participants would keep waiting""",
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
    {
        "name": "重试的参与者又被记成失败",
        "clause": "10.1",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """            if participant.logical_call_id in keep:
                continue""",
        "new": """            if False:
                continue""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_concurrency.py::"
                "test_a_retried_participant_is_not_recorded_as_failed",
                "-q",
            ],
        ],
    },
    {
        "name": "失败类型不再落库",
        "clause": "10.1",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """        call.error_kind = kind""",
        "new": """        call.error_kind = None""",
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
        "name": "冲突文件又被挪到第一个",
        "clause": "12.4",
        "file": "agentfuse/scenarios/common.py",
        "old": """DIVERGENT_FILE = "src/gamma.py\"""",
        "new": """DIVERGENT_FILE = "src/alpha.py\"""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_divergent.py::test_the_separation_is_reliable",
                "-q",
            ],
        ],
    },
    {
        "name": "场景目录与运行时脱节",
        "clause": "13",
        "file": "agentfuse/scenarios/catalogue.py",
        "old": """            "rounds": DIVERGENT_ROUNDS,""",
        "new": """            "rounds": 1500,""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_scenarios.py::"
                "test_the_catalogue_matches_the_runtime",
                "-q",
            ],
        ],
    },
    {
        "name": "超时的失败类型不再落库",
        "clause": "10.1",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """            elif outcome.kind == ERROR_TIMEOUT:
                execution.status = ExecutionStatus.TIMEOUT
                execution.error = outcome.message
                execution.error_kind = outcome.kind""",
        "new": """            elif outcome.kind == ERROR_TIMEOUT:
                execution.status = ExecutionStatus.TIMEOUT
                execution.error = outcome.message""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_concurrency.py::"
                "test_every_failure_kind_is_persisted",
                "-q",
            ],
        ],
    },
    {
        "name": "事务吞掉失败的语句",
        "clause": "16",
        "file": "agentfuse/storage/database.py",
        "old": """                    except sqlite3.Error as exc:
                        if atomic:
                            # the transaction has to be rolled back
                            raise""",
        "new": """                    except sqlite3.Error as exc:
                        if False:
                            # the transaction has to be rolled back
                            raise""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_database.py::"
                "test_a_failing_statement_rolls_the_whole_transaction_back",
                "-q",
            ],
        ],
    },
    {
        "name": "崩溃恢复漏掉参与者",
        "clause": "10.1",
        "file": "agentfuse/storage/repository.py",
        "old": """                (
                    "UPDATE share_participants SET status = ?, detached_time = ?, "
                    "detach_reason = ? WHERE workflow_id = ? AND status IN (?, ?, ?)",
                    (
                        str(ParticipantStatus.FAILED),
                        now,
                        "interrupted by a runtime restart",
                        workflow_id,
                        str(ParticipantStatus.WAITING),
                        str(ParticipantStatus.SHARED_WAIT),
                        str(ParticipantStatus.RUNNING_INDEPENDENTLY),
                    ),
                ),""",
        "new": """                (
                    "UPDATE share_participants SET status = ?, detached_time = ?, "
                    "detach_reason = ? WHERE workflow_id = ? AND status IN (?, ?, ?)",
                    (
                        str(ParticipantStatus.FAILED),
                        now,
                        "interrupted by a runtime restart",
                        workflow_id,
                        str(ParticipantStatus.WAITING),
                        str(ParticipantStatus.SHARED_WAIT),
                        str(ParticipantStatus.RUNNING_INDEPENDENTLY),
                    ),
                ) if False else ("SELECT 1", ()),""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_database.py::"
                "test_the_recovery_brings_every_table_into_a_terminal_state",
                "-q",
            ],
        ],
    },
    {
        "name": "基线更新覆盖了其他场景",
        "clause": "13.5",
        "file": "scripts/check_regression.py",
        "old": """    scenarios = dict(baseline.get("scenarios", {}))
    scenarios.update(updated)
    baseline["scenarios"] = scenarios""",
        "new": """    scenarios = dict(baseline.get("scenarios", {}))
    scenarios = dict(updated)
    baseline["scenarios"] = scenarios""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_regression_guard.py::"
                "test_a_baseline_update_keeps_the_other_scenarios",
                "-q",
            ],
        ],
    },
    {
        "name": "缺基线时守卫判绿",
        "clause": "13.5",
        "file": "scripts/check_regression.py",
        "old": """    if not reference:
        return False, [
            f"{scenario}/{mode}: no reference recorded - the baseline does not "
            "cover this scenario, the guard cannot compare anything"
        ]""",
        "new": """    if not reference:
        return True, ["no reference recorded"]""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_regression_guard.py::"
                "test_a_missing_reference_is_a_failure_not_a_pass",
                "-q",
            ],
        ],
    },
    {
        "name": "守卫自检不再还原变异",
        "clause": "17.2",
        "file": "scripts/guard_selftest.py",
        "old": """        _restore(path, original)
        _MUTATED.pop(path, None)""",
        "new": """        _MUTATED.pop(path, None)""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_guard_selftest.py",
                "-q",
            ],
        ],
    },
    {
        "name": "审计不再检查 CI 工作流",
        "clause": "19",
        "file": "scripts/audit_requirements.py",
        "old": """        paths = re.findall(r"`([\w./]+\.(?:py|yml|yaml|toml|md))`", entry)""",
        "new": """        paths = re.findall(r"`([\w./]+\.py)`", entry)""",
        "expect": "CI 工作流",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
            ],
        ],
    },
    {
        "name": "被拒候选者仍算作组员",
        "clause": "8.2",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """        # the member list of the group follows ``group.participants``
        group.participants.pop(participant.logical_call_id, None)""",
        "new": """        # the member list of the group follows ``group.participants``""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_divergent.py::"
                "test_a_rejected_candidate_is_not_a_member_of_the_group",
                "-q",
            ],
        ],
    },
    {
        "name": "离组的参与者不再是终态",
        "clause": "8.2",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """            participant.detach_kind = "left_group\"""",
        "new": """            participant.detach_kind = "after_join\"""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_concurrency.py::"
                "test_a_retried_participant_is_not_recorded_as_failed",
                "-q",
            ],
        ],
    },
]


#: A guard that runs into the timeout has **not** proven anything: it could
#: hang for an unrelated reason (a deadlock of the test runner, a missing
#: dependency).  A timeout is therefore reported as *inconclusive* - the
#: mutation counts as not proven.
GUARD_TIMEOUT = 240

#: The exit code of a guard that ran into the timeout.
TIMEOUT_EXIT_CODE = 124


def _run(command: list[str]) -> tuple[int, str, str]:
    """Run a guard command.

    Returns the exit code, the combined output and the **stdout** alone - a
    machine readable report has to be parsed without the diagnostics of the
    error stream.
    """

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
        return TIMEOUT_EXIT_CODE, str(output)[-400:], str(output)[-400:]
    return (
        process.returncode,
        (process.stdout or "") + (process.stderr or ""),
        process.stdout or "",
    )


def _expected_signature(command: list[str], mutation: dict[str, Any]) -> str:
    """The text the output of the guard has to contain.

    A guard that fails for an unrelated reason (a syntax error, a missing
    file) would also exit non zero - and would "prove" that it detects the
    mutation.  The expected signature is therefore part of the proof: the
    guard has to name the check that failed.
    """

    explicit = mutation.get("expect")
    if explicit:
        return str(explicit)
    if "pytest" in command:
        # the test that has to fail
        for part in command:
            if part.startswith("tests/") and "::" in part:
                return part.split("::", 1)[1].split("::")[-1]
        return ""
    if "audit_requirements" in " ".join(command):
        # the summary of the audit: the number of the failed checks is only
        # greater than zero when a check really failed
        return "未通过"
    return ""


def _caught(
    code: int,
    output: str,
    signature: str,
    command: list[str] | None = None,
    stdout: str = "",
) -> tuple[bool, str]:
    """Did the guard really catch **this** mutation?"""

    if code == TIMEOUT_EXIT_CODE:
        return False, "the guard ran into the timeout - the mutation is not proven"
    if code == 0:
        return False, "the guard passed"
    command = command or []
    if "audit_requirements" in " ".join(command) and "--json" in command:
        # the machine readable report is stronger than a text match: the
        # number of the failed checks has to be greater than zero
        try:
            report = json.loads(stdout)
        except Exception:
            return False, "the audit did not produce a report"
        if int(report.get("failed", 0)) > 0:
            return True, f"the audit reported {report['failed']} failed checks"
        return False, "the audit did not report a failed check"
    if signature and signature not in output:
        return False, (
            f"the guard failed, but it does not name {signature!r} - "
            "it may have failed for an unrelated reason"
        )
    return True, "the guard failed as expected"


def _apply_edits(original: str, mutation: dict[str, Any]) -> str:
    """Apply the mutation to the source text.

    A mutation may break **several** places at once - sometimes two guards
    protect the same invariant and only the combination of both being broken
    lets the defect through.
    """

    text = original
    if mutation.get("old") is not None:
        text = text.replace(mutation["old"], mutation["new"], 1)
    for old_text, new_text in mutation.get("edits", ()):
        text = text.replace(old_text, new_text, 1)
    return text


def _restore(path: Path, original: str) -> None:
    """Put the original content back and prove it."""

    path.write_text(original, encoding="utf-8")
    if path.read_text(encoding="utf-8") != original:
        raise RuntimeError(f"could not restore {path}")


#: The files that are currently mutated.  An emergency exit (a cancelled CI
#: job, a ``kill`` on the console) must not leave the repository broken.
_MUTATED: dict[Path, str] = {}


def _restore_all() -> None:
    for path, original in list(_MUTATED.items()):
        try:
            path.write_text(original, encoding="utf-8")
        except Exception:  # pragma: no cover - the best effort at the very end
            pass
    _MUTATED.clear()


atexit.register(_restore_all)


def _install_signal_handlers() -> None:
    """A termination signal has to restore the mutated files."""

    def _handler(signum: int, _frame: Any) -> None:
        _restore_all()
        raise KeyboardInterrupt(f"the self test was interrupted (signal {signum})")

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, _handler)
        except (ValueError, OSError):  # pragma: no cover - not every platform
            pass


_install_signal_handlers()


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
    guards: list[dict[str, Any]] = []
    try:
        # The mutation is written **inside** the try: a failing write of the
        # second file must not leave the first one mutated.
        _MUTATED[path] = original
        path.write_text(_apply_edits(original, mutation), encoding="utf-8")
        if extra_path is not None:
            _MUTATED[extra_path] = extra_original
            extra_path.write_text(
                extra_original.replace(extra[1], extra[2], 1), encoding="utf-8"
            )
        for command in mutation["guards"]:
            code, output, stdout = _run(command)
            label = " ".join(command[1:3])
            signature = _expected_signature(command, mutation)
            caught, reason = _caught(code, output, signature, command, stdout)
            guards.append(
                {
                    "guard": label,
                    "exit_code": code,
                    "caught": caught,
                    "reason": reason,
                    "output": output[-400:],
                }
            )
    finally:
        # The working copy has to be left untouched - always, also when the
        # guard command or the script itself fails.
        _restore(path, original)
        _MUTATED.pop(path, None)
        if extra_path is not None and extra_original is not None:
            _restore(extra_path, extra_original)
            _MUTATED.pop(extra_path, None)
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
                if not guard["caught"] and guard.get("reason"):
                    print(f"                {guard['reason']}")
            if not item["ok"]:
                print(f"        {item['detail']}")
        print(f"\n{len(results) - len(failed)}/{len(results)} 个守卫证明了它们能发现问题")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
