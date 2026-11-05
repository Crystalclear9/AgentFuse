"""Remote snapshots must be unambiguous and validated before materialization."""
import os

import pytest

from agentfuse.runtime import remote


@pytest.mark.parametrize("name", ["./a", "a//b", "a/./b", "a/", ".", "", "a/../b"])
def test_noncanonical_snapshot_paths_are_rejected_before_writes(tmp_path, name):
    with pytest.raises(ValueError):
        remote.unpack_tree({"files": {"valid": "eA==", name: "eQ=="}, "directories": []}, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("tree", [
    {"files": {"valid": "eA==", "bad": "!"}, "directories": []},
    {"files": {"a": "eA==", "a/b": "eQ=="}, "directories": []},
    {"files": {"a": "eA=="}, "directories": ["a"]},
    {"files": {"a": "eA=="}, "directories": ["a/b"]},
    {"files": {}, "directories": ["a", "a"]},
])
def test_invalid_snapshot_is_rejected_without_partial_materialization(tmp_path, tree):
    with pytest.raises(ValueError):
        remote.unpack_tree(tree, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_snapshot_sender_rejects_missing_root(tmp_path):
    with pytest.raises((ValueError, FileNotFoundError)):
        remote.pack_tree(str(tmp_path / "missing"))


def test_snapshot_sender_enforces_directory_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(remote, "MAX_FILES", 2)
    for name in ("a", "b", "c"):
        (tmp_path / name).mkdir()
    with pytest.raises(ValueError, match="limit"):
        remote.pack_tree(str(tmp_path))


def test_snapshot_roundtrip_preserves_files_and_empty_directories(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    (source / "nested" / "empty").mkdir(parents=True)
    (source / "nested" / "data").write_bytes(b"\x00\xff\n")
    target.mkdir()
    tree = remote.pack_tree(str(source))
    remote.unpack_tree(tree, target)
    assert remote.pack_tree(str(target)) == tree


@pytest.mark.skipif(os.name != "nt", reason="Windows path alias semantics")
@pytest.mark.parametrize("name", ["a.", "a ", "NUL.txt", "dir/COM1", "a?b", "a|b"])
def test_windows_snapshot_aliases_are_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        remote.unpack_tree({"files": {"valid": "eA==", name: "eQ=="}, "directories": []}, tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="Windows path alias semantics")
def test_windows_snapshot_case_collisions_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        remote.unpack_tree({"files": {"a": "eA==", "A": "eQ=="}, "directories": []}, tmp_path)
    assert list(tmp_path.iterdir()) == []
