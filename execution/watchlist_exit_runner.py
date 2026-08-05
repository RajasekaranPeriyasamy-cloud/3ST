"""Monitor active watchlist trades — 3ST zone exit, risk exits, force exit."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from backtest_engine import _level, force_exit_due
from execution.positions_view import _fetch_ltp_map, _position_key
from execution.watchlist_close import close_watchlist_trade
from execution.watchlist_runner import _resolve_chart_token, chart_instrument_meta
from kite_client import fetch_historical_by_token, session_status
from strategy_3st import compute_signals
from watchlist_store import list_items, update_item


def _lookback_days(timeframe: str) -> int:
    if timeframe in {"1min", "3min"}:
        return 5
    return 10


def _exit_line_label(item: dict[str, Any], row: pd.Series) -> tuple[float | None, str]:
    st1 = float(row["st1"]) if pd.notna(row.get("st1")) else None
    st2 = float(row["st2"]) if pd.notna(row.get("st2")) else None
    st3 = float(row["st3"]) if pd.notna(row.get("st3")) else None
    if item.get("st1_enabled") and st1 is not None:
        return st1, "ST1"
    if item.get("st2_enabled") and st2 is not None:
        return st2, "ST2"
    if item.get("st3_enabled") and st3 is not None:
        return st3, "ST3"
    return st1, "ST1"


def _zone_exit_triggered(item: dict[str, Any], row: pd.Series, direction: str | None) -> bool:
    """Exit when close crosses opposite side of ST1 (long: below, short: above)."""
    if direction == "long":
        return bool(row.get("long_zone_exit"))
    if direction == "short":
        return bool(row.get("short_zone_exit"))
    return False


def _zone_exit_note(item: dict[str, Any], row: pd.Series, direction: str | None) -> str:
    st1 = float(row["st1"]) if pd.notna(row.get("st1")) else None
    close = float(row["close"])
    if direction == "long":
        return f"Long zone exit — close {close:.2f} below ST1 {st1:.2f}" if st1 else "Long zone exit"
    if direction == "short":
        return f"Short zone exit — close {close:.2f} above ST1 {st1:.2f}" if st1 else "Short zone exit"
    return "Zone exit"


def _latest_signals(item: dict[str, Any]) -> dict[str, Any] | None:
    chart = chart_instrument_meta(item)
    token = int(chart["instrument_token"])
    timeframe = str(item.get("timeframe") or "15min")
    st_method = str(item.get("st_method") or "heikin_ashi")
    end = date.today()
    start = end - timedelta(days=_lookback_days(timeframe))
    df = fetch_historical_by_token(token, timeframe, start, end)
    if df.empty or len(df) < 50:
        return None

    sig = compute_signals(
        df,
        atr1=int(item.get("atr1") or 21),
        factor1=float(item.get("factor1") or 1.0),
        atr2=int(item.get("atr2") or 14),
        factor2=float(item.get("factor2") or 2.0),
        atr3=int(item.get("atr3") or 7),
        factor3=float(item.get("factor3") or 3.0),
        st1_enabled=bool(item.get("st1_enabled", True)),
        st2_enabled=bool(item.get("st2_enabled", True)),
        st3_enabled=bool(item.get("st3_enabled", True)),
        adx_enabled=bool(item.get("adx_enabled", True)),
        adx_period=int(item.get("adx_period") or 14),
        adx_threshold=float(item.get("adx_threshold") or 20.0),
        st_method=st_method,  # type: ignore[arg-type]
    )
    row = sig.iloc[-1]
    prev = sig.iloc[-2] if len(sig) > 1 else row
    exit_line, exit_label = _exit_line_label(item, row)
    atr_raw = row.get("atr1")
    direction = _trade_side(item)
    zone_exit = _zone_exit_triggered(item, row, direction)
    return {
        "close": float(row["close"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "prev_high": float(prev["high"]),
        "prev_low": float(prev["low"]),
        "atr1": float(atr_raw) if pd.notna(atr_raw) else None,
        "st1": float(row["st1"]) if pd.notna(row.get("st1")) else None,
        "st1_upper": float(row["st1_upper"]) if pd.notna(row.get("st1_upper")) else None,
        "st1_lower": float(row["st1_lower"]) if pd.notna(row.get("st1_lower")) else None,
        "st2": float(row["st2"]) if pd.notna(row.get("st2")) else None,
        "st3": float(row["st3"]) if pd.notna(row.get("st3")) else None,
        "dir1": int(row["dir1"]),
        "dir2": int(row["dir2"]),
        "dir3": int(row["dir3"]),
        "long_zone_exit": bool(row["long_zone_exit"]),
        "short_zone_exit": bool(row["short_zone_exit"]),
        "zone_exit": zone_exit,
        "exit_line": exit_line,
        "exit_label": exit_label,
        "bar_time": str(sig.index[-1]),
        "chart_token": token,
        "chart_symbol": chart.get("tradingsymbol"),
        "chart_exchange": chart.get("exchange"),
        "timeframe": timeframe,
        "st_method": st_method,
    }


def _item_ltp(
    item: dict[str, Any],
    *,
    ltp_map: dict[str, float] | None = None,
) -> float | None:
    exchange = str(item.get("exchange") or "")
    tradingsymbol = str(item.get("tradingsymbol") or "")
    if not exchange or not tradingsymbol:
        return None
    key = _position_key(exchange, tradingsymbol)
    if ltp_map is not None:
        raw = ltp_map.get(key)
        if raw in (None, ""):
            return None
        return float(raw)
    ltp_map = _fetch_ltp_map([{"exchange": exchange, "tradingsymbol": tradingsymbol}])
    raw = ltp_map.get(key)
    if raw in (None, ""):
        return None
    return float(raw)


def _trade_side(item: dict[str, Any]) -> str:
    """Broker position side for exits — BUY=long, SELL=short (not 3ST zone label)."""
    es = str(item.get("entry_side") or "").upper()
    if es == "SELL":
        return "short"
    if es == "BUY":
        return "long"
    sig = str(item.get("signal") or "long").lower()
    return sig if sig in {"long", "short"} else "long"


def _target_level(item: dict[str, Any]) -> float | None:
    entry_px = float(item.get("entry_price") or 0)
    if entry_px <= 0:
        return None
    side = _trade_side(item)
    return _level(
        entry_px,
        item.get("tgt_mode") or "Off",  # type: ignore[arg-type]
        float(item.get("tgt_value") or 1),
        side,
        "tgt",
    )


def _update_trail_stop(
    item: dict[str, Any],
    signals: dict[str, Any],
    trail: float | None,
    *,
    ltp: float | None = None,
) -> tuple[float | None, float | None]:
    """
    Ratchet stop/trail for live monitoring.

    ATR trailing uses the best price since entry (low for short, high for long) when
    LTP is available — not the stale last closed bar (entry bar can be far from LTP).
    """
    side = _trade_side(item)
    tsl_mode = str(item.get("tsl_mode") or "Off")
    tsl_value = float(item.get("tsl_value") or 1.5)
    entry_px = float(item.get("entry_price") or 0)

    if trail is None and entry_px > 0:
        trail = _level(
            entry_px,
            item.get("sl_mode") or "Off",  # type: ignore[arg-type]
            float(item.get("sl_value") or 1),
            side,
            "sl",
        )

    raw_extreme = item.get("trail_extreme")
    extreme: float | None = float(raw_extreme) if raw_extreme not in (None, "") else None
    if extreme is None and entry_px > 0:
        extreme = entry_px
    elif extreme is None and ltp is not None:
        extreme = ltp

    if ltp is not None and extreme is not None:
        if side == "long":
            extreme = max(extreme, ltp)
        else:
            extreme = min(extreme, ltp)

    ref = extreme if (ltp is not None and extreme is not None) else float(signals.get("close") or 0)
    if ref <= 0:
        return trail, extreme

    if tsl_mode == "ATR":
        atr_raw = signals.get("atr1")
        if atr_raw is not None:
            atr_v = float(atr_raw) * tsl_value
            if side == "long":
                candidate = ref - atr_v
                trail = candidate if trail is None else max(trail, candidate)
            else:
                candidate = ref + atr_v
                trail = candidate if trail is None else min(trail, candidate)
    elif tsl_mode == "%":
        if side == "long" and signals.get("prev_high") is not None:
            candidate = float(signals["prev_high"]) * (1 - tsl_value / 100)
            trail = candidate if trail is None else max(trail, candidate)
        elif side == "short" and signals.get("prev_low") is not None:
            candidate = float(signals["prev_low"]) * (1 + tsl_value / 100)
            trail = candidate if trail is None else min(trail, candidate)
    elif tsl_mode == "Pts":
        if side == "long" and signals.get("prev_high") is not None:
            candidate = float(signals["prev_high"]) - tsl_value
            trail = candidate if trail is None else max(trail, candidate)
        elif side == "short" and signals.get("prev_low") is not None:
            candidate = float(signals["prev_low"]) + tsl_value
            trail = candidate if trail is None else min(trail, candidate)

    return trail, extreme


def _tsl_from_ltp(
    item: dict[str, Any],
    signals: dict[str, Any],
    ltp: float | None,
) -> float | None:
    """Instant TSL level from live LTP + ATR (no ratchet history)."""
    if ltp is None or str(item.get("tsl_mode") or "Off") != "ATR":
        return None
    atr_raw = signals.get("atr1")
    if atr_raw is None:
        return None
    atr_v = float(atr_raw) * float(item.get("tsl_value") or 1.5)
    side = _trade_side(item)
    return ltp - atr_v if side == "long" else ltp + atr_v


def _live_st1_bands(
    signals: dict[str, Any],
    ltp: float | None,
    *,
    factor1: float = 1.0,
) -> dict[str, Any]:
    """
    ST1 upper/lower bands with LTP as running close (Pine running-candle parity).
    Both levels move as live price changes.
    """
    bar_upper = signals.get("st1_upper")
    bar_lower = signals.get("st1_lower")
    if bar_upper is None or bar_lower is None:
        return {
            "bear_exit": bar_upper,
            "bull_entry": bar_lower,
            "dir1": signals.get("dir1"),
            "live": False,
        }
    if ltp is None:
        return {
            "bear_exit": round(float(bar_upper), 2),
            "bull_entry": round(float(bar_lower), 2),
            "dir1": int(signals.get("dir1") or 1),
            "live": False,
        }

    atr = float(signals.get("atr1") or 0)
    if atr <= 0:
        return {
            "bear_exit": round(float(bar_upper), 2),
            "bull_entry": round(float(bar_lower), 2),
            "dir1": int(signals.get("dir1") or 1),
            "live": False,
        }

    atr_v = atr * factor1
    prev_upper = float(bar_upper)
    prev_lower = float(bar_lower)
    prev_dir = int(signals.get("dir1") or 1)
    prev_close = float(signals.get("close") or ltp)

    high = max(float(signals.get("high") or ltp), ltp)
    low = min(float(signals.get("low") or ltp), ltp)
    src = (high + low) / 2.0

    lower_basic = src - atr_v
    upper_basic = src + atr_v

    lower = lower_basic if lower_basic > prev_lower or prev_close < prev_lower else prev_lower
    upper = upper_basic if upper_basic < prev_upper or prev_close > prev_upper else prev_upper

    dir1 = prev_dir
    if prev_dir == 1 and ltp < lower:
        dir1 = -1
    elif prev_dir == -1 and ltp > upper:
        dir1 = 1

    return {
        "bear_exit": round(upper, 2),
        "bull_entry": round(lower, 2),
        "dir1": dir1,
        "live": True,
    }


def _st_zone_levels_live(
    item: dict[str, Any],
    signals: dict[str, Any],
    ltp: float | None,
) -> dict[str, Any]:
    """
    Trade-direction ST exit / opposite entry — live dynamic bands.

    CE Short (signal=short): exit = Bear (upper band), re-entry = Bull (lower band).
    Long: exit = Bull (lower band), re-entry = Bear (upper band).
    """
    factor1 = float(item.get("factor1") or 1.0)
    bands = _live_st1_bands(signals, ltp, factor1=factor1)
    side = _trade_side(item)
    bear = bands.get("bear_exit")
    bull = bands.get("bull_entry")
    dir1 = bands.get("dir1")
    dir_label = "BULL" if int(dir1 or 0) == 1 else "BEAR"

    if side == "short":
        exit_price = bear
        exit_label = "Bear exit"
        entry_price = bull
        entry_label = "Bull entry"
    else:
        exit_price = bull
        exit_label = "Bull exit"
        entry_price = bear
        entry_label = "Bear entry"

    out: dict[str, Any] = {
        "st_exit_price": exit_price,
        "st_exit_label": exit_label,
        "st_entry_price": entry_price,
        "st_entry_label": entry_label,
        "st1_dir": dir_label,
        "st_bear_exit": bear,
        "st_bull_entry": bull,
        "st_bands_live": bool(bands.get("live")),
    }

    if ltp is not None and exit_price is not None:
        exit_f = float(exit_price)
        if side == "short":
            out["st_exit_ltp_distance"] = round(exit_f - ltp, 2)
            out["st_exit_at_ltp"] = ltp >= exit_f or int(dir1 or 0) == 1
        else:
            out["st_exit_ltp_distance"] = round(ltp - exit_f, 2)
            out["st_exit_at_ltp"] = ltp <= exit_f or int(dir1 or 0) == -1

    if ltp is not None and entry_price is not None:
        entry_f = float(entry_price)
        out["st_entry_ltp_distance"] = round(abs(ltp - entry_f), 2)

    return out


def _risk_exit_reason(item: dict[str, Any], ltp: float, trail: float | None) -> tuple[bool, str]:
    side = _trade_side(item)
    tgt = _target_level(item)
    tsl_mode = str(item.get("tsl_mode") or "Off")
    sl_mode = str(item.get("sl_mode") or "Off")

    if side == "long":
        if trail is not None and ltp <= trail:
            if tsl_mode != "Off":
                if tsl_mode == "ATR":
                    return True, f"Trailing SL (ATR×{item.get('tsl_value')})"
                return True, "Trailing SL"
            if sl_mode != "Off":
                return True, "Stop loss"
        if tgt is not None and ltp >= tgt:
            return True, "Target"
    else:
        if trail is not None and ltp >= trail:
            if tsl_mode != "Off":
                if tsl_mode == "ATR":
                    return True, f"Trailing SL (ATR×{item.get('tsl_value')})"
                return True, "Trailing SL"
            if sl_mode != "Off":
                return True, "Stop loss"
        if tgt is not None and ltp <= tgt:
            return True, "Target"
    return False, ""


def _bar_ts(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        return pd.to_datetime(value)
    except Exception:
        return None


def _entry_grace_active(item: dict[str, Any], signals: dict[str, Any]) -> bool:
    """Block zone exit until a new bar closes after entry (matches backtest bar-by-bar logic)."""
    signal_bar = _bar_ts(signals.get("bar_time"))
    if signal_bar is None:
        return False

    entry_bar = _bar_ts(item.get("entry_bar_time"))
    if entry_bar is None:
        entry_bar = _bar_ts(item.get("entry_at"))
    if entry_bar is None:
        return False

    return signal_bar <= entry_bar


def _force_exit_due(item: dict[str, Any]) -> bool:
    return force_exit_due(
        datetime.now(),
        force_exit=str(item.get("force_exit") or "15:20"),
        session_end=str(item.get("session_end") or "15:40"),
        system_mode=str(item.get("system_mode") or "Intraday"),
    )


def _price_divergence_note(item: dict[str, Any], signals: dict[str, Any], ltp: float | None) -> str | None:
    """Warn when bar close and live LTP diverge — skip on entry bar (expected after a move)."""
    if ltp is None or _entry_grace_active(item, signals):
        return None
    close = float(signals.get("close") or 0)
    if close <= 0:
        return None
    entry_bar = _bar_ts(item.get("entry_bar_time"))
    signal_bar = _bar_ts(signals.get("bar_time"))
    if entry_bar is not None and signal_bar is not None and entry_bar == signal_bar:
        return None
    gap = abs(ltp - close) / max(ltp, 1.0)
    if gap <= 0.12:
        return None
    tf = signals.get("timeframe") or item.get("timeframe") or "?"
    return (
        f"Bar close {close:.2f} vs LTP {ltp:.2f} on {tf} — "
        "check timeframe/symbol if not entry bar"
    )


def _st_method_note(st_method: str | None) -> str | None:
    if st_method and st_method != "heikin_ashi":
        return "PRS Pine script uses Heikin Ashi ST — values differ from Regular/Hybrid"
    return None


def exit_status_for_item(
    item: dict[str, Any],
    *,
    signals: dict[str, Any] | None = None,
    ltp_map: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Read-only exit snapshot for UI."""
    direction = _trade_side(item)
    if signals is None:
        signals = _latest_signals(item)
    if not signals:
        return {
            "exit_label": None,
            "exit_line": None,
            "zone_exit_triggered": False,
            "exit_note": "Waiting for candles",
        }

    ltp = _item_ltp(item, ltp_map=ltp_map)
    stored_trail = item.get("trail_stop")
    trail, extreme = _update_trail_stop(
        item,
        signals,
        float(stored_trail) if stored_trail not in (None, "") else None,
        ltp=ltp,
    )
    tsl_live = _tsl_from_ltp(item, signals, ltp)
    st_snap = _st_zone_levels_live(item, signals, ltp)
    tgt = _target_level(item)
    tsl_mode = str(item.get("tsl_mode") or "Off")
    diverge = _price_divergence_note(item, signals, ltp)
    method_note = _st_method_note(signals.get("st_method"))

    triggered = False
    note = "Monitoring exits"
    risk_hit, risk_reason = _risk_exit_reason(item, ltp, trail) if ltp is not None else (False, "")

    if risk_hit:
        triggered = True
        note = risk_reason
    elif _entry_grace_active(item, signals):
        note = "Waiting for next bar close after entry (3ST zone)"
    elif signals.get("zone_exit") or st_snap.get("st_exit_at_ltp"):
        triggered = True
        if st_snap.get("st_exit_at_ltp") and ltp is not None:
            note = f"{st_snap.get('st_exit_label')} @ {st_snap.get('st_exit_price')} (LTP {ltp:.2f})"
        else:
            pseudo = pd.Series(
                {
                    "dir1": signals.get("dir1", 0),
                    "dir2": signals.get("dir2", 0),
                    "dir3": signals.get("dir3", 0),
                }
            )
            note = _zone_exit_note(item, pseudo, direction)
    elif _force_exit_due(item):
        triggered = True
        note = f"Force exit — {item.get('force_exit') or '15:20'} reached"
    elif diverge:
        note = diverge
    elif method_note:
        note = method_note

    st1 = signals.get("st1")
    st1_dir = st_snap.get("st1_dir") or ("BULL" if int(signals.get("dir1") or 0) == 1 else "BEAR")

    return {
        "exit_label": st_snap.get("st_exit_label") or signals.get("exit_label"),
        "exit_line": st_snap.get("st_exit_price") or signals.get("exit_line"),
        "st1": st1,
        "st1_dir": st1_dir,
        "st_exit_price": st_snap.get("st_exit_price"),
        "st_exit_label": st_snap.get("st_exit_label"),
        "st_exit_ltp_distance": st_snap.get("st_exit_ltp_distance"),
        "st_exit_at_ltp": st_snap.get("st_exit_at_ltp"),
        "st_entry_price": st_snap.get("st_entry_price"),
        "st_entry_label": st_snap.get("st_entry_label"),
        "st_entry_ltp_distance": st_snap.get("st_entry_ltp_distance"),
        "st_bear_exit": st_snap.get("st_bear_exit"),
        "st_bull_entry": st_snap.get("st_bull_entry"),
        "st_bands_live": st_snap.get("st_bands_live"),
        # legacy aliases
        "st1_exit_price": st_snap.get("st_exit_price"),
        "st1_ltp_distance": st_snap.get("st_exit_ltp_distance"),
        "st1_exit_at_ltp": st_snap.get("st_exit_at_ltp"),
        "st2": signals.get("st2"),
        "st3": signals.get("st3"),
        "signal_close": signals.get("close"),
        "entry_bar_close": item.get("entry_bar_close"),
        "entry_bar_time": item.get("entry_bar_time") or signals.get("bar_time"),
        "last_close": signals.get("close"),
        "bar_time": signals.get("bar_time"),
        "ltp": ltp,
        "timeframe": signals.get("timeframe"),
        "st_method": signals.get("st_method"),
        "chart_symbol": signals.get("chart_symbol"),
        "zone_exit_triggered": triggered and bool(signals.get("zone_exit")),
        "risk_exit_triggered": risk_hit,
        "trail_stop": round(trail, 2) if trail is not None else None,
        "tsl_live": round(tsl_live, 2) if tsl_live is not None else None,
        "trail_extreme": round(extreme, 2) if extreme is not None else None,
        "target_level": tgt,
        "tsl_mode": tsl_mode if tsl_mode != "Off" else None,
        "tsl_value": float(item.get("tsl_value") or 1.5) if tsl_mode != "Off" else None,
        "exit_grace_active": _entry_grace_active(item, signals),
        "price_divergence": diverge,
        "force_exit": item.get("force_exit"),
        "session_end": item.get("session_end"),
        "force_exit_due": _force_exit_due(item),
        "exit_note": note,
    }


def _should_exit(
    item: dict[str, Any],
    signals: dict[str, Any],
    *,
    ltp: float | None = None,
    trail: float | None = None,
) -> tuple[bool, str, float | None, float | None, str]:
    """
    First matching exit wins — risk (SL/TSL/TGT), then 3ST zone, then force.

    Returns (should, reason, trail, extreme, kind) where ``kind`` is one of
    "risk" / "zone_ltp" (live-price dependent), "zone_bar" / "force" (not
    tick dependent), or "" (no exit).
    """
    if ltp is None:
        ltp = _item_ltp(item)

    stored_trail = trail
    if stored_trail is None:
        raw = item.get("trail_stop")
        stored_trail = float(raw) if raw not in (None, "") else None
    trail, extreme = _update_trail_stop(item, signals, stored_trail, ltp=ltp)

    if ltp is not None:
        risk_hit, risk_reason = _risk_exit_reason(item, ltp, trail)
        if risk_hit:
            return True, risk_reason, trail, extreme, "risk"

    if _entry_grace_active(item, signals):
        return False, "", trail, extreme, ""

    direction = _trade_side(item)
    st_live = _st_zone_levels_live(item, signals, ltp)
    if signals.get("zone_exit") or st_live.get("st_exit_at_ltp"):
        if st_live.get("st_exit_at_ltp") and ltp is not None:
            label = st_live.get("st_exit_label") or "ST zone exit"
            px = st_live.get("st_exit_price")
            return True, f"3ST {label} @ {px} (LTP {ltp:.2f})", trail, extreme, "zone_ltp"
        pseudo = pd.Series(
            {
                "dir1": signals.get("dir1", 0),
                "dir2": signals.get("dir2", 0),
                "dir3": signals.get("dir3", 0),
            }
        )
        return True, f"3ST zone exit ({_zone_exit_note(item, pseudo, direction)})", trail, extreme, "zone_bar"
    if _force_exit_due(item):
        return True, "Force exit (session)", trail, extreme, "force"
    return False, "", trail, extreme, ""


def scan_watchlist_exits(*, auto_close: bool = True) -> dict[str, Any]:
    """Evaluate active trades; optionally square off on 3ST zone exit."""
    from utils.logging import get_logger, log_event

    logger = get_logger("exit_runner")

    if not session_status().get("authenticated"):
        raise RuntimeError("Kite session required to monitor 3ST exits")

    active = list_items("active")
    closed: list[dict[str, Any]] = []
    monitoring: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    deferred: list[dict[str, Any]] = []

    from execution.ltp_cache import get_ltp_cache, market_health

    data_health = market_health()

    ltp_map = _fetch_ltp_map(
        [
            {"exchange": i.get("exchange"), "tradingsymbol": i.get("tradingsymbol")}
            for i in active
            if i.get("exchange") and i.get("tradingsymbol")
        ]
    )

    for item in active:
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        try:
            signals = _latest_signals(item)
            if not signals:
                monitoring.append({"id": item_id, "note": "insufficient candles"})
                continue

            ltp = _item_ltp(item, ltp_map=ltp_map)
            should, reason, trail, extreme, kind = _should_exit(
                item,
                signals,
                ltp=ltp,
            )
            snap = exit_status_for_item(item, signals=signals, ltp_map=ltp_map)
            patch: dict[str, Any] = {
                "exit_label": snap.get("exit_label"),
                "exit_line": snap.get("exit_line"),
                "last_signal_close": snap.get("last_close"),
            }
            chart_token = signals.get("chart_token")
            if chart_token and int(chart_token) != int(item.get("instrument_token") or 0):
                patch["instrument_token"] = int(chart_token)
            if trail is not None:
                patch["trail_stop"] = round(trail, 2)
            if extreme is not None:
                patch["trail_extreme"] = round(extreme, 2)
            update_item(item_id, patch)

            row = {**item, **snap, "exit_pending": should}

            # Price-based exits (SL/TSL/target, live-price ST zone) act on the live
            # tick. Before squaring off, force one authoritative REST reconfirm and
            # re-verify the exit still holds — otherwise defer this tick rather than
            # act on a stale/borderline price.
            if should and auto_close and kind in {"risk", "zone_ltp"}:
                rc = get_ltp_cache().rest_prices(
                    [{"exchange": item.get("exchange"), "tradingsymbol": item.get("tradingsymbol")}]
                )
                rc_ltp = rc.get(_position_key(str(item.get("exchange") or ""), str(item.get("tradingsymbol") or "")))
                if rc_ltp is None:
                    should = False
                    row["exit_pending"] = False
                    row["exit_note"] = f"Exit deferred — REST reconfirm failed ({reason})"
                    deferred.append({"id": item_id, "reason": reason, "cause": "reconfirm_unavailable"})
                    log_event(
                        logger,
                        logging.WARNING,
                        "auto_exit_deferred",
                        item_id=item_id,
                        reason=reason,
                        cause="reconfirm_unavailable",
                        tradingsymbol=item.get("tradingsymbol"),
                    )
                else:
                    should2, reason2, trail2, extreme2, kind2 = _should_exit(
                        item, signals, ltp=float(rc_ltp), trail=trail
                    )
                    if should2 and kind2 in {"risk", "zone_ltp"}:
                        ltp = float(rc_ltp)
                        reason = reason2
                        trail = trail2 if trail2 is not None else trail
                        extreme = extreme2 if extreme2 is not None else extreme
                    else:
                        should = False
                        row["exit_pending"] = False
                        row["exit_note"] = f"Exit deferred — not confirmed on REST (LTP {rc_ltp})"
                        deferred.append({"id": item_id, "reason": reason, "cause": "reconfirm_negated"})
                        log_event(
                            logger,
                            logging.INFO,
                            "auto_exit_deferred",
                            item_id=item_id,
                            reason=reason,
                            cause="reconfirm_negated",
                            reconfirm_ltp=rc_ltp,
                            tradingsymbol=item.get("tradingsymbol"),
                        )

            if should and auto_close:
                updated = close_watchlist_trade(item_id, reason)
                log_event(
                    logger,
                    logging.INFO,
                    "auto_exit_closed",
                    item_id=item_id,
                    reason=reason,
                    kind=kind,
                    tradingsymbol=item.get("tradingsymbol"),
                    exchange=item.get("exchange"),
                    ltp=ltp,
                    trail=trail,
                )
                closed.append(updated)
            else:
                monitoring.append(row)
        except Exception as e:
            errors.append({"id": item_id, "error": str(e)})

    if data_health.get("status") not in {"healthy", "rest_only"}:
        log_event(
            logger,
            logging.WARNING,
            "exit_scan_feed_degraded",
            feed_status=data_health.get("status"),
            last_tick_age_sec=data_health.get("last_tick_age_sec"),
            reconnects=data_health.get("reconnects"),
        )

    return {
        "ok": True,
        "scanned": len(active),
        "closed": closed,
        "monitoring": monitoring,
        "deferred": deferred,
        "errors": errors,
        "data_health": data_health,
    }
