"""Place option orders through paper or Kite broker with risk checks."""

from __future__ import annotations

from datetime import date
from typing import Any

from broker.base import Broker, OrderRequest
from execution.arming import get_arm_state
from risk import limits as risk_limits


def _open_position_count(broker: Broker) -> int:
    try:
        positions = broker.positions()
    except Exception:
        return 0
    return sum(1 for p in positions if int(p.get("quantity") or 0) != 0)


def place_leg_order(
    broker: Broker,
    leg: dict[str, Any],
    *,
    transaction_type: str,
    order_type: str = "MARKET",
    price: float | None = None,
    tag: str = "3ST",
    product: str = "MIS",
) -> dict[str, Any]:
    """Place a single option leg order after risk gate."""
    qty = int(leg.get("quantity") or 0)
    exchange = str(leg["exchange"])
    product_u = product.upper()

    risk_limits.check_order(
        qty=float(qty),
        product=product_u,
        exchange=exchange,
        open_positions=_open_position_count(broker),
    )

    req = OrderRequest(
        tradingsymbol=str(leg["tradingsymbol"]),
        exchange=exchange,
        transaction_type=transaction_type.upper(),  # type: ignore[arg-type]
        quantity=qty,
        product=product_u,  # type: ignore[arg-type]
        order_type=order_type,
        price=price,
        tag=tag[:20],
    )
    result = broker.place_order(req)
    return {
        "ok": result.ok,
        "order_id": result.order_id,
        "message": result.message,
        "raw": result.raw,
    }


def order_tag(leg_key: str, kind: str) -> str:
    """Idempotent-ish daily tag: 3ST-CE-20260710-entry."""
    d = date.today().strftime("%Y%m%d")
    return f"3ST-{leg_key.upper()}-{d}-{kind}"[:20]
