"""Place option orders through paper or Kite broker with risk checks."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from broker.base import Broker, OrderRequest
from broker.execution_support import (
    acquire_symbol_lock,
    get_cached_positions,
    invalidate_position_cache,
    net_position_qty,
    plan_order_to_target,
)
from execution.arming import get_arm_state
from risk import limits as risk_limits


def _broker_name(broker: Broker) -> str:
    name = type(broker).__name__.lower().replace("broker", "")
    return name or "kite"


def _read_net_qty(broker: Broker, leg: dict[str, Any], product: str) -> int:
    if hasattr(broker, "net_qty"):
        return int(broker.net_qty(str(leg["exchange"]), str(leg["tradingsymbol"]), product))  # type: ignore[attr-defined]
    rows = get_cached_positions(broker, loader=broker.positions)
    return net_position_qty(
        rows,
        tradingsymbol=str(leg["tradingsymbol"]),
        exchange=str(leg["exchange"]),
        product=product,
    )


def _open_position_count(broker: Broker) -> int:
    try:
        positions = broker.positions()
    except Exception:
        return 0
    return sum(1 for p in positions if int(p.get("quantity") or 0) != 0)


def _is_closing_leg(broker: Broker, leg: dict[str, Any], transaction_type: str) -> bool:
    """True when this order reduces or closes an existing leg (exits skip open-position cap)."""
    sym = str(leg.get("tradingsymbol") or "")
    exch = str(leg.get("exchange") or "")
    tx = transaction_type.upper()
    try:
        for pos in broker.positions():
            if str(pos.get("tradingsymbol")) != sym or str(pos.get("exchange")) != exch:
                continue
            qty = int(pos.get("quantity") or 0)
            if qty > 0 and tx == "SELL":
                return True
            if qty < 0 and tx == "BUY":
                return True
    except Exception:
        pass
    return False


def _is_reducing_toward_target(current_qty: int, target_qty: int) -> bool:
    if current_qty == 0:
        return False
    if target_qty == 0:
        return True
    if current_qty > 0 and target_qty < current_qty:
        return True
    if current_qty < 0 and target_qty > current_qty:
        return True
    return abs(target_qty) < abs(current_qty)


def _log_order_latency(
    *,
    leg: dict[str, Any],
    order_type: str,
    transaction_type: str,
    validation_ms: float,
    rtt_ms: float,
    total_ms: float,
    result: Any,
    broker: Broker,
) -> None:
    try:
        from execution.latency_log import log_latency

        log_latency(
            order_id=result.order_id,
            symbol=str(leg["tradingsymbol"]),
            order_type=order_type,
            transaction_type=transaction_type,
            latencies={
                "validation": validation_ms,
                "rtt": rtt_ms,
                "overhead": total_ms - rtt_ms,
                "total": total_ms,
            },
            status="SUCCESS" if result.ok else "FAILED",
            broker=_broker_name(broker),
            error=None if result.ok else result.message,
        )
    except Exception:
        pass


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
    sym = str(leg["tradingsymbol"])

    lock = acquire_symbol_lock(exchange, sym, product_u)
    with lock:
        t0 = time.perf_counter()
        risk_limits.check_order(
            qty=float(qty),
            product=product_u,
            exchange=exchange,
            open_positions=_open_position_count(broker),
            is_closing=_is_closing_leg(broker, leg, transaction_type),
        )

        req = OrderRequest(
            tradingsymbol=sym,
            exchange=exchange,
            transaction_type=transaction_type.upper(),  # type: ignore[arg-type]
            quantity=qty,
            product=product_u,  # type: ignore[arg-type]
            order_type=order_type,
            price=price,
            tag=tag[:20],
        )
        t1 = time.perf_counter()
        result = broker.place_order(req)
        t2 = time.perf_counter()

    _log_order_latency(
        leg=leg,
        order_type=order_type,
        transaction_type=transaction_type.upper(),
        validation_ms=(t1 - t0) * 1000.0,
        rtt_ms=(t2 - t1) * 1000.0,
        total_ms=(t2 - t0) * 1000.0,
        result=result,
        broker=broker,
    )

    return {
        "ok": result.ok,
        "order_id": result.order_id,
        "message": result.message,
        "raw": result.raw,
    }


def place_leg_to_target(
    broker: Broker,
    leg: dict[str, Any],
    *,
    target_qty: int,
    order_type: str = "MARKET",
    price: float | None = None,
    tag: str = "3ST",
    product: str = "MIS",
) -> dict[str, Any]:
    """
    Smart order: move signed net position to ``target_qty`` using broker truth.

    Negative target = short, positive = long, zero = flat.
    """
    exchange = str(leg["exchange"])
    sym = str(leg["tradingsymbol"])
    product_u = product.upper()

    lock = acquire_symbol_lock(exchange, sym, product_u)
    with lock:
        t0 = time.perf_counter()
        current_qty = _read_net_qty(broker, leg, product_u)
        plan = plan_order_to_target(current_qty, int(target_qty))
        if plan.noop:
            return {
                "ok": True,
                "order_id": None,
                "message": "Position already at target",
                "raw": {
                    "noop": True,
                    "current_qty": current_qty,
                    "target_qty": int(target_qty),
                },
            }

        assert plan.transaction_type is not None
        risk_limits.check_order(
            qty=float(plan.quantity),
            product=product_u,
            exchange=exchange,
            open_positions=_open_position_count(broker),
            is_closing=_is_reducing_toward_target(current_qty, int(target_qty)),
        )

        req = OrderRequest(
            tradingsymbol=sym,
            exchange=exchange,
            transaction_type=plan.transaction_type,  # type: ignore[arg-type]
            quantity=plan.quantity,
            product=product_u,  # type: ignore[arg-type]
            order_type=order_type,
            price=price,
            tag=tag[:20],
        )
        t1 = time.perf_counter()
        result = broker.place_order(req)
        t2 = time.perf_counter()
        invalidate_position_cache(broker)

    _log_order_latency(
        leg=leg,
        order_type=order_type,
        transaction_type=plan.transaction_type,
        validation_ms=(t1 - t0) * 1000.0,
        rtt_ms=(t2 - t1) * 1000.0,
        total_ms=(t2 - t0) * 1000.0,
        result=result,
        broker=broker,
    )

    raw = dict(result.raw or {})
    raw.update(
        {
            "current_qty": current_qty,
            "target_qty": int(target_qty),
            "delta_qty": plan.quantity,
            "transaction_type": plan.transaction_type,
        }
    )
    return {
        "ok": result.ok,
        "order_id": result.order_id,
        "message": result.message,
        "raw": raw,
    }


def order_tag(leg_key: str, kind: str) -> str:
    """Idempotent-ish daily tag: 3ST-CE-20260710-entry."""
    d = date.today().strftime("%Y%m%d")
    return f"3ST-{leg_key.upper()}-{d}-{kind}"[:20]
