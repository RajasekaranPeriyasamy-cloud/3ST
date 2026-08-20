"""Bar container shared by every engine.

The Pine script reads high[i] / low[i] / close[i] / volume[i] off the built-in
series. Python has no implicit series, so a run is described by an explicit,
oldest-first list of :class:`Bar`.

Indexing convention mirrors Pine's: offset 0 is the newest bar of the run,
offset 1 the one before it, and so on. :meth:`BarSeries.back` performs that
translation once so no other module has to think about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Sequence

__all__ = ["Bar", "BarSeries"]


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV candle, plus an optional pre-split buy/sell volume.

    buy_volume / sell_volume stay None for the Geometric engine, which derives
    them. The Intrabar and Footprint engines fill them in, and a None there
    means *the engine had no data for this bar* - never zero. That distinction
    is load-bearing: the Pine window search rejects a bar on na(engBuy[i]), so
    an absent reading is never mistaken for absent trade.
    """

    time: datetime | None
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float | None = None
    sell_volume: float | None = None

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_flat(self) -> bool:
        """A zero-range bar. It has no bell: its volume is a point atom."""
        return self.range <= 0.0


@dataclass(slots=True)
class BarSeries:
    """Oldest-first candles plus the symbol tick size.

    mintick is the price lattice step. Every row index in this package is an
    *absolute* lattice index round(price / mintick), which is what lets the
    table, the profile and the official footprint rows share one address space.
    """

    bars: list[Bar]
    mintick: float = 0.01
    symbol: str = ""

    def __post_init__(self) -> None:
        if self.mintick <= 0.0:
            raise ValueError("mintick must be positive")

    def __len__(self) -> int:
        return len(self.bars)

    def __iter__(self) -> Iterable[Bar]:
        return iter(self.bars)

    def back(self, offset: int) -> Bar:
        """Pine series[offset]: 0 = newest bar, 1 = the one before it."""
        return self.bars[len(self.bars) - 1 - offset]

    @property
    def last(self) -> Bar:
        return self.bars[-1]

    def tick_index(self, price: float) -> int:
        """Absolute lattice row of price: round(price / mintick).

        Each row owns half a tick either side of its own level, so a price on
        a boundary rounds to the nearer row, deterministically.
        """
        return int(round(price / self.mintick))

    def tick_price(self, index: int) -> float:
        return index * self.mintick

    @classmethod
    def from_ohlcv(
        cls,
        rows: Sequence[Sequence],
        mintick: float = 0.01,
        symbol: str = "",
    ) -> "BarSeries":
        """Build from (time, open, high, low, close, volume) tuples."""
        bars = [
            Bar(
                time=r[0],
                open=float(r[1]),
                high=float(r[2]),
                low=float(r[3]),
                close=float(r[4]),
                volume=float(r[5]),
            )
            for r in rows
        ]
        return cls(bars=bars, mintick=mintick, symbol=symbol)
