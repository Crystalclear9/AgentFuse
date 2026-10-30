"""Unit tests: the dependency validator against a workspace snapshot."""

from __future__ import annotations

import pytest

from agentfuse.core.state import ValidationStatus
from agentfuse.sharing.dependency import DependencyRecord
from agentfuse.sharing.validator import DependencyValidator


class _FakeSnapshots:
    """A minimal snapshot store that only knows one tree."""

    def __init__(self, tree):
        self._tree = tree

    def get(self, snapshot_id: str):
        class _Info:
            def __init__(self, path):
                self.tree_path = path

        return _Info(self._tree)


def _record(relative_path: str, fingerprint: str = "fp") -> DependencyRecord:
    return DependencyRecord(
        execution_id="exec-1",
        sequence_number=1,
        dependency_type="FILE_READ",
        relative_path=relative_path,
        fingerprint=fingerprint,
        observed_time=1.0,
    )


@pytest.fixture()
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('x')\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("secret\n", encoding="utf-8")
    return tmp_path


def test_a_relative_path_is_resolved_inside_the_snapshot(tree):
    validator = DependencyValidator(_FakeSnapshots(tree))
    fingerprint, reason = validator.fingerprint_for("snap", _record("src/main.py"))
    assert reason is None
    assert fingerprint


def test_a_path_with_dot_segments_is_normalised(tree):
    validator = DependencyValidator(_FakeSnapshots(tree))
    fingerprint, reason = validator.fingerprint_for("snap", _record("./src/main.py"))
    assert reason is None
    assert fingerprint


def test_an_absolute_path_is_refused(tree):
    """The validator must never read a file outside of the snapshot."""

    validator = DependencyValidator(_FakeSnapshots(tree))
    fingerprint, reason = validator.fingerprint_for("snap", _record("/etc/passwd"))
    assert fingerprint is None
    assert reason and "absolute" in reason.lower()


def test_a_parent_path_is_refused(tree):
    validator = DependencyValidator(_FakeSnapshots(tree))
    fingerprint, reason = validator.fingerprint_for("snap", _record("../outside.txt"))
    assert fingerprint is None
    assert reason and "escape" in reason.lower()


def test_a_windows_drive_path_is_refused(tree):
    validator = DependencyValidator(_FakeSnapshots(tree))
    fingerprint, reason = validator.fingerprint_for("snap", _record("C:/Windows/win.ini"))
    assert fingerprint is None
    assert reason and "absolute" in reason.lower()


def test_an_unknown_snapshot_is_unknown(tree):
    validator = DependencyValidator(_FakeSnapshots(tree))
    fingerprint, reason = validator.fingerprint_for("snap", _record("src/main.py"))
    assert fingerprint is not None

    class _Empty:
        def get(self, snapshot_id: str):
            raise KeyError(snapshot_id)

    validator = DependencyValidator(_Empty())
    fingerprint, reason = validator.fingerprint_for("missing", _record("src/main.py"))
    assert fingerprint is None
    assert reason and "unknown snapshot" in reason


def test_a_missing_file_is_unknown(tree):
    validator = DependencyValidator(_FakeSnapshots(tree))
    fingerprint, reason = validator.fingerprint_for("snap", _record("src/missing.py"))
    assert fingerprint is None
    assert reason and "does not exist" in reason


def test_the_prefix_validation_stops_at_the_first_mismatch(tree):
    validator = DependencyValidator(_FakeSnapshots(tree))
    # the fingerprint of the real file - the record has to describe the state
    # that the leader really observed
    fingerprint, reason = validator.fingerprint_for("snap", _record("src/main.py"))
    assert fingerprint is not None and reason is None
    good = _record("src/main.py", fingerprint)
    good.sequence_number = 1
    bad = _record("src/missing.py")
    bad.sequence_number = 2
    status, reason, matched = validator.validate_prefix("snap", [good, bad])
    assert status is ValidationStatus.UNKNOWN
    assert matched == 1
    assert reason


def test_the_prefix_validation_reports_a_mismatch(tree):
    validator = DependencyValidator(_FakeSnapshots(tree))
    wrong = _record("src/main.py", "not-the-real-fingerprint")
    status, reason, matched = validator.validate_prefix("snap", [wrong])
    assert status is ValidationStatus.MISMATCH
    assert matched == 0
    assert reason and "differs" in reason
