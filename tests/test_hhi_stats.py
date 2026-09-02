"""Tests for options/hhi_stats.py — the additive measurement-quality layer."""

from __future__ import annotations

import math
from datetime import date

import pytest

from options.hhi_stats import (
    DEFAULT_HILL_ORDERS,
    MIN_COHORT_FOR_PERCENTILE,
    bucket_for_dte,
    build_hhi_stats,
    cohort_summary,
    dropped_leg_inflation,
    hhi_with_se,
    hill_number,
    hill_profile,
    infer_dte,
    normalized_hhi,
    scheduled_expiry_weekday,
    strike_contributions,
    variance_explained_by_dte,
)

# The live NIFTY day-end series this module was designed against, read out of
# data/gamma_density_history.json on 2026-08-31. Hardcoding it is safe: these are
# closed sessions whose values cannot change, and the point of the fixture is to
# pin the *measured* behaviour the audit reported.
LIVE_NIFTY = [
    ("2026-08-03", 0.0734), ("2026-08-04", 0.3211), ("2026-08-05", 0.0587),
    ("2026-08-06", 0.0664), ("2026-08-07", 0.0536), ("2026-08-10", 0.0659),
    ("2026-08-11", 0.3878), ("2026-08-12", 0.0692), ("2026-08-13", 0.0773),
    ("2026-08-14", 0.0893), ("2026-08-17", 0.1373), ("2026-08-18", 0.8344),
    ("2026-08-19", 0.0826), ("2026-08-20", 0.0918), ("2026-08-21", 0.1034),
    ("2026-08-24", 0.1235), ("2026-08-25", 0.4258), ("2026-08-26", 0.0830),
    ("2026-08-27", 0.0748), ("2026-08-28", 0.0858), ("2026-08-31", 0.1029),
]


def _series():
    return [{"date": d, "hhi": h} for d, h in LIVE_NIFTY]


# ── Expiry weekday ────────────────────────────────────────────────────────────
def test_expiry_weekday_matches_analogue_cycles():
    """The rule is duplicated from analysis/. Assert it cannot drift."""
    from analysis.analogue_cycles import _expiry_weekday

    for u in ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY"):
        for d in (date(2025, 8, 1), date(2025, 9, 1), date(2026, 8, 31)):
            assert scheduled_expiry_weekday(u, d) == _expiry_weekday(u, d), (u, d)


def test_infer_dte_on_known_sessions():
    # 2026-09-01 is a Tuesday; both captures in the audit confirmed 1 DTE.
    assert date(2026, 9, 1).weekday() == 1
    assert infer_dte("2026-08-31", "NIFTY") == 1     # Monday
    assert infer_dte("2026-09-01", "NIFTY") == 0     # expiry day itself
    assert infer_dte("2026-08-26", "NIFTY") == 6     # Wednesday -> next Tue
    assert infer_dte("not-a-date", "NIFTY") is None


def test_infer_dte_respects_the_cutover():
    """Pre-2025-09-01 NIFTY expired Thursday, so the same weekday gives a different DTE."""
    assert infer_dte(date(2025, 8, 4), "NIFTY") == 3    # Mon -> Thu
    assert infer_dte(date(2026, 8, 3), "NIFTY") == 1    # Mon -> Tue


# ── Normalisation ─────────────────────────────────────────────────────────────
def test_normalized_hhi_rescales_the_floor():
    # N=41 (window +/-20): floor 1/41. The live 0.103 maps to ~0.081.
    assert normalized_hhi(0.103, 41) == pytest.approx(0.0806, abs=1e-4)
    # An index sitting exactly on its floor is zero concentration above it.
    assert normalized_hhi(1 / 41, 41) == pytest.approx(0.0, abs=1e-12)
    assert normalized_hhi(1.0, 41) == pytest.approx(1.0)


def test_normalized_hhi_makes_windows_comparable():
    """The whole point: a wider window must not read as a less concentrated book."""
    # Same book, two windows. Raw H falls purely because the floor fell.
    narrow, wide = normalized_hhi(0.103, 41), normalized_hhi(0.103 - (1 / 41 - 1 / 61), 61)
    assert narrow == pytest.approx(wide, abs=1e-3)


def test_normalized_hhi_undefined_at_n_below_two():
    assert normalized_hhi(0.5, 1) is None
    assert normalized_hhi(0.5, None) is None
    assert normalized_hhi(None, 41) is None


# ── Uncertainty ───────────────────────────────────────────────────────────────
def test_hhi_with_se_matches_plain_hhi():
    mass = [10.0, 20.0, 30.0, 40.0]
    out = hhi_with_se(mass)
    shares = [m / 100.0 for m in mass]
    assert out["hhi"] == pytest.approx(sum(s * s for s in shares))
    assert out["se"] is None  # no SE supplied -> no SE claimed


def test_strike_at_the_mean_share_contributes_no_variance():
    """The structural claim in the docstring, asserted rather than asserted-in-prose."""
    mass = [25.0, 25.0, 25.0, 25.0]        # every share = 0.25, H = 0.25
    out = hhi_with_se(mass, [1.0, 0.0, 0.0, 0.0])
    # p_i - H = 0 for every leg, so any per-leg error cancels to first order.
    assert out["se"] == pytest.approx(0.0, abs=1e-12)


def test_se_grows_with_distance_from_the_mean_share():
    concentrated = hhi_with_se([70.0, 10.0, 10.0, 10.0], [1.0, 1.0, 1.0, 1.0])["se"]
    even = hhi_with_se([25.0, 25.0, 25.0, 25.0], [1.0, 1.0, 1.0, 1.0])["se"]
    assert concentrated > even


def test_hhi_with_se_handles_degenerate_input():
    for bad in ([], [0.0, 0.0], [None, None]):
        out = hhi_with_se(bad)
        assert out["hhi"] is None and out["se"] is None


def test_dropped_leg_inflation():
    out = dropped_leg_inflation(kept_mass=90.0, dropped_mass=10.0)
    assert out["dropped_share"] == pytest.approx(0.10)
    assert out["inflation"] == pytest.approx(1 / 0.81, abs=1e-6)   # ~23% inflation
    assert dropped_leg_inflation(100.0, 0.0)["inflation"] == pytest.approx(1.0)
    assert dropped_leg_inflation(None, 5.0)["inflation"] is None


# ── Hill profile ──────────────────────────────────────────────────────────────
def test_hill_numbers_at_the_named_orders():
    p = [0.4, 0.3, 0.2, 0.1]
    assert hill_number(p, 0.0) == pytest.approx(4.0)                  # richness
    assert hill_number(p, 2.0) == pytest.approx(1 / sum(x * x for x in p))  # 1/HHI
    assert hill_number(p, math.inf) == pytest.approx(1 / 0.4)         # 1/max
    shannon = -sum(x * math.log(x) for x in p)
    assert hill_number(p, 1.0) == pytest.approx(math.exp(shannon))


def test_hill_is_monotone_non_increasing_in_order():
    p = [0.5, 0.25, 0.15, 0.10]
    vals = [hill_number(p, a) for a in DEFAULT_HILL_ORDERS]
    for a, b in zip(vals, vals[1:], strict=False):
        assert a >= b - 1e-9


def test_hill_normalises_unnormalised_mass():
    """Callers pass raw gamma mass, not shares. Both must agree."""
    assert hill_number([40.0, 30.0, 20.0, 10.0], 2.0) == pytest.approx(
        hill_number([0.4, 0.3, 0.2, 0.1], 2.0)
    )


def test_hill_profile_shape_and_empty_input():
    prof = hill_profile([0.4, 0.3, 0.3])
    assert [r["order"] for r in prof] == [0.0, 0.5, 1.0, 2.0, 3.0, "inf"]
    assert all(r["n_eff"] is None for r in hill_profile([]))


# ── Buckets and cohorts ───────────────────────────────────────────────────────
def test_bucket_boundaries():
    assert bucket_for_dte(0) == "0"
    assert bucket_for_dte(1) == "1-2" and bucket_for_dte(2) == "1-2"
    assert bucket_for_dte(3) == "3-7" and bucket_for_dte(7) == "3-7"
    assert bucket_for_dte(8) == "8+" and bucket_for_dte(45) == "8+"
    assert bucket_for_dte(None) is None


def test_eta_squared_reproduces_the_audit_measurement():
    """0.76 of HHI variance is DTE, not book structure — the headline finding."""
    rows = [(infer_dte(d, "NIFTY"), h) for d, h in LIVE_NIFTY]
    eta = variance_explained_by_dte(rows)
    assert eta == pytest.approx(0.76, abs=0.03)


def test_cohort_flips_the_headline_reading():
    """The board read -36% (unusually spread); DTE-matched reads ~+2% (average)."""
    out = cohort_summary(
        _series(), underlying="NIFTY", today_hhi=0.1029, today_date="2026-08-31"
    )
    assert out["today_dte"] == 1
    assert out["today_bucket"] == "1-2"
    assert out["mixed"]["vs_mean_pct"] < -20.0        # mixed sample: looks spread
    assert abs(out["cohort"]["vs_mean_pct"]) < 15.0   # matched: unremarkable
    # The sign of the conclusion actually reverses.
    assert out["mixed"]["vs_mean_pct"] < 0 < out["cohort"]["vs_mean_pct"]


def test_cohort_withholds_percentile_when_the_sample_is_too_thin():
    """A percentile over 5 observations can take 5 values. Do not quote one."""
    thin = [{"date": d, "hhi": h} for d, h in LIVE_NIFTY if infer_dte(d, "NIFTY") == 0]
    assert 0 < len(thin) < MIN_COHORT_FOR_PERCENTILE
    out = cohort_summary(thin, underlying="NIFTY", today_hhi=0.40, today_date="2026-09-01")
    assert out["cohort"]["n"] == len(thin)
    assert out["cohort"]["mean"] is not None      # the mean is still reported
    assert out["cohort"]["percentile"] is None    # the percentile is not


def test_cohort_skips_unparseable_rows_rather_than_bucketing_them():
    series = _series() + [{"date": "garbage", "hhi": 0.5}, {"date": "2026-08-20"}]
    out = cohort_summary(
        series, underlying="NIFTY", today_hhi=0.1029, today_date="2026-08-31"
    )
    assert out["n_total"] == len(LIVE_NIFTY)


def test_cohort_on_empty_series_is_all_none_not_zero():
    out = cohort_summary([], underlying="NIFTY", today_hhi=0.10, today_date="2026-08-31")
    assert out["n_total"] == 0
    assert out["cohort"] is None and out["mixed"] is None
    assert out["eta_sq"] is None


# ── Assembler ─────────────────────────────────────────────────────────────────
def test_build_hhi_stats_full():
    out = build_hhi_stats(
        underlying="NIFTY",
        concentration={"hhi": 0.103},
        strike_window=20,
        daily_series=_series(),
        mass=[40.0, 30.0, 20.0, 10.0],
        mass_se=[1.0, 1.0, 1.0, 1.0],
        kept_mass=132.0,
        dropped_mass=78.0,
        today="2026-08-31",
    )
    assert out["n_strikes"] == 41
    assert out["floor"] == pytest.approx(1 / 41)
    assert out["hhi_norm"] == pytest.approx(0.0806, abs=1e-4)
    assert out["se"] is not None and out["se_norm"] > out["se"]
    assert out["hill"] and out["cohort"]["today_dte"] == 1
    assert out["quality"]["dropped_share"] == pytest.approx(78 / 210)


def test_build_hhi_stats_degrades_field_by_field():
    """Partial input must yield partial output, never an exception."""
    out = build_hhi_stats(
        underlying="NIFTY", concentration=None, strike_window=None
    )
    assert out["hhi"] is None and out["hhi_norm"] is None
    assert out["se"] is None and out["cohort"] is None
    assert out["quality"]["dropped_share"] is None

    # Concentration but no window: the raw index survives, the normalised one cannot.
    out2 = build_hhi_stats(
        underlying="NIFTY", concentration={"hhi": 0.11}, strike_window=None
    )
    assert out2["hhi"] == 0.11 and out2["hhi_norm"] is None


def test_build_hhi_stats_never_writes_anything(tmp_path, monkeypatch):
    """Pure functions. The store guard would catch a write, but assert intent."""
    import options.hhi_stats as hs

    assert not hasattr(hs, "data_dir")
    assert not any(k.startswith("save") or k.startswith("append") for k in dir(hs))


# ── Per-strike contribution ───────────────────────────────────────────────────
def test_contributions_sum_to_the_index():
    """Every term is p_i^2 and they must account for 100% of HHI, exactly."""
    strikes = [24000.0, 24050.0, 24100.0, 24150.0]
    mass = [10.0, 20.0, 30.0, 40.0]
    rows = strike_contributions(strikes, mass)
    assert len(rows) == 4
    assert sum(r["share_sq"] for r in rows) == pytest.approx(0.30)   # 0.1^2+..+0.4^2
    assert sum(r["pct_of_index"] for r in rows) == pytest.approx(100.0)
    assert rows[-1]["cum_pct"] == pytest.approx(100.0)


def test_contributions_are_ranked_by_contribution_not_by_strike():
    rows = strike_contributions([24000.0, 24050.0, 24100.0], [10.0, 50.0, 20.0])
    assert [r["strike"] for r in rows] == [24050.0, 24100.0, 24000.0]
    assert [r["rank"] for r in rows] == [1, 2, 3]
    # cumulative is monotone non-decreasing
    cums = [r["cum_pct"] for r in rows]
    assert all(a <= b + 1e-9 for a, b in zip(cums, cums[1:], strict=False))


def test_squaring_makes_the_index_more_concentrated_than_the_book():
    """The point of the panel: 2x the share is 4x the HHI contribution."""
    rows = strike_contributions([1.0, 2.0], [2.0, 1.0])
    big, small = rows[0], rows[1]
    assert big["share"] / small["share"] == pytest.approx(2.0)
    assert big["share_sq"] / small["share_sq"] == pytest.approx(4.0)


def test_d_hhi_is_signed_and_vanishes_at_the_mean_share():
    """Strikes below the mean share push the index down as their mass grows."""
    rows = strike_contributions([1.0, 2.0, 3.0, 4.0], [25.0, 25.0, 25.0, 25.0])
    # every share == H == 0.25, so every sensitivity is zero
    assert all(r["d_hhi"] == pytest.approx(0.0, abs=1e-12) for r in rows)

    rows2 = strike_contributions([1.0, 2.0, 3.0, 4.0], [70.0, 10.0, 10.0, 10.0])
    assert rows2[0]["d_hhi"] > 0        # the leader pushes the index up
    assert rows2[-1]["d_hhi"] < 0       # a small strike pushes it down


def test_contributions_skip_zero_and_bad_mass():
    rows = strike_contributions(
        [24000.0, 24050.0, 24100.0, 24150.0], [10.0, 0.0, None, float("nan")]
    )
    assert [r["strike"] for r in rows] == [24000.0]
    assert rows[0]["pct_of_index"] == pytest.approx(100.0)


def test_contributions_degenerate_inputs():
    assert strike_contributions([], []) == []
    assert strike_contributions([24000.0], [0.0]) == []


def test_build_hhi_stats_includes_contributions():
    out = build_hhi_stats(
        underlying="NIFTY",
        concentration={"hhi": 0.30},
        strike_window=2,
        strikes=[24000.0, 24050.0, 24100.0, 24150.0],
        mass=[10.0, 20.0, 30.0, 40.0],
    )
    assert out["contributions"] is not None
    assert len(out["contributions"]) == 4
    assert out["contributions"][0]["strike"] == 24150.0


def test_build_hhi_stats_contributions_none_without_strikes():
    out = build_hhi_stats(
        underlying="NIFTY", concentration={"hhi": 0.3}, strike_window=20,
        mass=[10.0, 20.0],
    )
    assert out["contributions"] is None
