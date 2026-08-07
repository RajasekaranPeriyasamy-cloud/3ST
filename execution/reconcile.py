"""State reconciliation — treat the broker as the source of truth.

NAUTILUS_IMPROVEMENTS #1 (crash-only recovery). On startup and periodically while
live, we compare the local Live-Desk state (active watchlist trades) against the
broker's actual open positions and working ``3ST*`` orders, then repair drift:

- Local active, broker flat, no working order  -> mark closed locally (broker wins).
- Local active, broker open                    -> refresh entry qty/price from broker.
- Local active, working order but no position   -> leave active (fill still pending).
- Broker open, no local record                  -> report as orphan (optionally adopt).

This module never places or cancels orders at the venue; it only reads broker
state and repairs *local* records. It is safe to run repeatedly (idempotent).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from broker.base import Broker
from domain import open_orders_from_kite, open_positions_from_kite
from execution.arming import get_arm_state
from utils.logging import get_logger, log_event
from watchlist_store import add_item, list_items, update_item

logger = get_logger("reconcile")

# Throttle for the periodic scheduler hook.
_last_run_mono: float = 0.0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _item_key(item: dict[str, Any]) -> str:
    exch = str(item.get("exchange") or "").strip().upper()
    sym = str(item.get("tradingsymbol") or "").strip().upper()
    return f"{exch}:{sym}" if exch and sym else ""


def _item_is_live(item: dict[str, Any], global_mode: str) -> bool:
    """A trade is reconciled against Kite only if it is a live trade."""
    tm = str(item.get("trade_mode") or "").strip().lower()
    if tm == "live":
        return True
    if tm == "paper":
        return False
    return global_mode == "live"


def reconcile_from_broker(
    broker: Broker,
    *,
    items: list[dict[str, Any]],
    global_mode: str = "live",
    apply_changes: bool = True,
    adopt_orphans: bool = False,
) -> dict[str, Any]:
    """Core reconciliation against a broker's open positions / working orders.

    Broker-agnostic so it can be unit-tested with a fake broker. Only ``items``
    that qualify as live trades are considered for closing.
    """
    report: dict[str, Any] = {
        "ok": True,
        "checked_at": _now(),
        "mode": global_mode,
        "apply_changes": apply_changes,
        "adopt_orphans": adopt_orphans,
        "broker_open_positions": 0,
        "broker_open_3st_orders": 0,
        "matched": [],
        "pending_orders": [],
        "updated": [],
        "closed_stale": [],
        "orphan_positions": [],
        "adopted": [],
        "errors": [],
    }

    report["close_skipped"] = []

    positions_ok = True
    try:
        positions = open_positions_from_kite(broker.positions())
    except Exception as exc:
        positions_ok = False
        report["ok"] = False
        report["errors"].append({"stage": "positions", "error": str(exc)})
        positions = []

    # SAFETY: without a trustworthy positions snapshot we cannot decide anything.
    # A transient broker error must never be read as "broker flat" (which would
    # close every live trade). Abort with no mutations.
    if not positions_ok:
        report["aborted"] = "could not read broker positions — no changes made"
        return report

    orders_ok = True
    try:
        orders = open_orders_from_kite(broker.orders(), only_3st=True)
    except Exception as exc:
        orders_ok = False
        report["ok"] = False
        report["errors"].append({"stage": "orders", "error": str(exc)})
        orders = []

    report["broker_open_positions"] = len(positions)
    report["broker_open_3st_orders"] = len(orders)

    pos_by_key = {p.instrument.key: p for p in positions}
    order_keys = {o.instrument.key for o in orders}

    live_items = [i for i in items if _item_is_live(i, global_mode)]
    active_keys = {k for k in (_item_key(i) for i in live_items) if k}

    for item in live_items:
        item_id = str(item.get("id") or "")
        key = _item_key(item)
        if not key:
            report["errors"].append({"id": item_id, "error": "missing exchange/tradingsymbol"})
            continue

        pos = pos_by_key.get(key)
        if pos is not None:
            report["matched"].append({"id": item_id, "key": key, "quantity": pos.quantity})
            updates: dict[str, Any] = {}
            broker_qty = abs(pos.quantity)
            if broker_qty and int(item.get("entry_qty") or 0) != broker_qty:
                updates["entry_qty"] = broker_qty
            if pos.average_price is not None:
                cur = item.get("entry_price")
                cur_f = float(cur) if cur not in (None, "", 0) else None
                if cur_f is None or abs(cur_f - pos.average_price) > 0.01:
                    updates["entry_price"] = round(pos.average_price, 2)
            if updates and apply_changes:
                try:
                    update_item(item_id, updates)
                    report["updated"].append({"id": item_id, "key": key, **updates})
                except Exception as exc:
                    report["errors"].append({"id": item_id, "error": str(exc)})
            elif updates:
                report["updated"].append({"id": item_id, "key": key, "would_set": updates})
            continue

        if key in order_keys:
            report["pending_orders"].append({"id": item_id, "key": key})
            continue

        # SAFETY: if we couldn't read the order book, we can't be sure there is
        # no working order — never close on an incomplete picture.
        if not orders_ok:
            report["close_skipped"].append({"id": item_id, "key": key, "reason": "orders unavailable"})
            continue

        # Broker flat and no working 3ST order -> stale local active trade.
        entry = {
            "id": item_id,
            "key": key,
            "signal": item.get("signal"),
            "entry_price": item.get("entry_price"),
        }
        if apply_changes:
            try:
                update_item(
                    item_id,
                    {
                        "status": "closed",
                        "signal": None,
                        "exit_at": _now(),
                        "exit_reason": "reconcile: broker flat",
                    },
                )
                log_event(
                    logger,
                    logging.INFO,
                    "reconcile_closed_stale",
                    item_id=item_id,
                    key=key,
                )
            except Exception as exc:
                report["errors"].append({"id": item_id, "error": str(exc)})
                continue
        report["closed_stale"].append(entry)

    # Orphans: broker positions with no local active record.
    for key, pos in pos_by_key.items():
        if key in active_keys:
            continue
        orphan = {
            "key": key,
            "exchange": pos.instrument.exchange,
            "tradingsymbol": pos.instrument.tradingsymbol,
            "quantity": pos.quantity,
            "average_price": pos.average_price,
            "direction": pos.direction,
        }
        if adopt_orphans and apply_changes:
            try:
                created = add_item(
                    {
                        "exchange": pos.instrument.exchange,
                        "tradingsymbol": pos.instrument.tradingsymbol,
                        "trade_mode": "live",
                    }
                )
                update_item(
                    str(created["id"]),
                    {
                        "status": "active",
                        "signal": "short" if pos.quantity < 0 else "long",
                        "entry_side": "sell" if pos.quantity < 0 else "buy",
                        "entry_qty": abs(pos.quantity),
                        "entry_price": round(pos.average_price, 2) if pos.average_price else None,
                        "entry_mode": "reconciled",
                        "reconciled": True,
                        "entry_at": _now(),
                    },
                )
                orphan["adopted_id"] = created["id"]
                report["adopted"].append(orphan)
                log_event(
                    logger,
                    logging.WARNING,
                    "reconcile_adopted_orphan",
                    key=key,
                    item_id=created["id"],
                    quantity=pos.quantity,
                )
            except Exception as exc:
                report["errors"].append({"key": key, "error": str(exc)})
                report["orphan_positions"].append(orphan)
        else:
            report["orphan_positions"].append(orphan)

    return report


def reconcile_live_desk(
    *,
    apply_changes: bool = True,
    adopt_orphans: bool = False,
) -> dict[str, Any]:
    """Reconcile the live Kite desk. Skips cleanly when no Kite session/live mode."""
    from broker.kite_broker import KiteBroker
    from kite_auth import session_status

    state = get_arm_state()
    mode = str(state.get("mode") or "paper")

    if mode != "live":
        return {"ok": True, "skipped": True, "reason": "paper mode", "checked_at": _now(), "mode": mode}

    if not session_status().get("authenticated"):
        return {"ok": True, "skipped": True, "reason": "no kite session", "checked_at": _now(), "mode": mode}

    items = list_items("active")
    report = reconcile_from_broker(
        KiteBroker(),
        items=items,
        global_mode=mode,
        apply_changes=apply_changes,
        adopt_orphans=adopt_orphans,
    )
    report["skipped"] = False
    return report


def maybe_reconcile_periodic(min_interval_sec: float = 60.0) -> dict[str, Any] | None:
    """Scheduler hook: reconcile at most once per ``min_interval_sec`` while live.

    Returns the report if it ran, else ``None`` (throttled / not applicable).
    """
    global _last_run_mono
    if str(get_arm_state().get("mode") or "paper") != "live":
        return None
    if not list_items("active"):
        return None
    now = time.monotonic()
    if now - _last_run_mono < max(15.0, min_interval_sec):
        return None
    _last_run_mono = now
    try:
        return reconcile_live_desk(apply_changes=True, adopt_orphans=False)
    except Exception as exc:
        log_event(logger, logging.WARNING, "reconcile_periodic_failed", error=str(exc))
        return None
