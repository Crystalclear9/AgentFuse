"""The cleanup fallback must never change attributes through a hard link."""

import os
from pathlib import Path
import runpy

import pytest


def test_readonly_retry_refuses_to_chmod_a_shared_file(tmp_path, monkeypatch):
    script = Path(__file__).resolve().parents[2] / "scripts" / "clean_intermediates.py"
    retry = runpy.run_path(str(script))["readonly_retry"]
    monkeypatch.setitem(retry.__globals__, "ROOT", tmp_path)
    original = tmp_path / "protected.sqlite3"
    original.write_text("protected", encoding="utf-8")
    alias = tmp_path / "intermediate"
    try:
        os.link(original, alias)
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")

    failure = PermissionError("access denied")
    with pytest.raises(PermissionError, match="access denied"):
        retry(lambda _: pytest.fail("must not retry after chmod"), alias,
              (PermissionError, failure, None))
    assert original.read_text(encoding="utf-8") == "protected"
