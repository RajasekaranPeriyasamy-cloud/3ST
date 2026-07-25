"""Order Router — turns a SignalIntent into an order, with a ledger row.

Part of the Phase 3 execution-architecture plan (see
docs/CONVERSATION_SUMMARY.md). This module is deliberately thin: it does
**not** re-implement risk limits or the ARM gate — those already live in
``risk/limits.py`` (via ``execution.order_executor``) and
``execution.arming.require_armed_for_live`` (enforced inside
``broker.kite_broker.KiteBroker.place_order``/``cancel_order``). Routing
through here changes nothing about when an order is allowed to fire; it adds:

1. A same-day duplicate-tag guard, so the same signal can't fire two orders
   for the same leg on the same day (Phase 3 acceptance criterion).
2. Automatic ``execution/position_ledger.py`` bookkeeping (pending -> open /
   closed / error), so callers don't hand-roll their own state tracking.

Callers (Rolling Straddle, Watchlist, Premium Book, Survivor, Wave) migrate to
this one at a time — this file works standalone today against any ``Broker``
and doesn't require touching the existing runners' state files.
"""

from __future__ import annotations

from typing import Any

from broker.base import Broker
from execution import position_ledger as ledger
from execution.order_executor import place_leg_order, place_leg_to_target
from execution.signal_bus import SignalIntent


def submit_intent(broker: Broker, intent: SignalIntent) -> dict[str, Any]:
    """Execute one signal intent through the shared risk/ARM-gated order path.

    Returns the same shape ``order_executor`` returns (``ok``, ``order_id``,
    ``message``, ``raw``), plus ``leg_id`` and ``ledger`` (the updated row).
    A duplicate same-day ``enter`` for a leg that already has a pending/open
    row under this tag is rejected before touching the broker at all.
    """
    tag = intent.tag()

    if intent.kind == "enter" and ledger.has_open_tag(tag):
        return {
            "ok": False,
            "order_id": None,
            "message": f"Duplicate entry blocked — tag {tag} already placed today",
            "raw": {"duplicate": True},
            "leg_id": intent.leg_id,
            "ledger": ledger.get(intent.leg_id),
        }

    ledger.upsert_pending(intent)

    leg = {
        "exchange": intent.exchange,
        "tradingsymbol": intent.tradingsymbol,
        "quantity": intent.quantity or 0,
    }

    try:
        if intent.target_qty is not None:
            result = place_leg_to_target(
                broker,
                leg,
                target_qty=intent.target_qty,
                order_type=intent.order_type,
                price=intent.price,
                tag=tag,
                product=intent.product,
            )
        else:
            result = place_leg_order(
                broker,
                leg,
                transaction_type=intent.transaction_type,  # type: ignore[arg-type]
                order_type=intent.order_type,
                price=intent.price,
                tag=tag,
                product=intent.product,
            )
    except Exception as exc:  # risk gate / broker raised — record and re-raise nothing
        row = ledger.mark_error(intent.leg_id, str(exc))
        return {
            "ok": False,
            "order_id": None,
            "message": str(exc),
            "raw": None,
            "leg_id": intent.leg_id,
            "ledger": row,
        }

    if not result.get("ok"):
        row = ledger.mark_error(intent.leg_id, str(result.get("message") or "order failed"))
    elif result.get("raw", {}).get("noop"):
        # place_leg_to_target found the broker already at target — nothing changed.
        row = ledger.get(intent.leg_id) or {}
    elif intent.kind == "exit" or intent.target_qty == 0:
        row = ledger.mark_closed(
            intent.leg_id,
            exit_price=intent.price,
            order_id=result.get("order_id"),
            tag=tag,
        )
    else:
        resolved_qty = intent.quantity
        if resolved_qty is None and intent.target_qty is not None:
            resolved_qty = intent.target_qty
        row = ledger.mark_open(
            intent.leg_id,
            quantity=resolved_qty or 0,
            entry_price=intent.price,
            order_id=result.get("order_id"),
            tag=tag,
        )

    return {**result, "leg_id": intent.leg_id, "ledger": row}
