"""Persist Wave strategy config, runtime state, and log."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from config import INDEX_OPTIONS
from settings import data_dir

CONFIG_FILE = data_dir() / "wave_config.json"
STATE_FILE = data_dir() / "wave_state.json"
LOG_FILE = data_dir() / "wave_log.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "symbol_name": "NIFTY26JULFUT",
    "exchange": "NFO",
    "buy_gap": 25,
    "sell_gap": 25,
    "buy_quantity": 65,
    "sell_quantity": 65,
    "lot_size": 65,
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


def lot_size_for_symbol(symbol_name: str) -> int:
    sym = str(symbol_name or "").upper()
    if sym.startswith("BANKNIFTY"):
        return int(INDEX_OPTIONS.get("BANKNIFTY", {}).get("lot_size") or 30)
    if sym.startswith("SENSEX"):
        return int(INDEX_OPTIONS.get("SENSEX", {}).get("lot_size") or 20)
    if sym.startswith("NIFTY"):
        return int(INDEX_OPTIONS.get("NIFTY", {}).get("lot_size") or 65)
    return 1


def validate_quantities(cfg: dict[str, Any]) -> None:
    lot = int(cfg.get("lot_size") or lot_size_for_symbol(cfg.get("symbol_name", "")))
    if lot <= 0:
        lot = lot_size_for_symbol(cfg.get("symbol_name", ""))
    for field in ("buy_quantity", "sell_quantity"):
        qty = int(cfg[field])
        if qty <= 0:
            raise ValueError(f"{field} must be > 0")
        if qty % lot != 0:
            raise ValueError(
                f"{field}={qty} must be a multiple of lot size ({lot}); "
                f"e.g. {lot}, {lot * 2}, {lot * 3}"
            )


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_config()
    for k, v in patch.items():
        if v is not None:
            current[k] = v
    if not current.get("lot_size"):
        current["lot_size"] = lot_size_for_symbol(current.get("symbol_name", ""))
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
