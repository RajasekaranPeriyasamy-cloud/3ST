"""Close open broker legs for an active watchlist trade."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from broker.base import Broker
from broker.kite_broker import KiteBroker
from broker.paper_broker import get_paper_broker
from execution.arming import get_arm_state
from execution.order_executor import place_leg_order
from execution.watchlist_activation import _legs_for_signal, _product_for_item
from utils.logging import get_logger, log_event
from watchlist_store import get_item, update_item

logger = get_logger("watchlist_close")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _broker_for_close(trade_mode: str | None) -> Broker:
    arm = get_arm_state()
    mode = str(arm.get("mode") or "paper")
    armed = bool(arm.get("armed"))
    use_live = trade_mode == "live" or mode == "live"
    if use_live:
        if not armed:
            raise RuntimeError("ARM required to close LIVE positions on the exchange")
        return KiteBroker()
    return get_paper_broker()


def _open_legs(broker: Broker, item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return broker positions matching this watchlist item's legs."""
    legs = _legs_for_signal(item)
    if not legs:
        sym = str(item.get("tradingsymbol") or "")
        exch = str(item.get("exchange") or "")
        if sym and exch:
            legs = [{"tradingsymbol": sym, "exchange": exch, "quantity": item.get("entry_qty") or item.get("lot_size")}]
        else:
            return []

    try:
        positions = broker.positions()
    except Exception:
        positions = []

    open_rows: list[dict[str, Any]] = []
    for leg in legs:
        sym = str(leg.get("tradingsymbol") or "")
        exch = str(leg.get("exchange") or item.get("exchange") or "")
        for pos in positions:
            if str(pos.get("tradingsymbol")) != sym or str(pos.get("exchange")) != exch:
                continue
            qty = int(pos.get("quantity") or 0)
            if qty == 0:
                continue
            open_rows.append({**leg, "quantity": abs(qty), "open_qty": qty, "exchange": exch, "tradingsymbol": sym})
            break
    return open_rows


def close_watchlist_trade(item_id: str, reason: str = "manual") -> dict[str, Any]:
    item = get_item(item_id)
    if not item:
        raise KeyError(f"Watchlist item not found: {item_id}")
    if item.get("status") != "active":
        raise RuntimeError(f"Cannot close item in status '{item.get('status')}'")

    broker = _broker_for_close(item.get("trade_mode"))
    product = _product_for_item(item)
    open_legs = _open_legs(broker, item)

    exit_prices: list[float] = []
    for leg in open_legs:
        open_qty = int(leg.get("open_qty") or 0)
        tx = "SELL" if open_qty > 0 else "BUY"
        qty = int(leg.get("quantity") or abs(open_qty))
        result = place_leg_order(
            broker,
            {"tradingsymbol": leg["tradingsymbol"], "exchange": leg["exchange"], "quantity": qty},
            transaction_type=tx,
            tag=f"3ST-X-{item_id[:8]}",
            product=product,
        )
        if not result.get("ok"):
            raise RuntimeError(result.get("message") or "Exit order failed")
        raw = result.get("raw") or {}
        px = float(raw.get("price") or 0)
        if px > 0:
            exit_prices.append(px)

    exit_price = round(sum(exit_prices) / len(exit_prices), 2) if exit_prices else None
    if not open_legs:
        raise RuntimeError("No open broker legs to close for this trade")
    patch: dict[str, Any] = {
        "status": "closed",
        "signal": None,
        "exit_at": _now(),
        "exit_reason": reason,
    }
    if exit_price is not None:
        patch["exit_price"] = exit_price
    updated = update_item(item_id, patch)
    log_event(
        logger,
        logging.INFO,
        "watchlist_trade_closed",
        item_id=item_id,
        reason=reason,
        tradingsymbol=item.get("tradingsymbol"),
        exchange=item.get("exchange"),
        signal=item.get("signal"),
        exit_price=exit_price,
        legs_closed=len(open_legs),
    )
    return updated


def unlink_watchlist_item(item_id: str) -> dict[str, Any]:
    """Stop 3ST monitoring without placing a Kite exit order."""
    item = get_item(item_id)
    if not item:
        raise KeyError(f"Watchlist item not found: {item_id}")
    if item.get("status") != "active":
        return {"ok": True, "skipped": True, "reason": "not active", "item": item}
    updated = update_item(
        item_id,
        {
            "status": "closed",
            "signal": None,
            "exit_at": _now(),
            "exit_reason": "unlinked — Kite position unchanged",
        },
    )
    log_event(
        logger,
        logging.INFO,
        "watchlist_trade_unlinked",
        item_id=item_id,
        tradingsymbol=item.get("tradingsymbol"),
        exchange=item.get("exchange"),
    )
    return {"ok": True, "item": updated}
