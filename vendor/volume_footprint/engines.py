"""The three volume engines: where buy and sell volume come from.

Every downstream number in this package reads exactly two series - bar buy
volume and bar sell volume. The engine decides how those two are produced;
nothing else in the pipeline changes.

    Geometric   Splits the bar's own volume by where the bar closed inside
                its range. Works on any OHLCV data, anywhere, no extra feed.
    Intrabar    Aggregates directional volume from a lower-timeframe feed.
                Pine reads this through the TradingView/ta/14 library.
    Footprint   Real per-tick buy/sell rows from an exchange-grade feed.
                Pine gets these from request.footprint() on a Premium plan;
                in Python you supply the rows yourself.

The purity rule from the original is preserved: a bar the engine cannot fill
returns None, never 0.0. Missing data is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from .bars import Bar, BarSeries

__all__ = [
    "VolumeEngine",
    "geometric_share",
    "geometric_split",
    "intrabar_split",
    "FootprintRow",
    "apply_engine",
]


class VolumeEngine(str, Enum):
    GEOMETRIC = "Geometric"
    INTRABAR = "Intrabar"
    FOOTPRINT = "Footprint"


# ---------------------------------------------------------------- Geometric

def geometric_share(high: float, low: float, close: float) -> float:
    """Buy share of a bar's volume from the close position in its range.

        share = (close - low) / (high - low),   0.5 on a zero-range bar

    The reasoning: a candle that closed on its high spent the session being
    lifted, so most of its volume was buyer-initiated; one that closed on its
    low was hit down. It is an *estimator*, not a measurement - it cannot know
    that price ran up and got sold back into. That is exactly what the other
    two engines exist to fix.
    """
    rng = high - low
    if rng > 0.0:
        return (close - low) / rng
    return 0.5


def geometric_split(bar: Bar) -> tuple[float, float]:
    """(buy, sell) volume of one bar under the Geometric engine."""
    share = geometric_share(bar.high, bar.low, bar.close)
    return bar.volume * share, bar.volume * (1.0 - share)


# ----------------------------------------------------------------- Intrabar

def intrabar_split(intrabars: Sequence[Bar]) -> tuple[float | None, float | None]:
    """Aggregate one chart bar's buy/sell volume from its lower-timeframe bars.

    Re-implementation of what ta.requestUpAndDownVolume does on the LTF feed:
    each intrabar's whole volume is assigned to the side its own body points
    at. Ties (doji intrabars) fall back to the move against the previous
    intrabar close, and a still-tied intrabar splits 50/50 rather than being
    dropped, so the two sides always re-add to the bar volume.

    Returns (None, None) when no intrabar data covers this bar - the Pine
    engine likewise returns na past the plan's intrabar history and never
    substitutes anything in its place.
    """
    if not intrabars:
        return None, None

    up = 0.0
    down = 0.0
    prev_close: float | None = None
    for ib in intrabars:
        if ib.close > ib.open:
            up += ib.volume
        elif ib.close < ib.open:
            down += ib.volume
        elif prev_close is not None and ib.close > prev_close:
            up += ib.volume
        elif prev_close is not None and ib.close < prev_close:
            down += ib.volume
        else:
            up += ib.volume * 0.5
            down += ib.volume * 0.5
        prev_close = ib.close
    return up, down


# ---------------------------------------------------------------- Footprint

@dataclass(frozen=True, slots=True)
class FootprintRow:
    """One official per-price row of a real footprint feed.

    up_price is the row ceiling. The Pine port anchors rows by up_price() on
    purpose: a trade printing at the live price sits on the top edge of its own
    row, so ceiling-anchoring puts that volume on the lattice cell of the price
    itself and the axis row updates while price sits on it.
    """

    up_price: float
    down_price: float
    buy_volume: float
    sell_volume: float
    has_buy_imbalance: bool = False
    has_sell_imbalance: bool = False


def apply_engine(
    series: BarSeries,
    engine: VolumeEngine = VolumeEngine.GEOMETRIC,
    intrabars: Sequence[Sequence[Bar]] | None = None,
    footprint_rows: Sequence[Sequence[FootprintRow] | None] | None = None,
) -> BarSeries:
    """Return a copy of series with buy_volume / sell_volume filled in.

    intrabars and footprint_rows are indexed like series.bars (oldest first),
    one entry per chart bar.
    """
    out: list[Bar] = []
    for i, bar in enumerate(series.bars):
        buy: float | None
        sell: float | None
        if engine is VolumeEngine.GEOMETRIC:
            buy, sell = geometric_split(bar)
        elif engine is VolumeEngine.INTRABAR:
            group = intrabars[i] if intrabars is not None and i < len(intrabars) else ()
            buy, sell = intrabar_split(group)
            if sell is not None:
                sell = abs(sell)  # the library may sign the down leg
        else:
            rows = (
                footprint_rows[i]
                if footprint_rows is not None and i < len(footprint_rows)
                else None
            )
            if not rows:
                buy = sell = None
            else:
                buy = sum(r.buy_volume for r in rows)
                sell = sum(r.sell_volume for r in rows)
        out.append(
            Bar(bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume, buy, sell)
        )
    return BarSeries(bars=out, mintick=series.mintick, symbol=series.symbol)
