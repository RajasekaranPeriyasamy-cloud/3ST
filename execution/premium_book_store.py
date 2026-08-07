"""Persist Premium Book config, runtime state, and activity log."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from typing import Any

from config import (
    DEFAULT_ADX,
    DEFAULT_RISK,
    DEFAULT_SESSION,
    DEFAULT_ST,
    DEFAULT_ST_METHOD,
    INDEX_OPTIONS,
    MCX_SESSION,
    lock_mcx_market_session,
)

from settings import data_dir

PREMIUM_BOOK_UNDERLYINGS = ("NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "CRUDEOILM")

CONFIG_FILE = data_dir() / "premium_book_config.json"
STATE_FILE = data_dir() / "premium_book_state.json"
LOG_FILE = data_dir() / "premium_book_log.json"

SELL_STRUCTURES = ("bull_put", "bear_call")
# Legacy short dual-leg templates: manage/exit only — not selectable for new entries.
LEGACY_SHORT_STRUCTURES = frozenset({"short_strangle", "short_straddle"})
BUY_STRUCTURES = ("long_call", "long_put", "bull_call", "bear_put", "long_strangle")
STRUCTURES = SELL_STRUCTURES + BUY_STRUCTURES
DEFAULT_BUY_STRUCTURE = "bull_call"  # defined-risk debit vertical preferred for V1
DEFAULT_SELL_STRUCTURE = "bull_put"

TRADE_BIAS_SELL = "sell_premium"
TRADE_BIAS_BUY = "buy_hold"


def migrate_legacy_sell_structure(structure: str) -> str:
    """Map retired short strangle/straddle config picks to the default credit vertical."""
    s = str(structure or "")
    if s in LEGACY_SHORT_STRUCTURES:
        return DEFAULT_SELL_STRUCTURE
    return s


DEFAULT_CONFIG: dict[str, Any] = {
    "underlying": "NIFTY",
    "expiry": "",
    # sell_premium (default) | buy_hold — Buy & Hold off unless explicitly enabled
    "trade_bias": TRADE_BIAS_SELL,
    "structure": DEFAULT_SELL_STRUCTURE,
    # When true (sell book): pick structure from ST1+ST2 each tick —
    # above → bull_put, below → bear_call, flat/whipsaw → no entry.
    "auto_structure": True,
    "otm_offset": 1,
    "width_steps": 1,
    "timeframe": "5min",
    "entry_start": "09:20",
    "session_start": DEFAULT_SESSION["session_start"],
    "session_end": DEFAULT_SESSION["session_end"],
    "force_exit": DEFAULT_SESSION["force_exit"],
    "system_mode": "Intraday",
    "order_type": "MARKET",
    "product": "MIS",
    "tick_interval_sec": 60,
    # Kept for legacy open short legs (SL convert → credit vertical); unused for verticals.
    "convert_sl_to_spread": True,
    "auto_start_on_boot": False,
    "size_mode": "lots",
    "size_value": 1,
    "st_method": DEFAULT_ST_METHOD,
    "atr1": DEFAULT_ST["atr1"],
    "factor1": DEFAULT_ST["factor1"],
    "atr2": DEFAULT_ST["atr2"],
    "factor2": DEFAULT_ST["factor2"],
    "atr3": DEFAULT_ST["atr3"],
    "factor3": DEFAULT_ST["factor3"],
    "st1_enabled": True,
    "st2_enabled": True,
    "st3_enabled": True,
    # Entry requires ST1 zone + ST1&ST2 same direction; exits remain ST1-only.
    "entry_require_st1_st2": True,
    "adx_enabled": DEFAULT_ADX["enabled"],
    "adx_period": DEFAULT_ADX["period"],
    "adx_threshold": DEFAULT_ADX["threshold"],
    "sl_mode": DEFAULT_RISK["sl_mode"],
    "sl_value": DEFAULT_RISK["sl_value"],
    "tgt_mode": DEFAULT_RISK["tgt_mode"],
    "tgt_value": DEFAULT_RISK["tgt_value"],
    "tsl_mode": "ATR",
    "tsl_value": 1.2,
    "entry_exit_enabled": False,
    # ST1 structure entry/exit: one decision per TF bar (RS-style). ATR/SL/force stay live.
    "exit_on_bar_close_only": True,
}

DEFAULT_LEG: dict[str, Any] = {
    "status": "flat",
    "tradingsymbol": None,
    "exchange": None,
    "strike": None,
    "option_type": None,
    "entry_price": None,
    "entry_at": None,
    "entry_order_id": None,
    "entry_side": None,
    "position_side": None,
    "broker_qty": None,
    "managed_by": None,
    "last_action": None,
    "signal_bar_ts": None,
    "last_action_bar_ts": None,
    "last_exit_bar_ts": None,
    "atr_trail": None,
    "atr_extreme": None,
    "atr_live_ref": None,
    "ltp": None,
    "wing": None,  # hedge leg after SL convert
    "converted_structure": None,
}

DEFAULT_PACKAGE: dict[str, Any] = {
    "status": "flat",
    "structure": None,
    "legs": [],
    "net_credit": None,
    "net_debit": None,
    "max_loss": None,
    "entry_at": None,
    "last_action": None,
    "signal_bar_ts": None,
    "last_action_bar_ts": None,
    "last_exit_bar_ts": None,
    "atr_trail": None,
    "atr_extreme": None,
    "atr_live_ref": None,
}


def is_buy_structure(structure: str) -> bool:
    return str(structure or "") in BUY_STRUCTURES


def is_sell_structure(structure: str) -> bool:
    return str(structure or "") in SELL_STRUCTURES


def trade_bias_for_structure(structure: str) -> str:
    return TRADE_BIAS_BUY if is_buy_structure(structure) else TRADE_BIAS_SELL


def normalize_trade_bias(raw: Any) -> str:
    """Accept trade_bias or legacy book_side values."""
    v = str(raw or "").strip().lower()
    if v in (TRADE_BIAS_BUY, "buy", "buy_and_hold", "long"):
        return TRADE_BIAS_BUY
    if v in (TRADE_BIAS_SELL, "sell", "short"):
        return TRADE_BIAS_SELL
    return TRADE_BIAS_SELL

DEFAULT_STATE: dict[str, Any] = {
    "runner": "stopped",
    "morning_bar_seen": False,
    "morning_bar_at": None,
    "current_atm": None,
    "last_spot": None,
    "last_signal": None,
    "last_signal_at": None,
    "last_tick_at": None,
    # Desk-wide bar gates (RS pattern): one ST1/ST2 action per closed candle.
    "signal_bar_ts": None,
    "last_action_bar_ts": None,
    "last_exit_bar_ts": None,
    "session_date": None,
    "last_error": None,
    "preview": None,
    # Live auto pick (None = sit out). Must be in DEFAULT_STATE so save_state can clear to None.
    "active_structure": None,
    "auto_structure_reason": None,
    "package": deepcopy(DEFAULT_PACKAGE),
    "ce": deepcopy(DEFAULT_LEG),
    "pe": deepcopy(DEFAULT_LEG),
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


def lot_size_for(underlying: str) -> int:
    return int(INDEX_OPTIONS.get(underlying, {}).get("lot_size") or 1)


def order_quantity_from_config(cfg: dict[str, Any]) -> int:
    underlying = str(cfg.get("underlying") or "NIFTY")
    lot = lot_size_for(underlying)
    mode = str(cfg.get("size_mode") or "lots").lower()
    value = int(cfg.get("size_value") or 0)
    if value <= 0:
        raise ValueError(
            f"Set order size (size_mode={mode}, size_value>0). {underlying} lot size is {lot}."
        )
    if mode == "qty":
        if value % lot != 0:
            raise ValueError(
                f"Quantity {value} must be a multiple of {underlying} lot size ({lot})"
            )
        return value
    return value * lot


def apply_underlying_defaults(
    cfg: dict[str, Any],
    *,
    previous_underlying: str | None = None,
) -> dict[str, Any]:
    """MCX crude: NRML + session windows; equity indices keep NSE/BSE cash hours on switch."""
    u = str(cfg.get("underlying") or "NIFTY").upper()
    cfg["underlying"] = u
    meta = INDEX_OPTIONS.get(u, {})
    exch = str(meta.get("exchange") or "").upper()
    prev = (previous_underlying or "").upper() or None
    switched = prev is not None and prev != u

    if exch == "MCX":
        # Kite rejects MIS for MCX commodity options.
        cfg["product"] = str(meta.get("default_product") or "NRML")
        # Market hours are universal / fixed for MCX.
        lock_mcx_market_session(cfg)
        if switched:
            # Only entry / force exit pick up MCX defaults on underlying switch.
            session = meta.get("session") or MCX_SESSION
            cfg["force_exit"] = session.get("force_exit", MCX_SESSION["force_exit"])
            cfg["entry_start"] = session.get(
                "entry_start", MCX_SESSION.get("entry_start", "09:20")
            )
    elif switched and str(INDEX_OPTIONS.get(prev or "", {}).get("exchange") or "").upper() == "MCX":
        # Leaving MCX → restore cash-session defaults (user can still override before save).
        cfg["product"] = "MIS"
        cfg["session_start"] = DEFAULT_SESSION["session_start"]
        cfg["session_end"] = DEFAULT_SESSION["session_end"]
        cfg["force_exit"] = DEFAULT_SESSION["force_exit"]
        cfg["entry_start"] = "09:20"
    return cfg


def validate_config(cfg: dict[str, Any]) -> None:
    underlying = str(cfg.get("underlying") or "")
    if underlying not in PREMIUM_BOOK_UNDERLYINGS:
        raise ValueError(f"underlying must be one of {PREMIUM_BOOK_UNDERLYINGS}")
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")
    structure = migrate_legacy_sell_structure(str(cfg.get("structure") or ""))
    cfg["structure"] = structure
    if structure not in STRUCTURES:
        raise ValueError(f"structure must be one of {STRUCTURES}")
    bias = normalize_trade_bias(
        cfg.get("trade_bias") or cfg.get("book_side") or trade_bias_for_structure(structure)
    )
    if bias not in (TRADE_BIAS_SELL, TRADE_BIAS_BUY):
        raise ValueError("trade_bias must be 'sell_premium' or 'buy_hold'")
    if bias == TRADE_BIAS_BUY and not is_buy_structure(structure):
        raise ValueError(f"structure {structure!r} is not a buy & hold mode")
    if bias == TRADE_BIAS_SELL and not is_sell_structure(structure):
        raise ValueError(f"structure {structure!r} is not a sell-premium mode")
    otm = int(cfg.get("otm_offset") or 0)
    if otm < 0 or otm > 5:
        raise ValueError("otm_offset must be between 0 and 5")
    width = int(cfg.get("width_steps") or 1)
    if width < 1 or width > 5:
        raise ValueError("width_steps must be between 1 and 5")
    if not (cfg.get("st1_enabled") or cfg.get("st2_enabled") or cfg.get("st3_enabled")):
        raise ValueError("At least one SuperTrend must be enabled")
    meta = INDEX_OPTIONS[underlying]
    if str(meta.get("exchange") or "").upper() == "MCX" and str(cfg.get("product") or "").upper() != "NRML":
        raise ValueError("MCX commodity options require product NRML")
    order_quantity_from_config(cfg)


def get_config() -> dict[str, Any]:
    raw = _read_json(CONFIG_FILE, DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        raw = {}
    merged = {**DEFAULT_CONFIG, **raw}
    # Drop retired sell-book keys (ignored if still present on disk).
    merged.pop("sideways_structure", None)
    merged.pop("allow_dual_open", None)
    structure = migrate_legacy_sell_structure(str(merged.get("structure") or DEFAULT_SELL_STRUCTURE))
    merged["structure"] = structure
    # Prefer structure as source of truth; migrate legacy book_side.
    bias = trade_bias_for_structure(structure)
    if "trade_bias" not in raw and "book_side" in raw:
        bias = normalize_trade_bias(raw.get("book_side"))
        if bias == TRADE_BIAS_BUY and not is_buy_structure(structure):
            structure = DEFAULT_BUY_STRUCTURE
            merged["structure"] = structure
        elif bias == TRADE_BIAS_SELL and not is_sell_structure(structure):
            structure = DEFAULT_SELL_STRUCTURE
            merged["structure"] = structure
        bias = trade_bias_for_structure(structure)
    merged["trade_bias"] = bias
    merged["book_side"] = "buy" if bias == TRADE_BIAS_BUY else "sell"  # legacy alias
    return lock_mcx_market_session(merged)


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    from options.chain import resolve_expiry

    current = get_config()
    old_underlying = str(current.get("underlying") or "NIFTY")
    for k, v in patch.items():
        if v is not None:
            current[k] = v

    # Retire sideways / dual-open keys from persisted config.
    current.pop("sideways_structure", None)
    current.pop("allow_dual_open", None)
    if current.get("structure") is not None:
        current["structure"] = migrate_legacy_sell_structure(str(current["structure"]))

    # Align trade_bias ↔ structure when either (or legacy book_side) changes.
    if patch.get("structure") is not None:
        current["structure"] = migrate_legacy_sell_structure(str(current["structure"]))
        current["trade_bias"] = trade_bias_for_structure(str(current["structure"]))
    elif patch.get("trade_bias") is not None or patch.get("book_side") is not None:
        bias = normalize_trade_bias(current.get("trade_bias") or current.get("book_side"))
        current["trade_bias"] = bias
        if bias == TRADE_BIAS_BUY and not is_buy_structure(str(current.get("structure"))):
            current["structure"] = DEFAULT_BUY_STRUCTURE
        elif bias == TRADE_BIAS_SELL and not is_sell_structure(str(current.get("structure"))):
            current["structure"] = DEFAULT_SELL_STRUCTURE

    current = apply_underlying_defaults(current, previous_underlying=old_underlying)
    current = lock_mcx_market_session(current)
    underlying = str(current.get("underlying") or "NIFTY")
    expiry = str(current.get("expiry") or "")
    resolved = resolve_expiry(underlying, expiry or None)
    if resolved:
        current["expiry"] = resolved
    current["trade_bias"] = trade_bias_for_structure(str(current.get("structure") or DEFAULT_SELL_STRUCTURE))
    current["book_side"] = "buy" if current["trade_bias"] == TRADE_BIAS_BUY else "sell"
    validate_config(current)
    _write_json(CONFIG_FILE, current)
    return current


def get_state() -> dict[str, Any]:
    raw = _read_json(STATE_FILE, DEFAULT_STATE)
    if not isinstance(raw, dict):
        raw = {}
    merged = {**DEFAULT_STATE, **raw}
    for key in ("ce", "pe"):
        merged[key] = {**DEFAULT_LEG, **(merged.get(key) or {})}
    merged["package"] = {**DEFAULT_PACKAGE, **(merged.get("package") or {})}
    return merged


def save_state(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_state()
    for k, v in patch.items():
        if k in ("ce", "pe", "package") and isinstance(v, dict):
            base = DEFAULT_LEG if k in ("ce", "pe") else DEFAULT_PACKAGE
            current[k] = {**base, **(current.get(k) or {}), **v}
        elif v is not None or k in DEFAULT_STATE:
            current[k] = v
    _write_json(STATE_FILE, current)
    return current


def reset_daily_state_if_needed(today: str) -> dict[str, Any]:
    state = get_state()
    if state.get("session_date") == today:
        return state
    return save_state(
        {
            "session_date": today,
            "morning_bar_seen": False,
            "morning_bar_at": None,
            "last_error": None,
            "ce": deepcopy(DEFAULT_LEG),
            "pe": deepcopy(DEFAULT_LEG),
            "package": deepcopy(DEFAULT_PACKAGE),
        }
    )


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


def flat_leg() -> dict[str, Any]:
    return deepcopy(DEFAULT_LEG)


def flat_package() -> dict[str, Any]:
    return deepcopy(DEFAULT_PACKAGE)
