"""Tests for the Volume Footprint port.

These are not smoke tests. Each one pins an *invariant the original claims*, so
a regression shows up as a broken promise rather than a changed number:

* mass conservation - frame rows plus off-frame equal the bar volume;
* the residual self-check stays inside its own PPM tolerance;
* the overlap coefficient obeys its mathematical bounds and its two limits
  (identical sides -> 1, disjoint sides -> 0);
* the diagonal imbalance rule fires diagonally, and stays silent on untraded
  rows rather than treating them as zero opposing volume;
* the value area expansion terminates and covers at least its target share.

Run with:  pytest tests/ -v
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

# Vendored upstream package. Only divergence from the original test file: the
# import path is explicit instead of a sys.path insert, so it resolves the same
# way under pytest, uvicorn and the background schedulers.
from vendor.volume_footprint import (
    Bar,
    BarSeries,
    FootprintRow,
    Settings,
    VolumeEngine,
    apply_engine,
    build_profile,
    compute,
    diagonal_imbalance,
    gauss_view_rows,
    geometric_share,
    norm_cdf_fast,
    norm_cdf_precise,
    point_of_control,
    value_area,
)
from vendor.volume_footprint.engines import intrabar_split  # noqa: E402
from vendor.volume_footprint.profile import ProfileModel, band_mass, density  # noqa: E402
from vendor.volume_footprint.rows import footprint_view_rows  # noqa: E402

TICK = 0.05


def make_bar(o, h, low, c, v, t=None):
    return Bar(time=t or datetime(2026, 8, 19, 9, 15), open=o, high=h, low=low, close=c, volume=v)


def trending_series(n=40, mintick=TICK):
    """Deterministic bars - no RNG, so a failure is always reproducible."""
    bars = []
    price = 100.0
    t0 = datetime(2026, 8, 19, 9, 15)
    for i in range(n):
        drift = 0.15 if i % 3 else -0.05
        o = price
        c = o + drift
        h = max(o, c) + 0.10
        low = min(o, c) - 0.10
        q = lambda x: round(x / mintick) * mintick  # noqa: E731
        bars.append(
            Bar(t0 + timedelta(minutes=5 * i), q(o), q(h), q(low), q(c), 1000.0 + 10 * i)
        )
        price = c
    return apply_engine(BarSeries(bars, mintick=mintick, symbol="T"), VolumeEngine.GEOMETRIC)


# ------------------------------------------------------------------ mathkit

def test_norm_cdf_fast_matches_precise_within_its_stated_error():
    """A&S 26.2.17 claims |error| < 7.5e-8. Hold it to that across the line."""
    for z in [x / 10.0 for x in range(-60, 61)]:
        assert abs(norm_cdf_fast(z) - norm_cdf_precise(z)) < 7.5e-8


def test_norm_cdf_precise_known_values():
    assert norm_cdf_precise(0.0) == pytest.approx(0.5, abs=1e-15)
    assert norm_cdf_precise(1.959963984540054) == pytest.approx(0.975, abs=1e-12)
    assert norm_cdf_precise(-8.0) == pytest.approx(6.2209605742718e-16, rel=1e-6)


# ------------------------------------------------------------------ engines

def test_geometric_share_endpoints_and_flat_bar():
    assert geometric_share(110.0, 100.0, 110.0) == 1.0   # closed on the high
    assert geometric_share(110.0, 100.0, 100.0) == 0.0   # closed on the low
    assert geometric_share(110.0, 100.0, 105.0) == 0.5
    assert geometric_share(100.0, 100.0, 100.0) == 0.5   # zero range splits evenly


def test_geometric_split_conserves_bar_volume():
    bar = make_bar(100.0, 103.0, 99.0, 101.5, 5000.0)
    series = apply_engine(BarSeries([bar], mintick=TICK), VolumeEngine.GEOMETRIC)
    b = series.last
    assert b.buy_volume + b.sell_volume == pytest.approx(5000.0)


def test_intrabar_split_conserves_volume_and_reads_direction():
    ibs = [
        make_bar(100.0, 100.5, 100.0, 100.4, 100.0),   # up
        make_bar(100.4, 100.4, 99.9, 100.0, 200.0),    # down
        make_bar(100.0, 100.2, 100.0, 100.0, 50.0),    # doji, prev close 100.0 -> split
    ]
    up, down = intrabar_split(ibs)
    assert up + down == pytest.approx(350.0)
    assert up == pytest.approx(125.0)
    assert down == pytest.approx(225.0)


def test_intrabar_split_reports_missing_data_as_none_not_zero():
    """The purity rule: an absent reading must never look like absent trade."""
    assert intrabar_split([]) == (None, None)


def test_missing_engine_data_excludes_a_bar_from_the_window():
    series = trending_series(12)
    holed = [
        Bar(b.time, b.open, b.high, b.low, b.close, b.volume, None, None) if i == len(series) - 2
        else b
        for i, b in enumerate(series.bars)
    ]
    res = compute(BarSeries(holed, mintick=TICK), Settings(window_bars=5, view_ticks=6))
    assert all(c.offset != 1 for c in res.columns)


# --------------------------------------------------------------------- rows

def test_gauss_rows_conserve_mass_inside_a_wide_frame():
    """Frame rows + off-frame must re-add to the input volume, per side."""
    split = gauss_view_rows(
        buy_volume=700.0, sell_volume=300.0,
        high=101.0, low=99.0, close=100.4,
        axis_tick=int(round(100.0 / TICK)), view_ticks=60,
        tick=TICK, bell_div=3.0, imb_pct=300.0,
    )
    assert split.frame_buy + split.off_buy == pytest.approx(700.0, rel=1e-9)
    assert split.frame_sell + split.off_sell == pytest.approx(300.0, rel=1e-9)


def test_gauss_rows_conserve_mass_with_a_deliberately_narrow_frame():
    """The off-frame sums exist precisely so a tight frame loses nothing."""
    split = gauss_view_rows(
        700.0, 300.0, 101.0, 99.0, 100.4,
        axis_tick=int(round(100.0 / TICK)), view_ticks=2,
        tick=TICK, bell_div=3.0, imb_pct=300.0,
    )
    assert split.frame_buy + split.off_buy == pytest.approx(700.0, rel=1e-9)
    assert split.frame_sell + split.off_sell == pytest.approx(300.0, rel=1e-9)
    assert split.off_buy > 0.0  # a 2-tick frame cannot hold a 2-point range


def test_gauss_rows_never_paint_outside_the_bar_range():
    """A bar cannot trade at a price it never printed."""
    split = gauss_view_rows(
        500.0, 500.0, 100.50, 100.00, 100.25,
        axis_tick=int(round(100.25 / TICK)), view_ticks=20,
        tick=TICK, bell_div=3.0, imb_pct=300.0,
    )
    k_min = int(round(100.25 / TICK)) - 20
    for k, v in enumerate(split.buy_rows):
        price = (k_min + k) * TICK
        if v is not None:
            assert 100.00 - TICK <= price <= 100.50 + TICK


def test_flat_bar_puts_all_volume_on_one_row():
    """Zero-range bars are point atoms, not degenerate bells."""
    split = gauss_view_rows(
        600.0, 400.0, 100.0, 100.0, 100.0,
        axis_tick=int(round(100.0 / TICK)), view_ticks=5,
        tick=TICK, bell_div=3.0, imb_pct=300.0,
    )
    filled = [(k, v) for k, v in enumerate(split.buy_rows) if v is not None]
    assert len(filled) == 1
    assert filled[0][1] == pytest.approx(600.0)
    assert split.frame_sell == pytest.approx(400.0)


def test_buy_mass_sits_below_sell_mass_within_one_bar():
    """The model's core claim: buyers worked the lower leg, sellers the upper."""
    axis = int(round(100.0 / TICK))
    split = gauss_view_rows(
        500.0, 500.0, 101.0, 99.0, 100.0, axis, 40, TICK, 3.0, 300.0
    )
    k_min = axis - 40
    def centroid(rows):
        num = sum((k_min + k) * TICK * v for k, v in enumerate(rows) if v)
        den = sum(v for v in rows if v)
        return num / den
    assert centroid(split.buy_rows) < centroid(split.sell_rows)


def test_higher_concentration_narrows_the_distribution():
    """Volume Concentration is sigma = range / concentration, so it tightens."""
    axis = int(round(100.0 / TICK))
    wide = gauss_view_rows(1000.0, 0.0, 101.0, 99.0, 100.0, axis, 40, TICK, 1.0, 300.0)
    tight = gauss_view_rows(1000.0, 0.0, 101.0, 99.0, 100.0, axis, 40, TICK, 8.0, 300.0)
    peak_wide = max(v for v in wide.buy_rows if v is not None)
    peak_tight = max(v for v in tight.buy_rows if v is not None)
    assert peak_tight > peak_wide


def test_diagonal_imbalance_is_actually_diagonal():
    buy = [10.0, 10.0, 400.0, 10.0]
    sell = [10.0, 10.0, 10.0, 10.0]
    b_imb, _ = diagonal_imbalance(buy, sell, 300.0)
    assert b_imb[2] is True          # buy[2]=400 vs sell[1]=10
    assert b_imb == [False, False, True, False]


def test_imbalance_stays_silent_on_untraded_rows():
    """None must not be read as zero opposing volume."""
    buy = [None, 400.0, None]
    sell = [None, None, None]
    b_imb, s_imb = diagonal_imbalance(buy, sell, 300.0)
    assert not any(b_imb)
    assert not any(s_imb)


def test_footprint_rows_land_on_the_lattice_cell_of_their_up_price():
    rows = [
        FootprintRow(up_price=100.00, down_price=99.95, buy_volume=10.0, sell_volume=5.0),
        FootprintRow(up_price=100.05, down_price=100.00, buy_volume=20.0, sell_volume=1.0),
    ]
    axis = int(round(100.00 / TICK))
    split = footprint_view_rows(rows, axis, 3, TICK)
    assert split.buy_rows[3] == 10.0    # axis row
    assert split.buy_rows[4] == 20.0    # one tick up
    assert split.off_buy == 0.0


# ------------------------------------------------------------------ profile

def test_residual_self_check_stays_inside_tolerance():
    """RES below 1 PPM is the tool's own definition of EXACT."""
    m = build_profile(trending_series(30), period=23, bell_div=3.0)
    assert m.residual_ppm is not None
    assert m.residual_ppm < 1.0


def test_band_mass_over_the_whole_span_returns_total_side_volume():
    m = build_profile(trending_series(30), period=23)
    assert m.price_lo is not None
    mass = band_mass(m.price_lo, m.price_hi, m.comps_buy)
    assert mass == pytest.approx(m.sum_buy - m.atom_buy, rel=1e-9)


def test_overlap_is_a_coefficient_between_zero_and_one():
    m = build_profile(trending_series(30), period=23)
    assert m.overlap is not None
    assert 0.0 <= m.overlap <= 100.0 + 1e-9


def test_overlap_is_one_when_both_sides_are_identical():
    """Identical distributions share all their territory."""
    bar = make_bar(100.0, 101.0, 99.0, 100.0, 1000.0)
    s = BarSeries(
        [Bar(bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume, 500.0, 500.0)],
        mintick=TICK,
    )
    # close == midpoint makes mu_b == mu_s, so the two bells coincide exactly.
    m = build_profile(s, period=1)
    assert m.overlap == pytest.approx(100.0, abs=1e-4)


def test_overlap_falls_when_the_sides_separate():
    """A bar closing on its high pushes the two bells apart."""
    def ovl(close):
        b = make_bar(100.0, 101.0, 99.0, close, 1000.0)
        s = apply_engine(BarSeries([b], mintick=TICK), VolumeEngine.GEOMETRIC)
        return build_profile(s, period=1).overlap

    assert ovl(100.0) > ovl(100.8)


def test_flat_bars_do_not_break_the_profile():
    """Limit-locked or illiquid bars are atoms; a naive port divides by zero."""
    bars = [
        Bar(datetime(2026, 8, 19, 9, 15), 100.0, 100.0, 100.0, 100.0, 500.0, 250.0, 250.0),
        Bar(datetime(2026, 8, 19, 9, 20), 100.0, 100.5, 99.8, 100.2, 800.0, 500.0, 300.0),
        Bar(datetime(2026, 8, 19, 9, 25), 100.2, 100.2, 100.2, 100.2, 300.0, 150.0, 150.0),
    ]
    m = build_profile(BarSeries(bars, mintick=TICK), period=3)
    assert m.data_bars == 3
    assert m.atom_buy == pytest.approx(400.0)
    assert m.residual_ppm is not None and m.residual_ppm < 1.0


def test_density_is_zero_outside_every_component_range():
    m = build_profile(trending_series(20), period=10)
    assert m.price_hi is not None
    assert density(m.price_hi + 5.0, m.comps_buy) == 0.0
    assert density(m.price_lo - 5.0, m.comps_sell) == 0.0


def test_tilt_sign_follows_the_dominant_side():
    m = build_profile(trending_series(30), period=23)
    assert m.tilt is not None
    assert (m.tilt > 0) == (m.sum_buy > m.sum_sell)


# ------------------------------------------------------------------ metrics

def test_point_of_control_picks_the_busiest_level_and_keeps_ties_low():
    assert point_of_control([1.0, 9.0, 3.0]) == 1
    assert point_of_control([5.0, 5.0, 1.0]) == 0     # first index wins a tie
    assert point_of_control([0.0, 0.0]) == -1         # nothing traded


def test_value_area_covers_at_least_its_target_share():
    levels = [1.0, 2.0, 5.0, 20.0, 6.0, 3.0, 1.0]
    poc = point_of_control(levels)
    va = value_area(levels, poc, 70.0)
    assert va is not None
    assert va.accumulated >= va.target
    assert va.low_index <= poc <= va.high_index


def test_value_area_terminates_when_the_target_is_unreachable():
    """100 percent of a total that rounding cannot quite reach must still stop."""
    levels = [1.0] * 9
    va = value_area(levels, point_of_control(levels), 100.0)
    assert va is not None
    assert (va.low_index, va.high_index) == (0, 8)


def test_value_area_expands_toward_the_heavier_neighbour():
    levels = [0.0, 0.0, 1.0, 10.0, 8.0, 0.0, 0.0]
    va = value_area(levels, 3, 70.0)
    assert va is not None
    assert va.high_index == 4   # took the 8, not the 1


# ---------------------------------------------------------------- indicator

def test_window_search_skips_bars_that_never_traded_in_the_frame():
    """Every column must be a candle that actually left volume at these prices."""
    res = compute(trending_series(60), Settings(window_bars=5, view_ticks=3))
    axis = res.axis_tick
    for col in res.columns:
        lo = int(round(col.bar.low / TICK))
        hi = int(round(col.bar.high / TICK))
        assert lo <= axis + 3 and hi >= axis - 3


def test_row_sums_equal_the_sum_of_the_columns():
    res = compute(trending_series(60), Settings(window_bars=6, view_ticks=8))
    manual_buy = sum(c.split.frame_buy for c in res.columns)
    assert res.window_total_buy == pytest.approx(manual_buy, rel=1e-12)


def test_window_value_area_brackets_the_window_poc():
    res = compute(trending_series(60), Settings(window_bars=6, view_ticks=10))
    assert res.window_val_price is not None
    assert res.window_val_price <= res.window_poc_price <= res.window_vah_price


def test_chart_value_area_brackets_the_chart_poc():
    res = compute(trending_series(60), Settings(profile_period=23))
    assert res.chart_val_price is not None
    assert res.chart_val_price <= res.chart_poc_price <= res.chart_vah_price


def test_result_reports_exact_when_the_residual_is_small():
    res = compute(trending_series(60))
    assert res.residual_ok
    assert res.residual_label == "EXACT"


def test_axis_row_is_the_lattice_row_of_the_last_close():
    series = trending_series(30)
    res = compute(series)
    assert res.axis_tick == round(series.last.close / TICK)
    assert res.axis_price == pytest.approx(series.last.close, abs=TICK / 2)


def test_end_offset_walks_history():
    """The port's main gain over Pine: evaluate at any bar, not just the last."""
    series = trending_series(60)
    now = compute(series, end_offset=0)
    then = compute(series, end_offset=10)
    assert now.axis_tick != then.axis_tick
    assert then.profile is not None and then.profile.residual_ppm < 1.0


def test_balance_verdict_refuses_to_name_a_side_inside_the_dead_zone():
    """A small tilt must not be dressed up as a direction."""
    m = ProfileModel()
    m.overlap = 40.0          # off balance
    m.tilt = 3.0              # but only 3 percentage points of lean
    assert m.verdict(tilt_threshold=5.0) == "OFF BALANCE"
    assert m.verdict(tilt_threshold=2.0) == "OFF BALANCE TO BUY"
    m.tilt = -8.0
    assert m.verdict(tilt_threshold=5.0) == "OFF BALANCE TO SELL"


def test_balance_verdict_says_balanced_above_the_ovl_boundary():
    m = ProfileModel()
    m.overlap = 80.0
    m.tilt = 40.0             # heavy tilt is irrelevant once OVL says balanced
    assert m.verdict() == "BALANCED"
