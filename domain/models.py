"""Typed trading domain model for 3ST.

Lightweight, dependency-free dataclasses that replace ad-hoc ``dict[str, Any]``
for the core trading concepts, with fail-fast validation at construction time.

The ``from_kite`` parsers are intentionally lenient about *optional* fields
(prices/quantities that a venue snapshot may legitimately omit or zero out) but
strict about the fields that make a record usable (symbol, side, ids). This lets
reconciliation (#1) skip a single malformed row rather than crash.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from domain.errors import InvalidOrder
from domain.validation import (
    coerce_product,
    coerce_side,
    is_finite_positive,
    validate_price,
    validate_quantity,
    validate_signed_quantity,
)

OrderSide = Literal["BUY", "SELL"]
Product = Literal["MIS", "NRML", "CNC"]
OrderType = Literal["MARKET", "LIMIT", "SL", "SL-M"]

# Kite order statuses considered still working at the venue.
OPEN_ORDER_STATUSES = frozenset({"OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED", "PUT ORDER REQ RECEIVED"})
DEAD_ORDER_STATUSES = frozenset({"COMPLETE", "CANCELLED", "REJECTED", "EXPIRED"})


def _opt_price(value: Any) -> float | None:
    """Return a finite positive price, or None (zeros/NaN/blanks → None)."""
    return float(value) if is_finite_positive(value) else None


def _opt_float(value: Any) -> float | None:
    """Return any finite float (may be negative, e.g. P&L), else None."""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


@dataclass(frozen=True)
class Instrument:
    exchange: str
    tradingsymbol: str
    instrument_token: int | None = None
    lot_size: int | None = None

    def __post_init__(self) -> None:
        exch = str(self.exchange or "").strip().upper()
        sym = str(self.tradingsymbol or "").strip().upper()
        if not exch or not sym:
            raise InvalidOrder(
                f"Instrument requires exchange and tradingsymbol, got "
                f"exchange={self.exchange!r} tradingsymbol={self.tradingsymbol!r}"
            )
        object.__setattr__(self, "exchange", exch)
        object.__setattr__(self, "tradingsymbol", sym)

    @property
    def key(self) -> str:
        """Cache/quote key, e.g. ``NFO:NIFTY25JUL24000CE``."""
        return f"{self.exchange}:{self.tradingsymbol}"

    @classmethod
    def from_kite(cls, row: dict[str, Any]) -> "Instrument":
        tok = row.get("instrument_token")
        lot = row.get("lot_size")
        return cls(
            exchange=str(row.get("exchange") or ""),
            tradingsymbol=str(row.get("tradingsymbol") or ""),
            instrument_token=int(tok) if tok not in (None, "") else None,
            lot_size=int(lot) if lot not in (None, "") else None,
        )


@dataclass(frozen=True)
class Quote:
    """A validated last-traded price. ``last_price`` is always finite & positive."""

    instrument: Instrument
    last_price: float
    ts: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "last_price",
            validate_price(self.last_price, field=f"{self.instrument.key} last_price"),
        )


@dataclass
class Order:
    order_id: str
    instrument: Instrument
    side: OrderSide
    quantity: int
    product: Product = "MIS"
    order_type: str = "MARKET"
    status: str = ""
    price: float | None = None
    average_price: float | None = None
    filled_quantity: int = 0
    tag: str = ""

    @property
    def is_open(self) -> bool:
        return self.status.upper() in OPEN_ORDER_STATUSES

    @property
    def is_3st(self) -> bool:
        return self.tag.upper().startswith("3ST")

    @classmethod
    def from_kite(cls, row: dict[str, Any]) -> "Order":
        instrument = Instrument.from_kite(row)
        side = coerce_side(row.get("transaction_type"))
        # Lenient on qty (venue rows can be odd); strict enough to be usable.
        quantity = validate_quantity(row.get("quantity"), allow_zero=True)
        filled = row.get("filled_quantity")
        return cls(
            order_id=str(row.get("order_id") or ""),
            instrument=instrument,
            side=side,  # type: ignore[arg-type]
            quantity=quantity,
            product=coerce_product(row.get("product"), default="MIS"),  # type: ignore[arg-type]
            order_type=str(row.get("order_type") or "MARKET").upper(),
            status=str(row.get("status") or ""),
            price=_opt_price(row.get("price")),
            average_price=_opt_price(row.get("average_price")),
            filled_quantity=int(filled) if filled not in (None, "") else 0,
            tag=str(row.get("tag") or ""),
        )


@dataclass
class Fill:
    instrument: Instrument
    side: OrderSide
    quantity: int
    price: float
    order_id: str | None = None
    ts: str | None = None

    def __post_init__(self) -> None:
        self.side = coerce_side(self.side)  # type: ignore[assignment]
        self.quantity = validate_quantity(self.quantity)
        self.price = validate_price(self.price, field=f"{self.instrument.key} fill price")


@dataclass
class Position:
    instrument: Instrument
    quantity: int  # signed net (negative = short)
    average_price: float | None = None
    last_price: float | None = None
    pnl: float | None = None
    product: str | None = None

    @property
    def is_open(self) -> bool:
        return self.quantity != 0

    @property
    def direction(self) -> str:
        if self.quantity > 0:
            return "LONG"
        if self.quantity < 0:
            return "SHORT"
        return "FLAT"

    @property
    def net_value(self) -> float | None:
        """abs(qty) * average_price, if a valid average price is known."""
        if self.average_price is None:
            return None
        return abs(self.quantity) * self.average_price

    @classmethod
    def from_kite(cls, row: dict[str, Any]) -> "Position":
        return cls(
            instrument=Instrument.from_kite(row),
            quantity=validate_signed_quantity(row.get("quantity")),
            average_price=_opt_price(row.get("average_price")),
            last_price=_opt_price(row.get("last_price")),
            pnl=_opt_float(row.get("pnl")),
            product=(str(row["product"]).upper() if row.get("product") else None),
        )


def open_positions_from_kite(rows: list[dict[str, Any]]) -> list[Position]:
    """Parse Kite position rows into open :class:`Position` objects.

    Malformed rows are skipped (reconciliation should not crash on one bad row).
    """
    out: list[Position] = []
    for row in rows or []:
        try:
            pos = Position.from_kite(row)
        except InvalidOrder:
            continue
        if pos.is_open:
            out.append(pos)
    return out


def open_orders_from_kite(rows: list[dict[str, Any]], *, only_3st: bool = True) -> list[Order]:
    """Parse Kite order rows into working :class:`Order` objects (optionally 3ST-tagged)."""
    out: list[Order] = []
    for row in rows or []:
        try:
            order = Order.from_kite(row)
        except InvalidOrder:
            continue
        if only_3st and not order.is_3st:
            continue
        if order.is_open:
            out.append(order)
    return out
