"""Tests for the Volume Footprint session-tilt history and its comparison.

The thing worth pinning is not the arithmetic — it is the refusal to compare a
partial session against completed ones, and the null/label discipline that keeps
a thin or backfilled window from reading like a deep live one.
"""

from __future__ import annotations

import pytest

from analysis.volume_profile import tilt_history as th


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """Redirect the store, and prove the real one was never touched.

    Patches this module's own ``data_dir`` reference, not ``settings.data_dir`` —
    a fixture that patched the latter once appended 1,800 synthetic rows into a
    live archive, because stores bind the name at import.
    """
    monkeypatch.setattr(th, "data_dir", lambda: tmp_path)
    th.reset_for_tests()
    yield
    th.reset_for_tests()


def _seed(underlying: str, day: str, curve: dict[int, float], source: str = "backfill") -> None:
    for minute, tilt in curve.items():
        th.upsert_point(
            underlying, day, minute=minute, tilt_pp=tilt, bars=minute, source=source
        )


def test_store_writes_where_it_is_pointed_not_the_real_data_dir(tmp_path) -> None:
    _seed("NIFTY", "2026-08-20", {15: 1.0})
    assert (tmp_path / "volume_tilt_history.json").exists()
    assert th.history_file().parent == tmp_path


def test_checkpoint_floors_so_a_reading_is_ranked_against_the_same_minute() -> None:
    assert th.checkpoint_for(188) == 180
    assert th.checkpoint_for(180) == 180
    assert th.checkpoint_for(385) == 375
    # Below the floor there is nothing worth ranking.
    assert th.checkpoint_for(14) is None
    assert th.checkpoint_for(0) is None


def test_partial_session_is_never_ranked_against_completed_ones() -> None:
    """The whole reason sessions are stored as curves rather than closing values."""
    # Five prior sessions that drifted from mildly positive at 09:30 to strongly
    # negative by the close — the mean reversion that makes a naive comparison lie.
    for i, day in enumerate(("2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21")):
        _seed("NIFTY", day, {15: 2.0 + i, 375: -25.0 - i})

    # Today is 15 minutes in at +3pp. Against the 15-minute checkpoint that is
    # unremarkable; against those sessions' *closes* it would look extraordinary.
    early = th.compare_current("NIFTY", tilt_pp=3.0, bars=15)
    assert early["checkpoint_min"] == 15
    assert early["n"] == 5
    assert 20.0 <= early["percentile"] <= 80.0, "should sit inside the early-session spread"

    late = th.compare_current("NIFTY", tilt_pp=3.0, bars=375)
    assert late["checkpoint_min"] == 375
    assert late["percentile"] == 100.0, "+3pp really is above every close in the window"


def test_a_session_is_excluded_from_its_own_comparison(monkeypatch) -> None:
    today = th.datetime.now(tz=th.IST).date().isoformat()
    _seed("NIFTY", today, {375: -18.0})
    out = th.compare_current("NIFTY", tilt_pp=-18.0, bars=375)
    assert out["n"] == 0
    assert out["reason"] == "no_history"
    assert out["available"] is False


def test_too_early_and_thin_windows_refuse_rather_than_rank() -> None:
    for day in ("2026-08-17", "2026-08-18"):
        _seed("NIFTY", day, {375: -10.0})

    # Before the first checkpoint there is no comparison to make.
    early = th.compare_current("NIFTY", tilt_pp=-4.0, bars=9)
    assert early["available"] is False and early["reason"] == "too_early"

    # Two prior sessions is arithmetic, not evidence — n is still reported.
    thin = th.compare_current("NIFTY", tilt_pp=-4.0, bars=375)
    assert thin["available"] is False
    assert thin["reason"] == "window_too_thin"
    assert thin["n"] == 2


def test_backfilled_sessions_are_counted_and_labelled() -> None:
    for day in ("2026-08-17", "2026-08-18", "2026-08-19"):
        _seed("SENSEX", day, {375: -10.0}, source="backfill")
    for day in ("2026-08-20", "2026-08-21"):
        _seed("SENSEX", day, {375: 5.0}, source="live")

    out = th.compare_current("SENSEX", tilt_pp=0.0, bars=375)
    assert out["n"] == 5
    assert out["backfilled"] == 3, "a reader must be able to see how much is recomputed"
    assert {r["source"] for r in out["series"]} == {"backfill", "live"}


def test_a_live_point_upgrades_a_backfilled_session_never_the_reverse() -> None:
    """Live points were observed as they happened; backfill is a reconstruction."""
    _seed("NIFTY", "2026-08-20", {375: -10.0}, source="backfill")
    assert th.get_sessions("NIFTY")[0]["source"] == "backfill"

    th.upsert_point("NIFTY", "2026-08-20", minute=360, tilt_pp=-9.0, bars=360, source="live")
    assert th.get_sessions("NIFTY")[0]["source"] == "live"

    th.upsert_point("NIFTY", "2026-08-20", minute=345, tilt_pp=-8.0, bars=345, source="backfill")
    assert th.get_sessions("NIFTY")[0]["source"] == "live", "backfill must not overwrite live"


def test_an_unmeasured_tilt_is_dropped_not_stored_as_zero() -> None:
    th.upsert_point("NIFTY", "2026-08-20", minute=375, tilt_pp=None, bars=375)
    assert th.get_sessions("NIFTY") == []

    th.upsert_point("NIFTY", "2026-08-20", minute=375, tilt_pp=0.0, bars=375)
    assert th.get_sessions("NIFTY")[0]["curve"]["375"] == 0.0


def test_upsert_is_idempotent_so_a_rerun_converges() -> None:
    _seed("NIFTY", "2026-08-20", {375: -10.0})
    _seed("NIFTY", "2026-08-20", {375: -12.0})
    rows = th.get_sessions("NIFTY")
    assert len(rows) == 1
    assert rows[0]["curve"] == {"375": -12.0}
    assert rows[0]["final_tilt_pp"] == -12.0


def test_percentile_splits_ties_so_a_flat_window_is_not_0_or_100() -> None:
    for day in ("2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20", "2026-08-21"):
        _seed("NIFTY", day, {375: -10.0})
    out = th.compare_current("NIFTY", tilt_pp=-10.0, bars=375)
    assert out["percentile"] == 50.0
    assert out["median"] == -10.0 and out["min"] == -10.0 and out["max"] == -10.0


def test_purge_removes_one_underlying_and_leaves_the_other() -> None:
    _seed("NIFTY", "2026-08-20", {375: -10.0})
    _seed("SENSEX", "2026-08-20", {375: -10.0})
    assert th.purge_underlying("NIFTY") == 1
    assert th.get_sessions("NIFTY") == []
    assert len(th.get_sessions("SENSEX")) == 1


def test_retention_drops_the_oldest_sessions_first(monkeypatch) -> None:
    monkeypatch.setattr(th, "MAX_SESSIONS", 3)
    for day in ("2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20"):
        _seed("NIFTY", day, {375: -10.0})
    dates = [r["date"] for r in th.get_sessions("NIFTY")]
    assert dates == ["2026-08-18", "2026-08-19", "2026-08-20"]


def test_sampler_records_one_point_per_bucket_and_never_recomputes(monkeypatch) -> None:
    calls = {"peek": 0, "compute": 0}

    def _peek(u):
        calls["peek"] += 1
        return {
            "available": True,
            "bars": 188,
            "tilt_pp": -12.5,
            "overlap_pct": 80.0,
            "contract": {"tradingsymbol": f"{u}FUT", "expiry": "2026-09-29"},
        }

    def _boom(*_a, **_k):
        calls["compute"] += 1
        raise AssertionError("sampler must prefer the cached profile")

    monkeypatch.setattr("analysis.volume_profile.service.peek_volume_profile", _peek)
    monkeypatch.setattr("analysis.volume_profile.service.get_volume_profile", _boom)

    assert th.maybe_sample_tilt_history_periodic() is True
    assert calls["compute"] == 0

    rows = th.get_sessions("NIFTY")
    assert len(rows) == 1
    assert rows[0]["curve"] == {"180": -12.5}, "188 bars belongs to the 180 bucket"
    assert rows[0]["source"] == "live"

    # Same bucket again: no second write, and the cadence gate is bypassed so the
    # bucket check itself is what does the work.
    th.reset_for_tests()
    assert th.maybe_sample_tilt_history_periodic() is False
    assert len(th.get_sessions("NIFTY")[0]["curve"]) == 1


def test_sampler_survives_one_underlying_failing(monkeypatch) -> None:
    def _peek(u):
        if u == "NIFTY":
            raise RuntimeError("chain unavailable")
        return {"available": True, "bars": 60, "tilt_pp": 4.0, "contract": {}}

    monkeypatch.setattr("analysis.volume_profile.service.peek_volume_profile", _peek)
    monkeypatch.setattr("analysis.volume_profile.service.get_volume_profile", lambda *_a, **_k: {})

    assert th.maybe_sample_tilt_history_periodic() is True
    assert th.get_sessions("NIFTY") == []
    assert len(th.get_sessions("SENSEX")) == 1
