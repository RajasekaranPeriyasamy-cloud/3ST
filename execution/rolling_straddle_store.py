"""Persist rolling straddle config, runtime state, and activity log."""

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
    "size_mode": "lots",
    "size_value": 1,
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
    "entry_exit_enabled": True,
    "execution_mode": "auto",
    "exit_on_bar_close_only": True,
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
    "managed_by": None,
    "signal_bar_ts": None,
    "last_action_bar_ts": None,
    "last_exit_bar_ts": None,
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
    "state_underlying": None,
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


def lot_size_for(underlying: str) -> int:
    return int(INDEX_OPTIONS.get(underlying, {}).get("lot_size") or 1)


def order_quantity_from_config(cfg: dict[str, Any]) -> int:
    """Resolve configured order size to exchange quantity (lots × lot_size or raw qty)."""
    underlying = str(cfg.get("underlying") or "NIFTY")
    lot = lot_size_for(underlying)
    mode = str(cfg.get("size_mode") or "lots").lower()
    value = int(cfg.get("size_value") or 0)
    if value <= 0:
        raise ValueError(
            f"Set order size in Rolling Straddle config (size_mode={mode}, size_value>0). "
            f"{underlying} lot size is {lot}."
        )
    if mode == "qty":
        if value % lot != 0:
            raise ValueError(
                f"Quantity {value} must be a multiple of {underlying} lot size ({lot}); "
                f"e.g. {lot}, {lot * 2}, {lot * 3}"
            )
        return value
    return value * lot


def validate_order_size(cfg: dict[str, Any]) -> None:
    order_quantity_from_config(cfg)


def apply_underlying_defaults(cfg: dict[str, Any], *, previous_underlying: str | None = None) -> dict[str, Any]:
    """MCX: NRML + locked market hours 09:00–23:30. Entry/force exit user-editable."""
    u = str(cfg.get("underlying") or "NIFTY").upper()
    cfg["underlying"] = u
    meta = INDEX_OPTIONS.get(u, {})
    exch = str(meta.get("exchange") or "").upper()
    prev = (previous_underlying or "").upper() or None
    switched = prev is not None and prev != u

    if exch == "MCX":
        cfg["product"] = "NRML"
        lock_mcx_market_session(cfg)
        if switched:
            session = meta.get("session") or MCX_SESSION
            cfg["force_exit"] = session.get("force_exit", MCX_SESSION["force_exit"])
            cfg["entry_start"] = session.get(
                "entry_start", MCX_SESSION.get("entry_start", "09:20")
            )
    elif switched and str(INDEX_OPTIONS.get(prev or "", {}).get("exchange") or "").upper() == "MCX":
        cfg["product"] = "MIS"
        cfg["session_start"] = DEFAULT_SESSION["session_start"]
        cfg["session_end"] = DEFAULT_SESSION["session_end"]
        cfg["force_exit"] = DEFAULT_SESSION["force_exit"]
        cfg["entry_start"] = "09:20"
    return cfg


def get_config() -> dict[str, Any]:
    raw = _read_json(CONFIG_FILE, DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        raw = {}
    merged = {**DEFAULT_CONFIG, **raw}
    return lock_mcx_market_session(merged)


def clear_spot_state_for_underlying(underlying: str, *, reason: str, old_underlying: str | None = None) -> None:
    """Drop cached spot/ATM when the configured underlying changes."""
    from execution.rolling_straddle import _time_reached

    cfg = get_config()
    entry_start = str(cfg.get("entry_start") or "09:20")
    state = get_state()
    ce = dict(state.get("ce") or {})
    pe = dict(state.get("pe") or {})
    for leg in (ce, pe):
        leg["signal_strike"] = None
    patch: dict[str, Any] = {
        "state_underlying": underlying,
        "last_spot": None,
        "current_atm": None,
        "prev_atm": None,
        "last_roll_direction": None,
        "last_signal": None,
        "last_signal_at": None,
        "ce": ce,
        "pe": pe,
    }
    if _time_reached(entry_start):
        # Entry window already open — do not re-gate ticks after mid-day underlying switch.
        patch["morning_bar_seen"] = True
        patch["morning_bar_at"] = state.get("morning_bar_at") or datetime.now().isoformat(timespec="seconds")
    else:
        patch["morning_bar_seen"] = False
        patch["morning_bar_at"] = None
    save_state(patch)
    append_log(
        "underlying_reset",
        reason,
        {"underlying": underlying, "old_underlying": old_underlying},
    )


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    from options.chain import resolve_expiry

    current = get_config()
    old_underlying = str(current.get("underlying") or "NIFTY")
    for k, v in patch.items():
        if v is not None:
            current[k] = v
    current = apply_underlying_defaults(current, previous_underlying=old_underlying)
    current = lock_mcx_market_session(current)
    underlying = str(current.get("underlying") or "NIFTY")
    if underlying != old_underlying:
        clear_spot_state_for_underlying(
            underlying,
            reason=f"{old_underlying} -> {underlying}",
            old_underlying=old_underlying,
        )
    # Resolve on every save, not just expiry/underlying patches: an expired contract must
    # never survive an unrelated settings save. This is a no-op for any tradeable expiry —
    # resolve_expiry() returns a listed, unexpired date unchanged, so a deliberately chosen
    # far month is preserved. Matches premium_book_store.save_config().
    expiry_after = str(current.get("expiry") or "")
    resolved = resolve_expiry(underlying, expiry_after or None)
    if resolved:
        if resolved != expiry_after:
            append_log(
                "expiry_corrected",
                f"{expiry_after or '(empty)'} -> {resolved}",
                {"underlying": underlying},
            )
        current["expiry"] = resolved
    validate_order_size(current)
    _write_json(CONFIG_FILE, current)
    # Underlying string may already match while cached spot is from another instrument.
    from execution.rolling_straddle import _spot_state_stale

    state = get_state()
    if _spot_state_stale(current, state):
        clear_spot_state_for_underlying(
            underlying,
            reason=f"Spot scale mismatch for {underlying} (cached {state.get('last_spot')})",
            old_underlying=str(state.get("state_underlying") or old_underlying),
        )
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


# Keys that may be explicitly cleared with null in save_state().
_NULLABLE_STATE_KEYS = frozenset({
    "last_signal",
    "last_signal_at",
    "last_tick_at",
    "last_spot",
    "current_atm",
    "prev_atm",
    "last_roll_direction",
    "morning_bar_at",
    "state_underlying",
})


def save_state(patch: dict[str, Any]) -> dict[str, Any]:
    current = get_state()
    for k, v in patch.items():
        if k in {"ce", "pe"} and isinstance(v, dict):
            current[k] = {**current[k], **v}
        elif v is not None or k in _NULLABLE_STATE_KEYS:
            current[k] = v
    _write_json(STATE_FILE, current)
    return current


def reset_daily_state_if_needed(today: str) -> dict[str, Any]:
    """Roll daily counters on a new session date without dropping open algo legs."""
    state = get_state()
    session = state.get("session_date")
    if session == today:
        return state
    # Mid-day restart before session_date was stamped — do not wipe legs.
    if session is None:
        state["session_date"] = today
        _write_json(STATE_FILE, state)
        return state
    fresh = deepcopy(DEFAULT_STATE)
    fresh["session_date"] = today
    fresh["runner"] = state.get("runner", "stopped")
    fresh["scheduler_running"] = state.get("scheduler_running", False)
    if state.get("morning_bar_seen"):
        fresh["morning_bar_seen"] = True
        fresh["morning_bar_at"] = state.get("morning_bar_at")
    for leg_key in ("ce", "pe"):
        old_leg = state.get(leg_key) or {}
        if old_leg.get("status") == "open" and str(old_leg.get("managed_by") or "") == "algo":
            fresh[leg_key] = {
                **deepcopy(DEFAULT_LEG),
                **old_leg,
                "entries_today": 0,
                "reentries_used": 0,
            }
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
