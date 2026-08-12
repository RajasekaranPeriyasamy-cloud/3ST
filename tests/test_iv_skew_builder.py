"""IV Skew builder — synthetic chain and quotes, no Kite.

The fake chain prices every leg with Black-76 off a known forward and a known
vol curve, so the builder's output can be checked against what was priced in.
Expiries derive from ``date.today()`` so these cannot rot (see CLAUDE.md).
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from vollib.black import black

from analysis.iv_skew import builder
from analysis.iv_skew.builder import (
    build_iv_skew,
    half_width_for,
    price_from_quote,
    usable_expiries,
)

R = 0.065
STEP = 50.0
FORWARD = 24_500.0
SPOT = 24_400.0  # a real basis, so pricing off spot would be visibly wrong

NEAR = (date.today() + timedelta(days=7)).isoformat()
FAR = (date.today() + timedelta(days=35)).isoformat()
PAST = (date.today() - timedelta(days=1)).isoformat()


def vol_curve(strike: float, forward: float) -> float:
    """Put-skewed: 12% ATM, richer as strikes fall."""
    return 0.12 + 0.5 * math.log(forward / strike)


def _symbol(expiry: str, strike: float, otype: str) -> str:
    return f"FAKE{expiry.replace('-', '')}{int(strike)}{otype}"


def _parse(symbol: str) -> tuple[float, str]:
    otype = symbol[-2:]
    return float(symbol[12:-2]), otype


@pytest.fixture
def fake_chain(monkeypatch):
    """Wire the builder to a synthetic NIFTY-shaped chain."""
    strikes = [FORWARD + i * STEP for i in range(-60, 61)]

    def fake_expiries(underlying):
        return [PAST, NEAR, FAR]

    def fake_spot(underlying):
        return SPOT, "index"

    def fake_get_chain(underlying, expiry):
        chain_calls.append(expiry)
        return {
            "exchange": "NFO",
            "strikes": [
                {
                    "strike": k,
                    "ce": {"tradingsymbol": _symbol(expiry, k, "CE"), "exchange": "NFO"},
                    "pe": {"tradingsymbol": _symbol(expiry, k, "PE"), "exchange": "NFO"},
                }
                for k in strikes
            ],
        }

    builder.clear_chain_cache()
    chain_calls: list[str] = []
    calls: list[list[str]] = []

    def fake_quotes(keys):
        calls.append(list(keys))
        out = {}
        for key in keys:
            _, symbol = key.split(":", 1)
            strike, otype = _parse(symbol)
            expiry = f"{symbol[4:8]}-{symbol[8:10]}-{symbol[10:12]}"
            tte = builder.time_to_expiry_years(expiry)
            if tte is None:
                continue
            sigma = vol_curve(strike, FORWARD)
            price = black("c" if otype == "CE" else "p", FORWARD, strike, tte, R, sigma)
            if price <= 0:
                continue
            out[key] = {
                "last_price": price,
                "depth": {
                    "buy": [{"price": price * 0.995}],
                    "sell": [{"price": price * 1.005}],
                },
            }
        return out

    monkeypatch.setattr(builder, "list_expiries", fake_expiries)
    monkeypatch.setattr(builder, "get_index_spot_detail", fake_spot)
    monkeypatch.setattr(builder, "get_chain", fake_get_chain)
    monkeypatch.setattr(builder, "_quote_batches", fake_quotes)
    return {"quotes": calls, "chains": chain_calls}


# --- window sizing ----------------------------------------------------------


def test_half_width_grows_with_tenor():
    near = half_width_for(FORWARD, 7 / 365, 0.12, STEP, wing_delta=0.10, min_half_width=6, max_half_width=40)
    far = half_width_for(FORWARD, 90 / 365, 0.12, STEP, wing_delta=0.10, min_half_width=6, max_half_width=40)
    assert far > near


def test_half_width_grows_with_vol():
    calm = half_width_for(FORWARD, 30 / 365, 0.12, STEP, wing_delta=0.10, min_half_width=6, max_half_width=40)
    wild = half_width_for(FORWARD, 30 / 365, 0.45, STEP, wing_delta=0.10, min_half_width=6, max_half_width=40)
    assert wild > calm


def test_half_width_respects_floor_and_cap():
    tiny = half_width_for(FORWARD, 0.5 / 365, 0.10, STEP, wing_delta=0.10, min_half_width=6, max_half_width=40)
    huge = half_width_for(FORWARD, 2.0, 0.90, STEP, wing_delta=0.10, min_half_width=6, max_half_width=40)
    assert tiny == 6
    assert huge == 40


def test_half_width_survives_degenerate_inputs():
    assert half_width_for(0, 0, 0, STEP, wing_delta=0.10, min_half_width=6, max_half_width=40) == 6


# --- expiry filtering -------------------------------------------------------


def test_usable_expiries_drops_settled_contracts(monkeypatch):
    """A settled expiry stays in the instrument dump and must not be offered."""
    monkeypatch.setattr(builder, "list_expiries", lambda u: [PAST, NEAR, FAR])
    assert usable_expiries("NIFTY", max_expiries=99) == [NEAR, FAR]


# --- quote quality ----------------------------------------------------------


def test_price_prefers_the_mid():
    q = {"last_price": 90.0, "depth": {"buy": [{"price": 99.0}], "sell": [{"price": 101.0}]}}
    assert price_from_quote(q, 0.5) == (100.0, "mid")


def test_price_falls_back_to_ltp_without_two_sided_depth():
    q = {"last_price": 90.0, "depth": {"buy": [{"price": 0}], "sell": [{"price": 0}]}}
    assert price_from_quote(q, 0.5) == (90.0, "ltp")


def test_wide_spread_leg_is_dropped_not_priced():
    """MCX wing shape: 0.05 bid / 0.40 offer solves to an artifact of the spread."""
    q = {"last_price": 0.2, "depth": {"buy": [{"price": 0.05}], "sell": [{"price": 0.40}]}}
    assert price_from_quote(q, 0.5) == (None, "wide_spread")


@pytest.mark.parametrize(
    "quote,reason",
    [
        (None, "no_quote"),
        ({"last_price": 0, "depth": {}}, "no_price"),
    ],
)
def test_unusable_quotes_report_why(quote, reason):
    assert price_from_quote(quote, 0.5) == (None, reason)


# --- end to end -------------------------------------------------------------


def test_snapshot_recovers_the_forward_not_the_spot(fake_chain):
    snap = build_iv_skew("NIFTY")
    near = snap["expiries"][0]
    assert snap["reference"] == pytest.approx(SPOT)
    assert near["forward"] == pytest.approx(FORWARD, abs=1.0)
    assert near["forward_basis"] == pytest.approx(FORWARD - SPOT, abs=1.0)
    assert near["atm_parity_gap"] == pytest.approx(0.0, abs=0.01)


def test_snapshot_reports_the_priced_in_skew(fake_chain):
    snap = build_iv_skew("NIFTY")
    for exp in snap["expiries"]:
        assert exp["ok"]
        assert exp["risk_reversal"] < 0  # put-skewed curve went in
        assert exp["put_iv"] > exp["call_iv"]
        assert exp["quality"] == "interpolated"
        assert exp["atm_iv"] == pytest.approx(12.0, abs=0.05)


def test_settled_expiry_is_not_in_the_snapshot(fake_chain):
    snap = build_iv_skew("NIFTY")
    assert [e["expiry"] for e in snap["expiries"]] == [NEAR, FAR]


def test_window_is_sized_per_expiry_not_fixed(fake_chain):
    snap = build_iv_skew("NIFTY")
    near, far = snap["expiries"]
    assert far["half_width"] > near["half_width"]
    # and both actually reached past the 25d target they were sized for
    assert near["call_delta_range"][0] < 0.25
    assert far["call_delta_range"][0] < 0.25


def test_snapshot_costs_exactly_two_quote_calls(fake_chain):
    """Batched across expiries — adding an expiry must not add a round trip."""
    build_iv_skew("NIFTY")
    assert len(fake_chain["quotes"]) == 2


def test_chain_is_resolved_once_per_expiry_then_cached(fake_chain):
    """get_chain profiled at 3.5s per expiry — the refresh loop must not repay it."""
    build_iv_skew("NIFTY")
    assert sorted(fake_chain["chains"]) == [NEAR, FAR]

    build_iv_skew("NIFTY")
    assert sorted(fake_chain["chains"]) == [NEAR, FAR]  # second snapshot added none

    builder.clear_chain_cache()
    build_iv_skew("NIFTY")
    assert len(fake_chain["chains"]) == 4


def test_explicit_expiry_selection(fake_chain):
    snap = build_iv_skew("NIFTY", expiries=[FAR])
    assert [e["expiry"] for e in snap["expiries"]] == [FAR]


def test_unknown_expiry_is_rejected(fake_chain):
    with pytest.raises(RuntimeError, match="listed"):
        build_iv_skew("NIFTY", expiries=["2020-01-01"])


def test_unknown_underlying_is_rejected():
    with pytest.raises(ValueError, match="Unknown underlying"):
        build_iv_skew("NOTATHING")


def test_missing_reference_price_is_reported(monkeypatch):
    """The reason from get_index_spot_detail must reach the caller, not be dropped."""
    monkeypatch.setattr(builder, "get_index_spot_detail", lambda u: (None, "No LTP for NSE:NIFTY 50"))
    with pytest.raises(RuntimeError, match="No LTP for NSE:NIFTY 50"):
        build_iv_skew("NIFTY")


def test_index_reference_is_labelled_spot(fake_chain):
    assert build_iv_skew("NIFTY")["reference_source"] == "index"


def test_mcx_reference_is_labelled_as_the_future(monkeypatch):
    """MCX prices off the front future, and the page says so.

    The second value from get_index_spot_detail is a failure reason, not a
    source; reading it as one labelled every MCX underlying "spot".
    """
    monkeypatch.setattr(builder, "list_expiries", lambda u: [NEAR])
    monkeypatch.setattr(builder, "get_index_spot_detail", lambda u: (266.3, None))
    monkeypatch.setattr(builder, "get_chain", lambda u, e: {"exchange": "MCX", "strikes": []})
    monkeypatch.setattr(builder, "_quote_batches", lambda keys: {})
    assert build_iv_skew("NATURALGAS")["reference_source"] == "future"


def test_extrapolated_wing_is_flagged_with_a_warning(fake_chain, monkeypatch):
    """Cap the window below what 25Δ needs — the desk must say so, not guess."""
    monkeypatch.setitem(builder.IV_SKEW_DEFAULTS, "max_half_width", 2)
    snap = build_iv_skew("NIFTY")
    far = snap["expiries"][-1]
    assert far["quality"] == "extrapolated"
    assert any("extrapolated" in w for w in far["warnings"])


def test_a_good_chain_is_reported_clean(fake_chain):
    snap = build_iv_skew("NIFTY")
    for exp in snap["expiries"]:
        assert exp["confidence"] == "clean"
        assert exp["warnings"] == []


def test_thin_chain_is_flagged_degraded(fake_chain, monkeypatch):
    """Every other *strike* has no usable market, on both legs.

    The desk must not print a confident 25Δ off what survives — this is the
    76-DTE BANKNIFTY shape that produced RR +6.90 labelled "interpolated".
    Alternating whole strikes rather than alternating legs, because dropping
    every put would empty a wing outright, which is a different failure.
    """
    priced = builder._quote_batches

    def thin(keys):
        out = priced(keys)
        for i, quote in enumerate(out.values()):
            if (i // 2) % 2:
                p = quote["last_price"]
                quote["depth"] = {"buy": [{"price": p * 0.2}], "sell": [{"price": p * 3.0}]}
        return out

    monkeypatch.setattr(builder, "_quote_batches", thin)
    snap = build_iv_skew("NIFTY")
    degraded = [e for e in snap["expiries"] if e.get("confidence") == "degraded"]
    assert degraded
    assert any("chain is thin" in w for e in degraded for w in e["warnings"])


def test_sparse_wing_near_the_target_warns(fake_chain, monkeypatch):
    """Interpolating 25Δ across a wide delta bracket is not a measurement."""
    monkeypatch.setitem(builder.IV_SKEW_DEFAULTS, "max_bracket_gap", 0.01)
    snap = build_iv_skew("NIFTY")
    assert any("sparse" in w for e in snap["expiries"] for w in e["warnings"])
    assert all(e["confidence"] == "degraded" for e in snap["expiries"])


def test_dead_quotes_yield_a_clean_error_not_a_crash(fake_chain, monkeypatch):
    monkeypatch.setattr(builder, "_quote_batches", lambda keys: {})
    snap = build_iv_skew("NIFTY")
    assert all(not e["ok"] for e in snap["expiries"])
    assert all("quote" in e["error"] for e in snap["expiries"])


def test_points_carry_both_wings_for_charting(fake_chain):
    snap = build_iv_skew("NIFTY")
    points = snap["expiries"][0]["points"]
    assert {p["option_type"] for p in points} == {"CE", "PE"}
    assert [p["strike"] for p in points] == sorted(p["strike"] for p in points)
