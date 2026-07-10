"""Risk limits for live / paper execution."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    max_qty: float = 500.0
    max_open_positions: int = 2
    max_daily_loss: float = 10_000.0
    max_orders_per_minute: int = 10
    allowed_products: list[str] = field(default_factory=lambda: ["MIS", "NRML"])
    allowed_exchanges: list[str] = field(default_factory=lambda: ["NSE", "BSE", "NFO", "BFO"])


_LIMITS = RiskLimits()
_daily_pnl: float = 0.0
_orders_this_minute: list[float] = []


def get_limits() -> dict:
    return {
        "max_qty": _LIMITS.max_qty,
        "max_open_positions": _LIMITS.max_open_positions,
        "max_daily_loss": _LIMITS.max_daily_loss,
        "max_orders_per_minute": _LIMITS.max_orders_per_minute,
        "allowed_products": _LIMITS.allowed_products,
        "allowed_exchanges": _LIMITS.allowed_exchanges,
        "daily_pnl": _daily_pnl,
    }


def update_limits(**kwargs) -> dict:
    for k, v in kwargs.items():
        if hasattr(_LIMITS, k) and v is not None:
            setattr(_LIMITS, k, v)
    return get_limits()


def record_pnl(delta: float) -> None:
    global _daily_pnl
    _daily_pnl += float(delta)


def check_order(
    *,
    qty: float,
    product: str,
    exchange: str,
    open_positions: int,
) -> None:
    import time

    now = time.time()
    global _orders_this_minute
    _orders_this_minute = [t for t in _orders_this_minute if now - t < 60]
    if len(_orders_this_minute) >= _LIMITS.max_orders_per_minute:
        raise RuntimeError("Risk: max orders per minute exceeded")

    if qty <= 0 or qty > _LIMITS.max_qty:
        raise RuntimeError(f"Risk: qty {qty} outside 1..{_LIMITS.max_qty}")
    if product.upper() not in {p.upper() for p in _LIMITS.allowed_products}:
        raise RuntimeError(f"Risk: product {product} not allowed")
    if exchange.upper() not in {e.upper() for e in _LIMITS.allowed_exchanges}:
        raise RuntimeError(f"Risk: exchange {exchange} not allowed")
    if open_positions >= _LIMITS.max_open_positions:
        raise RuntimeError("Risk: max open positions reached")
    if _daily_pnl <= -abs(_LIMITS.max_daily_loss):
        raise RuntimeError("Risk: max daily loss breached")

    _orders_this_minute.append(now)
