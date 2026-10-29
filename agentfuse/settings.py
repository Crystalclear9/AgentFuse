"""Runtime settings and on-disk layout for AgentFuse.

Everything AgentFuse produces at runtime (database, workspace snapshots, tool
results, benchmark reports, logs) lives underneath a single *home* directory so
that a deployment is self contained and easy to remove.

Resolution order for the home directory:

1. explicit ``home`` argument,
2. ``AGENTFUSE_HOME`` environment variable,
3. ``<repository root>/.agentfuse`` next to the installed package.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "default.toml"


def _repo_root() -> Path:
    """Return the repository root.

    When AgentFuse is installed as a wheel the package lives in site-packages,
    in that case we fall back to the current working directory so that the home
    directory still stays next to the project the user is working in.
    """

    if (REPO_ROOT / "pyproject.toml").exists():
        return REPO_ROOT
    return Path.cwd()


@dataclass(frozen=True)
class RuntimeSettings:
    """Immutable runtime configuration."""

    home: Path
    db_path: Path
    workspaces_dir: Path
    results_dir: Path
    reports_dir: Path
    logs_dir: Path
    screenshots_dir: Path
    max_workers: int
    max_memory_budget_mb: int
    max_rss_soft_limit_mb: int
    default_timeout_seconds: int
    resource_sample_interval_ms: int
    share_coalesce_window_ms: int
    snapshot_max_files: int
    snapshot_max_total_bytes: int
    snapshot_exclude: tuple[str, ...]
    api_host: str
    api_port: int
    api_log_level: str
    config_path: Path | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # derived directories
    # ------------------------------------------------------------------
    def ensure_directories(self) -> "RuntimeSettings":
        for directory in (
            self.home,
            self.db_path.parent,
            self.workspaces_dir,
            self.results_dir,
            self.reports_dir,
            self.logs_dir,
            self.screenshots_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def with_overrides(self, **kwargs: Any) -> "RuntimeSettings":
        return replace(self, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "home": str(self.home),
            "db_path": str(self.db_path),
            "max_workers": self.max_workers,
            "max_memory_budget_mb": self.max_memory_budget_mb,
            "max_rss_soft_limit_mb": self.max_rss_soft_limit_mb,
            "default_timeout_seconds": self.default_timeout_seconds,
            "resource_sample_interval_ms": self.resource_sample_interval_ms,
            "share_coalesce_window_ms": self.share_coalesce_window_ms,
            "snapshot_max_files": self.snapshot_max_files,
            "snapshot_max_total_bytes": self.snapshot_max_total_bytes,
            "snapshot_exclude": list(self.snapshot_exclude),
            "api_host": self.api_host,
            "api_port": self.api_port,
        }


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_settings(
    config_path: str | os.PathLike[str] | None = None,
    home: str | os.PathLike[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> RuntimeSettings:
    """Build :class:`RuntimeSettings` from the TOML config plus env/CLI overrides."""

    path = Path(config_path) if config_path else None
    if path is None:
        env_config = os.environ.get("AGENTFUSE_CONFIG")
        candidate = Path(env_config) if env_config else DEFAULT_CONFIG_PATH
        path = candidate if candidate.exists() else None

    raw: dict[str, Any] = {}
    if path is not None:
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    raw = _deep_merge(raw, overrides or {})

    runtime = raw.get("runtime", {})
    workspace = raw.get("workspace", {})
    api = raw.get("api", {})
    database = raw.get("database", {})

    if home is not None:
        home_path = Path(home).expanduser().resolve()
    elif os.environ.get("AGENTFUSE_HOME"):
        home_path = Path(os.environ["AGENTFUSE_HOME"]).expanduser().resolve()
    else:
        home_path = (_repo_root() / ".agentfuse").resolve()

    db_filename = Path(database.get("filename", "db/agentfuse.sqlite3"))
    db_path = db_filename if db_filename.is_absolute() else home_path / db_filename

    settings = RuntimeSettings(
        home=home_path,
        db_path=db_path,
        workspaces_dir=home_path / "workspaces",
        results_dir=home_path / "results",
        reports_dir=home_path / "reports",
        logs_dir=home_path / "logs",
        screenshots_dir=home_path / "screenshots",
        max_workers=int(runtime.get("max_workers", 2)),
        max_memory_budget_mb=int(runtime.get("max_memory_budget_mb", 1024)),
        max_rss_soft_limit_mb=int(runtime.get("max_rss_soft_limit_mb", 0)),
        default_timeout_seconds=int(runtime.get("default_timeout_seconds", 60)),
        resource_sample_interval_ms=int(runtime.get("resource_sample_interval_ms", 50)),
        share_coalesce_window_ms=int(runtime.get("share_coalesce_window_ms", 0)),
        snapshot_max_files=int(workspace.get("max_files", 5000)),
        snapshot_max_total_bytes=int(workspace.get("max_total_bytes", 64 * 1024 * 1024)),
        snapshot_exclude=tuple(workspace.get("exclude", ())),
        api_host=str(api.get("host", "127.0.0.1")),
        api_port=int(api.get("port", 8765)),
        api_log_level=str(api.get("log_level", "info")),
        config_path=path,
        raw=raw,
    )
    return settings
