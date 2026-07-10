"""Persist Wave strategy config, runtime state, and log."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from settings import data_dir

CONFIG_FILE = data_dir() / "wave_config.json"
STATE_FILE = data_dir() / "wave_state.json"
LOG_FILE = data_dir() / "wave_log.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "symbol_name": "NIFTY25SEPFUT",
    "exchange": "NFO",
    "buy_gap": 25,
    "sell_gap": 25,
    "buy_quantity": 75,
    "sell_quantity": 75,
    "lot_size": 75,
    "cool_off_time": 10,
    "product_type": "NRML",
    "order_type": "LIMIT",
    "variety": "REGULAR",
    "tag": "WaveScraper",
    "min_nifty_delta": -100,
    "max_nifty_delta": 100,
    "min_bank_nifty_delta": -100,
    "max_bank_nifty_delta": 100,
    "interest_rate": 10.0,
    "todays_volatility": 20.0,
    "delta_calculation_days": 10,
    "margin_spread": 100.0,
    "margin_single_pe_ce": 100.0,
    "margin_both_pe_ce": 100.0,
    "check_interval_sec": 60,
    "auto_start_on_boot": False,
}

DEFAULT_STATE: dict[str, Any] = {
    "runner": "stopped",
    "initialized": False,
    "last_check_at": None,
    "last_spot": None,
    "active_orders": 0,
    "waves_completed": 0,
    "last_error": None,
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path, default):
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return deepcopy(default)


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_config() -> dict[str, Any]:
    raw = _read_json(CONFIG_FILE, DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        raw = {}
    return {**DEFAULT_CONFIG, **raw}


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
    return {**DEFAULT_STATE, **raw}


def save_state(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_state()
    current.update({k: v for k, v in patch.items() if v is not None or k in DEFAULT_STATE})
    _write_json(STATE_FILE, current)
    return current


def append_log(event: str, detail: str = "", extra: dict[str, Any] | None = None) -> None:
    rows = _read_json(LOG_FILE, [])
    if not isinstance(rows, list):
        rows = []
    rows.append({"at": _now(), "event": event, "detail": detail, **(extra or {})})
    if len(rows) > 500:
        rows = rows[-500:]
    _write_json(LOG_FILE, rows)


def get_log(limit: int = 100) -> list[dict[str, Any]]:
    rows = _read_json(LOG_FILE, [])
    if not isinstance(rows, list):
        return []
    return rows[-limit:][::-1]
