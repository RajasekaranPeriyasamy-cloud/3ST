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


# --- OI ladder -------------------------------------------------------------
#
# The ladder derives nothing: every OI column is read off the gamma snapshot,
# which has already run ``attach_strike_oi_baselines``. These pin that promise,
# the null discipline around a missing baseline, and the single-snapshot
# contract that keeps this desk at one option-chain pull.


def _snap(strikes, **over):
    out = {
        "expiry": "2026-08-27",
        "spot": 24610.0,
        "atm_strike": 24600.0,
        "strikes": strikes,
        "oi_baseline_mode": "session_open",
        "oi_baseline_note": "session open · 6 legs",
        "oi_baseline_open_count": 6,
        "oi_baseline_prev_close_count": 0,
    }
    out.update(over)
    return out


def _srow(strike, *, ce_oi=None, pe_oi=None, ce_base=None, pe_base=None, src="open"):
    return {
        "strike": strike,
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "ce_oi_base": ce_base,
        "pe_oi_base": pe_base,
        "ce_doi": None if (ce_oi is None or ce_base is None) else ce_oi - ce_base,
        "pe_doi": None if (pe_oi is None or pe_base is None) else pe_oi - pe_base,
        "ce_oi_base_source": src,
        "pe_oi_base_source": src,
    }


def test_oi_ladder_carries_open_current_and_delta_off_the_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(
        vp,
        "strike_band_volume",
        lambda *_a, **_k: {
            "available": True,
            "bands": [
                {"strike": 24550.0, "buy": 10.0, "sell": 5.0, "total": 15.0},
                {"strike": 24600.0, "buy": 40.0, "sell": 60.0, "total": 100.0},
            ],
        },
    )
    snap = _snap(
        [
            _srow(24550, ce_oi=1200, pe_oi=900, ce_base=1000, pe_base=1000),
            _srow(24600, ce_oi=5000, pe_oi=8000, ce_base=4000, pe_base=6000),
        ]
    )

    out = vp._oi_ladder_from_snapshot("NIFTY", snap)

    assert out["available"] is True
    assert out["strike_step"] == 50.0
    lo, hi = out["rows"]

    # Open OI, current OI and ΔOI are passed through, not recomputed.
    assert (lo["ce_open_oi"], lo["ce_oi"], lo["ce_doi"]) == (1000, 1200, 200)
    assert (lo["pe_open_oi"], lo["pe_oi"], lo["pe_doi"]) == (1000, 900, -100)
    assert lo["net_doi"] == 100

    # Volume is merged onto the same strike, so ΔOI reads against what traded.
    assert (hi["volume"], hi["buy_volume"], hi["sell_volume"]) == (100.0, 40.0, 60.0)

    assert out["total_ce_doi"] == 200 + 1000
    assert out["total_pe_doi"] == -100 + 2000
    # One shared bar scale across both sides — the largest absolute move.
    assert out["max_abs_doi"] == 2000
    assert out["oi_baseline_note"] == "session open · 6 legs"
    assert out["spot"] == 24610.0
    assert out["price_axis"] == "index"


def test_oi_ladder_keeps_an_uncaptured_baseline_null_never_zero(monkeypatch) -> None:
    """``None`` means *could not be measured*; ``0`` means genuinely unchanged."""
    monkeypatch.setattr(vp, "strike_band_volume", lambda *_a, **_k: {"available": False})
    snap = _snap(
        [
            # No baseline on either side — nothing can be said about the move.
            _srow(24600, ce_oi=5000, pe_oi=8000, ce_base=None, pe_base=None, src=None),
            # Baseline captured and the strike genuinely did not move.
            _srow(24650, ce_oi=3000, pe_oi=None, ce_base=3000, pe_base=None),
        ]
    )

    blind, flat = vp._oi_ladder_from_snapshot("NIFTY", snap)["rows"]

    assert blind["ce_doi"] is None and blind["pe_doi"] is None
    assert blind["net_doi"] is None, "no baseline on either side must not read as flat"
    assert flat["ce_doi"] == 0, "a measured, unmoved strike is 0 — not null"
    # One side measured, the other not: the *net* is still unmeasurable.
    assert flat["pe_doi"] is None
    assert flat["net_doi"] is None


def test_oi_ladder_renders_when_the_session_is_too_thin_for_a_profile(monkeypatch) -> None:
    """The OI half and the volume half fail independently, on purpose."""
    monkeypatch.setattr(
        vp,
        "strike_band_volume",
        lambda *_a, **_k: {"available": False, "reason": "thin_session", "bands": []},
    )
    out = vp._oi_ladder_from_snapshot(
        "NIFTY", _snap([_srow(24600, ce_oi=5000, pe_oi=8000, ce_base=4000, pe_base=6000)])
    )

    assert out["available"] is True
    assert out["volume_available"] is False
    assert out["volume_reason"] == "thin_session"
    row = out["rows"][0]
    assert row["volume"] is None
    assert row["ce_doi"] == 1000


def test_strike_step_is_the_modal_gap_so_one_gap_cannot_widen_the_bands() -> None:
    # 24700 missing: mean gap is 62.5, the mode is still 50.
    strikes = [{"strike": k} for k in (24550, 24600, 24650, 24750)]
    assert vp._infer_strike_step(strikes) == 50.0
    assert vp._infer_strike_step([]) == 50.0


def test_levels_and_ladder_come_from_one_snapshot_and_one_cache(monkeypatch) -> None:
    """Opening this desk must not cost a second option-chain pull."""
    calls = {"n": 0}

    def _fake(underlying, **kwargs):
        calls["n"] += 1
        # History-free, or this page would double-write the session trail and
        # record a page visit as a pin sample.
        assert kwargs["include_history"] is False
        return _snap([_srow(24600, ce_oi=5000, pe_oi=8000, ce_base=4000, pe_base=6000)])

    monkeypatch.setattr("options.gamma_density.build_gamma_snapshot", _fake)
    monkeypatch.setattr(vp, "strike_band_volume", lambda *_a, **_k: {"available": False})

    levels = vp.gamma_levels("NIFTY")
    ladder = vp.strike_oi_ladder("NIFTY")

    assert calls["n"] == 1, "levels and ladder must share one snapshot"
    assert levels["available"] is True and levels["spot"] == 24610.0
    assert ladder["available"] is True and ladder["rows"][0]["ce_doi"] == 1000

    vp.reset_cache()
    vp.gamma_levels("NIFTY")
    assert calls["n"] == 2, "reset_cache must clear the shared entry"


def test_ladder_reports_gamma_failure_without_taking_the_profile_down(monkeypatch) -> None:
    def _boom(*_a, **_k):
        raise RuntimeError("no option chain")

    monkeypatch.setattr("options.gamma_density.build_gamma_snapshot", _boom)

    ladder = vp.strike_oi_ladder("NIFTY")
    assert ladder["available"] is False
    assert ladder["reason"] == "gamma_unavailable"
    assert ladder["rows"] == []
    assert vp.gamma_levels("NIFTY")["reason"] == "gamma_unavailable"


def test_oi_ladder_percentage_matches_the_oi_tracker_formula(monkeypatch) -> None:
    """``doi / baseline * 100`` — the same number the session-change boards print."""
    monkeypatch.setattr(vp, "strike_band_volume", lambda *_a, **_k: {"available": False})
    snap = _snap(
        [
            _srow(24600, ce_oi=5000, pe_oi=4500, ce_base=4000, pe_base=6000),
            # Opened empty: a percentage here would turn the first contract
            # written into an infinite move.
            _srow(24650, ce_oi=800, pe_oi=None, ce_base=0, pe_base=None),
        ]
    )

    moved, from_empty = vp._oi_ladder_from_snapshot("NIFTY", snap)["rows"]

    assert moved["ce_doi_pct"] == 25.0  # +1000 on 4000
    assert moved["pe_doi_pct"] == -25.0  # -1500 on 6000
    assert from_empty["ce_doi"] == 800
    assert from_empty["ce_doi_pct"] is None, "a zero baseline has no percentage"
    assert from_empty["pe_doi_pct"] is None
