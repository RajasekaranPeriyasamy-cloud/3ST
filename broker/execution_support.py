"""Per-symbol order locks and position-book cache (OpenAlgo-style execution primitives)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

_POSITION_CACHE_TTL_SEC = 1.0

_symbol_locks: dict[str, threading.Lock] = {}
_symbol_locks_guard = threading.Lock()

_position_cache: dict[str, dict[str, Any]] = {}
_position_cache_guard = threading.Lock()


def symbol_lock_key(exchange: str, tradingsymbol: str, product: str) -> str:
    return f"{exchange.upper()}:{tradingsymbol.upper()}:{product.upper()}"


def acquire_symbol_lock(exchange: str, tradingsymbol: str, product: str) -> threading.Lock:
    key = symbol_lock_key(exchange, tradingsymbol, product)
    with _symbol_locks_guard:
        lock = _symbol_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _symbol_locks[key] = lock
        return lock


def position_cache_key(broker: Any) -> str:
    return type(broker).__name__


def get_cached_positions(broker: Any, *, loader) -> list[dict[str, Any]]:
    """Return broker net positions; cache ~1s per broker class to cut REST churn."""
    key = position_cache_key(broker)
    now = time.monotonic()
    with _position_cache_guard:
        entry = _position_cache.get(key)
        if entry and (now - float(entry["ts"])) < _POSITION_CACHE_TTL_SEC:
            return list(entry["rows"])
    rows = loader()
    with _position_cache_guard:
        _position_cache[key] = {"ts": now, "rows": list(rows)}
    return rows


def invalidate_position_cache(broker: Any | None = None) -> None:
    with _position_cache_guard:
        if broker is None:
            _position_cache.clear()
        else:
            _position_cache.pop(position_cache_key(broker), None)


def net_position_qty(
    positions: list[dict[str, Any]],
    *,
    tradingsymbol: str,
    exchange: str,
    product: str | None = None,
) -> int:
    sym = tradingsymbol.upper()
    exch = exchange.upper()
    prod = product.upper() if product else None
    for row in positions:
        if str(row.get("tradingsymbol") or "").upper() != sym:
            continue
        if str(row.get("exchange") or "").upper() != exch:
            continue
        if prod and str(row.get("product") or "").upper() != prod:
            continue
        return int(row.get("quantity") or 0)
    return 0


@dataclass(frozen=True)
class TargetOrderPlan:
    """How to move from current signed qty to target signed qty."""

    current_qty: int
    target_qty: int
    transaction_type: str | None
    quantity: int
    noop: bool = False


def plan_order_to_target(current_qty: int, target_qty: int) -> TargetOrderPlan:
    delta = int(target_qty) - int(current_qty)
    if delta == 0:
        return TargetOrderPlan(
            current_qty=current_qty,
            target_qty=target_qty,
            transaction_type=None,
            quantity=0,
            noop=True,
        )
    if delta > 0:
        return TargetOrderPlan(
            current_qty=current_qty,
            target_qty=target_qty,
            transaction_type="BUY",
            quantity=abs(delta),
        )
    return TargetOrderPlan(
        current_qty=current_qty,
        target_qty=target_qty,
        transaction_type="SELL",
        quantity=abs(delta),
    )
