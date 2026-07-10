"""Scan watchlist items for fresh 3ST entry signals."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from config import INDEX_OPTIONS
from execution.arming import get_arm_state
from instruments import resolve_by_token, resolve_underlying_index_token
from kite_client import fetch_historical_by_token, session_status
from strategies.three_st import ThreeSTStrategy
from watchlist_store import list_items, mark_triggered


def _resolve_chart_token(item: dict[str, Any]) -> int:
    if item.get("product") == "options_spread":
        spread = item.get("spread") or {}
        underlying = spread.get("underlying")
        if underlying and underlying in INDEX_OPTIONS:
            return resolve_underlying_index_token(underlying)
    token = item.get("instrument_token")
    if token is None:
        raise RuntimeError("No instrument_token on watchlist item")
    return int(token)


def _strategy_from_item(item: dict[str, Any]) -> ThreeSTStrategy:
    return ThreeSTStrategy(
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
        st_method=item.get("st_method") or "heikin_ashi",
    )


def _latest_entry_signal(item: dict[str, Any]) -> tuple[str, str] | None:
    token = _resolve_chart_token(item)
    resolve_by_token(token)
    timeframe = item.get("timeframe") or "15min"
    end = date.today()
    start = end - timedelta(days=10)
    df = fetch_historical_by_token(token, timeframe, start, end)
    if df.empty or len(df) < 50:
        return None

    strat = _strategy_from_item(item)
    sig = strat.on_bar(df)
    if sig is None:
        return None
    if sig.action == "enter_long":
        return "long", sig.reason
    if sig.action == "enter_short":
        return "short", sig.reason
    return None


def scan_watchlist(*, require_armed: bool = False) -> dict[str, Any]:
    """Evaluate waiting watchlist rows; promote to triggered on fresh 3ST entry."""
    if not session_status().get("authenticated"):
        raise RuntimeError("Kite session required to scan for signals")

    arm = get_arm_state()
    if require_armed and not arm.get("armed"):
        return {"ok": True, "scanned": 0, "triggered": [], "note": "DISARMED — scan skipped"}

    waiting = list_items("waiting")
    triggered: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for item in waiting:
        item_id = item.get("id")
        if not item_id:
            continue
        try:
            hit = _latest_entry_signal(item)
            if hit is None:
                continue
            direction, note = hit
            updated = mark_triggered(item_id, direction, note)
            triggered.append(updated)
        except Exception as e:
            errors.append({"id": str(item_id), "error": str(e)})

    return {
        "ok": True,
        "scanned": len(waiting),
        "triggered": triggered,
        "errors": errors,
        "armed": arm.get("armed"),
    }
