"""Unit tests for NSE higher-order Greeks engine (no Kite)."""

from __future__ import annotations

import math

from vollib.black_scholes.greeks.analytical import (
    delta as bs_delta,
    gamma as bs_gamma,
    vega as bs_vega,
)

from options.greeks_engine import bs_vanna, compute_greeks, d1_d2
from options.gamma_density_provider import StaticGammaDensityDataProvider
from options import greeks_desk as gd
from options import trade_suggestions as ts
from vollib.black_scholes import black_scholes as bs_price

R = 0.065
Q = 0.0
SPOT = 20000.0
STRIKE = 20000.0
TTE = 7.0 / 365.0
IV = 0.15
STEP = 50
EXPIRY = "2026-07-28"


def test_first_order_matches_vollib():
    g = compute_greeks(
        spot=SPOT,
        strike=STRIKE,
        tte_years=TTE,
        iv=IV,
        option_type="CE",
        risk_free_rate=R,
        dividend_yield=Q,
    )
    d = float(bs_delta("c", SPOT, STRIKE, TTE, R, IV))
    gm = float(bs_gamma("c", SPOT, STRIKE, TTE, R, IV))
    # py_vollib vega is already per 1% IV
    v = float(bs_vega("c", SPOT, STRIKE, TTE, R, IV))
    assert g["delta"] is not None and abs(g["delta"] - d) < 1e-4
    assert g["gamma"] is not None and abs(g["gamma"] - gm) < 1e-6
    assert g["vega"] is not None and abs(g["vega"] - v) < 1e-3


def test_second_order_finite_and_vanna_fd():
    g = compute_greeks(
        spot=SPOT,
        strike=STRIKE,
        tte_years=TTE,
        iv=IV,
        option_type="CE",
        risk_free_rate=R,
        dividend_yield=Q,
    )
    for key in ("vanna", "charm", "vomma", "zomma", "speed", "color", "rho"):
        assert g[key] is not None
        assert math.isfinite(float(g[key]))

    eps = 1e-4
    d_up = float(bs_delta("c", SPOT, STRIKE, TTE, R, IV + eps))
    d_dn = float(bs_delta("c", SPOT, STRIKE, TTE, R, IV - eps))
    fd_vanna = (d_up - d_dn) / (2 * eps)
    vn = bs_vanna(
        spot=SPOT,
        strike=STRIKE,
        tte_years=TTE,
        iv=IV,
        risk_free_rate=R,
        dividend_yield=Q,
    )
    assert vn is not None
    assert abs(vn - fd_vanna) / max(abs(fd_vanna), 1e-8) < 0.05


def test_put_call_gamma_speed_identical():
    ce = compute_greeks(
        spot=SPOT, strike=STRIKE, tte_years=TTE, iv=IV, option_type="CE",
        risk_free_rate=R, dividend_yield=Q,
    )
    pe = compute_greeks(
        spot=SPOT, strike=STRIKE, tte_years=TTE, iv=IV, option_type="PE",
        risk_free_rate=R, dividend_yield=Q,
    )
    assert abs(float(ce["gamma"]) - float(pe["gamma"])) < 1e-10
    assert abs(float(ce["speed"]) - float(pe["speed"])) < 1e-10
    assert abs(float(ce["vanna"]) - float(pe["vanna"])) < 1e-10


def test_d1_d2_invalid():
    assert d1_d2(0, STRIKE, TTE, IV, R, Q) is None
    assert d1_d2(SPOT, STRIKE, 0, IV, R, Q) is None


def _price(flag: str, strike: float) -> float:
    return float(bs_price(flag, SPOT, strike, TTE, R, IV))


def _fake_chain(underlying: str, expiry: str) -> dict:
    strikes = []
    quotes = {}
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
        # Heavier OTM calls / ITM puts so dealer GEX changes sign across spot
        ce_oi = 200000 if k >= SPOT else 40000
        pe_oi = 200000 if k <= SPOT else 40000
        quotes[f"NFO:{ce_sym}"] = {"oi": ce_oi, "last_price": _price("c", k)}
        quotes[f"NFO:{pe_sym}"] = {"oi": pe_oi, "last_price": _price("p", k)}
    _fake_chain.quotes = quotes  # type: ignore[attr-defined]
    return {
        "underlying": underlying,
        "expiry": expiry,
        "exchange": "NFO",
        "strike_step": STEP,
        "lot_size": 65,
        "strikes": strikes,
    }


def test_greeks_desk_and_suggestions(monkeypatch):
    chain = _fake_chain("NIFTY", EXPIRY)
    prov = StaticGammaDensityDataProvider(
        chain=chain,
        spot=SPOT,
        quotes=_fake_chain.quotes,  # type: ignore[attr-defined]
        expiries=[EXPIRY],
    )
    monkeypatch.setattr(gd, "time_to_expiry_years", lambda e: TTE)

    snap = gd.build_greeks_snapshot("NIFTY", EXPIRY, strike_window=5, provider=prov)
    assert snap["spot"] == SPOT
    assert snap["strikes"]
    assert "ce_charm" in snap["strikes"][0]
    assert "signals" in snap
    assert snap["total_gex"] is not None
    assert snap.get("signals", {}).get("gamma")
    assert any("ce_vanna" in r for r in snap["strikes"])

    ideas = ts.build_trade_suggestions("NIFTY", EXPIRY, strike_window=5, provider=prov)
    assert isinstance(ideas["suggestions"], list)
    assert "portfolio_greeks" in ideas
    assert ideas["levels"].get("pin_level") is not None
    assert ideas.get("signals")
