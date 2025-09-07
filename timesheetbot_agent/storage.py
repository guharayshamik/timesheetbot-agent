# timesheetbot_agent/storage.py
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
import logging
import tempfile
import shutil

logger = logging.getLogger(__name__)

APP_DIRNAME = ".tsbot"  # ~/.tsbot
PROFILE_FILENAME = "profile.json"
SESSION_FILENAME = "session.json"
SETTINGS_FILENAME = "settings.json"
GENERATED_DIRNAME = "generated_timesheets"


# ---------- path helpers ----------

def get_app_dir() -> Path:
    """
    Cross-platform data dir:
      macOS/Linux: ~/.tsbot
      Windows: %USERPROFILE%\\.tsbot
    """
    home = Path.home()
    app_dir = home / APP_DIRNAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_profile_path() -> Path:
    return get_app_dir() / PROFILE_FILENAME


def get_session_path() -> Path:
    return get_app_dir() / SESSION_FILENAME


def get_settings_path() -> Path:
    return get_app_dir() / SETTINGS_FILENAME


def get_generated_dir() -> Path:
    d = get_app_dir() / GENERATED_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- json io (robust) ----------

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"[storage] failed to read {path}: {e}")
        return {}


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """
    Write JSON atomically to avoid corrupting files on crash/kill.
    """
    tmp_dir = Path(tempfile.gettempdir())
    tmp_path = tmp_dir / (path.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # move into place
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(tmp_path), str(path))


# ---------- profile (registration) ----------

def load_profile() -> Dict[str, Any]:
    """
    Returns user profile (registration) fields or {} if not registered yet.
    """
    return _read_json(get_profile_path())


def save_profile(profile: Dict[str, Any]) -> None:
    """
    Persist full registration payload.
    """
    if not isinstance(profile, dict):
        raise TypeError("profile must be a dict")
    _atomic_write_json(get_profile_path(), profile)


def clear_profile() -> None:
    """
    Remove profile (used by de-registration).
    """
    p = get_profile_path()
    if p.exists():
        try:
            p.unlink()
        except Exception as e:
            logger.warning(f"[storage] failed to remove profile: {e}")


# ---------- session (in-progress CLI state) ----------

def load_session() -> Dict[str, Any]:
    """
    Returns ephemeral session state for the CLI workflow, e.g.:
      {
        "mode": "govtech" | "napta",
        "month": "September",
        "leave_details": [
            ["11-August", "13-August", "Annual Leave"],
            ...
        ],
        "recent_leave_month": "August"
      }
    """
    return _read_json(get_session_path())


def save_session(session: Dict[str, Any]) -> None:
    """
    Persist ephemeral session state between CLI turns.
    """
    if not isinstance(session, dict):
        raise TypeError("session must be a dict")
    _atomic_write_json(get_session_path(), session)


def clear_session() -> None:
    """
    Clears the CLI session file.
    """
    p = get_session_path()
    if p.exists():
        try:
            p.unlink()
        except Exception as e:
            logger.warning(f"[storage] failed to remove session: {e}")


# ---------- misc helpers used by engine/cli ----------

def is_registered() -> bool:
    return bool(load_profile())


def get_output_path(month_name: str, filename_stub: str) -> Path:
    """
    Helper to build a file path under ~/.tsbot/generated_timesheets/
    Example:
      get_output_path("August", "August_2025_Timesheet_Shamik_Guha_Ray.xlsx")
    """
    out_dir = get_generated_dir()
    return out_dir / filename_stub


def upsert_session_field(key: str, value: Any) -> Dict[str, Any]:
    """
    Convenience: load session, set field, save, and return updated session.
    """
    s = load_session()
    s[key] = value
    save_session(s)
    return s
