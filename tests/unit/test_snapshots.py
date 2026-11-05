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
    # Installing local tools beside the source must not make importing this
    # checkout exceed snapshot limits or copy runtime data recursively.
    from agentfuse.settings import load_settings
    defaults = load_settings(home=source.parent / "home")
    for name in (".tools", ".venv-local", ".venv-wsl", ".agentfuse", "_manual_delete"):
        (source / name).mkdir()
        (source / name / "artifact.txt").write_text("generated", encoding="utf-8")
    default_store = SnapshotStore(source.parent / "default-snapshots", exclude=defaults.snapshot_exclude)
    assert set(default_store.create_workspace(source).manifest) == set(info.manifest)


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


def test_empty_directories_are_copied_compared_and_verified_after_reload(store, source):
    base = store.create_workspace(source)
    (source / "empty" / "nested").mkdir(parents=True)
    snapshot = store.create_workspace(source)
    reloaded = SnapshotStore(store.root)
    assert reloaded.verify_integrity(snapshot.snapshot_id)
    assert (snapshot.tree_path / "empty" / "nested").is_dir()
    assert reloaded.compare_snapshots(base.snapshot_id, snapshot.snapshot_id)["added"] == [
        "empty", "empty/nested",
    ]
    fork = reloaded.fork_snapshot(snapshot.snapshot_id)
    assert reloaded.verify_integrity(fork.snapshot_id)
    (snapshot.tree_path / "empty" / "nested").rmdir()
    assert not reloaded.verify_integrity(snapshot.snapshot_id)
    (base.tree_path / "extra").mkdir()
    assert not reloaded.verify_integrity(base.snapshot_id)


def test_legacy_manifest_remains_usable_but_rejects_new_empty_directories(store, source):
    from agentfuse.workspace.manifest import write_manifest

    info = store.create_workspace(source)
    write_manifest(info.path / "manifest.json", snapshot_id=info.snapshot_id,
                   workspace_id=info.workspace_id, parent_snapshot_id=None,
                   creation_time=info.creation_time, manifest=info.manifest)
    reopened = SnapshotStore(store.root)
    assert reopened.verify_integrity(info.snapshot_id)
    assert reopened.verify_integrity(reopened.fork_snapshot(info.snapshot_id).snapshot_id)
    (info.tree_path / "new-empty").mkdir()
    assert not reopened.verify_integrity(info.snapshot_id)


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
