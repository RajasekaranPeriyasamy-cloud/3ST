"""Active watchlist trades with live quotes — even when paper position is missing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from execution.positions_view import _fetch_ltp_map, _position_key, build_positions_view
from execution.watchlist_exit_runner import exit_status_for_item
from selection_store import get_selection
from watchlist_store import add_item, get_item, list_items, update_item

_SELECTION_KEYS = (
    "instrument_token",
    "name",
    "segment",
    "lot_size",
    "timeframe",
    "product",
    "st_method",
    "system_mode",
    "session_start",
    "session_end",
    "force_exit",
    "atr1",
    "factor1",
    "atr2",
    "factor2",
    "atr3",
    "factor3",
    "st1_enabled",
    "st2_enabled",
    "st3_enabled",
    "adx_enabled",
    "adx_period",
    "adx_threshold",
    "sl_mode",
    "sl_value",
    "tgt_mode",
    "tgt_value",
    "tsl_mode",
    "tsl_value",
    "product_type",
    "entry_mode",
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _selection_fields(sel: dict[str, Any]) -> dict[str, Any]:
    return {k: sel[k] for k in _SELECTION_KEYS if sel.get(k) not in (None, "")}


def _trade_row(
    item: dict[str, Any],
    *,
    desk: dict[str, Any],
    positions: list[dict[str, Any]],
    ltp_map: dict[str, float],
) -> dict[str, Any]:
    exchange = str(item.get("exchange") or "")
    tradingsymbol = str(item.get("tradingsymbol") or "")
    key = _position_key(exchange, tradingsymbol) if exchange and tradingsymbol else ""
    pos = _find_position(positions, exchange, tradingsymbol) if exchange and tradingsymbol else None

    entry_raw = item.get("entry_price")
    if entry_raw is None and pos:
        entry_raw = pos.get("average_price")
    entry = float(entry_raw) if entry_raw not in (None, "", 0) else None

    qty_raw = item.get("entry_qty")
    if qty_raw is None and pos:
        qty_raw = abs(int(pos.get("quantity") or 0))
    if qty_raw is None:
        qty_raw = int(item.get("lot_size") or 0)
    qty = int(qty_raw or 0)

    ltp_raw = ltp_map.get(key) if key else None
    if ltp_raw is None and pos:
        ltp_raw = pos.get("last_price")
    ltp = float(ltp_raw) if ltp_raw not in (None, "") else None

    pnl = pos.get("pnl") if pos else None
    if pnl is None and entry is not None and ltp is not None and qty:
        pnl = round(_compute_pnl(item.get("signal"), entry, ltp, qty), 2)

    if pos is not None:
        status = "running"
    elif ltp is not None and entry is not None:
        status = "tracking"
    elif entry is not None:
        status = "no_quote"
    else:
        status = "no_position"

    exit_snap: dict[str, Any] = {}
    try:
        exit_snap = exit_status_for_item(item)
    except Exception:
        exit_snap = {}

    return {
        "id": item.get("id"),
        "tradingsymbol": tradingsymbol,
        "exchange": exchange,
        "signal": item.get("signal"),
        "entry_side": item.get("entry_side"),
        "trade_mode": item.get("trade_mode") or desk.get("mode") or "paper",
        "entry_mode": item.get("entry_mode") or "manual",
        "timeframe": item.get("timeframe"),
        "entry_price": round(entry, 2) if entry is not None else None,
        "quantity": qty,
        "last_price": round(ltp, 2) if ltp is not None else None,
        "pnl": float(pnl) if pnl is not None else None,
        "status": status,
        "exit_label": exit_snap.get("exit_label") or item.get("exit_label"),
        "exit_line": exit_snap.get("exit_line") or item.get("exit_line"),
        "st1": exit_snap.get("st1"),
        "st1_dir": exit_snap.get("st1_dir"),
        "st1_exit_price": exit_snap.get("st1_exit_price"),
        "st1_ltp_distance": exit_snap.get("st1_ltp_distance"),
        "st1_exit_at_ltp": exit_snap.get("st1_exit_at_ltp"),
        "st_exit_price": exit_snap.get("st_exit_price"),
        "st_exit_label": exit_snap.get("st_exit_label"),
        "st_exit_ltp_distance": exit_snap.get("st_exit_ltp_distance"),
        "st_exit_at_ltp": exit_snap.get("st_exit_at_ltp"),
        "st_entry_price": exit_snap.get("st_entry_price"),
        "st_entry_label": exit_snap.get("st_entry_label"),
        "st_bear_exit": exit_snap.get("st_bear_exit"),
        "st_bull_entry": exit_snap.get("st_bull_entry"),
        "st_bands_live": exit_snap.get("st_bands_live"),
        "tsl_live": exit_snap.get("tsl_live"),
        "trail_extreme": exit_snap.get("trail_extreme"),
        "entry_bar_close": exit_snap.get("entry_bar_close") or exit_snap.get("signal_close"),
        "entry_bar_time": exit_snap.get("entry_bar_time") or item.get("entry_bar_time"),
        "signal_close": exit_snap.get("signal_close"),
        "timeframe": exit_snap.get("timeframe") or item.get("timeframe"),
        "st_method": exit_snap.get("st_method") or item.get("st_method"),
        "price_divergence": exit_snap.get("price_divergence"),
        "exit_note": exit_snap.get("exit_note") or "Exit monitoring",
        "zone_exit_triggered": bool(exit_snap.get("zone_exit_triggered")),
        "risk_exit_triggered": bool(exit_snap.get("risk_exit_triggered")),
        "trail_stop": exit_snap.get("trail_stop") or item.get("trail_stop"),
        "target_level": exit_snap.get("target_level"),
        "tsl_mode": exit_snap.get("tsl_mode") or item.get("tsl_mode"),
        "tsl_value": exit_snap.get("tsl_value") or item.get("tsl_value"),
        "force_exit": exit_snap.get("force_exit") or item.get("force_exit"),
        "session_end": exit_snap.get("session_end") or item.get("session_end"),
        "force_exit_due": bool(exit_snap.get("force_exit_due")),
        "kite_product": item.get("kite_product") or item.get("product_type"),
        "system_mode": item.get("system_mode"),
        "order_ids": item.get("order_ids") or [],
        "entry_at": item.get("entry_at"),
    }


def _ltp_map_for_items(items: list[dict[str, Any]]) -> dict[str, float]:
    pseudo = [
        {"exchange": i.get("exchange"), "tradingsymbol": i.get("tradingsymbol")}
        for i in items
        if i.get("exchange") and i.get("tradingsymbol")
    ]
    return _fetch_ltp_map(pseudo)


def _find_position(positions: list[dict[str, Any]], exchange: str, tradingsymbol: str) -> dict[str, Any] | None:
    for p in positions:
        if str(p.get("exchange")) == exchange and str(p.get("tradingsymbol")) == tradingsymbol:
            if int(p.get("quantity") or 0) != 0:
                return p
    return None


def _compute_pnl(signal: str | None, entry: float, ltp: float, qty: int) -> float:
    if signal == "short":
        return (entry - ltp) * qty
    return (ltp - entry) * qty


def _desk_trade_mode(mode: str) -> str:
    return "live" if str(mode or "paper") == "live" else "paper"


def _item_matches_desk_mode(item: dict[str, Any], desk_mode: str) -> bool:
    tm = str(item.get("trade_mode") or desk_mode).strip().lower()
    return tm == _desk_trade_mode(desk_mode)


def _cleanup_stale_active_items(*, desk_mode: str, positions: list[dict[str, Any]]) -> None:
    """Close active rows that were exited or have no broker leg in the current desk mode."""
    pos_keys = {
        _position_key(str(p.get("exchange") or ""), str(p.get("tradingsymbol") or ""))
        for p in positions
        if int(p.get("quantity") or 0) != 0
    }
    for item in list_items("active"):
        if not _item_matches_desk_mode(item, desk_mode):
            continue
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        if item.get("exit_at"):
            update_item(
                item_id,
                {
                    "status": "closed",
                    "signal": None,
                    "exit_reason": item.get("exit_reason") or "cleanup: already exited",
                },
            )
            continue
        exch = str(item.get("exchange") or "")
        sym = str(item.get("tradingsymbol") or "")
        key = _position_key(exch, sym) if exch and sym else ""
        if key and key not in pos_keys:
            update_item(
                item_id,
                {
                    "status": "closed",
                    "signal": None,
                    "exit_at": _now(),
                    "exit_reason": "cleanup: broker flat",
                },
            )


def build_active_trades_view() -> dict[str, Any]:
    desk = build_positions_view()
    desk_mode = str(desk.get("mode") or "paper")
    _cleanup_stale_active_items(desk_mode=desk_mode, positions=desk.get("positions") or [])
    items = [i for i in list_items("active") if _item_matches_desk_mode(i, desk_mode)]
    positions = desk.get("positions") or []
    ltp_map = _ltp_map_for_items(items)

    rows: list[dict[str, Any]] = []
    for item in items:
        rows.append(_trade_row(item, desk=desk, positions=positions, ltp_map=ltp_map))

    monitored_keys = {
        _position_key(str(r.get("exchange") or ""), str(r.get("tradingsymbol") or ""))
        for r in rows
        if r.get("exchange") and r.get("tradingsymbol")
    }
    orphans: list[dict[str, Any]] = []
    for pos in positions:
        qty = int(pos.get("quantity") or 0)
        if qty == 0:
            continue
        exch = str(pos.get("exchange") or "")
        sym = str(pos.get("tradingsymbol") or "")
        key = _position_key(exch, sym)
        if key in monitored_keys:
            continue
        orphans.append(
            {
                "exchange": exch,
                "tradingsymbol": sym,
                "quantity": qty,
                "average_price": pos.get("average_price"),
                "product": pos.get("product"),
                "pnl": pos.get("pnl"),
            }
        )

    return {
        "mode": desk.get("mode"),
        "trades": rows,
        "count": len(rows),
        "orphans": orphans,
        "orphan_count": len(orphans),
    }


def _adopt_single_position(pos: dict[str, Any], *, desk_mode: str, st_defaults: dict[str, Any], active_keys: set[str]) -> dict[str, Any] | None:
    qty = int(pos.get("quantity") or 0)
    if qty == 0:
        return None
    exch = str(pos.get("exchange") or "")
    sym = str(pos.get("tradingsymbol") or "")
    key = _position_key(exch, sym)
    if key in active_keys:
        return None

    all_items = list_items()
    match = None
    for item in sorted(all_items, key=lambda x: str(x.get("updated_at") or ""), reverse=True):
        if _position_key(str(item.get("exchange") or ""), str(item.get("tradingsymbol") or "")) == key:
            match = item
            break

    patch: dict[str, Any] = {
        **st_defaults,
        "status": "active",
        "signal": "short" if qty < 0 else "long",
        "entry_side": "SELL" if qty < 0 else "BUY",
        "entry_qty": abs(qty),
        "entry_price": round(float(pos.get("average_price") or 0), 2),
        "entry_at": _now(),
        "trade_mode": "live",
        "signal_note": "Linked for 3ST exit monitoring",
        "kite_product": pos.get("product") or st_defaults.get("product_type"),
        "exit_at": None,
        "exit_reason": None,
        "exit_price": None,
    }
    if match:
        for k, v in st_defaults.items():
            if match.get(k) not in (None, ""):
                patch[k] = match[k]
        return update_item(str(match["id"]), patch)

    sel = get_selection()
    payload = {
        **st_defaults,
        "exchange": exch,
        "tradingsymbol": sym,
        "name": pos.get("name") or sel.get("name") or sym,
        "entry_mode": sel.get("entry_mode") or "manual",
        "product": sel.get("product") or "underlying",
        "segment": sel.get("segment") or "option",
    }
    try:
        from instruments import resolve_by_symbol

        meta = resolve_by_symbol(exch, sym)
        payload["instrument_token"] = meta.get("instrument_token")
        if meta.get("lot_size"):
            payload["lot_size"] = meta.get("lot_size")
    except Exception:
        pass
    created = add_item(payload)
    return update_item(str(created["id"]), patch)


def adopt_position(exchange: str, tradingsymbol: str) -> dict[str, Any]:
    """Link one open broker position to watchlist exit monitoring."""
    sel = get_selection()
    st_defaults = _selection_fields(sel)
    desk = build_positions_view()
    desk_mode = str(desk.get("mode") or "paper")
    if desk_mode != "live":
        raise RuntimeError("Switch Live Desk to Live mode before linking positions")
    positions = desk.get("positions") or []
    _cleanup_stale_active_items(desk_mode=desk_mode, positions=positions)
    active_keys = {
        _position_key(str(i.get("exchange") or ""), str(i.get("tradingsymbol") or ""))
        for i in list_items("active")
    }
    target = None
    for pos in positions:
        if str(pos.get("exchange") or "") == exchange and str(pos.get("tradingsymbol") or "") == tradingsymbol:
            target = pos
            break
    if not target:
        raise RuntimeError(f"No open broker position for {exchange}:{tradingsymbol}")
    adopted = _adopt_single_position(target, desk_mode=desk_mode, st_defaults=st_defaults, active_keys=active_keys)
    if not adopted:
        raise RuntimeError("Position already linked or unavailable")
    return {"ok": True, "count": 1, "items": [adopted]}


def close_broker_position(exchange: str, tradingsymbol: str, *, reason: str = "manual_close") -> dict[str, Any]:
    """Place a market exit for an open broker leg (orphan close)."""
    from broker.kite_broker import KiteBroker
    from execution.order_executor import place_leg_order

    desk = build_positions_view()
    if str(desk.get("mode") or "paper") != "live":
        raise RuntimeError("Switch to Live mode to close Kite positions")
    positions = desk.get("positions") or []
    pos = _find_position(positions, exchange, tradingsymbol)
    if not pos:
        raise RuntimeError(f"No open broker position for {exchange}:{tradingsymbol}")
    qty = int(pos.get("quantity") or 0)
    tx = "SELL" if qty > 0 else "BUY"
    result = place_leg_order(
        KiteBroker(),
        {"tradingsymbol": tradingsymbol, "exchange": exchange, "quantity": abs(qty)},
        transaction_type=tx,
        tag="3ST-orphan-x",
        product=str(pos.get("product") or "MIS"),
    )
    if not result.get("ok"):
        raise RuntimeError(result.get("message") or "Exit order failed")
    return {"ok": True, "order_id": result.get("order_id"), "reason": reason}


def adopt_open_positions() -> dict[str, Any]:
    """Link open broker positions to active watchlist rows for 3ST exit monitoring."""
    sel = get_selection()
    st_defaults = _selection_fields(sel)
    desk = build_positions_view()
    desk_mode = str(desk.get("mode") or "paper")
    if desk_mode != "live":
        raise RuntimeError("Switch Live Desk to Live mode before linking positions for live exit monitoring")
    positions = desk.get("positions") or []
    _cleanup_stale_active_items(desk_mode=desk_mode, positions=positions)
    all_items = list_items()
    active_keys = {
        _position_key(str(i.get("exchange") or ""), str(i.get("tradingsymbol") or ""))
        for i in list_items("active")
    }

    adopted: list[dict[str, Any]] = []
    for pos in positions:
        qty = int(pos.get("quantity") or 0)
        if qty == 0:
            continue
        exch = str(pos.get("exchange") or "")
        sym = str(pos.get("tradingsymbol") or "")
        key = _position_key(exch, sym)
        if key in active_keys:
            continue

        match = None
        for item in sorted(all_items, key=lambda x: str(x.get("updated_at") or ""), reverse=True):
            if _position_key(str(item.get("exchange") or ""), str(item.get("tradingsymbol") or "")) == key:
                match = item
                break

        patch: dict[str, Any] = {
            **st_defaults,
            "status": "active",
            "signal": "short" if qty < 0 else "long",
            "entry_side": "SELL" if qty < 0 else "BUY",
            "entry_qty": abs(qty),
            "entry_price": round(float(pos.get("average_price") or 0), 2),
            "entry_at": _now(),
            "trade_mode": "live",
            "signal_note": "Linked for 3ST exit monitoring",
            "kite_product": pos.get("product") or st_defaults.get("product_type"),
            "exit_at": None,
            "exit_reason": None,
            "exit_price": None,
        }
        if match:
            for k, v in st_defaults.items():
                if match.get(k) not in (None, ""):
                    patch[k] = match[k]
            updated = update_item(str(match["id"]), patch)
        else:
            payload = {
                **st_defaults,
                "exchange": exch,
                "tradingsymbol": sym,
                "name": pos.get("name") or sel.get("name") or sym,
                "entry_mode": sel.get("entry_mode") or "manual",
                "product": sel.get("product") or "underlying",
                "segment": sel.get("segment") or "option",
            }
            try:
                from instruments import resolve_by_symbol

                meta = resolve_by_symbol(exch, sym)
                payload["instrument_token"] = meta.get("instrument_token")
                if meta.get("lot_size"):
                    payload["lot_size"] = meta.get("lot_size")
            except Exception:
                pass
            created = add_item(payload)
            updated = update_item(str(created["id"]), patch)

        adopted.append(updated)
        active_keys.add(key)

    return {"ok": True, "count": len(adopted), "items": adopted}


def sync_active_trade_entry(item_id: str) -> dict[str, Any]:
    """Place or refresh paper entry for an active watchlist row."""
    from execution.watchlist_activation import place_watchlist_entry

    item = get_item(item_id)
    if not item:
        raise KeyError(f"Watchlist item not found: {item_id}")
    if item.get("status") != "active":
        raise RuntimeError("Only active trades can be synced")

    desk = build_positions_view()
    exchange = str(item.get("exchange") or "")
    tradingsymbol = str(item.get("tradingsymbol") or "")
    pos = _find_position(desk.get("positions") or [], exchange, tradingsymbol)

    if pos:
        patch = {
            "entry_price": float(pos.get("average_price") or item.get("entry_price") or 0),
            "entry_qty": abs(int(pos.get("quantity") or item.get("entry_qty") or 0)),
        }
        return update_item(item_id, patch)

    updated = place_watchlist_entry(item, allow_active=True)
    return updated
