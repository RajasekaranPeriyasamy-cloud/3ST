"""Risk limits for live / paper execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from settings import data_dir

LIMITS_FILE = data_dir() / "risk_limits.json"


@dataclass
class RiskLimits:
    max_qty: float = 500.0
    max_open_positions: int = 6
    max_daily_loss: float = 10_000.0
    max_orders_per_minute: int = 10
    allowed_products: list[str] = field(default_factory=lambda: ["MIS", "NRML"])
    allowed_exchanges: list[str] = field(default_factory=lambda: ["NSE", "BSE", "NFO", "BFO", "MCX", "NCO"])


_PERSIST_KEYS = ("max_qty", "max_open_positions", "max_daily_loss", "max_orders_per_minute")

_LIMITS = RiskLimits()
_daily_pnl: float = 0.0
_orders_this_minute: list[float] = []


def _persist_limits() -> None:
    payload = {k: getattr(_LIMITS, k) for k in _PERSIST_KEYS}
    LIMITS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_persisted_limits() -> None:
    """Restore user risk limits from disk (survives API restart / uvicorn reload)."""
    if not LIMITS_FILE.exists():
        return
    try:
        raw = json.loads(LIMITS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, dict):
        return
    for key in _PERSIST_KEYS:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if key in {"max_open_positions", "max_orders_per_minute"}:
            value = int(value)
        elif key in {"max_qty", "max_daily_loss"}:
            value = float(value)
        setattr(_LIMITS, key, value)


load_persisted_limits()


def get_limits() -> dict:
    return {
        "max_qty": _LIMITS.max_qty,
        "max_open_positions": _LIMITS.max_open_positions,
        "max_daily_loss": _LIMITS.max_daily_loss,
        "max_orders_per_minute": _LIMITS.max_orders_per_minute,
        "allowed_products": list(_LIMITS.allowed_products),
        "allowed_exchanges": list(_LIMITS.allowed_exchanges),
        "daily_pnl": _daily_pnl,
    }


def update_limits(**kwargs) -> dict:
    for k, v in kwargs.items():
        if hasattr(_LIMITS, k) and v is not None:
            setattr(_LIMITS, k, v)
    if any(k in kwargs and kwargs[k] is not None for k in _PERSIST_KEYS):
        _persist_limits()
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
    is_closing: bool = False,
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
    if not is_closing and open_positions >= _LIMITS.max_open_positions:
        raise RuntimeError(
            f"Risk: max open positions reached ({open_positions}/{_LIMITS.max_open_positions})"
        )
    if _daily_pnl <= -abs(_LIMITS.max_daily_loss):
        raise RuntimeError("Risk: max daily loss breached")

    _orders_this_minute.append(now)
