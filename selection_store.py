"""Persist user instrument / timeframe / spread selection for UI pages."""

from __future__ import annotations

import json
from typing import Any

from config import DEFAULT_ADX, DEFAULT_RISK, DEFAULT_SESSION, DEFAULT_ST, DEFAULT_ST_METHOD
from settings import data_dir

SELECTION_FILE = data_dir() / "selection.json"

_DEFAULT: dict[str, Any] = {
    "instrument_token": None,
    "exchange": None,
    "tradingsymbol": None,
    "name": None,
    "segment": "equity",
    "lot_size": 0,
    "timeframe": "15min",
    "product": "underlying",
    "spread": None,
    "st_method": DEFAULT_ST_METHOD,
    "system_mode": "Intraday",
    "session_start": DEFAULT_SESSION["session_start"],
    "session_end": DEFAULT_SESSION["session_end"],
    "force_exit": DEFAULT_SESSION["force_exit"],
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
    "product_type": "MIS",
}


def get_selection() -> dict[str, Any]:
    if not SELECTION_FILE.exists():
        return dict(_DEFAULT)
    try:
        data = json.loads(SELECTION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return dict(_DEFAULT)
    merged = dict(_DEFAULT)
    merged.update(data)
    return merged


def save_selection(payload: dict[str, Any]) -> dict[str, Any]:
    current = get_selection()
    current.update({k: v for k, v in payload.items() if v is not None})
    SELECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SELECTION_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return current


def clear_selection() -> dict[str, Any]:
    if SELECTION_FILE.exists():
        SELECTION_FILE.unlink()
    return dict(_DEFAULT)
