"""Centralized path/config resolution.

In desktop mode: paths point to %APPDATA%/stratscout/ (Win) or ~/Library/Application Support/stratscout/ (Mac).
In legacy/dev mode: paths point to the project-root data/ folder.
In cloud worker mode: paths come from S3/R2 mounts or are constructed per-tenant.

Resolution order:
  1. STRATSCOUT_DATA_DIR env var (explicit override)
  2. APP-detected OS-native location
  3. Fallback: project_root/data
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _project_root() -> Path:
    """Walk up from this file to find the algo-trading-schwab repo root."""
    return Path(__file__).resolve().parents[2]


def _os_app_dir() -> Path:
    """OS-native app data dir, used by the desktop build."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "stratscout"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "stratscout"
    # Linux / other
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "stratscout"


def data_dir() -> Path:
    """Return the root data directory for this process.

    Subdirectories: daily/, 15min/, smallcap/, options/, theta/
    """
    override = os.environ.get("STRATSCOUT_DATA_DIR")
    if override:
        return Path(override)
    if os.environ.get("STRATSCOUT_MODE") == "desktop":
        return _os_app_dir() / "data"
    # Legacy / dev: keep using the project-root data/ folder so existing feathers Just Work.
    return _project_root() / "data"


def daily_dir() -> Path:
    return data_dir() / "daily"


def intraday_dir() -> Path:
    return data_dir() / "15min"


def smallcap_dir() -> Path:
    return data_dir() / "smallcap"


def options_dir() -> Path:
    return data_dir() / "options"


def db_path(name: str) -> Path:
    """Path to a results SQLite DB. Lives alongside data dir."""
    return data_dir().parent / "db" / f"{name}.db" if os.environ.get("STRATSCOUT_MODE") == "desktop" \
        else _project_root() / f"{name}.db"
