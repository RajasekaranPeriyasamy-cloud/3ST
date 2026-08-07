"""Tests for session futures volume POC helper + snapshot enrichment."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from vollib.black_scholes import black_scholes as bs_price

from options import gamma_density as gd
from options import session_poc as sp
from options.gamma_density_provider import StaticGammaDensityDataProvider

IST = ZoneInfo("Asia/Kolkata")
R = 0.065
SPOT = 20000.0
STEP = 50
TTE = 5.0 / 365.0
IV = 0.14
EXPIRY = "2026-08-13"


def _bar(
    t: str,
    *,
    high: float,
    low: float,
    close: float,
    volume: float,
) -> dict:
    return {
        "date": datetime.fromisoformat(t).replace(tzinfo=IST),
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


# --- binning -----------------------------------------------------------------


def test_poc_bins_typical_price_to_strike_step() -> None:
    # Typical prices:
    #   bar1 (H+L+C)/3 = (24760+24740+24750)/3 = 24750 → bin 24750, vol 1000
    #   bar2             = (24810+24790+24800)/3 = 24800 → bin 24800, vol 500
    #   bar3             = (24755+24745+24750)/3 = 24750 → bin 24750, vol 2000
    # POC at 24750 with total volume 3500
    bars = [
        _bar("2026-08-06T09:15:00", high=24760, low=24740, close=24750, volume=1000),
        _bar("2026-08-06T09:16:00", high=24810, low=24790, close=24800, volume=500),
        _bar("2026-08-06T09:17:00", high=24755, low=24745, close=24750, volume=2000),
    ]
    poc, total, path = sp.poc_from_bars(bars, bin_step=50)
    assert poc == 24750.0
    assert total == 3500
    assert len(path) == 3
    assert path[0]["t"].startswith("2026-08-06T09:15:00")
    assert "+05:30" in path[0]["t"]
    assert path[0]["close"] == 24750.0
    assert path[1]["close"] == 24800.0


def test_poc_snaps_midpoint_typical_to_nearest_bin() -> None:
    # Typical ≈ 24724 → nearest 50-step = 24700; volume wins that bin
    bars = [
        _bar("2026-08-06T09:15:00", high=24730, low=24720, close=24722, volume=800),
        _bar("2026-08-06T09:16:00", high=24810, low=24790, close=24800, volume=100),
    ]
    poc, total, _path = sp.poc_from_bars(bars, bin_step=50)
    assert poc == 24700.0
    assert total == 900


def test_poc_empty_bars_returns_none() -> None:
    poc, total, path = sp.poc_from_bars([], bin_step=50)
    assert poc is None
    assert total == 0
    assert path == []


def test_poc_zero_volume_returns_none() -> None:
    bars = [
        _bar("2026-08-06T09:15:00", high=24760, low=24740, close=24750, volume=0),
        _bar("2026-08-06T09:16:00", high=24810, low=24790, close=24800, volume=0),
    ]
    poc, total, path = sp.poc_from_bars(bars, bin_step=50)
    assert poc is None
    assert total == 0
    assert len(path) == 2  # closes still available for strip chart


def test_compute_session_poc_zero_volume_none(monkeypatch: pytest.MonkeyPatch) -> None:
    sp.clear_session_poc_cache()
    monkeypatch.setattr(
        sp,
        "resolve_future",
        lambda u: {"instrument_token": 123, "tradingsymbol": "NIFTY25AUGFUT"},
    )
    bars = [
        _bar("2026-08-06T09:15:00", high=24760, low=24740, close=24750, volume=0),
    ]
    out = sp.compute_session_poc("NIFTY", bars=bars, when=datetime(2026, 8, 6, 10, 0, tzinfo=IST))
    assert out is None


def test_compute_session_poc_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    sp.clear_session_poc_cache()
    monkeypatch.setattr(
        sp,
        "resolve_future",
        lambda u: {"instrument_token": 123, "tradingsymbol": "NIFTY25AUGFUT"},
    )
    bars = [
        _bar("2026-08-06T09:15:00", high=24760, low=24740, close=24750, volume=1000),
        _bar("2026-08-06T09:16:00", high=24765, low=24745, close=24755, volume=2000),
    ]
    when = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    out = sp.compute_session_poc("NIFTY", bars=bars, when=when)
    assert out is not None
    assert out["poc"] == 24750.0
    assert out["fut_symbol"] == "NIFTY25AUGFUT"
    assert out["fut_token"] == 123
    assert out["bin_step"] == 50
    assert out["total_volume"] == 3000
    assert out["asof"].startswith("2026-08-06T10:00:00")
    assert "+05:30" in out["asof"]
    assert isinstance(out["path"], list) and len(out["path"]) == 2


def test_compute_session_poc_no_future_none(monkeypatch: pytest.MonkeyPatch) -> None:
    sp.clear_session_poc_cache()

    def _boom(_u: str):
        raise RuntimeError("no future")

    monkeypatch.setattr(sp, "resolve_future", _boom)
    assert sp.compute_session_poc("NIFTY", bars=[], use_cache=False) is None


# --- snapshot enrichment -----------------------------------------------------


def _price(flag: str, strike: float) -> float:
    return float(bs_price(flag, SPOT, strike, TTE, R, IV))


def _fake_chain(underlying: str, expiry: str) -> dict:
    strikes = []
    quotes: dict[str, dict] = {}
    for k in range(int(SPOT) - 5 * STEP, int(SPOT) + 5 * STEP + 1, STEP):
        ce_sym = f"{underlying}{k}CE"
        pe_sym = f"{underlying}{k}PE"
        strikes.append(
            {
                "strike": float(k),
                "ce": {
                    "tradingsymbol": ce_sym,
                    "instrument_token": k * 10 + 1,
                    "exchange": "NFO",
                    "lot_size": 65,
                },
                "pe": {
                    "tradingsymbol": pe_sym,
                    "instrument_token": k * 10 + 2,
                    "exchange": "NFO",
                    "lot_size": 65,
                },
            }
        )
        ce_px = _price("c", k)
        pe_px = _price("p", k)
        quotes[f"NFO:{ce_sym}"] = {
            "oi": 100000,
            "last_price": ce_px,
            "depth": {
                "buy": [{"price": ce_px * 0.995, "quantity": 100}],
                "sell": [{"price": ce_px * 1.005, "quantity": 100}],
            },
        }
        quotes[f"NFO:{pe_sym}"] = {
            "oi": 120000,
            "last_price": pe_px,
            "depth": {
                "buy": [{"price": pe_px * 0.995, "quantity": 100}],
                "sell": [{"price": pe_px * 1.005, "quantity": 100}],
            },
        }
    _fake_chain.quotes = quotes  # type: ignore[attr-defined]
    return {
        "underlying": underlying,
        "expiry": expiry,
        "exchange": "NFO",
        "strike_step": STEP,
        "lot_size": 65,
        "strikes": strikes,
    }


def test_gamma_snapshot_includes_session_poc_without_changing_math(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _fake_chain("NIFTY", EXPIRY)
    provider = StaticGammaDensityDataProvider(
        chain=chain,
        spot=SPOT,
        quotes=_fake_chain.quotes,  # type: ignore[attr-defined]
        expiries=[EXPIRY],
    )
    monkeypatch.setattr(gd, "time_to_expiry_years", lambda e: TTE)

    poc_payload = {
        "poc": 20000.0,
        "fut_symbol": "NIFTY25AUGFUT",
        "fut_token": 123,
        "bin_step": 50,
        "total_volume": 1_000_000,
        "asof": "2026-08-06T10:00:00+05:30",
        "path": [{"t": "2026-08-06T09:15:00+05:30", "close": 19980.0}],
    }
    monkeypatch.setattr(
        "options.session_poc.compute_session_poc",
        lambda u: poc_payload,
    )
    monkeypatch.setattr("options.cas_indicative.cas_for_snapshot", lambda u: None)

    snap = gd.build_gamma_snapshot(
        "NIFTY",
        provider=provider,
        include_multi_expiry=False,
        include_history=False,
    )
    assert snap["session_poc"] == poc_payload
    assert snap["spot"] == SPOT
    assert snap["atm_strike"] == SPOT
    # Fut POC must not replace spot used for ATM / GEX
    assert snap["atm_strike"] == SPOT
    assert isinstance(snap["strikes"], list) and snap["strikes"]
    assert snap["total_gex"] is not None


def test_oi_movers_snapshot_includes_session_poc(monkeypatch: pytest.MonkeyPatch) -> None:
    from options import oi_movers as om

    fake_snap = {
        "underlying": "NIFTY",
        "expiry": EXPIRY,
        "spot": SPOT,
        "atm_strike": SPOT,
        "spot_warning": None,
        "updated_at": "2026-08-06T10:00:00+05:30",
        "options_count": 5,
        "pcr": {
            "chain_oi": round(900 / 1100, 4),
            "call_oi_total": 1100,
            "put_oi_total": 900,
        },
        "calls": [{"key": "atm_ce", "strike": SPOT, "latest_oi": 1100}],
        "puts": [{"key": "atm_pe", "strike": SPOT, "latest_oi": 900}],
    }
    monkeypatch.setattr(om, "build_snapshot", lambda *a, **k: fake_snap)
    monkeypatch.setattr(
        om,
        "build_baselines",
        lambda *a, **k: {
            "atm_ce": {"oi": 1000, "source": "open", "open_oi": 1000, "prev_close_oi": 900},
            "atm_pe": {"oi": 850, "source": "open", "open_oi": 850, "prev_close_oi": 800},
        },
    )
    poc_payload = {
        "poc": 20050.0,
        "fut_symbol": "NIFTY25AUGFUT",
        "fut_token": 123,
        "bin_step": 50,
        "total_volume": 5000,
        "asof": "2026-08-06T10:00:00+05:30",
        "path": [{"t": "2026-08-06T09:15:00+05:30", "close": 20000.0}],
    }
    monkeypatch.setattr("options.session_poc.compute_session_poc", lambda u: poc_payload)
    monkeypatch.setattr("options.cas_indicative.cas_for_snapshot", lambda u: None)

    out = om.build_movers_snapshot("NIFTY")
    assert out["session_poc"] == poc_payload
    assert out["spot"] == SPOT
    assert out["atm_strike"] == SPOT
    assert "session" in out["change_boards"]
