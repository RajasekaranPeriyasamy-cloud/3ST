"""Persist Survivor strategy config, runtime state, and log."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from config import INDEX_OPTIONS
from settings import data_dir

CONFIG_FILE = data_dir() / "survivor_config.json"
STATE_FILE = data_dir() / "survivor_state.json"
LOG_FILE = data_dir() / "survivor_log.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "underlying": "NIFTY",
    "expiry": "",
    "symbol_initials": "",
    "index_symbol": "NSE:NIFTY 50",
    "pe_gap": 20,
    "ce_gap": 20,
    "pe_quantity": 65,
    "ce_quantity": 65,
    "pe_symbol_gap": 200,
    "ce_symbol_gap": 200,
    "min_price_to_sell": 15,
    "sell_multiplier_threshold": 5,
    "pe_reset_gap": 30,
    "ce_reset_gap": 30,
    "pe_start_point": 0,
    "ce_start_point": 0,
    "exchange": "NFO",
    "order_type": "MARKET",
    "product_type": "NRML",
    "tag": "Survivor",
    "tick_interval_sec": 15,
    "auto_start_on_boot": False,
}

DEFAULT_STATE: dict[str, Any] = {
    "runner": "stopped",
    "initialized": False,
    "last_tick_at": None,
    "last_spot": None,
    "nifty_pe_last_value": None,
    "nifty_ce_last_value": None,
    "pe_reset_gap_flag": 0,
    "ce_reset_gap_flag": 0,
    "trades_today": 0,
    "last_trade_at": None,
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


def lot_size_for(underlying: str) -> int:
    return int(INDEX_OPTIONS.get(underlying, {}).get("lot_size") or 1)


def validate_quantities(cfg: dict[str, Any]) -> None:
    underlying = str(cfg.get("underlying") or "NIFTY")
    lot = lot_size_for(underlying)
    for field in ("pe_quantity", "ce_quantity"):
        qty = int(cfg[field])
        if qty <= 0:
            raise ValueError(f"{field} must be > 0")
        if qty % lot != 0:
            raise ValueError(
                f"{field}={qty} must be a multiple of {underlying} lot size ({lot}); "
                f"e.g. {lot}, {lot * 2}, {lot * 3}"
            )


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_config()
    for k, v in patch.items():
        if v is not None:
            current[k] = v
    validate_quantities(current)
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
