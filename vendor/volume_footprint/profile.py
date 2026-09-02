"""The two-sided volume profile: a truncated-normal mixture per side.

Where the footprint table shows *bar by bar*, the profile collapses a period of
bars into two continuous curves - one fed only by buy volume, one only by sell
volume - drawn sideways off the right edge of the chart. Where the two overlap,
both sides were doing business at the same prices.

The mixture
-----------
Each bar with a real range contributes one truncated normal component per side,
with the same centres and width the table uses::

    mu_b  = low + (close - low)/2        mu_s  = close + (high - close)/2
    sigma = (high - low) / concentration
    Z     = Phi((high - mu)/sigma) - Phi((low - mu)/sigma)      truncation mass
    coef  = w / (sigma * Z)                                     intensity

A side's *intensity* at price p is the sum, over every component whose
truncation range contains p, of ``coef * phi((p - mu)/sigma)``. The unit is
volume per unit price - a density in the physics sense, not a probability.
Dividing by the side's total volume turns it into the mixture's probability
density, which is what the overlap coefficient needs.

Flat bars have zero variance. They are not squeezed into a degenerate bell;
they enter the model as **point atoms** at their own price and are carried
through the arithmetic separately. This matters on illiquid options strikes
and on limit-locked commodity bars, where a naive implementation divides by
zero or silently drops the volume.

The three readings
------------------
* **OVL** - overlapping coefficient, the integral of the pointwise minimum of
  the two volume-normalised densities, plus the matched atomic mass. Range
  0 to 1. Near 1 both sides worked the same levels: balance and rotation. Away
  from 1 each side held its own price territory: a directional market.
* **Tilt** - which side carried more volume, in percentage points.
* **RES** - the residual self-check, in parts per million. The area under each
  curve, plus the atoms, must add back to the volume that side really traded.
  RES is how far the two came apart. It is a health reading of the *tool*, not
  a signal about the market: EXACT means the picture is faithful, DRIFT means
  do not trade the shape until it settles.

Integration
-----------
Composite Simpson on pieces cut at every bar boundary - where the mixture's
active component set jumps and the integrand is not smooth - and, for the
overlap term, additionally at every crossing of the two densities, located by
bisection to machine tolerance. Each piece's active set is fixed by its
midpoint, so every integrand Simpson ever sees is smooth. This is why RES comes
out below one part per million rather than at the percent level a naive
uniform-grid integration would give.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .bars import BarSeries
from .mathkit import norm_cdf_precise, norm_pdf

__all__ = [
    "ProfComp",
    "ProfileModel",
    "build_profile",
    "density",
    "band_mass",
    "sample_curves",
]


@dataclass(frozen=True, slots=True)
class ProfComp:
    """One truncated-normal component of a profile side.

    The bell contributes density only inside the bar's own price range; outside
    it the component is exactly zero.
    """

    mu: float
    sigma: float
    coef: float
    price_lo: float
    price_hi: float


def density(price: float, comps: Sequence[ProfComp]) -> float:
    """Volume intensity of one side at one price. Unit: volume per unit price."""
    dens = 0.0
    for c in comps:
        if c.price_lo <= price <= c.price_hi:
            dens += c.coef * norm_pdf((price - c.mu) / c.sigma)
    return dens


def band_mass(band_lo: float, band_hi: float, comps: Sequence[ProfComp]) -> float:
    """Volume of one side inside a price band.

    Summing this over a full lattice returns the side's total volume, so the
    figure is directly comparable with a table row - which is exactly how the
    chart POC label reports the same *kind* of number as the dashboard POC.
    """
    mass = 0.0
    for c in comps:
        seg_lo = max(band_lo, c.price_lo)
        seg_hi = min(band_hi, c.price_hi)
        if seg_hi > seg_lo:
            z_all = norm_cdf_precise((c.price_hi - c.mu) / c.sigma) - norm_cdf_precise(
                (c.price_lo - c.mu) / c.sigma
            )
            if z_all > 0.0:
                z_seg = norm_cdf_precise((seg_hi - c.mu) / c.sigma) - norm_cdf_precise(
                    (seg_lo - c.mu) / c.sigma
                )
                mass += c.coef * c.sigma * z_seg
    return mass


@dataclass(slots=True)
class ProfileModel:
    """A built profile: components, atoms, totals and the three readings."""

    comps_buy: list[ProfComp] = field(default_factory=list)
    comps_sell: list[ProfComp] = field(default_factory=list)
    atom_prices: list[float] = field(default_factory=list)
    atom_buys: list[float] = field(default_factory=list)
    atom_sells: list[float] = field(default_factory=list)
    sum_buy: float = 0.0
    sum_sell: float = 0.0
    atom_buy: float = 0.0
    atom_sell: float = 0.0
    price_lo: float | None = None
    price_hi: float | None = None
    data_bars: int = 0

    # Filled by analyse().
    integral_buy: float = 0.0
    integral_sell: float = 0.0
    overlap: float | None = None       # OVL, percent 0-100
    residual_ppm: float | None = None  # RES, parts per million
    tilt: float | None = None          # percentage points, + = buy side

    @property
    def span(self) -> float:
        if self.price_lo is None or self.price_hi is None:
            return 0.0
        return self.price_hi - self.price_lo

    def is_balanced(self, threshold: float = 75.0) -> bool:
        """OVL at or above 75 percent is the script's balance boundary."""
        return self.overlap is not None and self.overlap >= threshold

    def verdict(self, tilt_threshold: float = 5.0) -> str:
        """The label the chart prints: BALANCED, or which way it is off.

        Below ``tilt_threshold`` percentage points the label refuses to name a
        side. That refusal is the point: calling a direction on a two-point
        edge is dressing a coin flip up as information.
        """
        if self.overlap is None:
            return "NO DATA"
        if self.is_balanced():
            return "BALANCED"
        if self.tilt is not None and self.tilt >= tilt_threshold:
            return "OFF BALANCE TO BUY"
        if self.tilt is not None and self.tilt <= -tilt_threshold:
            return "OFF BALANCE TO SELL"
        return "OFF BALANCE"


def build_profile(
    series: BarSeries,
    period: int = 23,
    bell_div: float = 3.0,
    end_offset: int = 0,
) -> ProfileModel:
    """Build the mixture over the last ``period`` bars ending at ``end_offset``.

    ``end_offset`` follows the Pine convention: 0 is the newest bar. Bars whose
    engine volume is missing are skipped entirely rather than zero-filled.
    """
    m = ProfileModel()
    n = len(series)
    for i in range(end_offset, end_offset + period):
        if i >= n:
            break
        bar = series.back(i)
        bv = bar.buy_volume
        sv = bar.sell_volume
        if bv is None or sv is None:
            continue

        m.data_bars += 1
        m.price_lo = bar.low if m.price_lo is None else min(m.price_lo, bar.low)
        m.price_hi = bar.high if m.price_hi is None else max(m.price_hi, bar.high)
        m.sum_buy += bv
        m.sum_sell += sv

        rng = bar.high - bar.low
        if rng <= 0.0:
            m.atom_prices.append(bar.close)
            m.atom_buys.append(bv)
            m.atom_sells.append(sv)
            m.atom_buy += bv
            m.atom_sell += sv
            continue

        sg = rng / bell_div
        mu_b = bar.low + (bar.close - bar.low) / 2.0
        mu_s = bar.close + (bar.high - bar.close) / 2.0
        z_b = norm_cdf_precise((bar.high - mu_b) / sg) - norm_cdf_precise(
            (bar.low - mu_b) / sg
        )
        z_s = norm_cdf_precise((bar.high - mu_s) / sg) - norm_cdf_precise(
            (bar.low - mu_s) / sg
        )
        if bv > 0.0 and z_b > 0.0:
            m.comps_buy.append(ProfComp(mu_b, sg, bv / (sg * z_b), bar.low, bar.high))
        if sv > 0.0 and z_s > 0.0:
            m.comps_sell.append(ProfComp(mu_s, sg, sv / (sg * z_s), bar.low, bar.high))

    _analyse(m)
    return m


def _piece_cuts(m: ProfileModel) -> list[float]:
    """Sorted, de-duplicated integration boundaries.

    The span edges plus every component's truncation bounds from both sides.
    These are precisely the prices where the mixture's active set changes and
    the integrand kinks; Simpson is exact on smooth pieces and sloppy across
    kinks, so we never let a piece straddle one.
    """
    pts: list[float] = []
    if m.price_lo is not None:
        pts.append(m.price_lo)
    if m.price_hi is not None:
        pts.append(m.price_hi)
    for c in m.comps_buy:
        pts.append(c.price_lo)
        pts.append(c.price_hi)
    for c in m.comps_sell:
        pts.append(c.price_lo)
        pts.append(c.price_hi)
    pts.sort()

    cuts: list[float] = []
    prev: float | None = None
    for p in pts:
        if prev is None or p != prev:
            cuts.append(p)
        prev = p
    return cuts


def _simpson(lo: float, hi: float, n_sub: int, fn) -> float:
    """Composite Simpson over [lo, hi] with an even number of sub-intervals."""
    h = (hi - lo) / n_sub
    acc = fn(lo) + fn(hi)
    for k in range(1, n_sub):
        acc += (4.0 if k % 2 == 1 else 2.0) * fn(lo + k * h)
    return acc * h / 3.0


def _sub_count(lo: float, hi: float, sigma_min: float) -> int:
    """Sub-interval count, bounded by the narrowest active bell.

    Simpson's error term scales with the fourth derivative, which for a normal
    is governed by sigma. Tying the step to the smallest active sigma keeps the
    error uniformly small no matter how wide the piece is.
    """
    n = max(12, int(math.ceil(16.0 * (hi - lo) / sigma_min)))
    return n + (n % 2)


def _analyse(m: ProfileModel) -> None:
    """Fill in integrals, OVL, residual and tilt. Port of the Pine last-bar block."""
    if m.price_lo is None or (m.sum_buy + m.sum_sell) <= 0.0:
        return

    cuts = _piece_cuts(m)
    int_b = 0.0
    int_s = 0.0
    int_min = 0.0

    for seg in range(len(cuts) - 1):
        seg_lo = cuts[seg]
        seg_hi = cuts[seg + 1]
        if seg_hi <= seg_lo:
            continue
        seg_mid = 0.5 * (seg_lo + seg_hi)

        act_b = [c for c in m.comps_buy if c.price_lo <= seg_mid <= c.price_hi]
        act_s = [c for c in m.comps_sell if c.price_lo <= seg_mid <= c.price_hi]
        sigmas = [c.sigma for c in act_b] + [c.sigma for c in act_s]
        if not sigmas:
            continue
        sigma_min = min(sigmas)

        n_sub = _sub_count(seg_lo, seg_hi, sigma_min)
        int_b += _simpson(seg_lo, seg_hi, n_sub, lambda p: density(p, act_b))
        int_s += _simpson(seg_lo, seg_hi, n_sub, lambda p: density(p, act_s))

        if m.sum_buy > 0.0 and m.sum_sell > 0.0:
            int_min += _overlap_on_piece(
                seg_lo, seg_hi, act_b, act_s, m.sum_buy, m.sum_sell, sigma_min
            )

    atom_term = _atom_overlap(m)

    resid_b = abs((int_b + m.atom_buy) / m.sum_buy - 1.0) if m.sum_buy > 0.0 else 0.0
    resid_s = abs((int_s + m.atom_sell) / m.sum_sell - 1.0) if m.sum_sell > 0.0 else 0.0

    m.integral_buy = int_b
    m.integral_sell = int_s
    m.residual_ppm = (resid_b + resid_s) * 1_000_000.0
    m.overlap = (
        100.0 * (int_min + atom_term) if m.sum_buy > 0.0 and m.sum_sell > 0.0 else None
    )
    m.tilt = (
        100.0 * (m.sum_buy - m.sum_sell) / (m.sum_buy + m.sum_sell)
        if (m.sum_buy + m.sum_sell) > 0.0
        else None
    )


def _overlap_on_piece(
    seg_lo: float,
    seg_hi: float,
    act_b: Sequence[ProfComp],
    act_s: Sequence[ProfComp],
    sum_b: float,
    sum_s: float,
    sigma_min: float,
) -> float:
    """Integral of min(f_buy, f_sell) over one smooth piece.

    The pointwise minimum has a corner wherever the two densities cross, so the
    piece is cut again at every crossing - found by a 12-point scan for a sign
    change, then bisection to machine tolerance - and Simpson runs on each
    sub-piece against whichever side is smaller there.
    """

    def diff(p: float) -> float:
        return density(p, act_b) / sum_b - density(p, act_s) / sum_s

    sub_cuts = [seg_lo]
    scan_n = 12
    d_prev = diff(seg_lo)
    for j in range(1, scan_n + 1):
        x_j = seg_lo + (seg_hi - seg_lo) * j / scan_n
        d_cur = diff(x_j)
        if d_prev * d_cur < 0.0:
            r_lo = seg_lo + (seg_hi - seg_lo) * (j - 1) / scan_n
            r_hi = x_j
            it = 0
            while r_hi - r_lo > abs(0.5 * (r_lo + r_hi)) * 2.3e-16 and it < 90:
                r_mid = 0.5 * (r_lo + r_hi)
                if diff(r_lo) * diff(r_mid) <= 0.0:
                    r_hi = r_mid
                else:
                    r_lo = r_mid
                it += 1
            sub_cuts.append(0.5 * (r_lo + r_hi))
        d_prev = d_cur
    sub_cuts.append(seg_hi)

    total = 0.0
    for j in range(len(sub_cuts) - 1):
        sub_lo = sub_cuts[j]
        sub_hi = sub_cuts[j + 1]
        if sub_hi <= sub_lo:
            continue
        sub_mid = 0.5 * (sub_lo + sub_hi)
        use_buy = density(sub_mid, act_b) / sum_b <= density(sub_mid, act_s) / sum_s
        n2 = _sub_count(sub_lo, sub_hi, sigma_min)
        if use_buy:
            total += _simpson(sub_lo, sub_hi, n2, lambda p: density(p, act_b) / sum_b)
        else:
            total += _simpson(sub_lo, sub_hi, n2, lambda p: density(p, act_s) / sum_s)
    return total


def _atom_overlap(m: ProfileModel) -> float:
    """Overlap contributed by flat bars.

    Atoms sitting on one exact price pool their mass per side; the matched
    minimum of the two pooled shares joins the OVL. Atoms are point masses, so
    they contribute to the overlap only where *both* sides put mass on the very
    same price - which is the honest reading.
    """
    n = len(m.atom_prices)
    if n == 0 or m.sum_buy <= 0.0 or m.sum_sell <= 0.0:
        return 0.0

    used = [False] * n
    term = 0.0
    for a1 in range(n):
        if used[a1]:
            continue
        mass_b = 0.0
        mass_s = 0.0
        for a2 in range(a1, n):
            if not used[a2] and m.atom_prices[a2] == m.atom_prices[a1]:
                used[a2] = True
                mass_b += m.atom_buys[a2]
                mass_s += m.atom_sells[a2]
        term += min(mass_b / m.sum_buy, mass_s / m.sum_sell)
    return term


def sample_curves(
    m: ProfileModel, resolution: int = 100
) -> tuple[list[float], list[float], list[float]]:
    """Sample both bells on a uniform price grid across the profile span.

    Returns ``(prices, buy_intensity, sell_intensity)``. Resolution changes how
    finely the curve is traced; it never changes the curve's shape, because
    every sample is an exact evaluation of the analytic mixture rather than an
    interpolation of a histogram.
    """
    if m.price_lo is None or m.span <= 0.0:
        return [], [], []

    step = m.span / resolution
    prices = [m.price_lo + k * step for k in range(resolution + 1)]
    buys = [density(p, m.comps_buy) for p in prices]
    sells = [density(p, m.comps_sell) for p in prices]
    return prices, buys, sells
