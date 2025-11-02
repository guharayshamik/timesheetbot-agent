# timesheetbot_agent/config_loader.py
from dataclasses import dataclass
from pathlib import Path
import re
import json
import yaml
from typing import Optional

# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses for structured config
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DatesCfg:
    months: dict
    range_regex: re.Pattern

@dataclass
class LeaveCfg:
    synonyms: dict
    canonical: set

@dataclass
class OrgCfg:
    finance_cc_email: str

@dataclass
class CliCfg:
    engine_keywords: list[str]
    command_aliases: dict[str, list[str]]

@dataclass
class UiCfg:
    govtech_examples: list[str]

@dataclass
class AppCfg:
    leave: LeaveCfg
    dates: DatesCfg
    org: OrgCfg
    cli: CliCfg
    ui: UiCfg


# Singletons (memoized after first load)
_cfg: Optional[AppCfg] = None
_holidays: Optional[dict[str, str]] = None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _config_dir() -> Path:
    # The config/ folder lives inside the package: timesheetbot_agent/config/
    return Path(__file__).parent / "config"

def _require_keys(data: dict, keys: list[str], root_label: str = "config") -> None:
    missing = [k for k in keys if k not in data]
    if missing:
        raise KeyError(f"Missing keys in {root_label}: {missing}")


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def load_config() -> AppCfg:
    """
    Load and cache the app configuration from config/app_config.yaml.
    """
    global _cfg
    if _cfg:
        return _cfg

    cfg_path = _config_dir() / "app_config.yaml"
    data = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    # Minimal validation to fail fast on bad edits
    _require_keys(data, ["leave_types", "dates", "org", "cli", "ui"], "app_config.yaml")
    _require_keys(data["leave_types"], ["synonyms", "canonical"], "leave_types")
    _require_keys(data["dates"], ["months", "range_separators"], "dates")
    _require_keys(data["org"], ["finance_cc_email"], "org")
    # Ensure CLI section has both engine keywords and aliases
    _require_keys(data["cli"], ["engine_keywords", "command_aliases"], "cli")
    _require_keys(data["ui"], ["govtech_examples"], "ui")

    # Compile range separator regex from tokens in YAML
    seps: list[str] = data["dates"]["range_separators"]
    # Example: separators like ["-", "to", "–"] -> regex "(?:\-|to|–)"
    range_pat = r"(?:%s)" % "|".join(map(re.escape, seps))

    # Build dataclasses
    leave_cfg = LeaveCfg(
        synonyms=data["leave_types"]["synonyms"],
        canonical=set(data["leave_types"]["canonical"]),
    )

    dates_cfg = DatesCfg(
        months=data["dates"]["months"],
        range_regex=re.compile(range_pat, flags=re.I),
    )

    org_cfg = OrgCfg(
        finance_cc_email=data["org"]["finance_cc_email"],
    )

    cli_section = data["cli"]
    cli_cfg = CliCfg(
        engine_keywords=cli_section.get("engine_keywords", []),
        command_aliases=cli_section.get("command_aliases", {}),
    )

    ui_cfg = UiCfg(
        govtech_examples=data["ui"]["govtech_examples"],
    )

    _cfg = AppCfg(
        leave=leave_cfg,
        dates=dates_cfg,
        org=org_cfg,
        cli=cli_cfg,
        ui=ui_cfg,
    )
    return _cfg


def load_sg_holidays() -> dict[str, str]:
    """
    Load and cache Singapore public holidays from config/holidays_sg_2024_2029.json.
    """
    global _holidays
    if _holidays is not None:
        return _holidays

    json_path = _config_dir() / "holidays_sg_2024_2029.json"
    _holidays = json.loads(json_path.read_text(encoding="utf-8"))
    return _holidays
