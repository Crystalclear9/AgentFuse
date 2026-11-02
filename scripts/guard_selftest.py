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
_NEW_MUTATIONS: list[dict[str, Any]] = [
    {
        "name": "P2 清单不再完整",
        "clause": "19",
        "file": "scripts/audit_requirements.py",
        # one clause disappears from the list - the audit has to notice that
        # the document no longer lists all ten clauses of the requirement
        "old": """    "跨会话持久化结果缓存",
    "GPU 资源调度",""",
        "new": """    "GPU 资源调度",""",
        "expect_audit": "19",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "验收清单不再完整",
        "clause": "23",
        "file": "scripts/audit_requirements.py",
        # one box of the acceptance checklist disappears
        "old": """    ("\u53ef\u6253\u5f00\u672c\u5730 Web UI", "test", "test_the_dashboard_is_served"),""",
        "new": """    ("\u53ef\u6253\u5f00\u672c\u5730 Web UI", "test", "test_the_dashboard_is_served"),
    ("\u4e34\u65f6\u653e\u5165\u7684\u4f1a\u8bae", "test", "test_the_dashboard_is_served"),""",
        "expect_audit": "23",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "守卫自检不再要求指名的检查",
        "clause": "17.2",
        "file": "scripts/guard_selftest.py",
        # the proof becomes weak again: any non zero exit counts
        "old": """    if not signature:
        return False, (
            "the guard has no named expectation - a non zero exit proves "
            "nothing (add \\"expect\\" or a tests/...::test_name selector)"
        )""",
        "new": """    if not signature:
        return True, "the guard failed as expected\"""",
        "expect": "test_a_guard_without_a_named_expectation_is_not_proven",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_guard_selftest.py::test_a_guard_without_a_named_expectation_is_not_proven",
                "-q",
            ],
        ],
    },
    {
        "name": "绕过检测不再覆盖 stat",
        "clause": "7.2",
        "file": "agentfuse/workspace/reader.py",
        # os.stat is no longer audited - a tool that only stats a file would
        # not be detected any more
        "old": """    "os.scandir",
    "os.listdir",
    "os.stat",
    "os.lstat",
)""",
        "new": """    "os.scandir",
    "os.listdir",
)""",
        "expect_audit": "7.2",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "长跑观测不再是门",
        "clause": "17.5",
        "file": "scripts/soak.py",
        # the gate is removed: the soak reports a problem but passes anyway
        "old": """    if problems:
        print()
        print("  长跑观测未通过：")
        for problem in problems:
            print(f"    - {problem}")
        return 1""",
        "new": """    if problems:
        print()
        print("  长跑观测未通过：")
        for problem in problems:
            print(f"    - {problem}")
        return 0""",
        "expect": "test_the_soak_gate_reports_a_problem",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_soak_gate.py::test_the_soak_gate_reports_a_problem",
                "-q",
            ],
        ],
    },
]






#: Every mutation of the delivery: the new ones first, the older ones after.

def _node_executable() -> str:
    """The node executable - the front end harness is JavaScript.

    The mutation points that guard the front end have to run it with **node**;
    running it with the python interpreter would fail for the wrong reason and
    would prove nothing.
    """

    import shutil

    return shutil.which("node") or "node"


_LEGACY_MUTATIONS: list[dict[str, Any]] = [
    {
        "name": "依赖完整性不再决定工具能否共享",
        "clause": "7.2 / 21.3",
        "file": "agentfuse/tools/registry.py",
        "old": (
            '            implementation=python_ast_analyzer,\n'
            '            implementation_reference=describe_implementation(python_ast_analyzer),\n'
            '            dependency_tracking=True,\n'
        ),
        "new": (
            '            implementation=python_ast_analyzer,\n'
            '            implementation_reference=describe_implementation(python_ast_analyzer),\n'
            '            dependency_tracking=False,\n'
        ),
        "expect": [
            "test_a_controlled_tool_is_shared",
            "test_two_equivalent_calls_share_a_single_physical_execution",
        ],
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
        "expect_audit": "16",
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
        # the counter of the **commit** failure: a statement that violates a
        # constraint is only detected when the transaction is committed
        "old": (
            "            # a deferred foreign key is checked when the transaction is\n"
            "            # committed - the failure must not take the writer thread down\n"
            "            self.write_errors += 1\n"
            "            self.last_error = f\"transaction failed: {type(exc).__name__}: {exc}\""
        ),
        "new": (
            "            # a deferred foreign key is checked when the transaction is\n"
            "            # committed - the failure must not take the writer thread down\n"
            "            self.last_error = f\"transaction failed: {type(exc).__name__}: {exc}\""
        ),
        "expect_audit": "16",
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
        "expect": "test_the_dashboard_renders_the_real_data",
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
        "expect_audit": "10.1",
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
        "expect": "test_candidate_key_does_not_contain_the_snapshot",
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
        "expect": "test_the_random_source_of_a_call_is_reproducible",
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
            """        failed_participant = self._fail_participant_of(call, message)""",
            """        failed_participant = None""",
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
        "expect_audit": "7.4",
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
        # the context makes the pattern unique - without it the pattern would
        # match the definition of this mutation itself
        # The pattern of a mutation of **this** file also appears in the
        # definition of the mutation itself - the self test therefore
        # replaces the **last** occurrence (the code is below the list).
        "old": """    finally:
        # The working copy has to be left untouched - always, also when the
        # guard command or the script itself fails.
        _restore(path, original)
        _MUTATED.pop(path, None)""",
        "new": """    finally:
        # The working copy has to be left untouched - always, also when the
        # guard command or the script itself fails.
        _MUTATED.pop(path, None)""",
        "expect": "test_a_mutation_is_reverted_even_if_the_guard_fails",
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
        "name": "关闭时不再关闭数据库",
        "clause": "17.2",
        "file": "agentfuse/app.py",
        # a flush that times out must not keep the database open - on Windows
        # an open handle locks the file
        "old": """            try:
                self.database.flush()
            except TimeoutError as exc:""",
        "new": """            try:
                self.database.flush()
            except ValueError as exc:""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_database.py::"
                "test_a_stuck_writer_does_not_keep_the_database_open",
                "-q",
            ],
        ],
    },
    {
        "name": "共享状态不再原子落库",
        "clause": "8.2",
        "file": "agentfuse/storage/repository.py",
        "old": """        rows: list[tuple[str, tuple[Any, ...]]] = [self._share_group_row(group)]
        rows.extend(self._share_participant_row(participant) for participant in participants)
        rows.extend(self._logical_call_row(call) for call in calls)
        self.db.transaction(rows)""",
        "new": """        self.db.write(*self._share_group_row(group))
        for participant in participants:
            self.db.write(*self._share_participant_row(participant))
        for call in calls:
            self.db.write(*self._logical_call_row(call))""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_database.py::"
                "test_the_share_state_is_written_atomically",
                "-q",
            ],
        ],
    },
    {
        "name": "文本检查被算成行为检查",
        "clause": "17.2",
        "file": "scripts/audit_requirements.py",
        "old": """                    "已覆盖" if function in scenario_tests else "未被任何测试执行",
                    # it is a text match, not a behaviour
                    kind="structural",""",
        "new": """                    "已覆盖" if function in scenario_tests else "未被任何测试执行",""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_audit_report.py::"
                "test_a_text_match_is_not_counted_as_behaviour",
                "-q",
            ],
        ],
    },
    {
        "name": "审计不再报告检查的种类",
        "clause": "17.2",
        "file": "scripts/audit_requirements.py",
        # the report has to say which checks really run the runtime - without
        # the kind the audit looks stronger than it is
        "old": """            "detail": detail,
            "kind": kind or PHASE,""",
        "new": """            "detail": detail,""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_audit_report.py::"
                "test_the_report_names_the_kind_of_every_check",
                "-q",
            ],
        ],
    },
    {
        "name": "审计不再检查 CI 工作流",
        "clause": "19",
        "file": ".github/workflows/ci.yml",
        # the workflow without macOS - the audit has to notice that the
        # promised platform is missing
        "old": "os: [windows-latest, ubuntu-latest, macos-latest]",
        "new": "os: [windows-latest, ubuntu-latest]",
        "expect_audit": "19",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "审计不再检查 CI 工作流的内容",
        "clause": "19",
        "file": "scripts/audit_requirements.py",
        # the check of the CI workflow has to be **gone** - an empty list would
        # only skip it
        "old": """        record(
            "19",
            "CI 工作流的内容",
            not absent,
            f"缺少: {absent}" if absent else f"{len(CI_WORKFLOW_EXPECTATIONS)} 项都在",
        )""",
        "new": """        pass""",
        "expect": "test_the_ci_workflow_is_checked_for_its_content",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_audit_report.py::test_the_ci_workflow_is_checked_for_its_content",
                "-q",
            ],
        ],
    },
    {
        "name": "前端不再显示等待原因",
        "clause": "14",
        "file": "agentfuse/web/app.js",
        "old": """event.metadata.reason || event.metadata.wait_reason || event.metadata.relative_path""",
        "new": """event.metadata.reason || event.metadata.relative_path""",
        "expect": "分享事件显示等待原因",
        "guards": [
            [
                _node_executable(),
                "tests/frontend/dom_harness.mjs",
            ],
        ],
    },
    {
        "name": "前端不再显示加入被拒的计数",
        "clause": "14",
        "file": "agentfuse/web/app.js",
        "old": """      { label: "加入被拒", value: data.rejected_candidates, sub: "rejected_at_join" },
""",
        "new": "",
        "expect": "总览显示加入被拒的计数",
        "guards": [
            [
                _node_executable(),
                "tests/frontend/dom_harness.mjs",
            ],
        ],
    },
    {
        "name": "前端录制的响应与接口不一致",
        "clause": "14",
        "file": "tests/frontend/payloads.json",
        # the last occurrence belongs to the metrics
        "old": """   "retried_participants": 0,
""",
        "new": "",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/frontend/test_frontend.py::"
                "test_the_recorded_payloads_have_the_shape_of_the_current_api",
                "-q",
            ],
        ],
    },
    {
        "name": "deferred 模式不再校验快照完整性",
        "clause": "8.7",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """            # The **final** integrity check runs in every mode: a snapshot that
            # was tampered with after the join must not receive a shared
            # result - not even in the deferred mode (requirement 8.7 and the
            # correctness checklist of the sharing protocol).
            if not self._snapshot_is_trustworthy(participant.snapshot_id or "", force=True):
                self._detach_participant_locked(
                    group,
                    participant,
                    "snapshot integrity check failed before the result was shared",
                )
                continue
""",
        "new": "",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_resources_and_integrity.py::"
                "test_a_snapshot_tampered_after_the_join_is_not_shared_in_the_deferred_mode",
                "-q",
            ],
        ],
    },
    {
        "name": "绕过检测不再覆盖目录列举",
        "clause": "21.3",
        "file": "agentfuse/workspace/reader.py",
        "old": """    "os.scandir",
    "os.listdir",
""",
        "new": "",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_dependency_tracking.py::"
                "test_a_tool_that_only_lists_directories_is_detected",
                "-q",
            ],
        ],
    },
    {
        "name": "API 读路径不再让出事件循环",
        "clause": "16.5",
        "file": "agentfuse/api/routes/overview.py",
        "old": """    data = await read(lambda: _overview(app.repository, app.registry))""",
        "new": """    data = _overview(app.repository, app.registry)""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_api.py::test_the_api_does_not_block_the_event_loop",
                "-q",
            ],
        ],
    },
    {
        "name": "API 不再拒绝非回环监听",
        "clause": "2.2",
        "file": "agentfuse/api/app.py",
        "old": """    require_loopback(settings.api_host, allow_remote=allow_remote)""",
        "new": """    pass  # the dashboard would be exposed""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_api.py::test_the_api_refuses_a_public_address",
                "-q",
            ],
        ],
    },
    {
        "name": "取消的调用不再终止自己的 worker",
        "clause": "10.4",
        "file": "agentfuse/runtime/coordinator.py",
        "old": """            else:
                # The call runs on its own (a tool that is not shareable) - it
                # has no group, therefore its **own** execution has to be
                # terminated.  Otherwise the worker would keep running after
                # the call was cancelled.
                execution = self._executions.get(call.physical_execution_id or "")
                if execution is not None and execution.status is ExecutionStatus.RUNNING:
                    terminate.append(execution)
""",
        "new": "",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/integration/test_concurrency.py::"
                "test_a_cancelled_independent_call_stops_its_worker",
                "-q",
            ],
        ],
    },
    {
        "name": "审计不再运行 CLI 命令",
        "clause": "15",
        "file": "scripts/audit_requirements.py",
        "old": """    for command in ("init", "serve", "demo", "compare", "trace", "inspect", "tools"):
        result = subprocess.run(
            [sys.executable, "-m", "agentfuse", command, "--help"],""",
        "new": """    for command in ():
        result = subprocess.run(
            [sys.executable, "-m", "agentfuse", command, "--help"],""",
        "expect": "P0",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
            ],
        ],
    },
    {
        "name": "共享状态不再原子落库",
        "clause": "16",
        "file": "agentfuse/storage/repository.py",
        "old": """        rows: list[tuple[str, tuple[Any, ...]]] = [self._share_group_row(group)]
        rows.extend(self._share_participant_row(participant) for participant in participants)
        rows.extend(self._logical_call_row(call) for call in calls)
        self.db.transaction(rows)""",
        "new": """        self.db.write(*self._share_group_row(group))
        for participant in participants:
            self.db.write(*self._share_participant_row(participant))
        for call in calls:
            self.db.write(*self._logical_call_row(call))""",
        "expect": "半写",
        "guards": [
            [
                sys.executable,
                "scripts/acceptance_check.py",
            ],
        ],
    },
    {
        "name": "公平性不再比较数据",
        "clause": "13.2",
        "file": "scripts/audit_requirements.py",
        "old": """    if not tools:
        return {}
    return {
        "same tool implementations and requests": len(
            {frozenset(value) for value in tools.values()}
        )
        == 1,
        "same timeout": len({frozenset(value) for value in limits.values()}) == 1,
    }""",
        "new": """    if not tools:
        return {}
    return {
        "same tool implementations and requests": True,
        "same timeout": True,
    }""",
        "guards": [
            [
                sys.executable,
                "-m",
                "pytest",
                "tests/unit/test_audit_fairness.py::"
                "test_the_fairness_comparison_reports_the_differences",
                "-q",
            ],
        ],
    },
    {
        "name": "事件不再有稳定的序号",
        "clause": "5.1",
        "file": "agentfuse/core/events.py",
        "old": """            sequence = next(self._sequences[workflow_id or "-"])""",
        "new": """            sequence = 0""",
        "expect_audit": "5.1",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "受控读取不再拒绝越界路径",
        "clause": "7.2",
        "file": "agentfuse/workspace/reader.py",
        "old": """        if part == "..":
            raise WorkspaceBoundaryError(f"path escapes the workspace root: {path!r}")""",
        "new": """        if part == "..":
            continue""",
        "expect_audit": "7.2",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
    {
        "name": "结果缓存不再按配置开启",
        "clause": "10.2",
        "file": "agentfuse/app.py",
        "old": """            result_cache_entries=self.settings.result_cache_entries,""",
        "new": """            result_cache_entries=0,""",
        "expect": "结果缓存",
        "guards": [
            [
                sys.executable,
                "scripts/acceptance_check.py",
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
    {
        "name": "README 的演示数字不再与实测一致",
        "clause": "22",
        "file": "README.md",
        # the number the README claims for the physical executions of the
        # shared scenario - the audit runs the scenario and compares it
        "old": "物理执行数    | 1        <- 两次逻辑调用只做了一次真实计算",
        "new": "物理执行数    | 2        <- 两次逻辑调用只做了一次真实计算",
        "expect_audit": "22",
        "guards": [
            [
                sys.executable,
                "scripts/audit_requirements.py",
                "--json",
            ],
        ],
    },
]


#: Every mutation of the delivery: the new ones first, the older ones after.
MUTATIONS: list[dict[str, Any]] = _NEW_MUTATIONS + _LEGACY_MUTATIONS


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


def _expected_signature(
    command: list[str], mutation: dict[str, Any], index: int = 0
) -> str:
    """The text the output of the guard has to contain.

    A guard that fails for an unrelated reason (a syntax error, a missing
    file) would also exit non zero - and would "prove" that it detects the
    mutation.  The expected signature is therefore part of the proof: the
    guard has to name the check that failed.

    **An empty signature is not an acceptable proof.**  It used to be: a
    mutation without a named expectation was counted as "caught" as soon as
    the guard exited non zero, so a crash anywhere in the test file proved
    the guard worked.  Now an empty signature means the mutation point is
    **not proven** - the guard has to name the failing check.
    """

    explicit = mutation.get("expect")
    if isinstance(explicit, (list, tuple)):
        # one expectation per guard: a mutation can break a different test in
        # every file it is guarded by
        if index < len(explicit):
            return str(explicit[index])
        return ""
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


def _expected_audit_clause(command: list[str], mutation: dict[str, Any]) -> str:
    """The clause of the audit that has to fail for this mutation.

    ``expect_audit`` names the clause (``§8.2`` becomes ``8.2``).  Without it
    the mutation cannot claim that it proved anything about a **specific**
    clause - the audit may have failed for an unrelated reason.
    """

    explicit = mutation.get("expect_audit")
    if not explicit:
        return ""
    return str(explicit).lstrip("§")


def _caught(
    code: int,
    output: str,
    signature: str,
    command: list[str] | None = None,
    stdout: str = "",
    mutation: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Did the guard really catch **this** mutation?

    The proof has three parts:

    1. the guard has to fail (exit non zero, no timeout);
    2. the guard has to **name** the check that failed - otherwise a crash
       anywhere in the test file would count as a proof;
    3. for the audit the failed check has to be the **expected** one - a
       mutation that makes *some* check fail is not a proof either.
    """

    if code == TIMEOUT_EXIT_CODE:
        return False, "the guard ran into the timeout - the mutation is not proven"
    if code == 0:
        if "skipped" in output.lower() and "no tests ran" in output.lower():
            return False, (
                "the guard skipped the test (a missing dependency?) - the "
                "mutation is not proven"
            )
        return False, "the guard passed"
    command = command or []
    if "audit_requirements" in " ".join(command) and "--json" in command:
        # the machine readable report is stronger than a text match: the
        # number of the failed checks has to be greater than zero **and** the
        # expected clause has to be among them
        try:
            report = json.loads(stdout)
        except Exception:
            return False, "the audit did not produce a report"
        if int(report.get("failed", 0)) <= 0:
            return False, "the audit did not report a failed check"
        failed_checks = [
            (str(item.get("section", "")), str(item.get("clause", "")))
            for item in report.get("checks", [])
            if not item.get("ok")
        ]
        expected = _expected_audit_clause(command, mutation)
        if expected:
            named = [
                clause
                for section, clause in failed_checks
                if section == expected or expected in clause
            ]
            if not named:
                seen = ["\u00a7{0} {1}".format(section, clause) for section, clause in failed_checks[:3]]
                return False, (
                    "the audit failed, but not on \u00a7{0} - it failed on {1} instead".format(
                        expected, seen
                    )
                )
            return True, "the audit reported the expected failure: \u00a7{0} {1}".format(
                expected, named[0]
            )
        # no explicit clause is expected: every failed clause has to be named
        # in the output, otherwise the proof is not specific enough
        if not failed_checks:
            return False, "the audit did not name the failed check"
        return True, f"the audit reported {len(failed_checks)} failed checks"
    if not signature:
        return False, (
            "the guard has no named expectation - a non zero exit proves "
            "nothing (add \"expect\" or a tests/...::test_name selector)"
        )
    if signature not in output:
        return False, (
            f"the guard failed, but it does not name {signature!r} - "
            "it may have failed for an unrelated reason"
        )
    return True, "the guard failed as expected"


def _replace_once(text: str, old_text: str, new_text: str) -> str:
    """Replace one occurrence of a pattern.

    The **last** occurrence is used: the pattern of a mutation of this file
    also appears in the definition of the mutation itself (the definition is
    above the code), and the code is the one that has to be broken.
    """

    index = text.rfind(old_text)
    if index < 0:
        return text
    return text[:index] + new_text + text[index + len(old_text):]


def _apply_edits(original: str, mutation: dict[str, Any]) -> str:
    """Apply the mutation to the source text.

    A mutation may break **several** places at once - sometimes two guards
    protect the same invariant and only the combination of both being broken
    lets the defect through.
    """

    text = original
    if mutation.get("old") is not None:
        text = _replace_once(text, mutation["old"], mutation["new"])
    for old_text, new_text in mutation.get("edits", ()):
        text = _replace_once(text, old_text, new_text)
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
        for index, command in enumerate(mutation["guards"]):
            code, output, stdout = _run(command)
            label = " ".join(command[1:3])
            signature = _expected_signature(command, mutation, index)
            caught, reason = _caught(code, output, signature, command, stdout, mutation)
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
