"""Persist rolling straddle config, runtime state, and activity log."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from config import DEFAULT_ADX, DEFAULT_RISK, DEFAULT_SESSION, DEFAULT_ST, DEFAULT_ST_METHOD
from settings import data_dir

CONFIG_FILE = data_dir() / "rolling_straddle_config.json"
STATE_FILE = data_dir() / "rolling_straddle_state.json"
LOG_FILE = data_dir() / "rolling_straddle_log.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "underlying": "NIFTY",
    "expiry": "",
    "timeframe": "5min",
    "entry_start": "09:20",
    "session_start": DEFAULT_SESSION["session_start"],
    "session_end": DEFAULT_SESSION["session_end"],
    "force_exit": DEFAULT_SESSION["force_exit"],
    "system_mode": "Intraday",
    "order_type": "MARKET",
    "product": "MIS",
    "tick_interval_sec": 60,
    "trade_mode": "Both",
    "max_reentries_ce": 1,
    "max_reentries_pe": 1,
    "reentry_style": "zone_active",
    "allow_dual_open": True,
    "auto_start_on_boot": False,
    "st_method": DEFAULT_ST_METHOD,
    "atr1": DEFAULT_ST["atr1"],
    "factor1": DEFAULT_ST["factor1"],
    "atr2": DEFAULT_ST["atr2"],
    "factor2": DEFAULT_ST["factor2"],
    "atr3": DEFAULT_ST["atr3"],
    "factor3": DEFAULT_ST["factor3"],
    "st1_enabled": DEFAULT_ST["st1_enabled"],
    "st2_enabled": DEFAULT_ST["st2_enabled"],
    "st3_enabled": DEFAULT_ST["st3_enabled"],
    "adx_enabled": DEFAULT_ADX["enabled"],
    "adx_period": DEFAULT_ADX["period"],
    "adx_threshold": DEFAULT_ADX["threshold"],
    "sl_mode": DEFAULT_RISK["sl_mode"],
    "sl_value": DEFAULT_RISK["sl_value"],
    "tgt_mode": DEFAULT_RISK["tgt_mode"],
    "tgt_value": DEFAULT_RISK["tgt_value"],
    "tsl_mode": DEFAULT_RISK["tsl_mode"],
    "tsl_value": DEFAULT_RISK["tsl_value"],
}

DEFAULT_LEG: dict[str, Any] = {
    "status": "flat",
    "reentries_used": 0,
    "entries_today": 0,
    "tradingsymbol": None,
    "exchange": None,
    "strike": None,
    "entry_price": None,
    "entry_at": None,
    "entry_order_id": None,
    "last_action": None,
    "blocked": False,
}

DEFAULT_STATE: dict[str, Any] = {
    "runner": "stopped",
    "scheduler_running": False,
    "morning_bar_seen": False,
    "morning_bar_at": None,
    "current_atm": None,
    "prev_atm": None,
    "last_roll_direction": None,
    "last_spot": None,
    "last_signal": None,
    "last_signal_at": None,
    "last_tick_at": None,
    "session_date": None,
    "ce": deepcopy(DEFAULT_LEG),
    "pe": deepcopy(DEFAULT_LEG),
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path, default: dict | list) -> dict | list:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(default)


def _write_json(path, data: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_config() -> dict[str, Any]:
    raw = _read_json(CONFIG_FILE, DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        raw = {}
    merged = {**DEFAULT_CONFIG, **raw}
    return merged


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_config()
    for k, v in patch.items():
        if v is not None:
            current[k] = v
    _write_json(CONFIG_FILE, current)
    return current


def get_state() -> dict[str, Any]:
    raw = _read_json(STATE_FILE, DEFAULT_STATE)
    if not isinstance(raw, dict):
        raw = {}
    state = deepcopy(DEFAULT_STATE)
    state.update({k: v for k, v in raw.items() if k not in {"ce", "pe"}})
    state["ce"] = {**DEFAULT_LEG, **(raw.get("ce") or {})}
    state["pe"] = {**DEFAULT_LEG, **(raw.get("pe") or {})}
    return state


def save_state(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_state()
    for k, v in patch.items():
        if k in {"ce", "pe"} and isinstance(v, dict):
            current[k] = {**current[k], **v}
        elif v is not None or k in {"last_signal", "current_atm", "prev_atm"}:
            current[k] = v
    _write_json(STATE_FILE, current)
    return current


def reset_daily_state_if_needed(today: str) -> dict[str, Any]:
    state = get_state()
    if state.get("session_date") == today:
        return state
    fresh = deepcopy(DEFAULT_STATE)
    fresh["session_date"] = today
    fresh["runner"] = state.get("runner", "stopped")
    fresh["scheduler_running"] = state.get("scheduler_running", False)
    _write_json(STATE_FILE, fresh)
    return fresh


def append_log(event: str, detail: str = "", extra: dict[str, Any] | None = None) -> None:
    rows = _read_json(LOG_FILE, [])
    if not isinstance(rows, list):
        rows = []
    rows.append(
        {
            "at": _now(),
            "event": event,
            "detail": detail,
            **(extra or {}),
        }
    )
    if len(rows) > 500:
        rows = rows[-500:]
    _write_json(LOG_FILE, rows)


def get_log(limit: int = 100) -> list[dict[str, Any]]:
    rows = _read_json(LOG_FILE, [])
    if not isinstance(rows, list):
        return []
    return rows[-limit:][::-1]
