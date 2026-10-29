"""Dependency inspection.

Combines the dependency ledger of a physical execution with the validation
records of its share group, so that the UI can show, for every observed
dependency, the leader fingerprint and the follower validation result.
"""

from __future__ import annotations

from typing import Any

from ..core.state import ValidationStatus


def build_dependency_inspection(repository: Any, execution_id: str) -> dict[str, Any]:
    """Return the dependency ledger of an execution with the validation results."""

    execution = repository.get_execution(execution_id)
    if execution is None:
        return {}
    dependencies = repository.list_dependencies(execution_id)

    group_id = execution.get("share_group_id")
    validations = (
        repository.list_validations(group_id) if group_id else []
    )
    # participants are fetched per workflow
    participants = [
        row
        for row in repository.list_participants(execution.get("workflow_id") or "")
        if row["share_group_id"] == group_id
    ]

    by_sequence: dict[int, list[dict[str, Any]]] = {}
    for validation in validations:
        sequence = validation.get("dependency_sequence")
        if sequence is None:
            continue
        by_sequence.setdefault(int(sequence), []).append(validation)

    rows: list[dict[str, Any]] = []
    for dependency in dependencies:
        sequence = int(dependency["sequence_number"])
        checks = by_sequence.get(sequence, [])
        rows.append(
            {
                "sequence_number": sequence,
                "dependency_type": dependency["dependency_type"],
                "relative_path": dependency["relative_path"],
                "leader_fingerprint": dependency["fingerprint"],
                "observed_time": dependency["observed_time"],
                "validations": [
                    {
                        "logical_call_id": check["logical_call_id"],
                        "validation_status": check["validation_status"],
                        "mismatch_reason": check["mismatch_reason"],
                        "validation_time": check["validation_time"],
                        "elapsed_ms": check["elapsed_ms"],
                    }
                    for check in checks
                ],
            }
        )

    return {
        "execution": execution,
        "share_group_id": group_id,
        "dependencies": rows,
        "participants": participants,
        "summary": {
            "dependency_count": len(rows),
            "validation_count": len(validations),
            "validation_failures": len(
                [v for v in validations if v["validation_status"] != ValidationStatus.MATCH]
            ),
        },
    }
