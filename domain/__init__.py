"""Typed trading domain model + fail-fast validation for 3ST.

See ``docs/NAUTILUS_IMPROVEMENTS.md`` §4 for the design and rationale.
"""

from __future__ import annotations

from domain.errors import DomainError, InvalidMarketData, InvalidOrder
from domain.models import (
    DEAD_ORDER_STATUSES,
    OPEN_ORDER_STATUSES,
    Fill,
    Instrument,
    Order,
    OrderSide,
    OrderType,
    Position,
    Product,
    Quote,
    open_orders_from_kite,
    open_positions_from_kite,
)
from domain.validation import (
    coerce_product,
    coerce_side,
    is_finite_positive,
    safe_price,
    validate_optional_price,
    validate_price,
    validate_quantity,
    validate_signed_quantity,
)

__all__ = [
    "DomainError",
    "InvalidMarketData",
    "InvalidOrder",
    "Instrument",
    "Quote",
    "Order",
    "Fill",
    "Position",
    "OrderSide",
    "OrderType",
    "Product",
    "OPEN_ORDER_STATUSES",
    "DEAD_ORDER_STATUSES",
    "open_orders_from_kite",
    "open_positions_from_kite",
    "is_finite_positive",
    "safe_price",
    "validate_price",
    "validate_optional_price",
    "validate_quantity",
    "validate_signed_quantity",
    "coerce_side",
    "coerce_product",
]
