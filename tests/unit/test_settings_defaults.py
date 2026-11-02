"""The defaults of the code and the configuration file must agree.

The runtime reads ``config/default.toml`` when it is there.  An installation
without that file (a wheel) falls back to the **code** defaults.  If the two
drift apart, the behaviour of an installation differs from the behaviour of
the repository - and nobody notices.

This test compares every value of the configuration file with the value the
code falls back to.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from agentfuse.settings import load_settings

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config" / "default.toml"


def _config() -> dict:
    with CONFIG.open("rb") as handle:
        return tomllib.load(handle)


def test_the_code_defaults_match_the_configuration(tmp_path: Path, monkeypatch):
    """Without a configuration file the runtime has to behave like the repository."""

    import agentfuse.settings as settings_module

    # an installation without a configuration file (a wheel)
    monkeypatch.setattr(
        settings_module, "DEFAULT_CONFIG_PATH", tmp_path / "no-config.toml"
    )
    raw = _config()
    settings = load_settings(home=tmp_path / "home")
    assert settings.config_path is None, "the test must not find a configuration"

    runtime = raw["runtime"]
    workspace = raw["workspace"]
    api = raw["api"]
    database = raw["database"]

    assert settings.max_workers == runtime["max_workers"]
    assert settings.max_memory_budget_mb == runtime["max_memory_budget_mb"]
    assert settings.max_rss_soft_limit_mb == runtime["max_rss_soft_limit_mb"]
    assert settings.scheduling_strategy == runtime["scheduling_strategy"]
    assert settings.result_cache_entries == runtime["result_cache_entries"]
    assert settings.worker_pool_size == runtime["worker_pool_size"]
    assert settings.default_timeout_seconds == runtime["default_timeout_seconds"]
    assert (
        settings.resource_sample_interval_ms == runtime["resource_sample_interval_ms"]
    )
    assert settings.snapshot_max_files == workspace["max_files"]
    assert settings.snapshot_max_total_bytes == workspace["max_total_bytes"]
    assert tuple(settings.snapshot_exclude) == tuple(workspace["exclude"])
    assert settings.api_host == api["host"]
    assert settings.api_port == api["port"]
    assert settings.api_log_level == api["log_level"]
    assert settings.db_path.name == Path(database["filename"]).name


def test_the_configuration_file_is_read(tmp_path: Path):
    """With the configuration file the values of the file win."""

    raw = _config()
    settings = load_settings(home=tmp_path / "home", config_path=CONFIG)
    assert settings.config_path == CONFIG
    assert settings.worker_pool_size == raw["runtime"]["worker_pool_size"]
    assert settings.result_cache_entries == raw["runtime"]["result_cache_entries"]
