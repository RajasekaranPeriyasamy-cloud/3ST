"""Measurement-quality statistics for the gamma concentration index.

Pure functions, no I/O, no store writes. Everything here is *additive* — the
existing HHI readouts keep computing exactly as they did; this module answers a
different question: **how much should you believe the number on the screen?**

Four corrections, in the order they matter (see docs/gamma-density/ audit):

1. **DTE conditioning.** HHI is dominated by time to expiry, not book structure.
   Measured on 22 live NIFTY sessions, DTE explains **eta^2 = 0.76** of the
   variance, and expiry-day HHI runs ~5x the level of the surrounding week. A
   percentile taken over a DTE-mixed sample therefore reports mostly-calendar as
   though it were mostly-market: on 2026-08-31 the board read "-36% vs 5-session
   mean" (unusually spread) where the DTE-matched comparison read "+2.3%" (dead
   average for 1 DTE). Same book, opposite conclusions.

2. **Window normalisation.** ``H >= 1/N``, so widening ``strike_window``
   mechanically lowers HHI without the book changing. ``H*`` rescales the floor
   away and is comparable across window settings.

3. **Uncertainty.** A bare ``0.103`` claims a precision it does not have. The
   delta-method SE is exact to first order and costs one pass.

4. **Shape.** HHI is the Hill number of order 2 alone. The profile across orders
   says *why* concentration is what it is, and reconciles HHI against the Gini
   the desk already shows beside it.

Nothing here reads or writes ``data/``. DTE is derived from the row date at read
time rather than backfilled into the store, so no existing row is mutated and a
wrong expiry rule can be corrected by redeploying rather than by migrating.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Any

__all__ = [
    "DEFAULT_HILL_ORDERS",
    "DTE_BUCKETS",
    "bucket_for_dte",
    "cohort_summary",
    "dropped_leg_inflation",
    "hhi_with_se",
    "hill_number",
    "hill_profile",
    "infer_dte",
    "normalized_hhi",
    "strike_contributions",
    "scheduled_expiry_weekday",
    "variance_explained_by_dte",
    "build_hhi_stats",
]

# ── Expiry weekday ────────────────────────────────────────────────────────────
# Mirrors ``analysis.analogue_cycles._expiry_weekday``, which is the canonical
# statement of the regime (NSE/BSE circulars, effective 2025-09-01). Duplicated
# rather than imported because that name is private and options/ should not reach
# into analysis/; ``test_hhi_stats.py`` asserts the two agree so they cannot drift.
EXPIRY_WEEKDAY_CUTOVER = date(2025, 9, 1)


def scheduled_expiry_weekday(underlying: str, on: date) -> int:
    """Python weekday (Mon=0) of the scheduled weekly expiry, before holiday snap."""
    u = (underlying or "").upper()
    revised = on >= EXPIRY_WEEKDAY_CUTOVER
    if u == "NIFTY":
        return 1 if revised else 3
    if u == "BANKNIFTY":
        return 1 if revised else 2
    if u == "SENSEX":
        return 3 if revised else 1
    return 1 if revised else 3


def infer_dte(day: date | str, underlying: str) -> int | None:
    """Calendar days from ``day`` to the next scheduled weekly expiry.

    Returns ``0`` on expiry day itself. ``None`` when the date is unparseable.

    This is *scheduled* expiry — it does not snap for exchange holidays, so a
    holiday-shortened week can read one day long. That error is bounded at one
    day and never crosses a bucket boundary except at the 2/3 edge, which is why
    :data:`DTE_BUCKETS` puts its seam there rather than mid-week.
    """
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day)
        except ValueError:
            return None
    if not isinstance(day, date):
        return None
    target = scheduled_expiry_weekday(underlying, day)
    return (target - day.weekday()) % 7


# ── DTE buckets ───────────────────────────────────────────────────────────────
# Seams chosen from the desk's own documented ranges (README: 0-DTE 0.18-0.32,
# 1-2 DTE 0.08-0.13, weekly/monthly 0.02-0.07) rather than picked for round
# numbers. Expiry day is its own bucket because it is not a tail of the others.
DTE_BUCKETS: tuple[tuple[int, int | None, str], ...] = (
    (0, 0, "0"),
    (1, 2, "1-2"),
    (3, 7, "3-7"),
    (8, None, "8+"),
)


def bucket_for_dte(dte: int | None) -> str | None:
    if dte is None:
        return None
    for lo, hi, label in DTE_BUCKETS:
        if dte >= lo and (hi is None or dte <= hi):
            return label
    return None


# ── 1. Window normalisation ───────────────────────────────────────────────────
def normalized_hhi(hhi: float | None, n_strikes: int | None) -> float | None:
    """Hall-Tideman normalised HHI: rescales the ``1/N`` floor away.

    ``H* = (H - 1/N) / (1 - 1/N)``, mapping ``[1/N, 1] -> [0, 1]`` so that two
    sessions measured on different ``strike_window`` settings are comparable.
    Returns ``None`` when ``N < 2`` (the map is undefined at ``N = 1``).
    """
    if hhi is None or n_strikes is None or n_strikes < 2:
        return None
    floor = 1.0 / float(n_strikes)
    return (float(hhi) - floor) / (1.0 - floor)


# ── 2. Uncertainty ────────────────────────────────────────────────────────────
def hhi_with_se(
    mass: Sequence[float],
    mass_se: Sequence[float] | None = None,
    *,
    n_strikes: int | None = None,
) -> dict[str, float | None]:
    """HHI and its delta-method standard error under per-leg mass errors.

    With ``p_i = m_i / M`` and ``H = sum p_i^2``::

        dH/dm_i = (2/M) * (p_i - H)

    so a strike whose share equals ``H`` contributes *nothing* to the variance —
    only strikes far from the mean share drive the uncertainty. Errors are taken
    as independent across legs, which is the right first cut: the dominant term
    is per-leg quote noise, not a common IV shift.

    ``mass_se=None`` returns the point estimate with ``se=None`` rather than
    pretending to a precision that was never supplied.
    """
    m = [float(x) for x in mass if x is not None and math.isfinite(float(x))]
    if not m:
        return {"hhi": None, "se": None, "hhi_norm": None, "se_norm": None}
    total = math.fsum(m)
    if total <= 0:
        return {"hhi": None, "se": None, "hhi_norm": None, "se_norm": None}

    p = [x / total for x in m]
    hhi = math.fsum(v * v for v in p)

    se: float | None = None
    if mass_se is not None:
        s = [float(x) if x is not None and math.isfinite(float(x)) else 0.0 for x in mass_se]
        if len(s) == len(p):
            var = math.fsum(
                ((2.0 / total) * (p_i - hhi) * s_i) ** 2
                for p_i, s_i in zip(p, s, strict=False)
            )
            se = math.sqrt(var)

    out: dict[str, float | None] = {"hhi": hhi, "se": se}
    if n_strikes and n_strikes >= 2:
        scale = 1.0 - 1.0 / float(n_strikes)
        out["hhi_norm"] = normalized_hhi(hhi, n_strikes)
        out["se_norm"] = None if se is None else se / scale
    else:
        out["hhi_norm"] = None
        out["se_norm"] = None
    return out


def dropped_leg_inflation(kept_mass: float | None, dropped_mass: float | None) -> dict[str, float | None]:
    """How much excluding illiquid legs inflates HHI.

    If dropped legs carried share ``s`` of the true mass, renormalising the
    survivors scales every remaining share by ``1/(1-s)`` and therefore HHI by
    roughly ``1/(1-s)^2`` — before accounting for the squares that left with them,
    which makes this a *lower* bound on the distortion.
    """
    if kept_mass is None or dropped_mass is None:
        return {"dropped_share": None, "inflation": None}
    kept = float(kept_mass)
    dropped = float(dropped_mass)
    total = kept + dropped
    if total <= 0 or dropped < 0:
        return {"dropped_share": None, "inflation": None}
    s = dropped / total
    if s >= 1.0:
        return {"dropped_share": 1.0, "inflation": None}
    return {"dropped_share": s, "inflation": 1.0 / ((1.0 - s) ** 2)}


# ── 3. Shape ──────────────────────────────────────────────────────────────────
DEFAULT_HILL_ORDERS: tuple[float, ...] = (0.0, 0.5, 1.0, 2.0, 3.0, math.inf)


def hill_number(shares: Iterable[float], alpha: float) -> float | None:
    """Hill number of order ``alpha`` — the effective-strike count at that order.

    ``N_0`` = richness, ``N_1`` = exp(Shannon), ``N_2`` = 1/HHI,
    ``N_inf`` = 1/max share. A profile that falls steeply from ``N_0`` to ``N_2``
    means one strike dominates; a flat profile means broad, even mass.
    """
    p = [float(x) for x in shares if x is not None and float(x) > 0]
    if not p:
        return None
    total = math.fsum(p)
    if total <= 0:
        return None
    p = [x / total for x in p]

    if math.isinf(alpha):
        return 1.0 / max(p)
    if math.isclose(alpha, 1.0):
        return math.exp(-math.fsum(x * math.log(x) for x in p))
    s = math.fsum(x**alpha for x in p)
    if s <= 0:
        return None
    return s ** (1.0 / (1.0 - alpha))


def hill_profile(
    shares: Iterable[float], orders: Sequence[float] = DEFAULT_HILL_ORDERS
) -> list[dict[str, Any]]:
    p = list(shares)
    return [
        {"order": ("inf" if math.isinf(a) else a), "n_eff": hill_number(p, a)}
        for a in orders
    ]


# ── 4. DTE-conditioned cohorts ────────────────────────────────────────────────
def _percentile_inclusive(value: float, sample: Sequence[float]) -> float | None:
    if not sample:
        return None
    return 100.0 * sum(1 for v in sample if v <= value) / len(sample)


def cohort_summary(
    series: Sequence[dict[str, Any]],
    *,
    underlying: str,
    today_hhi: float | None,
    today_date: date | str | None = None,
    hhi_key: str = "hhi",
) -> dict[str, Any]:
    """Compare today against a DTE-matched cohort instead of a mixed sample.

    Returns both readings — matched and mixed — because the *difference* between
    them is the finding. Rows whose date will not parse are skipped rather than
    silently bucketed.

    Deliberately reports ``percentile: None`` when the cohort is smaller than
    :data:`MIN_COHORT_FOR_PERCENTILE`. A percentile over five observations can
    take only five values; quoting one implies a resolution the sample does not
    have. The cohort mean and count are reported regardless.
    """
    rows: list[tuple[int, float]] = []
    for r in series:
        v = r.get(hhi_key)
        if v is None:
            continue
        d = infer_dte(r.get("date"), underlying)
        if d is None:
            continue
        try:
            rows.append((d, float(v)))
        except (TypeError, ValueError):
            continue

    all_vals = [v for _, v in rows]
    out: dict[str, Any] = {
        "n_total": len(rows),
        "today_dte": infer_dte(today_date, underlying) if today_date else None,
        "today_bucket": None,
        "cohort": None,
        "mixed": None,
        "eta_sq": variance_explained_by_dte(rows),
        "min_cohort_for_percentile": MIN_COHORT_FOR_PERCENTILE,
    }
    if today_hhi is None or not rows:
        return out

    h = float(today_hhi)
    out["mixed"] = {
        "n": len(all_vals),
        "mean": math.fsum(all_vals) / len(all_vals),
        "percentile": _percentile_inclusive(h, all_vals),
    }

    bucket = bucket_for_dte(out["today_dte"])
    out["today_bucket"] = bucket
    if bucket is None:
        return out

    cohort = [v for d, v in rows if bucket_for_dte(d) == bucket]
    if cohort:
        mean = math.fsum(cohort) / len(cohort)
        out["cohort"] = {
            "bucket": bucket,
            "n": len(cohort),
            "mean": mean,
            "vs_mean_pct": (h / mean - 1.0) * 100.0 if mean else None,
            "percentile": (
                _percentile_inclusive(h, cohort)
                if len(cohort) >= MIN_COHORT_FOR_PERCENTILE
                else None
            ),
        }
        mixed_mean = out["mixed"]["mean"]
        out["mixed"]["vs_mean_pct"] = (h / mixed_mean - 1.0) * 100.0 if mixed_mean else None
    return out


MIN_COHORT_FOR_PERCENTILE = 8


# ── Per-strike contribution ───────────────────────────────────────────────────
def strike_contributions(
    strikes: Sequence[float], mass: Sequence[float]
) -> list[dict[str, Any]]:
    """Decompose HHI into the per-strike terms that produce it.

    ``H = sum p_i^2``, so strike ``i`` contributes exactly ``p_i^2`` and owns
    ``p_i^2 / H`` of the index. Because the term is *squared*, the index is far
    more concentrated than the book: a strike with twice the share contributes
    four times the HHI. That is what this table makes visible, and it is the
    reason it earns a place on a tab about measurement rather than about market
    structure — it says **which strikes' data quality the index actually depends
    on.** A single illiquid leg near the top of this list makes HHI fragile in a
    way the headline number cannot show.

    Rows are returned sorted by contribution, descending, with a running
    cumulative share so "the top five strikes produce X% of the index" is read
    directly rather than computed by eye.

    ``d_hhi`` is the delta-method sensitivity ``dH/dm_i`` scaled by total mass —
    i.e. ``2 * (p_i - H)``. It is signed on purpose: strikes *below* the mean
    share push the index **down** when their mass grows. A strike sitting near
    ``p_i = H`` moves the index barely at all however its mass changes, which is
    the same fact :func:`hhi_with_se` uses for the variance.
    """
    pairs = [
        (float(k), float(m))
        for k, m in zip(strikes, mass, strict=False)
        if k is not None and m is not None and math.isfinite(float(m)) and float(m) > 0
    ]
    if not pairs:
        return []
    total = math.fsum(m for _, m in pairs)
    if total <= 0:
        return []

    hhi = math.fsum((m / total) ** 2 for _, m in pairs)
    if hhi <= 0:
        return []

    rows = []
    for k, m in pairs:
        p = m / total
        rows.append(
            {
                "strike": k,
                "share": p,
                "share_sq": p * p,
                "pct_of_index": 100.0 * (p * p) / hhi,
                "d_hhi": 2.0 * (p - hhi),
            }
        )
    rows.sort(key=lambda r: r["share_sq"], reverse=True)

    run = 0.0
    for rank, r in enumerate(rows, start=1):
        run += r["pct_of_index"]
        r["rank"] = rank
        r["cum_pct"] = min(100.0, run)
    return rows


def variance_explained_by_dte(rows: Sequence[tuple[int, float]]) -> float | None:
    """One-way eta^2 of HHI on DTE bucket — how much of the spread is calendar.

    Measured 0.76 on the desk's own 22-session NIFTY history, which is the whole
    argument for conditioning: three-quarters of what a mixed percentile reports
    as "unusual concentration" is the position in the expiry cycle.
    """
    vals = [v for _, v in rows]
    if len(vals) < 3:
        return None
    grand = math.fsum(vals) / len(vals)
    sst = math.fsum((v - grand) ** 2 for v in vals)
    if sst <= 0:
        return None
    groups: dict[str, list[float]] = {}
    for d, v in rows:
        b = bucket_for_dte(d)
        if b is not None:
            groups.setdefault(b, []).append(v)
    if len(groups) < 2:
        return None
    ssb = math.fsum(
        len(g) * (math.fsum(g) / len(g) - grand) ** 2 for g in groups.values()
    )
    return max(0.0, min(1.0, ssb / sst))


# ── Assembler ─────────────────────────────────────────────────────────────────
def build_hhi_stats(
    *,
    underlying: str,
    concentration: dict[str, Any] | None,
    strike_window: int | None,
    daily_series: Sequence[dict[str, Any]] | None = None,
    shares: Sequence[float] | None = None,
    mass: Sequence[float] | None = None,
    strikes: Sequence[float] | None = None,
    mass_se: Sequence[float] | None = None,
    kept_mass: float | None = None,
    dropped_mass: float | None = None,
    legs_quoted: int | None = None,
    legs_total: int | None = None,
    today: date | str | None = None,
) -> dict[str, Any]:
    """Assemble the additive ``hhi_stats`` block. Never raises on partial input.

    Every field is independently ``None``-able: a caller with no ``mass_se`` still
    gets the normalised index and the cohort comparison. Absent inputs read as
    unmeasured, never as zero.
    """
    conc = concentration or {}
    hhi = conc.get("hhi")
    n_strikes = None if strike_window is None else 2 * int(strike_window) + 1

    stats: dict[str, Any] = {
        "n_strikes": n_strikes,
        "floor": (1.0 / n_strikes) if n_strikes and n_strikes >= 2 else None,
        "hhi": hhi,
        "hhi_norm": normalized_hhi(hhi, n_strikes),
        "se": None,
        "se_norm": None,
        "hill": None,
        "quality": dropped_leg_inflation(kept_mass, dropped_mass),
        "cohort": None,
        "contributions": None,
    }

    # Leg counts are a *different* quantity from the mass share above and are
    # labelled as such: a leg that failed the spread filter may have carried any
    # amount of gamma. This says how hard the filter is biting, not how much mass
    # left. Reported because it is available and moves; the mass-weighted share
    # stays null until per-leg mass survives the filter step.
    if legs_quoted is not None and legs_total:
        stats["quality"]["legs_quoted"] = int(legs_quoted)
        stats["quality"]["legs_total"] = int(legs_total)
        stats["quality"]["legs_dropped_pct"] = (
            100.0 * (int(legs_total) - int(legs_quoted)) / int(legs_total)
        )
    else:
        stats["quality"]["legs_quoted"] = None
        stats["quality"]["legs_total"] = None
        stats["quality"]["legs_dropped_pct"] = None

    if mass:
        m = hhi_with_se(mass, mass_se, n_strikes=n_strikes)
        stats["se"] = m.get("se")
        stats["se_norm"] = m.get("se_norm")

    if shares:
        stats["hill"] = hill_profile(shares)
    elif mass:
        stats["hill"] = hill_profile(mass)

    if strikes and mass:
        stats["contributions"] = strike_contributions(strikes, mass)

    if daily_series:
        stats["cohort"] = cohort_summary(
            daily_series,
            underlying=underlying,
            today_hhi=hhi,
            today_date=today or (daily_series[-1].get("date") if daily_series else None),
        )
    return stats
