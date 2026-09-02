"""Splitting one bar's volume across one-tick price rows.

This is the geometric heart of the indicator, ported from Pine
``f_gaussViewRows`` and ``f_fpViewRows``. Both produce the *same* eight-part
output contract, which is what lets the window search treat every engine alike.

The model
---------
A candle reports four prices and one volume. Where inside the range did that
volume actually trade? The script answers with two truncated normal bells::

    buy bell    mu_b  = low   + (close - low)  / 2     the lower half-journey
    sell bell   mu_s  = close + (high - close) / 2     the upper half-journey
    both        sigma = (high - low) / concentration

Read it as a story of the bar: buyers did their work between the low and the
close, so their volume centres midway through that leg; sellers did theirs
between the close and the high, so their volume centres midway through that
one. The bells are truncated to the bar range - a bar cannot trade at a price
it never printed - and each bell is renormalised by its own truncation mass Z
so the two sides still add back to exactly the bar volume.

The lattice
-----------
Row k is the absolute tick index ``round(price / mintick)`` and owns the price
band ``[(k - 0.5) * tick, (k + 0.5) * tick]``. A row's value is the exact
truncated-normal mass of that band clipped to the bar range::

    row_k = w * ( Phi((top_k - mu)/sigma) - Phi((bot_k - mu)/sigma) ) / Z

Rows are never rescaled and never merged, so every cell is the analytic truth
of its own price level. Mass falling outside the visible frame is returned
separately rather than dropped - per side, frame rows plus off-frame sum equal
the input volume up to floating-point noise. That invariant is what the
residual self-check later measures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .engines import FootprintRow
from .mathkit import norm_cdf_fast

__all__ = ["RowSplit", "gauss_view_rows", "footprint_view_rows", "diagonal_imbalance"]


@dataclass(slots=True)
class RowSplit:
    """One bar laid onto the visible frame.

    ``buy_rows`` / ``sell_rows`` are indexed 0 at the frame bottom and hold
    ``None`` where the bar never traded. ``None`` is never read as zero: an
    untraded row has no opposing volume to be imbalanced against, so it stays
    silent.
    """

    buy_rows: list[float | None]
    sell_rows: list[float | None]
    tick_lo: int
    tick_hi: int
    off_buy: float
    off_sell: float
    buy_imbalance: list[bool] = field(default_factory=list)
    sell_imbalance: list[bool] = field(default_factory=list)

    @property
    def frame_buy(self) -> float:
        return sum(v for v in self.buy_rows if v is not None)

    @property
    def frame_sell(self) -> float:
        return sum(v for v in self.sell_rows if v is not None)

    def has_frame_data(self) -> bool:
        return any(v is not None for v in self.buy_rows) or any(
            v is not None for v in self.sell_rows
        )


def diagonal_imbalance(
    buy_rows: Sequence[float | None],
    sell_rows: Sequence[float | None],
    imb_pct: float,
) -> tuple[list[bool], list[bool]]:
    """The classical diagonal imbalance rule, applied to one column of rows.

    A level carries a BUY imbalance when its buy volume exceeds the sell volume
    one level BELOW it by the threshold ratio; a SELL imbalance when its sell
    volume exceeds the buy volume one level ABOVE it by the same ratio::

        buy_imb[k]   <=>   buy[k]  > sell[k-1] * ratio
        sell_imb[k]  <=>   sell[k] > buy[k+1]  * ratio

    The diagonal is not a quirk - it is the whole point. A limit order book
    pairs an aggressive buy lifting the ask against the resting bid one tick
    under it. Comparing ``buy[k]`` against ``sell[k]`` would compare two prints
    that never met; comparing ``buy[k]`` against ``sell[k-1]`` compares the two
    sides of the same trade decision. 300 percent, the classical figure,
    demands three times the opposing volume before a level is called
    imbalanced.

    Rows the bar never traded stay silent - ``None`` is never treated as zero
    opposing volume, which would make every edge row trivially imbalanced.
    """
    n = len(buy_rows)
    buy_imb = [False] * n
    sell_imb = [False] * n
    ratio = imb_pct / 100.0

    for k in range(1, n):
        b_cur = buy_rows[k]
        s_below = sell_rows[k - 1]
        if b_cur is not None and s_below is not None and b_cur > 0.0:
            if b_cur > s_below * ratio:
                buy_imb[k] = True

    for k in range(0, n - 1):
        s_cur = sell_rows[k]
        b_above = buy_rows[k + 1]
        if s_cur is not None and b_above is not None and s_cur > 0.0:
            if s_cur > b_above * ratio:
                sell_imb[k] = True

    return buy_imb, sell_imb


def gauss_view_rows(
    buy_volume: float | None,
    sell_volume: float | None,
    high: float,
    low: float,
    close: float,
    axis_tick: int,
    view_ticks: int,
    tick: float,
    bell_div: float,
    imb_pct: float,
) -> RowSplit:
    """Port of Pine ``f_gaussViewRows``. See the module docstring for the model.

    ``axis_tick`` is the lattice index the frame is centred on (normally the
    newest close, so the frame behaves like an aircraft altimeter: price stays
    put and the ladder slides behind it). ``view_ticks`` rows are shown on
    either side.
    """
    n_view = 2 * view_ticks + 1
    b_rows: list[float | None] = [None] * n_view
    s_rows: list[float | None] = [None] * n_view
    k_min = axis_tick - view_ticks
    k_max = axis_tick + view_ticks
    b_off = 0.0
    s_off = 0.0

    if (
        buy_volume is None
        or sell_volume is None
        or high is None
        or low is None
        or close is None
        or tick <= 0.0
    ):
        return RowSplit(
            b_rows, s_rows, 0, 0, 0.0, 0.0, [False] * n_view, [False] * n_view
        )

    bv = float(buy_volume)
    sv = float(sell_volume)
    rng = high - low
    tick_lo = int(round(low / tick))
    tick_hi = int(round(high / tick))

    if rng <= 0.0:
        # Flat bar: all volume sits on the single lattice row of its price.
        flat_row = int(round(close / tick))
        if k_min <= flat_row <= k_max:
            b_rows[flat_row - k_min] = bv
            s_rows[flat_row - k_min] = sv
        else:
            b_off = bv
            s_off = sv
    else:
        mu_b = low + (close - low) / 2.0
        mu_s = close + (high - close) / 2.0
        sigma = rng / bell_div

        cdf_b_low = norm_cdf_fast((low - mu_b) / sigma)
        cdf_s_low = norm_cdf_fast((low - mu_s) / sigma)
        z_b = norm_cdf_fast((high - mu_b) / sigma) - cdf_b_low
        z_s = norm_cdf_fast((high - mu_s) / sigma) - cdf_s_low
        ok_b = z_b > 1e-12
        ok_s = z_s > 1e-12

        frame_bot = (k_min - 0.5) * tick
        frame_top = (k_max + 0.5) * tick

        # Mass below the frame floor.
        if frame_bot > low:
            cap_b = min(frame_bot, high)
            if ok_b:
                b_off += (
                    max(0.0, (norm_cdf_fast((cap_b - mu_b) / sigma) - cdf_b_low) / z_b)
                    * bv
                )
            if ok_s:
                s_off += (
                    max(0.0, (norm_cdf_fast((cap_b - mu_s) / sigma) - cdf_s_low) / z_s)
                    * sv
                )

        # Frame rows the bar actually reaches. Row boundaries rise
        # monotonically, so each boundary CDF is computed once and reused as
        # the next row's floor - one CDF call per row, not two.
        k_start = max(tick_lo, k_min)
        k_end = min(tick_hi, k_max)
        if k_start <= k_end:
            bot_start = max((k_start - 0.5) * tick, low)
            prev_b = norm_cdf_fast((bot_start - mu_b) / sigma)
            prev_s = norm_cdf_fast((bot_start - mu_s) / sigma)
            for k in range(k_start, k_end + 1):
                band_top = min((k + 0.5) * tick, high)
                next_b = norm_cdf_fast((band_top - mu_b) / sigma)
                next_s = norm_cdf_fast((band_top - mu_s) / sigma)
                b_rows[k - k_min] = (
                    max(0.0, (next_b - prev_b) / z_b) * bv if ok_b else 0.0
                )
                s_rows[k - k_min] = (
                    max(0.0, (next_s - prev_s) / z_s) * sv if ok_s else 0.0
                )
                prev_b = next_b
                prev_s = next_s

        # Mass above the frame ceiling.
        if frame_top < high:
            cap_t = max(frame_top, low)
            if ok_b:
                b_off += (
                    max(
                        0.0,
                        (
                            norm_cdf_fast((high - mu_b) / sigma)
                            - norm_cdf_fast((cap_t - mu_b) / sigma)
                        )
                        / z_b,
                    )
                    * bv
                )
            if ok_s:
                s_off += (
                    max(
                        0.0,
                        (
                            norm_cdf_fast((high - mu_s) / sigma)
                            - norm_cdf_fast((cap_t - mu_s) / sigma)
                        )
                        / z_s,
                    )
                    * sv
                )

    b_imb, s_imb = diagonal_imbalance(b_rows, s_rows, imb_pct)
    return RowSplit(b_rows, s_rows, tick_lo, tick_hi, b_off, s_off, b_imb, s_imb)


def footprint_view_rows(
    rows: Sequence[FootprintRow] | None,
    axis_tick: int,
    view_ticks: int,
    tick: float,
) -> RowSplit:
    """Port of Pine ``f_fpViewRows``: lay official footprint rows onto the frame.

    Same output contract as :func:`gauss_view_rows`, so callers never branch on
    engine. Rows are anchored by ``up_price``, putting a print at the live
    price on the lattice cell of that price itself.
    """
    n_view = 2 * view_ticks + 1
    b_rows: list[float | None] = [None] * n_view
    s_rows: list[float | None] = [None] * n_view
    b_imb = [False] * n_view
    s_imb = [False] * n_view
    k_min = axis_tick - view_ticks
    k_max = axis_tick + view_ticks
    tick_lo = 0
    tick_hi = 0
    b_off = 0.0
    s_off = 0.0

    if rows:
        tick_lo = int(round(rows[0].up_price / tick))
        tick_hi = int(round(rows[-1].up_price / tick))
        for row in rows:
            k = int(round(row.up_price / tick))
            if k_min <= k <= k_max:
                b_rows[k - k_min] = row.buy_volume
                s_rows[k - k_min] = row.sell_volume
                b_imb[k - k_min] = row.has_buy_imbalance
                s_imb[k - k_min] = row.has_sell_imbalance
            else:
                b_off += row.buy_volume
                s_off += row.sell_volume

    return RowSplit(b_rows, s_rows, tick_lo, tick_hi, b_off, s_off, b_imb, s_imb)
