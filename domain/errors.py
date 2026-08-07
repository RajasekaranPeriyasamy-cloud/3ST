"""Domain-level exceptions for 3ST (fail-fast on invalid trading data)."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for all 3ST domain errors."""


class InvalidMarketData(DomainError):
    """Raised when market data (price/quote) violates a fundamental invariant.

    Examples: NaN / infinite / non-positive price, missing last_price where one
    is required for a trading decision. Corrupt market data is worse than no
    data — a single bad price can cascade into a wrong exit or position size.
    """


class InvalidOrder(DomainError):
    """Raised when an order/position field violates a fundamental invariant.

    Examples: negative quantity where only positive is valid, unknown side or
    product, missing required identifiers.
    """
