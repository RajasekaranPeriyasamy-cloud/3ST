"""Point of Control, Value Area, and balance metrics.

These are the classical Market Profile statistics. The Pine script computes
each of them twice - once over the table's window of bars (the "dashboard"
domain) and once over the profile's period (the "chart" domain) - using the
*same* two routines here, fed different arrays. Keeping one implementation is
deliberate: if the dashboard and the chart disagreed about what a POC is, the
two halves of the indicator would be telling different stories.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

__all__ = [
    "ValueArea",
    "point_of_control",
    "value_area",
    "balance_tilt",
    "first_imbalances_from_poc",
]


@dataclass(frozen=True, slots=True)
class ValueArea:
    """Index span of the value area, inclusive on both ends."""

    low_index: int
    high_index: int
    accumulated: float
    target: float

    @property
    def width(self) -> int:
        return self.high_index - self.low_index + 1


def point_of_control(level_volumes: Sequence[float]) -> int:
    """Index of the level that traded the most volume, or -1 if none did.

    The POC is the price at which the market did the most business. It is a
    magnet in ranging markets - price keeps coming back to where the inventory
    is - and a reference point in trending ones: a break away from it that
    holds is one of the cleaner signs that the auction has genuinely moved.

    Ties keep the *first* (lowest) index, exactly like the Pine ``>`` test.
    """
    best = 0.0
    best_k = -1
    for k, vol in enumerate(level_volumes):
        if vol > best:
            best = vol
            best_k = k
    return best_k


def value_area(
    level_volumes: Sequence[float],
    poc_index: int,
    pct: float = 70.0,
) -> ValueArea | None:
    """Classical value area expansion outward from the POC.

    Start at the POC row. Repeatedly look at the neighbour above and the
    neighbour below, take whichever holds more volume, and add it to the band.
    Stop once the band holds ``pct`` of the period's total volume. 70 percent
    is the classical figure - one standard deviation of a normal distribution,
    which is where the convention comes from.

    What it means at the desk: inside the band the market *accepted* price and
    kept building business. Past the edges, trade thinned out fast. Price
    re-entering the band tends to travel across it to the far edge; price
    holding beyond an edge usually means the auction has moved on to do
    business elsewhere.

    The expansion is boundary-guarded exactly as in Pine: an exhausted side is
    never stepped again, so the loop terminates after at most ``n`` passes even
    when the target can never be reached.

    Note this is the *stepwise* TPO-style expansion, one row at a time, not the
    two-rows-at-a-time variant some platforms use. The original commits to one
    rule and applies it in both domains.
    """
    n = len(level_volumes)
    if poc_index < 0 or n == 0:
        return None

    total = float(sum(level_volumes))
    target = pct / 100.0 * total
    acc = float(level_volumes[poc_index])
    lo = hi = poc_index

    while acc < target and (lo > 0 or hi < n - 1):
        can_up = hi < n - 1
        can_dn = lo > 0
        vol_up = level_volumes[hi + 1] if can_up else 0.0
        vol_dn = level_volumes[lo - 1] if can_dn else 0.0

        if not can_up:
            lo -= 1
            acc += vol_dn
        elif not can_dn:
            hi += 1
            acc += vol_up
        elif vol_up >= vol_dn:
            hi += 1
            acc += vol_up
        else:
            lo -= 1
            acc += vol_dn

    return ValueArea(low_index=lo, high_index=hi, accumulated=acc, target=target)


def balance_tilt(sum_buy: float, sum_sell: float) -> float | None:
    """Which side carried more of the period's volume, in percentage points.

        tilt = 100 * (buy - sell) / (buy + sell)

    Positive leans to the buy side, negative to the sell side. This is delta
    expressed as a share of volume, so it is comparable across symbols and
    across sessions in a way raw delta is not.
    """
    total = sum_buy + sum_sell
    if total <= 0.0:
        return None
    return 100.0 * (sum_buy - sum_sell) / total


def first_imbalances_from_poc(
    buy_levels: Sequence[float],
    sell_levels: Sequence[float],
    poc_index: int,
    imb_pct: float,
) -> tuple[tuple[int, bool] | None, tuple[int, bool] | None]:
    """First imbalanced level above and below the POC.

    Returns ``((index, is_buy_side), ...)`` for the up direction and the down
    direction, or ``None`` on a side with no hit.

    Marking only the *first* imbalance each way is a signal-to-noise decision,
    not a shortcut: in a trending profile half the ladder can be imbalanced,
    and a chart with thirty dotted lines on it says nothing. The first level
    away from the POC on each side is where the auction stopped being two-sided
    - that is the edge worth watching.
    """
    n = len(buy_levels)
    ratio = imb_pct / 100.0
    up: tuple[int, bool] | None = None
    down: tuple[int, bool] | None = None

    if 0 <= poc_index < n:
        for k in range(poc_index + 1, n):
            hit_b = buy_levels[k] > 0.0 and buy_levels[k] > sell_levels[k - 1] * ratio
            hit_s = (
                k <= n - 2
                and sell_levels[k] > 0.0
                and sell_levels[k] > buy_levels[k + 1] * ratio
            )
            if hit_b or hit_s:
                up = (k, bool(hit_b))
                break

        for k in range(poc_index - 1, -1, -1):
            hit_b = k >= 1 and buy_levels[k] > 0.0 and buy_levels[k] > sell_levels[k - 1] * ratio
            hit_s = (
                k <= n - 2
                and sell_levels[k] > 0.0
                and sell_levels[k] > buy_levels[k + 1] * ratio
            )
            if hit_b or hit_s:
                down = (k, bool(hit_b))
                break

    return up, down
