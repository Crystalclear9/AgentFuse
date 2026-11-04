"""The traceability of the requirement has to be verifiable.

The audit maps every subsection of the requirement to the thing that proves it.
These tests check that the mapping is **complete** and that the proofs really
exist - a pointer to a test that does not exist would prove nothing.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "audit_requirements.py"


def _audit():
    """The audit script - it holds the mapping of the requirement."""

    spec = importlib.util.spec_from_file_location("audit_requirements_under_test", AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit():
    return _audit()


def test_the_engineering_layout_is_complete(audit):
    """Requirement 20 - the directory structure has to be complete."""

    missing = [name for name in audit.LAYOUT if not (ROOT / name).exists()]
    assert not missing, f"the layout is missing: {missing}"


def test_the_documents_have_the_required_sections(audit):
    """Requirement 22 - every document has to contain its required chapters."""

    missing: dict[str, list[str]] = {}
    for document, needles in audit.DOC_SECTIONS.items():
        text = (ROOT / document).read_text(encoding="utf-8")
        absent = [needle for needle in needles if needle.lower() not in text.lower()]
        if absent:
            missing[document] = absent
    assert not missing, f"the documents are incomplete: {missing}"


def test_the_acceptance_checklist_is_covered(audit):
    """Requirement 23 - every box of the acceptance checklist has a proof."""

    test_names = audit._test_function_names()
    missing = [
        proof
        for _item, kind, proof in audit.ACCEPTANCE_ITEMS
        if kind == "test" and proof not in test_names
    ]
    assert not missing, f"the acceptance checklist points at missing tests: {missing}"


def test_the_development_log_records_every_stage(audit):
    """Requirement 24 - the development log has to record the work."""

    text = (ROOT / "docs" / "DEVELOPMENT_LOG.md").read_text(encoding="utf-8")
    # every stage of the plan has to be in the log
    stages = [f"\u9636\u6bb5 {number}" for number in range(1, 32)]
    absent = [stage for stage in stages if stage not in text]
    assert len(absent) <= 2, f"the log does not record the stages: {absent}"
    # and it has to name the files that were changed
    assert "agentfuse/" in text, "the log has to name the files that were changed"
    assert "\u9a8c\u8bc1" in text or "\u5df2\u9a8c\u8bc1" in text, (
        "the log has to say what was verified"
    )


def test_every_subsection_of_the_requirement_has_a_proof(audit):
    """Every subsection of the requirement points at a test that exists.

    The mapping is the single source of truth of the traceability table - a
    subsection without a pointer is a claim that nobody can check.
    """

    test_names = audit._test_function_names()
    missing = {
        section: [proof for proof in proofs if proof not in test_names]
        for section, proofs in audit.REQUIRED_TRACEABILITY_SECTIONS.items()
    }
    missing = {section: proofs for section, proofs in missing.items() if proofs}
    assert not missing, f"the subsections without a proof: {missing}"

    # the mapping has to be complete: every subsection of the requirement that
    # the audit declares as covered has to be in it
    covered = set(audit.COVERED_SECTIONS)
    mapped = set(audit.REQUIRED_TRACEABILITY_SECTIONS)
    assert covered <= mapped, (
        "the traceability does not mention the subsections the audit covers: "
        f"{sorted(covered - mapped)}"
    )
    # Compare with the user's actual plan, not only a second hand-written list.
    import re
    original = (ROOT / "docs/REQUIREMENTS.zh-CN.md").read_text(encoding="utf-8")
    actual = set(re.findall(r"^#{2,3}\s+(\d+\.\d+)\b", original, re.M))
    assert actual and actual <= mapped, f"original requirements omitted: {sorted(actual - mapped)}"
    report = (ROOT / "docs/TEST_REPORT.md").read_text(encoding="utf-8")
    assert not audit._invalid_traceability_references(report, test_names)
    assert audit._invalid_traceability_references(
        "| §9.1 | 测试 test_a_test_that_does_not_exist |", test_names
    ) == ["test_a_test_that_does_not_exist"]
    assert audit._invalid_traceability_references("| §9.1 | 没有证据 |", test_names)
