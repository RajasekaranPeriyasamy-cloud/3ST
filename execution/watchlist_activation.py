"""Activate a triggered watchlist item — entry snapshot + paper/live orders."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from broker.base import Broker
from broker.kite_broker import KiteBroker
from broker.paper_broker import PaperBroker, get_paper_broker
from execution.arming import get_arm_state
from execution.order_executor import place_leg_order
from watchlist_store import get_item, update_item


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _broker_for_activation() -> tuple[Broker, str]:
    arm = get_arm_state()
    mode = str(arm.get("mode") or "paper")
    armed = bool(arm.get("armed"))
    if mode == "live":
        if not armed:
            raise RuntimeError(
                "LIVE mode is DISARMED — click ARM on Live Desk to place orders on the exchange"
            )
        return KiteBroker(), "live"
    return get_paper_broker(), "paper"


def _seed_paper_ltp(broker: PaperBroker, exchange: str, tradingsymbol: str) -> float:
    try:
        return float(broker.ltp(exchange, tradingsymbol))
    except RuntimeError:
        pass
    try:
        from kite_auth import get_kite_client

        kite = get_kite_client()
        key = f"{exchange}:{tradingsymbol}"
        px = float(kite.ltp(key)[key]["last_price"])
        broker.set_ltp(exchange, tradingsymbol, px)
        return px
    except Exception:
        px = 1.0
        broker.set_ltp(exchange, tradingsymbol, px)
        return px


def _quote_ltp(exchange: str, tradingsymbol: str) -> float:
    try:
        from kite_auth import get_kite_client

        kite = get_kite_client()
        key = f"{exchange}:{tradingsymbol}"
        return float(kite.ltp(key)[key]["last_price"])
    except Exception:
        return 0.0


def _product_for_item(item: dict[str, Any]) -> str:
    """Kite order product — MIS intraday, NRML carry, CNC equity delivery."""
    explicit = str(item.get("product_type") or item.get("kite_product") or "").upper()
    if explicit in {"MIS", "NRML", "CNC"}:
        return explicit

    system_mode = str(item.get("system_mode") or "Intraday")
    segment = str(item.get("segment") or "")
    exchange = str(item.get("exchange") or "").upper()

    if segment == "equity":
        return "MIS" if system_mode == "Intraday" else "CNC"

    # MCX commodity options are NRML-only on Kite — intraday flat via 3ST force exit
    if exchange == "MCX" and segment in {"option", "options"}:
        return "NRML"

    return "MIS" if system_mode == "Intraday" else "NRML"


def _legs_for_signal(item: dict[str, Any]) -> list[dict[str, Any]]:
    spread = item.get("spread") or {}
    signal = item.get("signal") or "long"
    key = "legs_long" if signal == "long" else "legs_short"
    legs = spread.get(key) or []
    if legs:
        return legs
    if item.get("tradingsymbol") and item.get("exchange"):
        qty = int(item.get("lot_size") or 1)
        if item.get("entry_side"):
            side = str(item["entry_side"]).upper()
        else:
            side = "BUY" if signal == "long" else "SELL"
        return [
            {
                "tradingsymbol": item["tradingsymbol"],
                "exchange": item["exchange"],
                "quantity": qty,
                "side": side,
            }
        ]
    return []


def place_watchlist_entry(item: dict[str, Any], *, allow_active: bool = False) -> dict[str, Any]:
    """Place broker orders and persist entry fields on the watchlist row."""
    item_id = str(item.get("id") or "")
    if not item_id:
        raise RuntimeError("Watchlist item missing id")

    status = item.get("status")
    if status not in {"triggered", "waiting", "active"}:
        raise RuntimeError(f"Cannot enter trade in status '{status}'")
    if status == "active" and not allow_active:
        raise RuntimeError("Trade already active")

    broker, trade_mode = _broker_for_activation()
    product = _product_for_item(item)
    legs = _legs_for_signal(item)
    if not legs:
        raise RuntimeError("No tradable legs on watchlist item")

    order_ids: list[str] = list(item.get("order_ids") or [])
    fills: list[tuple[float, int]] = []

    for leg in legs:
        exchange = str(leg.get("exchange") or item.get("exchange") or "")
        sym = str(leg.get("tradingsymbol") or "")
        qty = int(leg.get("quantity") or item.get("lot_size") or 1)
        if qty <= 0:
            qty = 1
        tx = str(leg.get("side") or ("BUY" if item.get("signal") == "long" else "SELL")).upper()
        tag = f"3ST-WL-{item_id[:8]}"

        if isinstance(broker, PaperBroker):
            _seed_paper_ltp(broker, exchange, sym)

        result = place_leg_order(
            broker,
            {"tradingsymbol": sym, "exchange": exchange, "quantity": qty},
            transaction_type=tx,
            tag=tag,
            product=product,
        )

        if not result.get("ok"):
            raise RuntimeError(result.get("message") or "Order failed")
        if result.get("order_id"):
            order_ids.append(str(result["order_id"]))

        raw = result.get("raw") or {}
        fill_px = float(raw.get("price") or 0)
        if fill_px <= 0:
            fill_px = _quote_ltp(exchange, sym)
        if fill_px <= 0 and isinstance(broker, PaperBroker):
            fill_px = _seed_paper_ltp(broker, exchange, sym)
        fills.append((fill_px, qty))

    total_qty = sum(q for _, q in fills)
    entry_price = (
        round(sum(px * q for px, q in fills) / total_qty, 2) if total_qty else 0.0
    )

    entry_at = item.get("entry_at") or _now()
    patch: dict[str, Any] = {
        "status": "active",
        "entry_price": entry_price,
        "entry_qty": total_qty,
        "entry_at": entry_at,
        "trade_mode": trade_mode,
        "order_ids": list(dict.fromkeys(order_ids)),
        "trail_stop": None,
        "trail_extreme": entry_price if entry_price > 0 else None,
        # Record the product actually used to place every leg above — not a
        # fresh re-derivation (that previously self-referenced `patch` while
        # it was still being built and raised UnboundLocalError on every
        # activation; see tests/test_watchlist_activation.py).
        "kite_product": product,
    }

    try:
        from execution.watchlist_exit_runner import _latest_signals

        signals = _latest_signals({**item, **patch})
        if signals:
            patch["entry_bar_time"] = signals["bar_time"]
            patch["entry_bar_close"] = signals["close"]
    except Exception:
        pass

    return update_item(item_id, patch)


def activate_watchlist_item(item_id: str) -> dict[str, Any]:
    item = get_item(item_id)
    if not item:
        raise KeyError(f"Watchlist item not found: {item_id}")
    if item.get("status") not in {"triggered", "waiting"}:
        raise RuntimeError(f"Cannot activate item in status '{item.get('status')}'")
    return place_watchlist_entry(item)


def _normalize_manual_side(
    side: str | None = None,
    signal: str | None = None,
) -> tuple[str, str]:
    if side:
        s = side.lower()
        if s == "buy":
            return "long", "BUY"
        if s == "sell":
            return "short", "SELL"
    if signal in {"long", "short"}:
        return signal, "BUY" if signal == "long" else "SELL"
    raise ValueError("side must be 'buy' or 'sell'")


def manual_enter_watchlist_item(
    item_id: str,
    *,
    side: str | None = None,
    signal: str | None = None,
) -> dict[str, Any]:
    """Manual push on waiting queue — user picks BUY or SELL."""
    item = get_item(item_id)
    if not item:
        raise KeyError(f"Watchlist item not found: {item_id}")
    if item.get("status") not in {"waiting", "triggered"}:
        raise RuntimeError(f"Cannot enter trade in status '{item.get('status')}'")
    return trigger_manual_side(item_id, side=side, signal=signal)


def trigger_manual_side(
    item_id: str,
    *,
    side: str | None = None,
    signal: str | None = None,
    require_exchange: bool = False,
) -> dict[str, Any]:
    """Apply BUY or SELL on waiting/triggered/active rows (re-places entry if no open leg)."""
    from execution.desk_trades import _find_position
    from execution.live_workflow import validate_live_execution
    from execution.positions_view import build_positions_view

    if require_exchange:
        validate_live_execution()

    item = get_item(item_id)
    if not item:
        raise KeyError(f"Watchlist item not found: {item_id}")
    if str(item.get("entry_mode") or "manual") != "manual":
        raise RuntimeError("Manual BUY/SELL only for manual entry mode")
    if item.get("status") not in {"waiting", "triggered", "active"}:
        raise RuntimeError(f"Cannot trigger side in status '{item.get('status')}'")

    norm_signal, tx_side = _normalize_manual_side(side, signal)

    desk = build_positions_view()
    exchange = str(item.get("exchange") or "")
    tradingsymbol = str(item.get("tradingsymbol") or "")
    pos = _find_position(desk.get("positions") or [], exchange, tradingsymbol)
    if pos is not None:
        open_qty = int(pos.get("quantity") or 0)
        want_buy = tx_side == "BUY"
        if (want_buy and open_qty > 0) or (not want_buy and open_qty < 0):
            patch = {
                "signal": norm_signal,
                "entry_side": tx_side,
                "entry_price": float(pos.get("average_price") or item.get("entry_price") or 0),
                "entry_qty": abs(open_qty),
            }
            return update_item(item_id, patch)
        raise RuntimeError("Close the open leg before switching to the opposite side")

    was_active = item.get("status") == "active"
    updated = update_item(
        item_id,
        {
            "signal": norm_signal,
            "entry_side": tx_side,
            "signal_at": _now(),
            "signal_note": f"Manual {tx_side}",
            "status": "active" if was_active else "triggered",
        },
    )
    return place_watchlist_entry(updated, allow_active=was_active)
