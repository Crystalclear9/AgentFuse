"""The audit has to say how strong it really is.

A requirement audit that mixes "the runtime does the right thing" with "the
name exists in the source" and does not tell them apart looks stronger than
it is.  The report therefore names the kind of every check and prints the
split - and this test makes sure that the split is real.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "scripts" / "audit_requirements.py"


def _report() -> dict:
    """Run the audit and return its machine readable report."""

    process = subprocess.run(
        [sys.executable, str(AUDIT), "--json"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    assert process.returncode == 0, process.stdout[-2000:] + process.stderr[-2000:]
    return json.loads(process.stdout)


def test_the_report_names_the_kind_of_every_check():
    """Every check has to say whether it runs the runtime or reads the source."""

    report = _report()
    assert report["checks"], "the audit has to produce checks"
    for check in report["checks"]:
        assert check.get("kind") in ("behavioural", "structural"), (
            f"the check {check.get('clause')} has no kind: {check}"
        )


def test_the_report_prints_the_split():
    """The split has to be in the report - and it has to add up."""

    report = _report()
    behavioural = report["behavioural"]
    structural = report["structural"]
    assert behavioural > 0, "the audit has to run the runtime at least once"
    assert structural > 0, "the audit also checks the structure of the code"
    assert behavioural + structural == report["total"], (
        "the split has to cover every check"
    )


def test_the_behavioural_checks_are_the_ones_that_run_the_runtime():
    """The behavioural section really executes the scenarios."""

    report = _report()
    behavioural = [
        check for check in report["checks"] if check["kind"] == "behavioural"
    ]
    assert len(behavioural) == report["behavioural"]
    # the behavioural checks are the ones of the runtime clauses
    sections = {check["section"] for check in behavioural}
    assert sections & {"8.7", "9.4", "10.1", "12.4", "16", "17.2"}, (
        f"the behavioural checks do not cover the runtime clauses: {sections}"
    )


def test_a_text_match_is_not_counted_as_behaviour():
    """A check that only reads the source is a **structural** check.

    The audit runs the runtime in the behavioural section - but a few of the
    checks there only look at the text of the test files.  They have to say
    so, otherwise the report claims more than it proves.
    """

    report = _report()
    text_checks = [
        check
        for check in report["checks"]
        if "由自动化测试执行" in check["clause"]
    ]
    assert text_checks, "the audit has to check that the scenarios are tested"
    for check in text_checks:
        assert check["kind"] == "structural", (
            f"{check['clause']} only reads the source - it is not a behaviour"
        )


def test_the_ci_workflow_is_checked_for_its_content():
    """A CI file that exists but does not run the tests is no proof.

    The requirement names the CI workflow as an implemented P2 clause - the
    audit therefore has to look at what the file does, not only whether it is
    there.
    """

    report = _report()
    content_checks = [
        check
        for check in report["checks"]
        if "CI 工作流" in check["clause"] and "内容" in check["clause"]
    ]
    assert content_checks, "the audit has to check the content of the CI workflow"
    for check in content_checks:
        assert check["ok"], (
            f"the CI workflow does not do what it promises: {check['detail']}"
        )
