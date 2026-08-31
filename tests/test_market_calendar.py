"""Tests for utils/market_calendar.py and the in_session gate it now guards."""

from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from utils import market_calendar as mc


@pytest.fixture(autouse=True)
def _clear_calendar_cache():
    mc.clear_cache()
    yield
    mc.clear_cache()


def _write_calendar(tmp_path, monkeypatch, *, years, holidays):
    f = tmp_path / "market_holidays.json"
    f.write_text(
        json.dumps({"coverage_years": years, "holidays": holidays}), encoding="utf-8"
    )
    monkeypatch.setattr(mc, "HOLIDAY_FILE", f)
    mc.clear_cache()
    return f


# ── Weekend gate — exact, no data needed ──────────────────────────────────────
def test_weekend_detection():
    assert mc.is_weekend(date(2026, 8, 22))      # Saturday — the observed bug
    assert mc.is_weekend(date(2026, 8, 23))      # Sunday
    assert not mc.is_weekend(date(2026, 8, 21))  # Friday
    assert not mc.is_weekend(date(2026, 8, 24))  # Monday


def test_the_observed_bad_row_is_now_rejected():
    """2026-08-22 wrote a daily_hhi row. It must not be a trading day."""
    assert mc.trading_day_confidence(date(2026, 8, 22)) == "weekend"
    assert not mc.is_trading_day(date(2026, 8, 22))


def test_weekend_beats_an_empty_calendar(tmp_path, monkeypatch):
    """The weekend gate must not depend on the holiday file existing."""
    _write_calendar(tmp_path, monkeypatch, years=[], holidays={})
    assert mc.holiday_coverage() == frozenset()
    assert not mc.is_trading_day(date(2026, 8, 22))
    assert mc.trading_day_confidence(date(2026, 8, 22)) == "weekend"


# ── Holiday gate ──────────────────────────────────────────────────────────────
def test_known_holiday_is_rejected(tmp_path, monkeypatch):
    _write_calendar(
        tmp_path, monkeypatch, years=[2026], holidays={"NSE": ["2026-08-26"]}
    )
    assert mc.trading_day_confidence(date(2026, 8, 26)) == "holiday"
    assert not mc.is_trading_day(date(2026, 8, 26))
    # a neighbouring weekday in the same covered year is still a trading day
    assert mc.trading_day_confidence(date(2026, 8, 27)) == "trading"


def test_holidays_are_per_exchange(tmp_path, monkeypatch):
    _write_calendar(
        tmp_path, monkeypatch, years=[2026],
        holidays={"NSE": ["2026-08-26"], "MCX": []},
    )
    assert not mc.is_trading_day(date(2026, 8, 26), "NSE")
    assert mc.is_trading_day(date(2026, 8, 26), "MCX")


def test_outside_coverage_falls_open_and_says_so(tmp_path, monkeypatch):
    """A calendar that runs out must not silently claim certainty."""
    _write_calendar(
        tmp_path, monkeypatch, years=[2025], holidays={"NSE": ["2025-01-26"]}
    )
    # 2026 is not covered — weekday passes, but the answer is flagged unverified
    assert mc.trading_day_confidence(date(2026, 8, 27)) == "trading_unverified"
    assert mc.is_trading_day(date(2026, 8, 27)) is True
    # and the weekend gate still applies outside coverage
    assert mc.trading_day_confidence(date(2026, 8, 22)) == "weekend"
    assert mc.is_trading_day(date(2026, 8, 22)) is False


def test_coverage_is_reported():
    assert isinstance(mc.holiday_coverage(), frozenset)


def test_missing_file_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(mc, "HOLIDAY_FILE", tmp_path / "does-not-exist.json")
    mc.clear_cache()
    assert mc.holiday_coverage() == frozenset()
    assert mc.is_trading_day(date(2026, 8, 27)) is True
    assert mc.is_trading_day(date(2026, 8, 22)) is False


def test_malformed_entry_does_not_void_the_calendar(tmp_path, monkeypatch):
    f = tmp_path / "market_holidays.json"
    f.write_text(
        json.dumps(
            {"coverage_years": [2026], "holidays": {"NSE": ["2026-08-26", "garbage"]}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mc, "HOLIDAY_FILE", f)
    mc.clear_cache()
    assert not mc.is_trading_day(date(2026, 8, 26))     # the good date still bites
    assert mc.is_trading_day(date(2026, 8, 27))


def test_corrupt_file_does_not_raise(tmp_path, monkeypatch):
    f = tmp_path / "market_holidays.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(mc, "HOLIDAY_FILE", f)
    mc.clear_cache()
    assert mc.holiday_coverage() == frozenset()
    assert mc.is_trading_day(date(2026, 8, 27)) is True


def test_shipped_calendar_parses():
    """The committed data/market_holidays.json must at least be readable."""
    mc.clear_cache()
    assert isinstance(mc.holiday_coverage(), frozenset)


# ── The gate this was built for ───────────────────────────────────────────────
def test_in_session_rejects_the_saturday_that_wrote_a_row():
    """10:04 on 2026-08-22 is inside 09:15-15:40 and used to pass."""
    from options.gamma_density_history import IST as HIST_IST
    from options.gamma_density_history import in_session

    saturday = datetime(2026, 8, 22, 10, 4, 57, tzinfo=HIST_IST)
    assert not in_session("NIFTY", saturday)

    # The same clock time on the preceding Friday is still a session.
    friday = datetime(2026, 8, 21, 10, 4, 57, tzinfo=HIST_IST)
    assert in_session("NIFTY", friday)


def test_in_session_still_gates_on_time_of_day():
    from options.gamma_density_history import IST as HIST_IST
    from options.gamma_density_history import in_session

    monday = datetime(2026, 8, 24, 17, 0, tzinfo=HIST_IST)   # after close
    assert not in_session("NIFTY", monday)
    assert in_session("NIFTY", datetime(2026, 8, 24, 11, 0, tzinfo=HIST_IST))


def test_in_session_rejects_weekends_for_mcx_too():
    from options.gamma_density_history import IST as HIST_IST
    from options.gamma_density_history import in_session

    # MCX runs to 23:30, so a Saturday evening would otherwise pass the clock gate.
    assert not in_session("CRUDEOIL", datetime(2026, 8, 22, 20, 0, tzinfo=HIST_IST))
    assert in_session("CRUDEOIL", datetime(2026, 8, 21, 20, 0, tzinfo=HIST_IST))


def test_upsert_daily_hhi_will_not_write_on_a_weekend(tmp_path, monkeypatch):
    """End to end: the store write is what the gate protects."""
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    saturday = datetime(2026, 8, 22, 10, 4, tzinfo=gdh.IST)

    assert gdh.upsert_daily_hhi("NIFTY", 0.1029, when=saturday) == []
    assert gdh.get_daily_hhi_series("NIFTY") == []

    # force= is the documented escape hatch and must still work.
    out = gdh.upsert_daily_hhi("NIFTY", 0.1029, when=saturday, force=True)
    assert len(out) == 1
