"""Depth-aware executable prices for the arbitrage scanner.

An arbitrage screen built on last-traded price is a fiction generator: LTP on
an illiquid wing can be minutes old and nowhere near where either side of the
book actually is. Every detector in this package therefore prices a BUY leg at
the **ask** and a SELL leg at the **bid**, and refuses a row when either side
is missing.

``Quote.depth_qty`` carries the top-of-book size so a detector can also refuse
a row it could not actually fill. Kite's ``quote()`` returns five levels; only
level one is used — deeper levels move while a multi-leg order is being placed,
and counting them would flatter the screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

QUOTE_BATCH = 500

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Quote:
    """Top-of-book snapshot for one instrument."""

    key: str
    bid: float | None = None
    ask: float | None = None
    bid_qty: int = 0
    ask_qty: int = 0
    ltp: float | None = None
    oi: int = 0
    volume: int = 0
    timestamp: str | None = None

    @property
    def tradable(self) -> bool:
        return (
            self.bid is not None
            and self.ask is not None
            and self.bid > 0
            and self.ask > 0
            and self.ask >= self.bid
        )

    @property
    def mid(self) -> float | None:
        if not self.tradable:
            return self.ltp if (self.ltp or 0) > 0 else None
        return 0.5 * (float(self.bid or 0) + float(self.ask or 0))

    @property
    def spread(self) -> float | None:
        if not self.tradable:
            return None
        return float(self.ask or 0) - float(self.bid or 0)

    @property
    def spread_pct(self) -> float | None:
        mid = self.mid
        spread = self.spread
        if mid is None or spread is None or mid <= 0:
            return None
        return 100.0 * spread / mid

    def executable(self, side: Side) -> float | None:
        """Price you would actually pay/receive: ask to buy, bid to sell."""
        if str(side).upper() == "BUY":
            return self.ask if (self.ask or 0) > 0 else None
        return self.bid if (self.bid or 0) > 0 else None

    def depth_qty(self, side: Side) -> int:
        """Top-of-book quantity available on the side you would hit."""
        return self.ask_qty if str(side).upper() == "BUY" else self.bid_qty

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "bid": self.bid,
            "ask": self.ask,
            "bid_qty": self.bid_qty,
            "ask_qty": self.ask_qty,
            "ltp": self.ltp,
            "oi": self.oi,
            "volume": self.volume,
            "mid": round(self.mid, 4) if self.mid is not None else None,
            "spread": round(self.spread, 4) if self.spread is not None else None,
            "spread_pct": round(self.spread_pct, 3) if self.spread_pct is not None else None,
        }


def _as_float(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_quote(key: str, raw: dict[str, Any] | None) -> Quote:
    """Normalise one Kite ``quote()`` entry into a :class:`Quote`.

    Falls back to the flat ``buy_price``/``sell_price`` fields when ``depth`` is
    absent, which is what a thin MCX mini contract often returns.
    """
    if not raw:
        return Quote(key=key)

    depth = raw.get("depth") if isinstance(raw.get("depth"), dict) else {}
    buy = depth.get("buy") or []
    sell = depth.get("sell") or []

    bid = _as_float(buy[0].get("price")) if buy else None
    ask = _as_float(sell[0].get("price")) if sell else None
    bid_qty = _as_int(buy[0].get("quantity")) if buy else 0
    ask_qty = _as_int(sell[0].get("quantity")) if sell else 0

    if bid is None:
        bid = _as_float(raw.get("buy_price")) or _as_float(raw.get("best_bid"))
    if ask is None:
        ask = _as_float(raw.get("sell_price")) or _as_float(raw.get("best_ask"))

    oi = _as_int(raw.get("oi"))
    if not oi:
        oi = _as_int(raw.get("open_interest"))

    return Quote(
        key=key,
        bid=bid,
        ask=ask,
        bid_qty=bid_qty,
        ask_qty=ask_qty,
        ltp=_as_float(raw.get("last_price")),
        oi=oi,
        volume=_as_int(raw.get("volume") or raw.get("volume_traded")),
        timestamp=str(raw.get("timestamp") or "") or None,
    )


def fetch_quotes(keys: list[str]) -> dict[str, Quote]:
    """Batched ``exchange:tradingsymbol`` quote fetch.

    Batches of ``QUOTE_BATCH`` because that is Kite's per-call instrument cap.
    A failed batch yields empty quotes for its keys rather than aborting the
    scan — one dead batch should cost you those rows, not the sweep.
    """
    from kite_client import fetch_quote_batch

    unique = list(dict.fromkeys(k for k in keys if k))
    out: dict[str, Quote] = {}
    for i in range(0, len(unique), QUOTE_BATCH):
        chunk = unique[i : i + QUOTE_BATCH]
        try:
            raw = fetch_quote_batch(chunk)
        except Exception:
            raw = {}
        for key in chunk:
            out[key] = parse_quote(key, raw.get(key))
    return out


def quote_key(exchange: str, tradingsymbol: str) -> str:
    return f"{str(exchange).upper()}:{str(tradingsymbol).upper()}"
