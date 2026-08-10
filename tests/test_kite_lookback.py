"""Kite historical lookback vs default range.

These were one constant (KITE_MAX_DAYS) serving both jobs, so the clamp on an
explicit start date used the *default* span. A backtest asking for 1-minute bars
older than 60 days had its start silently moved forward, and nothing surfaced it.

Measured 2026-08-10 against a live session: NIFTY index, BANKNIFTY index and
RELIANCE cash all served 1-minute bars 1825 days back, at every interval. So the
60 was never a real limit — it is Kite's per-request cap, which chunking handles.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from config import KITE_DEFAULT_RANGE_DAYS, KITE_INTERVALS, KITE_MAX_LOOKBACK_DAYS
from kite_client import (
    default_kite_date_range,
    kite_default_range_days,
    kite_max_lookback_days,
)


def test_every_timeframe_has_both_limits():
    for tf in KITE_INTERVALS:
        assert tf in KITE_MAX_LOOKBACK_DAYS, f"{tf} missing a lookback ceiling"
        assert tf in KITE_DEFAULT_RANGE_DAYS, f"{tf} missing a default range"


@pytest.mark.parametrize("tf", sorted(KITE_INTERVALS))
def test_ceiling_is_never_below_default_range(tf):
    """The clamp must never cut into the span "use max" asks for."""
    assert kite_max_lookback_days(tf) >= kite_default_range_days(tf)


def test_one_minute_ceiling_reflects_measurement():
    """Regression on the actual bug: 1-min was clamped to 60 days.

    Kite served 1825. If this is ever lowered back toward 60, an explicit
    request for older 1-minute data starts being silently truncated again.
    """
    assert kite_max_lookback_days("1min") >= 730


def test_default_range_stays_conservative():
    """Raising the ceiling must not make every default backtest 30x heavier.

    1825 days of 1-minute bars is ~470k rows over ~31 chunked requests. "use max"
    should keep asking for a short span; callers wanting more pass a start date.
    """
    assert kite_default_range_days("1min") <= 120


def test_default_date_range_uses_default_not_ceiling():
    start, end = default_kite_date_range("1min")
    assert end == date.today()
    assert start == date.today() - timedelta(days=kite_default_range_days("1min"))
    assert start > date.today() - timedelta(days=kite_max_lookback_days("1min"))


def test_unknown_timeframe_falls_back_without_raising():
    assert kite_max_lookback_days("nonsense") > 0
    assert kite_default_range_days("nonsense") > 0


def test_explicit_old_start_survives_the_clamp():
    """The clamp arithmetic used at api/main.py backtest call sites.

    A start 400 days back is well inside the 1-min ceiling, so it must pass
    through untouched. Under the old shared constant it was moved to today-60.
    """
    requested = date.today() - timedelta(days=400)
    earliest = date.today() - timedelta(days=kite_max_lookback_days("1min"))
    clamped = max(requested, earliest)
    assert clamped == requested
