"""Unit tests for CAS estimate proxy blend + official sanity (no live Kite)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from options.cas_estimate import (
    blend_proxy_estimate,
    compute_cas_estimate,
    constituent_coverage,
    rebuild_index_from_constituents,
    resolve_ref_vwap_window,
    sanitize_official_indicative,
    try_constituent_estimate,
    vwap_from_bars,
)

IST = ZoneInfo("Asia/Kolkata")


def test_sanitize_rejects_garbage_vs_spot() -> None:
    spot = 24550.0
    assert sanitize_official_indicative(15.0, spot) is None
    assert sanitize_official_indicative(1866.0, spot) is None
    assert sanitize_official_indicative(None, spot) is None
    assert sanitize_official_indicative(24550.0, None) is None


def test_sanitize_accepts_within_band() -> None:
    spot = 24550.0
    # ~0.09% away — well inside 3%
    assert sanitize_official_indicative(24572.0, spot) == 24572.0
    # Exactly at 3% band edge
    edge = spot * 1.03
    assert sanitize_official_indicative(edge, spot) == edge
    # Just outside
    assert sanitize_official_indicative(spot * 1.0301, spot) is None
    assert sanitize_official_indicative(spot * 0.969, spot) is None


def test_blend_proxy_full_weights() -> None:
    est, comps = blend_proxy_estimate(
        synth_f=100.0,
        fut_ltp=200.0,
        ref_vwap=300.0,
    )
    # 0.40*100 + 0.35*200 + 0.25*300 = 40 + 70 + 75 = 185
    # clamp to 300 ± 3% → [291, 309] → 185 clamped to 291
    assert est == 291.0
    assert comps["clamped"] is True
    assert abs(comps["weights_used"]["synth_f"] - 0.40) < 1e-9
    assert abs(comps["weights_used"]["fut_ltp"] - 0.35) < 1e-9
    assert abs(comps["weights_used"]["ref_vwap"] - 0.25) < 1e-9


def test_blend_proxy_no_clamp_when_inside_band() -> None:
    est, comps = blend_proxy_estimate(
        synth_f=24780.0,
        fut_ltp=24755.0,
        ref_vwap=24740.0,
    )
    expected = 0.40 * 24780.0 + 0.35 * 24755.0 + 0.25 * 24740.0
    assert est is not None
    assert abs(est - expected) < 1e-6
    assert comps["clamped"] is False


def test_blend_renormalizes_when_synth_missing() -> None:
    est, comps = blend_proxy_estimate(
        synth_f=None,
        fut_ltp=24755.0,
        ref_vwap=24740.0,
    )
    # weights 0.35 + 0.25 = 0.60 → fut 0.35/0.60, ref 0.25/0.60
    expected = (0.35 / 0.60) * 24755.0 + (0.25 / 0.60) * 24740.0
    assert est is not None
    assert abs(est - expected) < 1e-6
    assert "synth_f" not in comps["weights_used"]
    assert abs(comps["weights_used"]["fut_ltp"] - 0.35 / 0.60) < 1e-9


def test_blend_all_missing() -> None:
    est, comps = blend_proxy_estimate(synth_f=None, fut_ltp=None, ref_vwap=None)
    assert est is None
    assert comps["weights_used"] == {}


def test_resolve_ref_vwap_window_modes() -> None:
    start, end, mode = resolve_ref_vwap_window(datetime(2026, 8, 6, 14, 30, tzinfo=IST))
    assert mode == "session"
    assert start.hour == 9 and start.minute == 15
    assert end.hour == 14 and end.minute == 31  # exclusive next minute

    start2, end2, mode2 = resolve_ref_vwap_window(datetime(2026, 8, 6, 15, 8, tzinfo=IST))
    assert mode2 == "running_1500"
    assert start2.hour == 15 and start2.minute == 0
    assert end2.hour == 15 and end2.minute == 9

    start3, end3, mode3 = resolve_ref_vwap_window(datetime(2026, 8, 6, 15, 20, tzinfo=IST))
    assert mode3 == "pre_close_1515"
    assert (start3.hour, start3.minute) == (15, 0)
    assert (end3.hour, end3.minute) == (15, 15)


def test_vwap_from_bars_window() -> None:
    day = datetime(2026, 8, 6, 15, 20, tzinfo=IST)
    bars = [
        {
            "date": datetime(2026, 8, 6, 14, 59, tzinfo=IST),
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1_000_000,
        },
        {
            "date": datetime(2026, 8, 6, 15, 0, tzinfo=IST),
            "high": 110.0,
            "low": 90.0,
            "close": 100.0,
            "volume": 100.0,
        },
        {
            "date": datetime(2026, 8, 6, 15, 10, tzinfo=IST),
            "high": 210.0,
            "low": 190.0,
            "close": 200.0,
            "volume": 300.0,
        },
        {
            "date": datetime(2026, 8, 6, 15, 15, tzinfo=IST),
            "high": 999.0,
            "low": 999.0,
            "close": 999.0,
            "volume": 1_000_000,
        },
    ]
    # typicals: 100 (vol 100), 200 (vol 300) → VWAP = (100*100 + 200*300) / 400 = 175
    assert vwap_from_bars(bars, session_date=day) == 175.0


def test_vwap_running_1500_incomplete_window() -> None:
    """Before 15:15, only bars from 15:00→now count (not post-15:15)."""
    when = datetime(2026, 8, 6, 15, 8, tzinfo=IST)
    start, end, mode = resolve_ref_vwap_window(when)
    assert mode == "running_1500"
    bars = [
        {
            "date": datetime(2026, 8, 6, 15, 0, tzinfo=IST),
            "high": 110.0,
            "low": 90.0,
            "close": 100.0,
            "volume": 100.0,
        },
        {
            "date": datetime(2026, 8, 6, 15, 5, tzinfo=IST),
            "high": 210.0,
            "low": 190.0,
            "close": 200.0,
            "volume": 300.0,
        },
        {
            "date": datetime(2026, 8, 6, 15, 12, tzinfo=IST),  # after "now" end (15:09)
            "high": 999.0,
            "low": 999.0,
            "close": 999.0,
            "volume": 1_000_000,
        },
    ]
    # (100*100 + 200*300) / 400 = 175; 15:12 excluded
    assert vwap_from_bars(bars, start=start, end=end, session_date=when) == 175.0


def test_vwap_session_fallback_window() -> None:
    when = datetime(2026, 8, 6, 14, 0, tzinfo=IST)
    start, end, mode = resolve_ref_vwap_window(when)
    assert mode == "session"
    bars = [
        {
            "date": datetime(2026, 8, 6, 9, 15, tzinfo=IST),
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 100.0,
        },
        {
            "date": datetime(2026, 8, 6, 13, 0, tzinfo=IST),
            "high": 200.0,
            "low": 200.0,
            "close": 200.0,
            "volume": 100.0,
        },
        {
            "date": datetime(2026, 8, 6, 15, 0, tzinfo=IST),  # outside session end at 14:01
            "high": 999.0,
            "low": 999.0,
            "close": 999.0,
            "volume": 1_000_000,
        },
    ]
    assert vwap_from_bars(bars, start=start, end=end, session_date=when) == 150.0


def test_compute_cas_estimate_injected_no_fetch() -> None:
    out = compute_cas_estimate(
        "NIFTY",
        when=datetime(2026, 8, 6, 15, 20, tzinfo=IST),
        synth_f=24780.0,
        fut_ltp=24755.0,
        ref_vwap=24740.0,
        fut_poc=24700.0,
        fetch_missing=False,
        try_constituent=False,
    )
    assert out["estimate_method"] == "proxy_v1"
    expected = 0.40 * 24780.0 + 0.35 * 24755.0 + 0.25 * 24740.0
    assert out["estimate"] is not None
    assert abs(out["estimate"] - expected) < 1e-6
    assert out["estimate_components"]["fut_poc"] == 24700.0
    assert out["estimate_components"]["ref_vwap_window"] == "pre_close_1515"


def test_compute_cas_estimate_before_1515() -> None:
    """Objective 1: meaningful proxy estimate before CAS / before 15:15."""
    out = compute_cas_estimate(
        "NIFTY",
        when=datetime(2026, 8, 6, 14, 45, tzinfo=IST),
        synth_f=24780.0,
        fut_ltp=24755.0,
        ref_vwap=24740.0,
        fetch_missing=False,
        try_constituent=False,
    )
    assert out["estimate"] is not None
    assert out["estimate_method"] == "proxy_v1"
    assert out["estimate_components"]["ref_vwap_window"] == "session"
    expected = 0.40 * 24780.0 + 0.35 * 24755.0 + 0.25 * 24740.0
    assert abs(out["estimate"] - expected) < 1e-6


def test_compute_cas_estimate_synth_fut_only_before_ref() -> None:
    """Before VWAP exists, renormalize on Synth F + Fut LTP alone."""
    out = compute_cas_estimate(
        "NIFTY",
        when=datetime(2026, 8, 6, 11, 0, tzinfo=IST),
        synth_f=24780.0,
        fut_ltp=24755.0,
        ref_vwap=None,
        fetch_missing=False,
        try_constituent=False,
    )
    assert out["estimate"] is not None
    expected = (0.40 / 0.75) * 24780.0 + (0.35 / 0.75) * 24755.0
    assert abs(out["estimate"] - expected) < 1e-6
    assert "ref_vwap" not in out["estimate_components"]["weights_used"]


def test_constituent_scaffold_not_ready() -> None:
    est, meta = try_constituent_estimate("NIFTY")
    assert est is None
    assert meta["status"] == "scaffold"
    assert meta["method"] == "constituent_v1"


def test_rebuild_requires_coverage() -> None:
    weights = {"AAA": 0.5, "BBB": 0.5}
    prices = {"AAA": 100.0, "BBB": None}
    assert constituent_coverage(weights, prices) == 0.5
    est, meta = rebuild_index_from_constituents(weights, prices, divisor=1.0, coverage_threshold=0.9)
    assert est is None
    assert meta["reason"] == "coverage_below_threshold"

    prices2 = {"AAA": 100.0, "BBB": 200.0}
    est2, meta2 = rebuild_index_from_constituents(weights, prices2, divisor=1.0, coverage_threshold=0.9)
    assert est2 == 150.0  # 0.5*100 + 0.5*200
    assert meta2["ready"] is True
