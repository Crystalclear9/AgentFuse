"""Unit tests for the workspace snapshots."""

from __future__ import annotations

import pytest

from agentfuse.workspace.snapshots import SnapshotError, SnapshotStore


@pytest.fixture()
def store(tmp_path):
    return SnapshotStore(
        tmp_path / "workspaces",
        max_files=100,
        max_total_bytes=1_000_000,
        exclude=(".git", "__pycache__", "*.pyc"),
    )


@pytest.fixture()
def source(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("a = 1\n", encoding="utf-8")
    (root / "src" / "b.py").write_text("b = 2\n", encoding="utf-8")
    (root / "README.md").write_text("readme\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("secret\n", encoding="utf-8")
    return root


def test_snapshot_contains_the_files(store, source):
    info = store.create_workspace(source)
    assert set(info.manifest) == {"src/a.py", "src/b.py", "README.md"}
    assert ".git/config" not in info.manifest


def test_snapshots_are_immutable(store, source):
    info = store.create_workspace(source)
    target = info.tree_path / "src" / "a.py"
    target.write_text("changed = True\n", encoding="utf-8")
    # the content changed on disk, therefore the manifest no longer matches
    assert store.verify_integrity(info.snapshot_id) is False


def test_identical_content_produces_identical_manifests(store, source):
    first = store.create_workspace(source)
    second = store.create_workspace(source, workspace_id="other")
    assert first.manifest_hash == second.manifest_hash


def test_different_content_produces_different_manifests(store, source, tmp_path):
    first = store.create_workspace(source)
    other = tmp_path / "other"
    other.mkdir()
    (other / "README.md").write_text("different\n", encoding="utf-8")
    second = store.create_workspace(other)
    assert first.manifest_hash != second.manifest_hash


def test_fork_creates_an_independent_branch(store, source):
    base = store.create_workspace(source)
    fork = store.fork_snapshot(base.snapshot_id)
    assert fork.parent_snapshot_id == base.snapshot_id
    (fork.tree_path / "src" / "a.py").write_text("branch = True\n", encoding="utf-8")
    assert (base.tree_path / "src" / "a.py").read_text(encoding="utf-8") == "a = 1\n"


def test_compare_snapshots_reports_the_differences(store, source, tmp_path):
    base = store.create_workspace(source)
    other = tmp_path / "other"
    other.mkdir()
    (other / "README.md").write_text("readme\n", encoding="utf-8")
    (other / "new.txt").write_text("new\n", encoding="utf-8")
    changed = store.create_workspace(other)
    comparison = store.compare_snapshots(base.snapshot_id, changed.snapshot_id)
    assert "new.txt" in comparison["added"]
    assert comparison["identical"] is False


def test_compare_snapshots_reports_removed_and_changed_files(store, source, tmp_path):
    """§17.6 - a deleted file and a changed file are both recorded."""

    base = store.create_workspace(source)
    other = tmp_path / "other"
    other.mkdir()
    (other / "README.md").write_text("readme\n", encoding="utf-8")
    (other / "src").mkdir()
    # src/a.py was deleted, src/b.py was changed
    (other / "src" / "b.py").write_text("b = 3\n", encoding="utf-8")
    changed = store.create_workspace(other)

    comparison = store.compare_snapshots(base.snapshot_id, changed.snapshot_id)
    assert comparison["removed"] == ["src/a.py"]
    assert "src/b.py" in comparison["modified"]
    assert comparison["identical"] is False


def test_deleting_a_snapshot_removes_the_tree(store, source):
    info = store.create_workspace(source)
    store.delete_snapshot(info.snapshot_id)
    assert not info.path.exists()


def test_the_file_limit_is_enforced(tmp_path, source):
    store = SnapshotStore(tmp_path / "ws", max_files=1, max_total_bytes=10_000_000)
    with pytest.raises(SnapshotError):
        store.create_workspace(source)


def test_the_size_limit_is_enforced(tmp_path, source):
    store = SnapshotStore(tmp_path / "ws", max_files=100, max_total_bytes=10)
    with pytest.raises(SnapshotError):
        store.create_workspace(source)


def test_materialise_produces_a_writable_copy(store, source, tmp_path):
    info = store.create_workspace(source)
    working = store.materialise(info.snapshot_id, tmp_path / "working")
    (working / "src" / "a.py").write_text("edited = True\n", encoding="utf-8")
    assert (info.tree_path / "src" / "a.py").read_text(encoding="utf-8") == "a = 1\n"
