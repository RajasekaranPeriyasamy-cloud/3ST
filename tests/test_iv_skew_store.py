"""IV Skew archive and daily roll-up — filesystem only, no Kite, no network.

Every test redirects ``settings.data_dir`` at the store module, so nothing here
touches the real ``data/`` tree.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from analysis.iv_skew import runner, store

IST = ZoneInfo("Asia/Kolkata")

TODAY = date.today()
D1 = TODAY - timedelta(days=3)
D2 = TODAY - timedelta(days=2)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    return tmp_path


def snapshot(rr=-0.9, confidence="clean", *, underlying="NIFTY", expiries=None):
    """A build_iv_skew-shaped payload, trimmed to what compact_sample reads."""
    rows = expiries or [
        {
            "expiry": "2026-08-18",
            "dte": 6,
            "ok": True,
            "confidence": confidence,
            "quality": "interpolated",
            "risk_reversal": rr,
            "butterfly": 0.1,
            "atm_iv": 10.0,
            "call_iv": 9.6,
            "put_iv": 10.5,
            "forward": 24500.0,
            "forward_basis": 60.0,
            "atm_parity_gap": 0.01,
            "points": [{"strike": 24500, "iv": 10.0}] * 40,
        }
    ]
    return {
        "underlying": underlying,
        "reference": 24440.0,
        "reference_source": "index",
        "expiries": rows,
    }


def write(underlying, day, *rows, base_hour=10):
    """Append samples for a session, one per successive minute."""
    for i, (rr, confidence) in enumerate(rows):
        ts = datetime(day.year, day.month, day.day, base_hour, i, tzinfo=IST)
        sample = store.compact_sample(snapshot(rr, confidence, underlying=underlying), now=ts)
        store.append_sample(underlying, sample)


# --- compaction -------------------------------------------------------------


def test_compact_sample_drops_the_strike_curve(archive):
    """Points are two orders of magnitude of the payload and are not plotted
    from history — keeping them would balloon the archive for nothing."""
    sample = store.compact_sample(snapshot())
    assert "points" not in sample["expiries"][0]
    assert sample["expiries"][0]["rr"] == -0.9
    assert sample["expiries"][0]["rank"] == 0


def test_compact_sample_keeps_failed_rows_without_metrics(archive):
    bad = [{"expiry": "2026-09-01", "dte": 20, "ok": False, "confidence": "unavailable"}]
    row = store.compact_sample(snapshot(expiries=bad))["expiries"][0]
    assert row["ok"] is False
    assert "rr" not in row


# --- intraday ---------------------------------------------------------------


def test_round_trip_a_session(archive):
    write("NIFTY", D1, (-0.5, "clean"), (-0.7, "clean"))
    got = store.load_session("NIFTY", D1)
    assert [s["expiries"][0]["rr"] for s in got] == [-0.5, -0.7]


def test_truncated_final_line_costs_one_sample_not_the_day(archive):
    write("NIFTY", D1, (-0.5, "clean"), (-0.7, "clean"))
    path = store.session_file("NIFTY", D1)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-08-12T10:05:00+05:30", "expiri')
    assert len(store.load_session("NIFTY", D1)) == 2


def test_sessions_and_latest(archive):
    write("NIFTY", D1, (-0.5, "clean"))
    write("NIFTY", D2, (-0.6, "clean"))
    assert store.sessions_available("NIFTY") == [D1, D2]
    assert store.latest_session("NIFTY") == D2


def test_latest_session_is_none_on_an_empty_archive(archive):
    assert store.latest_session("NIFTY") is None


# --- roll-up ----------------------------------------------------------------


def test_rollup_takes_the_last_clean_reading_of_the_day(archive):
    write("NIFTY", D1, (-0.5, "clean"), (-0.8, "clean"), (-9.9, "degraded"))
    rows = store.rollup_day("NIFTY", D1)
    assert len(rows) == 1
    assert rows[0]["rr"] == -0.8  # not the later degraded one
    assert rows[0]["confidence"] == "clean"
    assert rows[0]["samples"] == 3


def test_rollup_falls_back_to_degraded_rather_than_dropping_a_day(archive):
    """A thin day is recorded honestly, carrying its confidence forward."""
    write("NIFTY", D1, (-9.9, "degraded"), (-8.8, "degraded"))
    rows = store.rollup_day("NIFTY", D1)
    assert rows[0]["rr"] == -8.8
    assert rows[0]["confidence"] == "degraded"


def test_rollup_ignores_unresolved_rows(archive):
    bad = [{"expiry": "2026-09-01", "dte": 20, "ok": False, "confidence": "unavailable"}]
    store.append_sample("NIFTY", store.compact_sample(snapshot(expiries=bad),
                                                      now=datetime(D1.year, D1.month, D1.day, 10, tzinfo=IST)))
    assert store.rollup_day("NIFTY", D1) == []


def test_ensure_rollup_is_idempotent(archive):
    write("NIFTY", D1, (-0.5, "clean"))
    assert store.ensure_rollup(["NIFTY"], today=TODAY) == 1
    assert store.ensure_rollup(["NIFTY"], today=TODAY) == 0
    assert len(store.load_daily()) == 1


def test_ensure_rollup_backfills_a_gap(archive):
    """Miss a week and the next read fills it in — the reason this is lazy."""
    write("NIFTY", D1, (-0.5, "clean"))
    write("NIFTY", D2, (-0.6, "clean"))
    assert store.ensure_rollup(["NIFTY"], today=TODAY) == 2


def test_todays_session_is_not_rolled_up_mid_day(archive):
    """A row written mid-session would freeze a partial day as if it were the close."""
    write("NIFTY", TODAY, (-0.5, "clean"))
    assert store.ensure_rollup(["NIFTY"], today=TODAY) == 0
    assert store.load_daily() == []


# --- daily series -----------------------------------------------------------


def test_daily_series_is_chronological_and_rank_filtered(archive):
    write("NIFTY", D1, (-0.5, "clean"))
    write("NIFTY", D2, (-0.6, "clean"))
    store.ensure_rollup(["NIFTY"], today=TODAY)

    series = store.daily_series("NIFTY", rank=0)
    assert [p["date"] for p in series["points"]] == [D1.isoformat(), D2.isoformat()]
    assert store.daily_series("NIFTY", rank=1)["points"] == []


def test_daily_series_excludes_degraded_but_names_them(archive):
    """Never silently drop — the caller sees which sessions were held back."""
    write("NIFTY", D1, (-0.5, "clean"))
    write("NIFTY", D2, (-9.9, "degraded"))
    store.ensure_rollup(["NIFTY"], today=TODAY)

    clean = store.daily_series("NIFTY", rank=0, clean_only=True)
    assert [p["date"] for p in clean["points"]] == [D1.isoformat()]
    assert clean["excluded_degraded"] == [D2.isoformat()]

    everything = store.daily_series("NIFTY", rank=0, clean_only=False)
    assert len(everything["points"]) == 2


def test_daily_series_limit_keeps_the_newest(archive):
    write("NIFTY", D1, (-0.5, "clean"))
    write("NIFTY", D2, (-0.6, "clean"))
    store.ensure_rollup(["NIFTY"], today=TODAY)
    series = store.daily_series("NIFTY", rank=0, limit=1)
    assert [p["date"] for p in series["points"]] == [D2.isoformat()]


def test_underlyings_do_not_bleed_into_each_other(archive):
    write("NIFTY", D1, (-0.5, "clean"))
    write("NATURALGAS", D1, (2.4, "clean"))
    store.ensure_rollup(["NIFTY", "NATURALGAS"], today=TODAY)
    assert store.daily_series("NIFTY")["points"][0]["rr"] == -0.5
    assert store.daily_series("NATURALGAS")["points"][0]["rr"] == 2.4


# --- pruning ----------------------------------------------------------------


def test_pruning_never_deletes_a_session_that_was_not_rolled_up(archive):
    """The daily row is the only durable copy — dropping raw first loses it."""
    old = TODAY - timedelta(days=200)
    write("NIFTY", old, (-0.5, "clean"))
    assert store.prune_intraday("NIFTY", retention_days=90) == []
    assert store.session_file("NIFTY", old).exists()

    store.ensure_rollup(["NIFTY"], today=TODAY)
    assert store.prune_intraday("NIFTY", retention_days=90) == [old.isoformat()]
    assert not store.session_file("NIFTY", old).exists()


def test_pruning_keeps_sessions_inside_the_window(archive):
    write("NIFTY", D1, (-0.5, "clean"))
    store.ensure_rollup(["NIFTY"], today=TODAY)
    assert store.prune_intraday("NIFTY", retention_days=90) == []


def test_coverage_reports_rollup_state(archive):
    write("NIFTY", D1, (-0.5, "clean"), (-0.6, "clean"))
    cov = store.coverage("NIFTY")
    assert cov["sessions"] == 1
    assert cov["days"][0]["samples"] == 2
    assert cov["days"][0]["rolled_up"] is False

    store.ensure_rollup(["NIFTY"], today=TODAY)
    assert store.coverage("NIFTY")["days"][0]["rolled_up"] is True


# --- runner session gating --------------------------------------------------


def test_index_and_mcx_have_different_session_windows():
    assert runner.session_window("NIFTY") == ("09:15", "15:40")
    assert runner.session_window("CRUDEOIL") == ("09:00", "23:30")


def test_mcx_still_in_session_after_the_indices_close():
    evening = datetime(2026, 8, 12, 18, 0, tzinfo=IST)  # a Wednesday
    assert runner.in_session("NIFTY", evening) is False
    assert runner.in_session("CRUDEOIL", evening) is True


def test_weekend_is_out_of_session_for_everything():
    saturday = datetime(2026, 8, 15, 12, 0, tzinfo=IST)
    assert saturday.weekday() == 5
    assert runner.in_session("NIFTY", saturday) is False
    assert runner.in_session("CRUDEOIL", saturday) is False


@pytest.mark.parametrize(
    "minute,expected_suffix",
    [(0, "10:00"), (4, "10:00"), (5, "10:05"), (9, "10:05"), (59, "10:55")],
)
def test_sample_bucket_dedupes_within_the_interval(minute, expected_suffix):
    ts = datetime(2026, 8, 12, 10, minute, tzinfo=IST)
    assert runner._bucket(ts).endswith(expected_suffix)


def test_sample_once_skips_out_of_session_underlyings(archive, monkeypatch):
    """Indices closed, MCX open — crude must still be sampled."""
    monkeypatch.setattr(runner, "underlyings", lambda: ("NIFTY", "CRUDEOIL"))
    monkeypatch.setattr(runner, "build_iv_skew", lambda u: snapshot(underlying=u))
    monkeypatch.setattr(runner.store, "data_dir", lambda: archive)

    report = runner.sample_once(now=datetime(2026, 8, 12, 18, 0, tzinfo=IST))
    assert list(report["underlyings"]) == ["CRUDEOIL"]
    assert report["sampled"] == 1


def test_one_failing_underlying_does_not_stop_the_others(archive, monkeypatch):
    def flaky(u):
        if u == "NIFTY":
            raise RuntimeError("chain unavailable")
        return snapshot(underlying=u)

    monkeypatch.setattr(runner, "underlyings", lambda: ("NIFTY", "BANKNIFTY"))
    monkeypatch.setattr(runner, "build_iv_skew", flaky)
    monkeypatch.setattr(runner.store, "data_dir", lambda: archive)

    report = runner.sample_once(now=datetime(2026, 8, 12, 11, 0, tzinfo=IST))
    assert "chain unavailable" in report["underlyings"]["NIFTY"]["error"]
    assert report["underlyings"]["BANKNIFTY"]["expiries"] == 1
