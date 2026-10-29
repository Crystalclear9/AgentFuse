"""Repository: the single place that talks to SQLite for domain entities."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from .database import Database
from .models import (
    AgentInvocationRecord,
    AgentRecord,
    BenchmarkRunRecord,
    DependencyRecord,
    LogicalToolCallRecord,
    PhysicalExecutionRecord,
    ResourceSample,
    ShareGroupRecord,
    ShareParticipantRecord,
    ValidationRecord,
    WorkflowRecord,
    WorkspaceSnapshotRecord,
)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _loads(raw: Any, default: Any = None) -> Any:
    if raw in (None, ""):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


class Repository:
    """Persistence facade over :class:`agentfuse.storage.database.Database`."""

    def __init__(self, database: Database) -> None:
        self.db = database

    # ------------------------------------------------------------------
    # workflows
    # ------------------------------------------------------------------
    def save_workflow(self, record: WorkflowRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO workflows
            (workflow_id, scenario, start_time, end_time, status, configuration,
             runtime_mode, random_seed, label, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.workflow_id,
                record.scenario,
                record.start_time,
                record.end_time,
                record.status,
                _dumps(record.configuration),
                record.runtime_mode,
                record.random_seed,
                record.label,
                record.error,
            ),
        )

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,))
        return self._workflow_row(row) if row else None

    def list_workflows(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM workflows ORDER BY start_time DESC LIMIT ?", (limit,)
        )
        return [self._workflow_row(row) for row in rows]

    @staticmethod
    def _workflow_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["configuration"] = _loads(data.get("configuration"), {})
        return data

    # ------------------------------------------------------------------
    # agents / invocations
    # ------------------------------------------------------------------
    def save_agent(self, record: AgentRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO agents
            (agent_id, workflow_id, external_identity, agent_description,
             registration_source, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.agent_id,
                record.workflow_id,
                record.external_identity,
                record.agent_description,
                record.registration_source,
                _dumps(record.metadata),
                record.created_at,
            ),
        )

    def list_agents(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM agents WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)
        )
        result = []
        for row in rows:
            data = dict(row)
            data["metadata"] = _loads(data.get("metadata"), {})
            result.append(data)
        return result

    def save_invocation(self, record: AgentInvocationRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO agent_invocations
            (invocation_id, workflow_id, agent_id, start_time, end_time,
             parent_invocation_id, status, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.invocation_id,
                record.workflow_id,
                record.agent_id,
                record.start_time,
                record.end_time,
                record.parent_invocation_id,
                record.status,
                _dumps(record.metadata),
            ),
        )

    def list_invocations(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM agent_invocations WHERE workflow_id = ? ORDER BY start_time",
            (workflow_id,),
        )
        result = []
        for row in rows:
            data = dict(row)
            data["metadata"] = _loads(data.get("metadata"), {})
            result.append(data)
        return result

    # ------------------------------------------------------------------
    # logical tool calls
    # ------------------------------------------------------------------
    def save_logical_call(self, record: LogicalToolCallRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO logical_tool_calls
            (logical_call_id, workflow_id, invocation_id, agent_id, tool_name, tool_version,
             normalized_arguments, workspace_snapshot_id, request_time, status,
             physical_execution_id, result_reference, result_hash, candidate_key,
             share_group_id, error, completed_time, retry_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.logical_call_id,
                record.workflow_id,
                record.invocation_id,
                record.agent_id,
                record.tool_name,
                record.tool_version,
                record.normalized_arguments,
                record.workspace_snapshot_id,
                record.request_time,
                record.status,
                record.physical_execution_id,
                record.result_reference,
                record.result_hash,
                record.candidate_key,
                record.share_group_id,
                record.error,
                record.completed_time,
                record.retry_count,
            ),
        )

    def get_logical_call(self, logical_call_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM logical_tool_calls WHERE logical_call_id = ?", (logical_call_id,)
        )
        return dict(row) if row else None

    def list_logical_calls(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM logical_tool_calls WHERE workflow_id = ? ORDER BY request_time",
            (workflow_id,),
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # physical executions
    # ------------------------------------------------------------------
    def save_execution(self, record: PhysicalExecutionRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO physical_executions
            (execution_id, workflow_id, tool_name, tool_version, arguments_hash, candidate_key,
             source_snapshot_id, worker_pid, start_time, finish_time, execution_status,
             cpu_time, peak_rss, exit_status, result_hash, result_reference, error,
             timeout_seconds, share_group_id, attempt, admission_reason, queued_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.execution_id,
                record.workflow_id,
                record.tool_name,
                record.tool_version,
                record.arguments_hash,
                record.candidate_key,
                record.source_snapshot_id,
                record.worker_pid,
                record.start_time,
                record.finish_time,
                record.execution_status,
                record.cpu_time,
                record.peak_rss,
                record.exit_status,
                record.result_hash,
                record.result_reference,
                record.error,
                record.timeout_seconds,
                record.share_group_id,
                record.attempt,
                record.admission_reason,
                record.queued_time,
            ),
        )

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM physical_executions WHERE execution_id = ?", (execution_id,)
        )
        return dict(row) if row else None

    def list_executions(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM physical_executions WHERE workflow_id = ? ORDER BY start_time",
            (workflow_id,),
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # share groups
    # ------------------------------------------------------------------
    def save_share_group(self, record: ShareGroupRecord) -> None:
        self.db.write(
            """
                INSERT OR REPLACE INTO share_groups
                (share_group_id, workflow_id, candidate_key, leader_execution_id,
                 leader_logical_call_id, creation_time, closed_time, status,
                 participant_count, participant_calls, mode, tool_name, tool_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.share_group_id,
                    record.workflow_id,
                    record.candidate_key,
                    record.leader_execution_id,
                    record.leader_logical_call_id,
                    record.creation_time,
                    record.closed_time,
                    record.status,
                    record.participant_count,
                    json.dumps(list(record.participant_calls), ensure_ascii=False),
                    record.mode,
                    record.tool_name,
                    record.tool_version,
                ),
        )

    def list_share_groups(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM share_groups WHERE workflow_id = ? ORDER BY creation_time",
            (workflow_id,),
        )
        return [dict(row) for row in rows]

    def save_participant(self, record: ShareParticipantRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO share_participants
            (share_group_id, logical_call_id, workflow_id, agent_id, invocation_id, snapshot_id,
             status, validated_prefix_length, validation_status, attached_time, detached_time,
             delivered_time, wait_duration, detach_reason, is_leader)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.share_group_id,
                record.logical_call_id,
                record.workflow_id,
                record.agent_id,
                record.invocation_id,
                record.snapshot_id,
                record.status,
                record.validated_prefix_length,
                record.validation_status,
                record.attached_time,
                record.detached_time,
                record.delivered_time,
                record.wait_duration,
                record.detach_reason,
                1 if record.is_leader else 0,
            ),
        )

    def list_participants(self, workflow_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM share_participants WHERE workflow_id = ? ORDER BY attached_time",
            (workflow_id,),
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------
    def save_snapshot(self, record: WorkspaceSnapshotRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO workspace_snapshots
            (snapshot_id, parent_snapshot_id, workspace_id, snapshot_path, creation_time,
             manifest_hash, source_reference, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.snapshot_id,
                record.parent_snapshot_id,
                record.workspace_id,
                record.snapshot_path,
                record.creation_time,
                record.manifest_hash,
                record.source_reference,
                _dumps(record.metadata),
            ),
        )

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM workspace_snapshots WHERE snapshot_id = ?", (snapshot_id,)
        )
        return dict(row) if row else None

    def list_snapshots(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        if workspace_id:
            rows = self.db.query(
                "SELECT * FROM workspace_snapshots WHERE workspace_id = ? ORDER BY creation_time",
                (workspace_id,),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM workspace_snapshots ORDER BY creation_time"
            )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # dependencies & validations
    # ------------------------------------------------------------------
    def save_dependency(self, record: DependencyRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO dependency_records
            (execution_id, sequence_number, dependency_type, relative_path, fingerprint,
             observed_time, snapshot_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.execution_id,
                record.sequence_number,
                record.dependency_type,
                record.relative_path,
                record.fingerprint,
                record.observed_time,
                record.snapshot_id,
            ),
        )

    def list_dependencies(self, execution_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM dependency_records WHERE execution_id = ? ORDER BY sequence_number",
            (execution_id,),
        )
        return [dict(row) for row in rows]

    def save_validation(self, record: ValidationRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO validation_records
            (validation_id, share_group_id, logical_call_id, execution_id, dependency_sequence,
             validation_status, validation_time, mismatch_reason, elapsed_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.validation_id,
                record.share_group_id,
                record.logical_call_id,
                record.execution_id,
                record.dependency_sequence,
                record.validation_status,
                record.validation_time,
                record.mismatch_reason,
                record.elapsed_ms,
            ),
        )

    def list_validations(self, share_group_id: str | None = None) -> list[dict[str, Any]]:
        if share_group_id:
            rows = self.db.query(
                "SELECT * FROM validation_records WHERE share_group_id = ? ORDER BY validation_time",
                (share_group_id,),
            )
        else:
            rows = self.db.query("SELECT * FROM validation_records ORDER BY validation_time")
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # resource samples
    # ------------------------------------------------------------------
    def save_resource_sample(self, sample: ResourceSample) -> None:
        self.db.write(
            """
            INSERT INTO resource_samples
            (workflow_id, execution_id, timestamp, rss_bytes, cpu_seconds, system_cpu_percent)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                sample.workflow_id,
                sample.execution_id,
                sample.timestamp,
                sample.rss_bytes,
                sample.cpu_seconds,
                sample.system_cpu_percent,
            ),
        )

    def list_resource_samples(self, execution_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM resource_samples WHERE execution_id = ? ORDER BY timestamp",
            (execution_id,),
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # tool results
    # ------------------------------------------------------------------
    def save_tool_result(
        self,
        result_id: str,
        workflow_id: str | None,
        execution_id: str | None,
        logical_call_id: str | None,
        result_hash: str,
        payload_path: str | None,
        payload_size: int | None,
        created_at: float,
    ) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO tool_results
            (result_id, workflow_id, execution_id, logical_call_id, result_hash,
             payload_path, payload_size, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                workflow_id,
                execution_id,
                logical_call_id,
                result_hash,
                payload_path,
                payload_size,
                created_at,
            ),
        )

    def get_tool_result(self, result_hash: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM tool_results WHERE result_hash = ? ORDER BY created_at LIMIT 1",
            (result_hash,),
        )
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # benchmark runs
    # ------------------------------------------------------------------
    def save_benchmark_run(self, record: BenchmarkRunRecord) -> None:
        self.db.write(
            """
            INSERT OR REPLACE INTO benchmark_runs
            (run_id, scenario, mode, repeat_index, workflow_id, started_at, finished_at, makespan,
             physical_executions, logical_calls, saved_executions, eligible_calls, shared_calls,
             share_success_rate, total_cpu_time, peak_rss, avg_share_wait, total_share_wait,
             validation_count, validation_failures, validation_ms, detach_count,
             first_conflict_at, follower_independent_at, correct, mismatched, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.scenario,
                record.mode,
                record.repeat_index,
                record.workflow_id,
                record.started_at,
                record.finished_at,
                record.makespan,
                record.physical_executions,
                record.logical_calls,
                record.saved_executions,
                record.eligible_calls,
                record.shared_calls,
                record.share_success_rate,
                record.total_cpu_time,
                record.peak_rss,
                record.avg_share_wait,
                record.total_share_wait,
                record.validation_count,
                record.validation_failures,
                record.validation_ms,
                record.detach_count,
                record.first_conflict_at,
                record.follower_independent_at,
                record.correct,
                record.mismatched,
                _dumps(record.payload),
            ),
        )

    def list_benchmark_runs(self, scenario: str | None = None) -> list[dict[str, Any]]:
        if scenario:
            rows = self.db.query(
                "SELECT * FROM benchmark_runs WHERE scenario = ? ORDER BY started_at", (scenario,)
            )
        else:
            rows = self.db.query("SELECT * FROM benchmark_runs ORDER BY started_at")
        result = []
        for row in rows:
            data = dict(row)
            data["payload"] = _loads(data.get("payload"), {})
            result.append(data)
        return result

    def get_benchmark_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM benchmark_runs WHERE run_id = ?", (run_id,))
        if row is None:
            return None
        data = dict(row)
        data["payload"] = _loads(data.get("payload"), {})
        return data

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # crash recovery
    # ------------------------------------------------------------------
    def recover_interrupted_workflows(self) -> list[str]:
        """Mark workflow runs that were interrupted by a runtime crash.

        A workflow that is still in state ``RUNNING`` without an end time
        cannot be alive any more - the process that owned it is gone.  Such
        runs are marked ``INTERRUPTED`` so that they are never mistaken for a
        completed run and the user can start a new one.

        Returns the identifiers of the recovered workflows.
        """

        from ..core.state import ExecutionStatus, LogicalCallStatus, WorkflowStatus

        rows = self.db.query(
            "SELECT workflow_id FROM workflows WHERE status = ? AND end_time IS NULL",
            (str(WorkflowStatus.RUNNING),),
        )
        recovered: list[str] = []
        now = time.time()
        for row in rows:
            workflow_id = row["workflow_id"]
            self.db.write(
                "UPDATE workflows SET status = ?, end_time = ? WHERE workflow_id = ?",
                (str(WorkflowStatus.INTERRUPTED), now, workflow_id),
            )
            self.db.write(
                "UPDATE physical_executions SET execution_status = ?, error = ? "
                "WHERE workflow_id = ? AND execution_status IN (?, ?, ?)",
                (
                    str(ExecutionStatus.INTERRUPTED),
                    "interrupted by a runtime restart",
                    workflow_id,
                    str(ExecutionStatus.CREATED),
                    str(ExecutionStatus.QUEUED),
                    str(ExecutionStatus.RUNNING),
                ),
            )
            self.db.write(
                "UPDATE logical_tool_calls SET status = ?, error = ? "
                "WHERE workflow_id = ? AND status IN (?, ?, ?, ?, ?)",
                (
                    str(LogicalCallStatus.INTERRUPTED),
                    "interrupted by a runtime restart",
                    workflow_id,
                    str(LogicalCallStatus.PENDING),
                    str(LogicalCallStatus.WAITING),
                    str(LogicalCallStatus.SHARED_WAIT),
                    str(LogicalCallStatus.RUNNING_INDEPENDENTLY),
                    str(LogicalCallStatus.DETACHED),
                ),
            )
            recovered.append(workflow_id)
        if recovered:
            self.db.flush()
        return recovered

    # ------------------------------------------------------------------
    # events
    # ------------------------------------------------------------------
    def list_events(self, workflow_id: str | None = None, limit: int = 5000) -> list[dict[str, Any]]:
        if workflow_id:
            rows = self.db.query(
                "SELECT * FROM runtime_events WHERE workflow_id = ? ORDER BY sequence LIMIT ?",
                (workflow_id, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM runtime_events ORDER BY timestamp, sequence LIMIT ?", (limit,)
            )
        result = []
        for row in rows:
            data = dict(row)
            data["metadata"] = _loads(data.get("metadata"), {})
            result.append(data)
        return result

    def count_events(self, workflow_id: str | None = None) -> int:
        if workflow_id:
            return int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM runtime_events WHERE workflow_id = ?", (workflow_id,)
                )
                or 0
            )
        return int(self.db.scalar("SELECT COUNT(*) FROM runtime_events") or 0)
