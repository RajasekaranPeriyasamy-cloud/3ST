"""Tests for gamma density history session merge + reversal detection."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from options.gamma_density_history import (
    build_chart_series,
    detect_spot_reversals,
    in_session,
    session_window,
)

IST = ZoneInfo("Asia/Kolkata")


def test_session_window_cash_vs_mcx() -> None:
    s, e = session_window("NIFTY")
    assert (s.hour, s.minute) == (9, 15)
    assert (e.hour, e.minute) == (15, 40)
    s2, e2 = session_window("CRUDEOIL")
    assert (s2.hour, s2.minute) == (9, 0)
    assert (e2.hour, e2.minute) == (23, 30)


def test_in_session_rejects_after_close() -> None:
    after = datetime(2026, 7, 24, 16, 45, tzinfo=IST)
    assert in_session("NIFTY", after) is False
    during = datetime(2026, 7, 24, 10, 10, tzinfo=IST)
    assert in_session("NIFTY", during) is True
    # CAS / F&O close window — still in session at 15:35
    cas = datetime(2026, 7, 24, 15, 35, tzinfo=IST)
    assert in_session("NIFTY", cas) is True
    assert in_session("NIFTY", datetime(2026, 7, 24, 15, 41, tzinfo=IST)) is False


def test_build_chart_series_keeps_day_spot_and_sparse_gex() -> None:
    today = datetime.now(tz=IST).date().isoformat()
    candles = [
        {"date": f"{today}T09:20:00+05:30", "close": 23600},
        {"date": f"{today}T10:10:00+05:30", "close": 23550},
        {"date": f"{today}T11:00:00+05:30", "close": 23680},
        {"date": f"{today}T14:00:00+05:30", "close": 23780},
    ]
    gex = [
        {
            "t": f"{today}T09:20:00+05:30",
            "spot": 23600,
            "total_gex": 1e6,
            "flip_level": 23650,
            "gamma_regime": "positive",
        },
        {
            "t": f"{today}T10:10:00+05:30",
            "spot": 23550,
            "total_gex": -5e5,
            "flip_level": 23600,
            "gamma_regime": "negative",
        },
        # afternoon gap — no GEX tick at 14:00
    ]
    series = build_chart_series("NIFTY", gex, candles)
    assert len(series) == 4
    assert series[0]["total_gex"] == 1e6
    assert series[1]["total_gex"] == -5e5
    assert series[3]["spot"] == 23780
    assert series[3]["total_gex"] is None  # no invented GEX after gap


def test_detect_bullish_reversal_around_trough() -> None:
    # Synthetic V: decline then sharp reclaim (+80 pts)
    base = 23600.0
    series = []
    ts0 = datetime(2026, 7, 24, 9, 30, tzinfo=IST)
    path = [0, -20, -40, -60, -80, -100, -90, -50, -20, 20, 40]  # trough at -100
    for i, d in enumerate(path):
        t = ts0.timestamp() + i * 60
        series.append(
            {
                "t": datetime.fromtimestamp(t, tz=IST).isoformat(),
                "ts_ms": int(t * 1000),
                "spot": base + d,
                "total_gex": -1e5 if d < -50 else 1e5,
                "gamma_regime": "negative" if d < -50 else "positive",
            }
        )
    revs = detect_spot_reversals(series, swing_bars=2, min_move_pts=50, confirm_bars=5)
    assert revs, "expected at least one bullish reversal"
    assert any(r["side"] == "bullish" for r in revs)
