"""Fail-fast validation helpers for market data and order fields.

Policy (mirrors NautilusTrader's data-integrity stance):

- ``validate_*`` raise :class:`InvalidMarketData` / :class:`InvalidOrder` and are
  used for values that drive trading decisions — better to abort a scan than act
  on a corrupt price.
- ``safe_price`` returns ``None`` instead of raising and is used at ingestion
  boundaries (WebSocket / REST feeds) so one bad tick never crashes the feed and
  never poisons the cache.
"""

from __future__ import annotations

import math
from typing import Any

from domain.errors import InvalidMarketData, InvalidOrder

_VALID_SIDES = {"BUY", "SELL"}
_VALID_PRODUCTS = {"MIS", "NRML", "CNC"}


def is_finite_positive(value: Any) -> bool:
    """True only for a finite, strictly-positive real number."""
    if value is None or isinstance(value, bool):
        return False
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(num) and num > 0.0


def safe_price(value: Any) -> float | None:
    """Coerce to a valid price, or ``None`` if invalid (reject-and-skip).

    Use at feed ingestion boundaries where a bad tick should be dropped rather
    than raising.
    """
    return float(value) if is_finite_positive(value) else None


def validate_price(value: Any, *, field: str = "price") -> float:
    """Return a valid price or raise :class:`InvalidMarketData` (fail-fast)."""
    price = safe_price(value)
    if price is None:
        raise InvalidMarketData(f"{field} must be a finite positive number, got {value!r}")
    return price


def validate_optional_price(value: Any, *, field: str = "price") -> float | None:
    """Like :func:`validate_price` but allows ``None``/empty (returns ``None``).

    Any *present* value must still be valid, otherwise raises.
    """
    if value is None or value == "":
        return None
    return validate_price(value, field=field)


def validate_quantity(value: Any, *, field: str = "quantity", allow_zero: bool = False) -> int:
    """Return a non-negative integer quantity or raise :class:`InvalidOrder`.

    Signed quantities (e.g. Kite net positions) should use :func:`validate_signed_quantity`.
    """
    if isinstance(value, bool) or value is None:
        raise InvalidOrder(f"{field} must be an integer, got {value!r}")
    try:
        qty = int(value)
    except (TypeError, ValueError):
        raise InvalidOrder(f"{field} must be an integer, got {value!r}") from None
    if qty < 0 or (qty == 0 and not allow_zero):
        raise InvalidOrder(f"{field} must be {'>= 0' if allow_zero else '> 0'}, got {qty}")
    return qty


def validate_signed_quantity(value: Any, *, field: str = "quantity") -> int:
    """Return an integer quantity that may be negative (net position size)."""
    if isinstance(value, bool) or value is None:
        raise InvalidOrder(f"{field} must be an integer, got {value!r}")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise InvalidOrder(f"{field} must be an integer, got {value!r}") from None


def coerce_side(value: Any, *, field: str = "transaction_type") -> str:
    """Normalize an order side to BUY/SELL or raise."""
    side = str(value or "").strip().upper()
    if side not in _VALID_SIDES:
        raise InvalidOrder(f"{field} must be one of {sorted(_VALID_SIDES)}, got {value!r}")
    return side


def coerce_product(value: Any, *, field: str = "product", default: str | None = None) -> str:
    """Normalize a product type to MIS/NRML/CNC, or raise / use default."""
    raw = str(value or "").strip().upper()
    if not raw and default is not None:
        return default
    if raw not in _VALID_PRODUCTS:
        raise InvalidOrder(f"{field} must be one of {sorted(_VALID_PRODUCTS)}, got {value!r}")
    return raw
