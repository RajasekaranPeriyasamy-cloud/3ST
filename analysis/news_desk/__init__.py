"""Live Market News desk.

Ingests public RSS feeds and NSE/BSE corporate announcements, resolves each
headline to NSE tradingsymbols, scores sentiment, and serves a live feed.

Deliberately imports nothing from ``broker/``, ``execution/`` or ``risk/`` — the
same isolation rule ``analysis/equity_report/`` follows. A publisher that hangs
for 30s must never be able to delay an order-placing tick.

Read-only with respect to the market: this desk places no orders.
"""

from __future__ import annotations

__all__ = ["feed", "feeds", "normalize", "runner", "sentiment", "store", "tickers"]
