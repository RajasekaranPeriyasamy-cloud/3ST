"""Resampling and annualisation tests -- the plumbing the timeframe study rests on.

If ``resample`` is wrong, every "timeframe effect" in the study is an artefact
of the grouping. If ``bars_per_year`` is wrong, every Sharpe is wrong by its
square root. Both are worth pinning down before reading a single result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpairs import bars as B  # noqa: E402


def _session(day: str, n: int = 75) -> pd.DatetimeIndex:
    """One NSE session of 5-minute stamps: 09:15 .. 15:30, 75 bars."""
    return pd.date_range(f"{day} 09:15", periods=n, freq="5min")


def _frame(days: list[str], n: int = 75) -> pd.DataFrame:
    idx = pd.DatetimeIndex(np.concatenate([_session(d, n) for d in days]))
    return pd.DataFrame({"A": np.arange(len(idx), dtype=float),
                         "B": np.arange(len(idx), dtype=float) * 2.0}, index=idx)


def test_5m_is_a_passthrough():
    px = _frame(["2024-01-01", "2024-01-02"])
    out = B.resample(px, "5m")
    pd.testing.assert_frame_equal(out, px)


def test_15m_divides_the_session_exactly():
    px = _frame(["2024-01-01", "2024-01-02"])
    out = B.resample(px, "15m")
    assert len(out) == 2 * 25
    assert out.attrs["stub_fraction"] == 0.0
    # first 15m bar closes on the third 5m bar, 09:25
    assert out.index[0] == pd.Timestamp("2024-01-01 09:25")
    assert out["A"].iloc[0] == 2.0     # last 5m value in the group


def test_coarser_timeframes_leave_a_session_stub():
    """375 minutes does not divide by 30/60/120/240 -- expect a short last bar."""
    px = _frame(["2024-01-01"])
    for tf, full, expected_bars in [("30m", 12, 13), ("60m", 6, 7),
                                    ("2h", 3, 4), ("4h", 1, 2)]:
        out = B.resample(px, tf)
        assert len(out) == expected_bars, f"{tf}: {len(out)} != {expected_bars}"
        assert out.attrs["stub_fraction"] == pytest.approx(1.0 / expected_bars)
        del full


def test_drop_stubs_removes_exactly_the_short_bars():
    px = _frame(["2024-01-01", "2024-01-02"])
    kept = B.resample(px, "60m")
    dropped = B.resample(px, "60m", drop_stubs=True)
    assert len(kept) == 14 and len(dropped) == 12
    assert dropped.attrs["stub_fraction"] == 0.0


def test_grouping_is_session_anchored_not_wall_clock():
    """The bug this guards against.

    ``px.resample('2h')`` anchors to midnight, so its first bin is 08:00-10:00 --
    a bar that straddles the pre-open and mixes the overnight gap into the first
    45 minutes of trade. Session anchoring must start the first bin at 09:15.
    """
    px = _frame(["2024-01-01", "2024-01-02"])
    ours = B.resample(px, "2h")
    theirs = px.resample("2h").last().dropna()

    first_day = ours.index[ours.index.normalize() == pd.Timestamp("2024-01-01")]
    # our first bar of the day closes 2h after 09:15, i.e. at 11:10 (the last
    # 5m stamp inside 09:15-11:15)
    assert first_day[0] == pd.Timestamp("2024-01-01 11:10")
    assert not (theirs.index == pd.Timestamp("2024-01-01 11:10")).any()


def test_no_bar_ever_spans_two_sessions():
    px = _frame(["2024-01-01", "2024-01-02", "2024-01-03"])
    for tf in B.TIMEFRAMES:
        out = B.resample(px, tf)
        per_day = out.index.normalize().value_counts()
        assert len(per_day) == 3, f"{tf} lost or merged a session"


def test_bars_per_year_scales_with_the_timeframe():
    px = _frame([f"2024-01-{d:02d}" for d in range(1, 21)])
    got = {tf: B.bars_per_year(pd.DatetimeIndex(B.resample(px, tf).index), trading_days=250)
           for tf in B.TIMEFRAMES}
    assert got["5m"] == 75 * 250
    assert got["15m"] == 25 * 250
    assert got["60m"] == 7 * 250       # 6 full + stub
    assert got["4h"] == 2 * 250        # 1 full + stub
    # monotonically decreasing as bars get coarser
    vals = [got[tf] for tf in B.TIMEFRAMES]
    assert all(a > b for a, b in zip(vals, vals[1:]))


def test_session_boundaries_marks_only_the_last_bar_of_each_day():
    px = _frame(["2024-01-01", "2024-01-02"])
    out = B.resample(px, "60m")
    mask = B.session_boundaries(pd.DatetimeIndex(out.index))
    assert mask.sum() == 2
    assert mask[6] and mask[13]        # 7 bars per session


def test_sharpe_annualisation_is_frequency_sensitive():
    """The failure mode in OpenAlgo's vectorbt example, in one assertion.

    Same return series, two annualisation factors -> Sharpe differs by the ratio
    of their square roots. Declaring 15m data as 5m inflates Sharpe by sqrt(3).
    """
    from kpairs.backtest import metrics

    rng = np.random.default_rng(0)
    idx = pd.date_range("2024-01-01", periods=2000, freq="15min")
    r = pd.Series(rng.normal(1e-5, 1e-3, 2000), index=idx)

    right = metrics(r, periods_per_year=25 * 250)["sharpe"]
    wrong = metrics(r, periods_per_year=75 * 250)["sharpe"]
    assert wrong / right == pytest.approx(np.sqrt(3), rel=1e-9)


def test_intraday_flat_rule_survives_the_execution_lag():
    """Regression: the square-off must land on the position, not the decision.

    ``force_flat`` marks bars the *position* must be flat on. The state machine
    emits decisions, which ``exec_lag`` then shifts forward. Constraining the
    decision at bar b leaves the position at b+lag still holding -- so an
    "intraday only" book quietly carries every overnight gap in the sample.
    """
    from kpairs.signals import positions_from_z

    flat = np.array([0, 0, 0, 1] * 3, dtype=bool)   # 3 sessions of 4 bars
    z = np.full(12, -3.0)                            # screaming entry, always
    for lag in (0, 1, 2):
        out = positions_from_z(z, entry=2.0, exit_=0.5, exec_lag=lag, force_flat=flat)
        held = out["position"][flat]
        assert not held.any(), f"exec_lag={lag} carries {held} through the close"


def test_intraday_flat_still_allows_intraday_holding():
    from kpairs.signals import positions_from_z

    flat = np.array([0] * 9 + [1], dtype=bool)
    z = np.full(10, -3.0)
    out = positions_from_z(z, entry=2.0, exit_=0.5, exec_lag=1, force_flat=flat)
    assert out["position"][1:9].all(), "should hold intraday, only flat at the close"
    assert out["position"][9] == 0.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
