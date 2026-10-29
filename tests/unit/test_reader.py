"""Unit tests for the controlled workspace reader."""

from __future__ import annotations

import pytest

from agentfuse.workspace.reader import (
    WorkspaceBoundaryError,
    WorkspaceReader,
    normalize_relative_path,
)


@pytest.fixture()
def reader(tmp_path):
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_bytes(b"print('a')\n")
    (root / "src" / "b.txt").write_bytes(b"text\n")
    (root / "empty").mkdir()
    observed = []
    workspace_reader = WorkspaceReader(root, reporter=lambda *args: observed.append(args))
    workspace_reader.observed_list = observed  # type: ignore[attr-defined]
    return workspace_reader


def test_read_text_reports_a_file_read_dependency(reader):
    assert reader.read_text("src/a.py") == "print('a')\n"
    types = [item[0] for item in reader.observed_list]
    assert "FILE_READ" in types


def test_read_bytes_reports_the_fingerprint(reader):
    data = reader.read_bytes("src/a.py")
    fingerprint = reader.observed_list[-1][2]
    import hashlib

    assert fingerprint == hashlib.sha256(data).hexdigest()


def test_list_dir_reports_a_directory_dependency(reader):
    entries = reader.list_dir("src")
    assert {entry["name"] for entry in entries} == {"a.py", "b.txt"}
    assert reader.observed_list[-1][0] == "DIRECTORY_LIST"


def test_exists_reports_both_states(reader):
    assert reader.exists("src/a.py") is True
    assert reader.observed_list[-1][0] == "PATH_EXISTS"
    assert reader.exists("src/missing.py") is False
    assert reader.observed_list[-1][0] == "PATH_NOT_EXISTS"


def test_stat_reports_a_file_stat_dependency(reader):
    info = reader.stat("src/a.py")
    assert info["size"] == len("print('a')\n")
    assert reader.observed_list[-1][0] == "FILE_STAT"


def test_missing_file_raises_and_reports_the_absence(reader):
    with pytest.raises(FileNotFoundError):
        reader.read_bytes("src/missing.py")
    assert reader.observed_list[-1][0] == "PATH_NOT_EXISTS"


def test_the_same_dependency_is_only_reported_once(reader):
    reader.read_text("src/a.py")
    reader.read_text("src/a.py")
    assert len([item for item in reader.observed_list if item[1] == "src/a.py"]) == 1


@pytest.mark.parametrize("path", ["../secret.txt", "src/../../secret.txt", "/etc/passwd"])
def test_paths_escaping_the_workspace_are_rejected(reader, path):
    with pytest.raises(WorkspaceBoundaryError):
        reader.read_bytes(path)


def test_symlinks_that_leave_the_workspace_are_rejected(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret\n")
    try:
        (root / "link.txt").symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - no symlink support
        pytest.skip("symlinks are not supported on this platform")
    reader = WorkspaceReader(root)
    with pytest.raises(WorkspaceBoundaryError):
        reader.read_bytes("link.txt")


def test_path_normalisation():
    assert normalize_relative_path("src\\a.py") == "src/a.py"
    assert normalize_relative_path("./src/a.py") == "src/a.py"
    assert normalize_relative_path("") == "."
    with pytest.raises(WorkspaceBoundaryError):
        normalize_relative_path("../x")
