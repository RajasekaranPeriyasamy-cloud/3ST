"""Unit tests for expiry-cycle analogue engine (no Kite)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from analysis.analogue_cycles import (
    analogue_config,
    build_analogue_snapshot,
    _cycle_slices,
    _generate_candidate_expiries,
)


def _synth_ohlc(n: int = 520, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    close = [20000.0]
    for _ in range(n - 1):
        close.append(close[-1] * (1 + float(rng.normal(0.0002, 0.008))))
    s = pd.Series(close, index=idx)
    return pd.DataFrame(
        {
            "open": s.shift(1).fillna(s.iloc[0]),
            "high": s * 1.004,
            "low": s * 0.996,
            "close": s,
        }
    )


def test_analogue_config():
    cfg = analogue_config()
    assert "monthly" in cfg["cycle_kinds"]
    assert cfg["default_similarity_band_pct"] > 0
    assert cfg["expiry_weekday_cutover"] == "2025-09-01"
    assert cfg["expiry_weekdays"]["NIFTY"]["before"] == "Thursday"
    assert cfg["expiry_weekdays"]["NIFTY"]["on_or_after_cutover"] == "Tuesday"
    assert cfg["expiry_weekdays"]["SENSEX"]["before"] == "Tuesday"
    assert cfg["expiry_weekdays"]["SENSEX"]["on_or_after_cutover"] == "Thursday"


def test_expiry_weekday_regimes():
    from analysis.analogue_cycles import EXPIRY_WEEKDAY_CUTOVER, _expiry_weekday

    assert _expiry_weekday("NIFTY", date(2025, 8, 28)) == 3  # Thu
    assert _expiry_weekday("NIFTY", EXPIRY_WEEKDAY_CUTOVER) == 1  # Tue
    assert _expiry_weekday("SENSEX", date(2025, 8, 26)) == 1  # Tue
    assert _expiry_weekday("SENSEX", date(2025, 9, 4)) == 3  # Thu
    assert _expiry_weekday("BANKNIFTY", date(2025, 8, 27)) == 2  # Wed
    assert _expiry_weekday("BANKNIFTY", date(2025, 9, 30)) == 1  # Tue


def test_weekly_expiries_flip_at_cutover():
    """NIFTY weeklies: Thu before Sep-2025, Tue on/after (incl. 02-Sep-25)."""
    idx = pd.bdate_range("2025-07-01", "2025-10-31")
    trading = {d.date() for d in idx}
    exps = _generate_candidate_expiries(
        "NIFTY",
        date(2025, 7, 1),
        date(2025, 10, 31),
        trading,
        "weekly",
    )
    pre = [e for e in exps if e < date(2025, 9, 1)]
    post = [e for e in exps if e >= date(2025, 9, 1)]
    assert pre and post
    assert date(2025, 8, 28) in pre
    assert date(2025, 9, 2) in post  # first revised weekly (Tue)
    assert all(e.weekday() == 3 for e in pre)
    assert all(e.weekday() == 1 for e in post)

    sensex = _generate_candidate_expiries(
        "SENSEX",
        date(2025, 7, 1),
        date(2025, 10, 31),
        trading,
        "weekly",
    )
    s_pre = [e for e in sensex if e < date(2025, 9, 1)]
    s_post = [e for e in sensex if e >= date(2025, 9, 1)]
    assert s_pre and s_post
    assert all(e.weekday() == 1 for e in s_pre)
    assert all(e.weekday() == 3 for e in s_post)


def test_generate_monthly_expiries():
    ohlc = _synth_ohlc()
    trading = {d.date() for d in ohlc.index}
    exps = _generate_candidate_expiries(
        "NIFTY",
        ohlc.index[0].date(),
        ohlc.index[-1].date(),
        trading,
        "monthly",
    )
    assert len(exps) >= 8
    # roughly one per month
    months = {(e.year, e.month) for e in exps}
    assert len(months) == len(exps)


def test_cycle_slices_and_snapshot():
    ohlc = _synth_ohlc(600)
    trading = {d.date() for d in ohlc.index}
    exps = _generate_candidate_expiries(
        "NIFTY",
        ohlc.index[0].date(),
        ohlc.index[-1].date() + timedelta(days=40),
        trading,
        "monthly",
    )
    cycles = _cycle_slices(ohlc["close"], exps)
    assert len(cycles) >= 5

    as_of = ohlc.index[-5].date()
    # fabricate listed expiries around as_of
    future = [e for e in exps if e >= as_of]
    assert future
    listed = [e.isoformat() for e in exps if abs((e - as_of).days) < 120]

    snap = build_analogue_snapshot(
        "NIFTY",
        cycle_kind="monthly",
        similarity_band_pct=8.0,
        ohlc=ohlc,
        listed_expiries=listed,
        as_of=as_of,
    )
    assert snap["engine"] == "analogue_cycles"
    assert snap["day_in_cycle"] >= 0
    assert isinstance(snap["current_path"], list)
    assert len(snap["current_path"]) >= 1
    # with wide band should usually match something
    assert snap["matched"] >= 0
    if snap["stats"]:
        assert 0 <= snap["stats"]["p_further_up"] <= 1
        assert snap["stats"]["median_expiry_level"] > 0


def test_on_expiry_day_rolls_to_next_cycle():
    """On expiry day, target next expiry; day 0 pending until next session."""
    ohlc = _synth_ohlc(400)
    trading = {d.date() for d in ohlc.index}
    exps = _generate_candidate_expiries(
        "NIFTY",
        ohlc.index[0].date(),
        ohlc.index[-1].date() + timedelta(days=40),
        trading,
        "weekly",
    )
    mid = exps[len(exps) // 2]
    assert mid in trading
    following = [e for e in exps if e > mid]
    assert following
    listed = [e.isoformat() for e in exps]
    snap = build_analogue_snapshot(
        "NIFTY",
        cycle_kind="weekly",
        similarity_band_pct=6.0,
        ohlc=ohlc.loc[: pd.Timestamp(mid)],
        listed_expiries=listed,
        as_of=mid,
    )
    assert snap["cycle_pending"] is True
    assert snap["prev_expiry"] == mid.isoformat()
    assert snap["current_expiry"] == following[0].isoformat()
    assert snap["day_in_cycle"] == 0
    assert snap["move_so_far_pct"] == 0.0


def test_override_move_changes_match_key():
    ohlc = _synth_ohlc(500)
    as_of = ohlc.index[-8].date()
    trading = {d.date() for d in ohlc.index}
    exps = _generate_candidate_expiries(
        "NIFTY",
        ohlc.index[0].date(),
        ohlc.index[-1].date() + timedelta(days=40),
        trading,
        "monthly",
    )
    listed = [e.isoformat() for e in exps]

    a = build_analogue_snapshot(
        "NIFTY",
        similarity_band_pct=3.0,
        ohlc=ohlc,
        listed_expiries=listed,
        as_of=as_of,
    )
    b = build_analogue_snapshot(
        "NIFTY",
        similarity_band_pct=3.0,
        override_move_pct=5.0,
        ohlc=ohlc,
        listed_expiries=listed,
        as_of=as_of,
    )
    assert a["move_used_for_match_pct"] != b["move_used_for_match_pct"]
    assert b["move_used_for_match_pct"] == 5.0
