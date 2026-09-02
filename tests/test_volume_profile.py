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
from analysis.volume_profile import tilt_history as vp_tilt

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


# --- price grid and per-side peaks ------------------------------------------


def test_grid_step_is_the_configured_strike_lattice_not_a_guess() -> None:
    """A gridline should land on a strike, so the chart reads against the ladder."""
    assert vp._grid_step("NIFTY") == 50.0
    assert vp._grid_step("SENSEX") == 100.0
    assert vp._grid_step("BANKNIFTY") == 100.0
    assert vp._grid_step("NATURALGAS") == 5.0
    assert vp._grid_step("NOT_A_SYMBOL") is None


def test_side_peaks_come_from_full_resolution_not_the_draw_curve() -> None:
    payload, res = vp.compute_volume_profile("NIFTY", bars=_bars(160), mintick=0.1)
    assert payload["available"] is True

    buy, sell = payload["buy_peak"], payload["sell_peak"]
    assert buy is not None and sell is not None
    assert payload["price_lo"] <= buy["price"] <= payload["price_hi"]
    assert payload["price_lo"] <= sell["price"] <= payload["price_hi"]

    # Each peak is that side's true maximum over the full arrays. The sampled
    # curve keeps only ~1 in N points and can miss a sharp peak by several ticks,
    # which is why POC and the value area never read it either.
    # abs=1e-4 because the payload rounds density to 4 dp for transport; the
    # peak *price* is what the chart marks and that is exact.
    assert buy["density"] == pytest.approx(max(res.profile_buy), abs=1e-4)
    assert sell["density"] == pytest.approx(max(res.profile_sell), abs=1e-4)
    best_buy = max(range(len(res.profile_buy)), key=lambda i: res.profile_buy[i])
    assert buy["price"] == round(res.profile_prices[best_buy], 2)
    assert max(c["buy"] for c in payload["curve"]) <= buy["density"] + 1e-6


def test_a_side_that_carried_no_volume_has_no_peak() -> None:
    """None, not the axis minimum — an empty side's peak is not a price."""

    class _Empty:
        profile_prices = [100.0, 101.0, 102.0]
        profile_buy = [0.0, 0.0, 0.0]
        profile_sell = [1.0, 4.0, 2.0]

    assert vp._side_peak(_Empty(), "buy") is None
    assert vp._side_peak(_Empty(), "sell") == {"price": 101.0, "density": 4.0}


# --- prominent peaks, and the two tilts that describe each one ---------------


def test_prominence_ignores_a_bump_on_a_taller_peak_shoulder() -> None:
    """A shoulder wobble is fitting noise, not a level worth labelling."""
    prices = [float(i) for i in range(11)]
    #             0    1    2     3    4    5    6    7    8    9   10
    vals = [0.0, 2.0, 6.0, 10.0, 6.5, 7.0, 3.0, 0.5, 4.0, 1.0, 0.0]
    peaks = {p["index"]: p["prominence"] for p in vp._prominence_peaks(prices, vals)}

    # The 10.0 summit stands clear of everything.
    assert peaks[3] == pytest.approx(10.0)
    # The 7.0 at index 5 sits on the big peak's shoulder: it only rises 0.5
    # above the saddle at 6.5, so it scores far below the 4.0 at index 8 which
    # rises 3.5 above its own saddle.
    assert peaks[5] == pytest.approx(0.5)
    assert peaks[8] == pytest.approx(3.5)
    assert peaks[8] > peaks[5]


def test_selection_keeps_the_taller_of_two_peaks_that_are_too_close() -> None:
    peaks = [
        {"price": 24300.0, "prominence": 10.0},
        {"price": 24310.0, "prominence": 8.0},  # inside the separation window
        {"price": 24400.0, "prominence": 5.0},
    ]
    kept = vp._select_peaks(peaks, min_separation=25.0, limit=4)
    assert [p["price"] for p in kept] == [24300.0, 24400.0]

    # limit is honoured even when everything is far apart.
    spread = [{"price": 24000.0 + 100 * i, "prominence": 10.0 - i} for i in range(6)]
    assert len(vp._select_peaks(spread, min_separation=25.0, limit=4)) == 4


def test_band_tilt_describes_the_band_not_the_whole_session() -> None:
    buys = [0.0, 10.0, 90.0, 10.0, 0.0]
    sells = [0.0, 10.0, 10.0, 10.0, 0.0]
    tilt, b, s = vp._band_tilt(buys, sells, 1, 3)
    assert (b, s) == (110.0, 30.0)
    assert tilt == pytest.approx(57.14, abs=0.01)

    # An empty band is unmeasurable, not balanced.
    assert vp._band_tilt([0.0, 0.0], [0.0, 0.0], 0, 1)[0] is None


class _FakeBar:
    def __init__(self, hhmm, low, high, vol, buy, sell):
        self.time = f"2026-08-26T{hhmm}+05:30"
        self.low, self.high, self.volume = low, high, vol
        self.buy_volume, self.sell_volume = buy, sell


def test_flow_window_is_a_window_and_only_claims_a_time_when_earned() -> None:
    """A profile peak is a price feature; its volume can span the whole session."""
    # Tight: everything traded inside ten minutes.
    tight = [_FakeBar(f"11:{m:02d}", 100.0, 101.0, 10.0, 7.0, 3.0) for m in range(30, 40)]
    out = vp._flow_at_band(tight, 100.0, 101.0)
    assert out["bar_count"] == 10
    assert out["flow_tilt_pp"] == pytest.approx(40.0)
    assert out["first"] == "11:30" and out["last"] == "11:39"
    assert out["concentrated"] is True

    # Spread: the same band revisited from open to close.
    spread = [_FakeBar(f"{h:02d}:00", 100.0, 101.0, 10.0, 7.0, 3.0) for h in range(10, 16)]
    out2 = vp._flow_at_band(spread, 100.0, 101.0)
    assert out2["concentrated"] is False, "6 hours must never be reported as a moment"
    assert out2["first"] == "10:00" and out2["last"] == "15:00"

    # A band nothing traded through reports no tilt rather than zero.
    miss = vp._flow_at_band(tight, 500.0, 501.0)
    assert miss["bar_count"] == 0 and miss["flow_tilt_pp"] is None


def test_peaks_are_ranked_tallest_first_and_agree_with_the_singular_peak() -> None:
    payload, _ = vp.compute_volume_profile("NIFTY", bars=_bars(200), mintick=0.1)
    for side in ("buy", "sell"):
        peaks = payload[f"{side}_peaks"]
        assert peaks, f"{side} side should find at least one peak"
        assert len(peaks) <= vp.MAX_PEAKS_PER_SIDE
        densities = [p["density"] for p in peaks]
        assert densities == sorted(densities, reverse=True)
        # [0] is the same level the singular key names.
        assert peaks[0]["price"] == pytest.approx(payload[f"{side}_peak"]["price"], abs=0.5)
        for p in peaks:
            assert p["band_lo"] <= p["price"] <= p["band_hi"]
            assert 0 < p["prominence_pct"] <= 100.0


def test_lookback_still_reaches_the_open_long_after_close() -> None:
    """The drift bug: a clamped lookback stops anchoring and starts sliding.

    ``fetch_minute_candles`` derives ``from_date = now - count``, so any upper
    clamp on the count turns the window into a rolling one once the session is
    older than the clamp. Observed 2026-08-26 at 20:25 IST: the shared
    600-minute helper produced a profile of 314 bars starting 10:26 instead of
    385 from 09:15, and POC/tilt/peaks moved on every cache expiry.
    """
    late = dt.datetime(2026, 8, 26, 20, 25, tzinfo=IST)
    lookback = vp.session_lookback_minutes("NIFTY", when=late)

    # now - lookback must land at or before the 09:15 open, never after it.
    reach = late - dt.timedelta(minutes=lookback)
    assert reach <= late.replace(hour=9, minute=15), (
        f"window starts {reach:%H:%M}, after the 09:15 open — the session is truncated"
    )
    assert lookback > 600, "a 600-minute clamp is exactly what caused the drift"

    # Still anchored at the far end of the day, and for MCX's longer session.
    midnight = dt.datetime(2026, 8, 26, 23, 59, tzinfo=IST)
    assert midnight - dt.timedelta(
        minutes=vp.session_lookback_minutes("NIFTY", when=midnight)
    ) <= midnight.replace(hour=9, minute=15)
    assert vp.session_lookback_minutes("CRUDEOIL", when=midnight) > 600

    # Before the open there is no session to reach back to.
    pre = dt.datetime(2026, 8, 26, 8, 0, tzinfo=IST)
    assert vp.session_lookback_minutes("NIFTY", when=pre) == vp.MIN_PROFILE_BARS


def test_concentration_boundary_is_inclusive_and_wide_windows_still_refuse() -> None:
    """The threshold decides moment-vs-range, never something-vs-nothing.

    Raised to 60 because at 45 two peaks with near-identical windows landed on
    opposite sides — one timed, one blank — which read as arbitrary rather than
    deliberate. Peaks past the boundary now carry their range instead.
    """
    def _band(minutes: int):
        bars = [
            _FakeBar(f"{10 + m // 60:02d}:{m % 60:02d}", 100.0, 101.0, 10.0, 6.0, 4.0)
            for m in range(0, minutes + 1)
        ]
        return vp._flow_at_band(bars, 100.0, 101.0)

    # A one-hour window is the boundary and counts as concentrated.
    assert _band(60)["concentrated"] is True
    # Comfortably past it, a range is the only honest answer.
    assert _band(180)["concentrated"] is False
    # The window itself is reported either way, so the chart always has a range
    # to fall back on.
    for m in (60, 180):
        out = _band(m)
        assert out["q1"] and out["q3"] and out["first"] and out["last"]


# --- POC trail ---------------------------------------------------------------


def test_poc_trail_groups_drift_into_levels_and_merges_revisits(monkeypatch) -> None:
    """Two things the trail has to get right on a real session.

    A stable POC drifts a few points across a morning and must stay ONE level;
    a POC that leaves and comes back must not become two. Measured 2026-08-26:
    NIFTY held ~24,346 for 84% of the session then migrated 71 points, and
    SENSEX oscillated between two prices producing six runs of two levels.
    """
    curve = [
        # A morning level drifting 5 points — one level, not four.
        {"minute": 15, "poc": 24342.9},
        {"minute": 30, "poc": 24343.1},
        {"minute": 45, "poc": 24347.6},
        {"minute": 60, "poc": 24348.2},
        # Away and back again — same level, second spell.
        {"minute": 75, "poc": 24277.0},
        {"minute": 90, "poc": 24346.0},
        # A genuine migration.
        {"minute": 105, "poc": 24277.5},
        {"minute": 120, "poc": 24278.1},
    ]
    monkeypatch.setattr(
        "analysis.volume_profile.tilt_history.poc_curve", lambda *_a, **_k: curve
    )
    out = vp.poc_trail("NIFTY", day="2026-08-26")

    assert out["available"] is True
    assert len(out["segments"]) == 4, "runs are chronological and must not be merged"
    assert len(out["levels"]) == 2, "but only two prices were ever in control"

    top = out["levels"][0]
    assert top["spells"] == 2, "a revisited price is one level with two spells"
    assert top["minutes"] > out["levels"][1]["minutes"]
    assert sum(lv["dwell_pct"] for lv in out["levels"]) == pytest.approx(100.0, abs=0.2)
    assert out["band_lo"] == 24277.0 and out["band_hi"] == 24348.2


def test_poc_trail_refuses_rather_than_drawing_a_partial_one(monkeypatch) -> None:
    # Not one of the sampled underlyings — there is no trail to have. Picked
    # from TILT_HISTORY_UNDERLYINGS rather than hardcoded, because this used to
    # name BANKNIFTY and silently changed meaning the day BANKNIFTY was added to
    # the sampler: the assertion still passed as written until it didn't, and a
    # test whose premise can rot without failing is worse than no test.
    unsampled = next(
        u for u in ("CRUDEOIL", "NATURALGAS", "GOLD")
        if u not in vp_tilt.TILT_HISTORY_UNDERLYINGS
    )
    assert vp.poc_trail(unsampled, day="2026-08-26")["reason"] == "not_sampled"

    # Sampled, but nothing recorded yet: a single point is not a trail.
    monkeypatch.setattr(
        "analysis.volume_profile.tilt_history.poc_curve",
        lambda *_a, **_k: [{"minute": 15, "poc": 24300.0}],
    )
    out = vp.poc_trail("NIFTY", day="2026-08-26")
    assert out["available"] is False
    assert out["reason"] == "no_trail_yet"
    assert out["segments"] == []
