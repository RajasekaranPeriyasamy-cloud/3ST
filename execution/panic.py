"""Emergency panic — cancel pending 3ST orders and optionally square off active trades."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Generator

from execution.arming import disarm, get_arm_state
from utils.logging import get_logger, log_event

logger = get_logger("panic")

_panic_active: ContextVar[bool] = ContextVar("panic_active", default=False)


def is_panic_active() -> bool:
    return _panic_active.get()


@contextmanager
def panic_mode() -> Generator[None, None, None]:
    token = _panic_active.set(True)
    try:
        yield
    finally:
        _panic_active.reset(token)


def _cancel_open_3st_orders() -> list[dict[str, Any]]:
    from kite_auth import get_kite_client
    from kite_client import session_status

    if not session_status().get("authenticated"):
        return []

    kite = get_kite_client()
    cancelled: list[dict[str, Any]] = []
    open_statuses = {"OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED"}

    for order in kite.orders():
        tag = str(order.get("tag") or "")
        status = str(order.get("status") or "")
        if not tag.startswith("3ST"):
            continue
        if status not in open_statuses:
            continue
        order_id = str(order.get("order_id") or "")
        if not order_id:
            continue
        try:
            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
            row = {
                "order_id": order_id,
                "tradingsymbol": order.get("tradingsymbol"),
                "status": status,
                "tag": tag,
            }
            cancelled.append(row)
            log_event(
                logger,
                logging.INFO,
                "panic_cancel_order",
                order_id=order_id,
                tradingsymbol=order.get("tradingsymbol"),
                tag=tag,
            )
        except Exception as exc:
            log_event(
                logger,
                logging.WARNING,
                "panic_cancel_failed",
                order_id=order_id,
                error=str(exc),
            )
    return cancelled


def run_panic(*, cancel_orders: bool = True, close_positions: bool = True) -> dict[str, Any]:
    """
    1. Square off active watchlist trades (live/paper per trade_mode).
    2. Cancel open exchange orders tagged 3ST*.
    3. DISARM the desk.
    """
    from watchlist_store import list_items

    from execution.watchlist_close import close_watchlist_trade

    arm_before = get_arm_state()
    closed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    with panic_mode():
        if close_positions:
            for item in list_items("active"):
                item_id = str(item.get("id") or "")
                if not item_id:
                    continue
                try:
                    updated = close_watchlist_trade(item_id, "panic")
                    closed.append(updated)
                    log_event(
                        logger,
                        logging.INFO,
                        "panic_close_trade",
                        item_id=item_id,
                        tradingsymbol=item.get("tradingsymbol"),
                        exchange=item.get("exchange"),
                    )
                except Exception as exc:
                    errors.append({"id": item_id, "error": str(exc)})
                    log_event(
                        logger,
                        logging.WARNING,
                        "panic_close_failed",
                        item_id=item_id,
                        error=str(exc),
                    )

        cancelled = _cancel_open_3st_orders() if cancel_orders else []

    disarmed = disarm()
    log_event(
        logger,
        logging.WARNING,
        "panic_complete",
        closed=len(closed),
        cancelled=len(cancelled) if cancel_orders else 0,
        errors=len(errors),
        was_armed=bool(arm_before.get("armed")),
    )

    return {
        "ok": True,
        "disarmed": disarmed,
        "closed": closed,
        "cancelled_orders": cancelled if cancel_orders else [],
        "errors": errors,
    }
