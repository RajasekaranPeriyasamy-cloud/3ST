"""Tests for the 3ST side of the Volume Footprint desk.

The vendored engine has its own 37 tests pinning the maths
(``tests/test_volume_footprint.py``). These pin the adapter's promises instead:
basis alignment, the thin-session guard, exact strike-band aggregation, and the
peek-only cache contract that keeps the ~200 ms integration off cheap callers.
"""

from __future__ import annotations

import datetime as dt
import math
import random

import pytest

from analysis.volume_profile import service as vp

IST = vp.IST


def _bars(n: int, *, start: float = 24600.0, seed: int = 3, offset: float = 0.0):
    """Synthetic minute session. ``offset`` shifts every price (a fake basis)."""
    rnd = random.Random(seed)
    t0 = dt.datetime(2026, 8, 20, 9, 15, tzinfo=IST)
    out, px = [], start
    for i in range(n):
        px += rnd.gauss(0, 3) + math.sin(i / 30.0) * 0.4
        hi, lo = px + abs(rnd.gauss(0, 2)), px - abs(rnd.gauss(0, 2))
        out.append(
            {
                "date": (t0 + dt.timedelta(minutes=i)).isoformat(),
                "open": round(px + offset, 2),
                "high": round(hi + offset, 2),
                "low": round(lo + offset, 2),
                "close": round(px + offset, 2),
                "volume": max(1, int(abs(rnd.gauss(1000, 300)))),
            }
        )
    return out


@pytest.fixture(autouse=True)
def _clear_cache():
    vp.reset_cache()
    yield
    vp.reset_cache()


def test_basis_shift_moves_futures_onto_the_index_axis() -> None:
    """A constant 120-point premium must be removed, bar by bar."""
    index_bars = _bars(60)
    fut_bars = _bars(60, offset=120.0)

    aligned, basis = vp.align_to_index_axis(fut_bars, index_bars)

    assert basis["mode"] == "per_bar"
    assert basis["matched_bars"] == 60
    assert basis["median"] == pytest.approx(120.0, abs=0.05)
    # Every aligned close should now sit on the index close, not 120 above it.
    for a, idx in zip(aligned, index_bars):
        assert a["close"] == pytest.approx(idx["close"], abs=0.05)
    # Volume is futures volume either way — it must not be touched.
    assert [a["volume"] for a in aligned] == [f["volume"] for f in fut_bars]


def test_unmatched_minutes_carry_the_last_basis_and_are_counted() -> None:
    """A gap in index bars must not silently look like a perfect alignment."""
    fut_bars = _bars(30, offset=100.0)
    index_bars = _bars(30)[:10]  # only the first ten minutes overlap

    aligned, basis = vp.align_to_index_axis(fut_bars, index_bars)

    assert basis["matched_bars"] == 10
    assert len(aligned) == 30
    # The tail was shifted by a stale basis; matched_bars is how a caller knows.
    assert basis["mode"] == "per_bar"


def test_no_index_bars_reports_none_rather_than_pretending() -> None:
    fut = _bars(30, offset=100.0)
    aligned, basis = vp.align_to_index_axis(fut, None)
    assert basis["mode"] == "none"
    assert basis["reason"] == "no_index_bars"
    assert [a["close"] for a in aligned] == [f["close"] for f in fut]


def test_thin_session_is_unmeasured_not_a_confident_shape() -> None:
    payload, res = vp.compute_volume_profile("NIFTY", bars=_bars(5), mintick=0.1)
    assert payload["available"] is False
    assert payload["reason"] == "too_few_bars"
    assert payload["bars"] == 5
    assert res is None
    # No POC is invented from five opening minutes.
    assert "poc" not in payload or payload.get("poc") is None


def test_no_bars_at_all_is_its_own_reason() -> None:
    payload, _ = vp.compute_volume_profile("NIFTY", bars=[], mintick=0.1)
    assert payload["reason"] == "no_session_bars"


def test_unknown_underlying_is_rejected_cleanly() -> None:
    payload, res = vp.compute_volume_profile("NOTATHING", bars=_bars(60))
    assert payload["available"] is False
    assert payload["reason"] == "unknown_underlying"
    assert res is None


def test_full_session_payload_carries_the_estimate_flag_and_readings() -> None:
    payload, res = vp.compute_volume_profile(
        "NIFTY", bars=_bars(90, offset=120.0), index_bars=_bars(90), mintick=0.1
    )
    assert payload["available"] is True
    # The one flag no consumer may forget: this split is inferred, not measured.
    assert payload["estimate"] is True
    assert payload["engine"] == "geometric"
    assert payload["price_axis"] == "index"
    assert payload["basis"]["mode"] == "per_bar"
    assert payload["poc"] is not None
    assert payload["val"] <= payload["poc"] <= payload["vah"]
    assert payload["value_area_pts"] == pytest.approx(payload["vah"] - payload["val"], abs=0.01)
    # The engine's own arithmetic self-check must be inside tolerance.
    assert payload["residual_label"] == "EXACT"
    assert payload["compute_ms"] >= 0
    assert res is not None


def test_mcx_takes_no_basis_correction() -> None:
    """MCX options are written on the future, so the axes already agree."""
    payload, _ = vp.compute_volume_profile("CRUDEOIL", bars=_bars(60, start=6100.0), mintick=1.0)
    assert payload["available"] is True
    assert payload["price_axis"] == "future"
    assert payload["basis"]["mode"] == "none"
    assert payload["basis"]["reason"] == "options_on_future"


def test_strike_bands_aggregate_exactly_and_report_off_frame() -> None:
    payload, res = vp.compute_volume_profile("NIFTY", bars=_bars(120), mintick=0.1)
    assert payload["available"] is True
    poc = payload["poc"]

    # A window wide enough to cover the whole session.
    wide = [poc - 2000 + 50 * i for i in range(81)]
    out = vp.strike_band_volume("NIFTY", wide, 50.0, result=res)
    total = (payload["total_buy"] or 0) + (payload["total_sell"] or 0)
    assert out["available"] is True
    assert out["covered"] == pytest.approx(total, rel=1e-3)
    assert out["off_frame_pct"] == pytest.approx(0.0, abs=0.5)

    # A window narrower than the session's own range must surface the mass it
    # excludes rather than normalising it away. Assert the premise first, so a
    # calmer synthetic session fails loudly instead of passing vacuously.
    span = payload["price_hi"] - payload["price_lo"]
    assert span > 50.0, f"session span {span:.1f} is not wider than one strike band"

    tight = vp.strike_band_volume("NIFTY", [poc], 50.0, result=res)
    assert tight["off_frame"] > 0
    assert tight["off_frame_pct"] > 0
    assert tight["covered"] < out["covered"]


def test_strike_bands_without_a_profile_degrade_quietly() -> None:
    out = vp.strike_band_volume("NIFTY", [], 50.0, result=None)
    assert out["available"] is False
    assert out["bands"] == []


def test_peek_never_computes_and_only_sees_fresh_entries(monkeypatch) -> None:
    """The contract that keeps the integration off session_poc and the strip."""
    called = {"n": 0}

    def _boom(*_a, **_k):
        called["n"] += 1
        raise AssertionError("peek must never compute")

    monkeypatch.setattr(vp, "compute_volume_profile", _boom)
    assert vp.peek_volume_profile("NIFTY") is None
    assert called["n"] == 0

    # A fresh cache entry is returned...
    import time as _time

    vp._CACHE[vp._cache_key("NIFTY", None)] = (_time.time(), {"available": True, "poc": 24600.0}, None)
    assert vp.peek_volume_profile("NIFTY")["poc"] == 24600.0

    # ...a stale one is not, and still does not trigger a computation.
    vp._CACHE[vp._cache_key("NIFTY", None)] = (_time.time() - vp.PROFILE_CACHE_TTL_SEC - 1, {"available": True}, None)
    assert vp.peek_volume_profile("NIFTY") is None

    # An unavailable payload is never offered as a POC source.
    vp._CACHE[vp._cache_key("NIFTY", None)] = (_time.time(), {"available": False, "reason": "too_few_bars"}, None)
    assert vp.peek_volume_profile("NIFTY") is None
    assert called["n"] == 0


def test_session_poc_prefers_the_footprint_when_cached() -> None:
    """One POC on the desk — and no extra Kite work to get it."""
    import time as _time

    from options.session_poc import compute_session_poc

    vp._CACHE[vp._cache_key("NIFTY", None)] = (
        _time.time(),
        {"available": True, "poc": 24612.5, "vah": 24650.0, "val": 24570.0,
         "asof": "2026-08-20T12:00:00+05:30", "bars": 165, "price_axis": "index"},
        None,
    )
    out = compute_session_poc("NIFTY")
    assert out is not None
    assert out["poc"] == 24612.5
    assert out["source"] == "footprint"


def test_cache_is_scoped_to_the_contract() -> None:
    """Switching to the far month must not serve the near month's profile."""
    import time as _time

    near = vp._cache_key("NIFTY", None)
    far = vp._cache_key("NIFTY", "2026-09-24")
    assert near != far

    vp._CACHE[near] = (_time.time(), {"available": True, "poc": 24600.0}, None)
    # A far-month request must miss the near-month entry rather than reuse it.
    with vp._CACHE_LOCK:
        assert vp._CACHE.get(far) is None
    # peek is deliberately front-month only — it backs session_poc and the strip.
    assert vp.peek_volume_profile("NIFTY")["poc"] == 24600.0
