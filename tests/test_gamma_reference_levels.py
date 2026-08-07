"""Unit tests for Prev Day / Prev Week reference levels on Gamma Density."""

from __future__ import annotations

from datetime import date, datetime

from options import gamma_density as gd


def _bar(d: date, high: float, low: float, close: float) -> dict:
    return {
        "date": datetime(d.year, d.month, d.day, 15, 30),
        "high": high,
        "low": low,
        "close": close,
        "open": close,
    }


def test_compute_reference_levels_prev_day_and_week():
    # today = Mon 2026-07-27 → prior ISO week is Mon 20 – Sun 26 Jul
    today = date(2026, 7, 27)
    bars = [
        _bar(date(2026, 7, 20), 24100, 23800, 23950),  # Mon prior week
        _bar(date(2026, 7, 21), 24200, 23900, 24100),
        _bar(date(2026, 7, 22), 24350, 24000, 24250),
        _bar(date(2026, 7, 23), 24400, 24100, 24300),
        _bar(date(2026, 7, 24), 24380, 24050, 24150),  # Fri prior week (close)
        # Sat/Sun typically no bars
        _bar(date(2026, 7, 27), 24500, 24200, 24400),  # today — excluded
    ]
    ref = gd.compute_reference_levels_from_daily_bars(bars, today=today)
    assert ref["prev_day_high"] == 24380.0
    assert ref["prev_day_low"] == 24050.0
    assert ref["prev_day_close"] == 24150.0
    assert ref["prev_week_high"] == 24400.0
    assert ref["prev_week_low"] == 23800.0
    assert ref["prev_week_close"] == 24150.0


def test_compute_reference_levels_empty_and_today_only():
    today = date(2026, 7, 27)
    assert gd.compute_reference_levels_from_daily_bars([], today=today) == gd._empty_reference_levels()
    only_today = [_bar(today, 24500, 24200, 24400)]
    ref = gd.compute_reference_levels_from_daily_bars(only_today, today=today)
    assert ref == gd._empty_reference_levels()


def test_build_reference_levels_soft_fails(monkeypatch):
    monkeypatch.setattr(gd, "fetch_underlying_daily_ohlc_bars", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    # Exception path inside build_reference_levels
    out = gd.build_reference_levels("NIFTY")
    assert out == gd._empty_reference_levels()


def test_build_reference_levels_uses_helper(monkeypatch):
    monkeypatch.setattr(
        gd,
        "fetch_underlying_daily_ohlc_bars",
        lambda *_a, **_k: [
            {
                "date": datetime(2026, 7, 24, 15, 30),
                "high": 24010,
                "low": 23800,
                "close": 23950,
            }
        ],
    )
    out = gd.build_reference_levels("NIFTY", today=date(2026, 7, 27))
    assert out["prev_day_close"] == 23950.0
    assert out["prev_day_high"] == 24010.0
    assert out["prev_day_low"] == 23800.0


def test_market_read_appends_day_week_closes():
    read = gd.build_gamma_market_read(
        gamma_regime="positive",
        concentration={"band": "mixed", "hhi": 0.2, "top1_share": 0.3, "dominant_strike": 24000, "pin_strike": 24000},
        conviction={"score": 1, "direction": "flat"},
        flip_level=24000,
        distance_to_flip=-20,
        call_wall=24100,
        put_wall=23900,
        reference_levels={"prev_day_close": 23950.0, "prev_week_close": 23800.0},
    )
    assert "Day close 23950" in read["levels_line"]
    assert "Week close 23800" in read["levels_line"]
