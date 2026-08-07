"""Unit tests for complementary pricing engine (isolated from 3ST)."""

from __future__ import annotations

from pricing.bs_engine import price_black_scholes, solve_iv_and_greeks
from pricing.desk import price_single, pricing_config
from pricing.heston_cos import HestonParams, heston_cos_price


def test_pricing_config_shape():
    cfg = pricing_config()
    assert "underlyings" in cfg
    assert "heston_defaults" in cfg
    assert cfg["risk_free_rate"] > 0


def test_bs_roundtrip_iv():
    spot, strike, t, r, iv = 24000.0, 24000.0, 7 / 365.0, 0.065, 0.14
    px = price_black_scholes(
        spot=spot, strike=strike, tte_years=t, iv=iv, option_type="CE", risk_free_rate=r
    )
    assert px is not None and px > 0
    solved = solve_iv_and_greeks(
        market_price=px,
        spot=spot,
        strike=strike,
        tte_years=t,
        option_type="CE",
        risk_free_rate=r,
    )
    assert solved["iv"] is not None
    assert abs(solved["iv"] - iv * 100) < 0.15
    assert solved["delta"] is not None
    assert 0.4 < float(solved["delta"]) < 0.6


def test_bs_fair_with_model_iv_edge():
    spot, strike, t = 24000.0, 24100.0, 14 / 365.0
    mkt = 120.0
    out = solve_iv_and_greeks(
        market_price=mkt,
        spot=spot,
        strike=strike,
        tte_years=t,
        option_type="CE",
        model_iv=0.12,
    )
    assert out["bs_fair_value"] is not None
    assert out["edge"] is not None
    assert out["rich_cheap"] in ("rich", "cheap")


def test_heston_cos_positive_atm():
    params = HestonParams(v0=0.04, kappa=2.0, theta=0.04, sigma=0.01, rho=0.0, r=0.065)
    out = heston_cos_price(
        spot=24000.0,
        strike=24000.0,
        tte_years=30 / 365.0,
        option_type="CE",
        params=params,
        n_terms=128,
    )
    assert out["price"] is not None
    assert out["price"] > 0
    # Near BS when vol-of-vol ≈ 0
    bs = price_black_scholes(
        spot=24000.0,
        strike=24000.0,
        tte_years=30 / 365.0,
        iv=0.2,
        option_type="CE",
        risk_free_rate=0.065,
    )
    assert bs is not None
    assert abs(out["price"] - bs) / bs < 0.08


def test_price_single_calculator():
    out = price_single(
        spot=24000.0,
        strike=24000.0,
        tte_years=7 / 365.0,
        option_type="CE",
        iv=14.0,
        include_heston=True,
    )
    assert out["bs"]["bs_fair_value"] is not None
    assert out["heston"] is not None
    assert out["heston"]["price"] is not None


def _row(strike: float, ce_ltp: float, ce_edge: float, pe_ltp: float, pe_edge: float,
         ce_fair: float | None = None, pe_fair: float | None = None) -> dict:
    return {
        "strike": strike,
        "ce": {
            "ltp": ce_ltp,
            "edge": ce_edge,
            "bs_fair": ce_fair if ce_fair is not None else ce_ltp - ce_edge,
        },
        "pe": {
            "ltp": pe_ltp,
            "edge": pe_edge,
            "bs_fair": pe_fair if pe_fair is not None else pe_ltp - pe_edge,
        },
    }


def test_recommendations_bull_put_and_call_debit():
    from pricing.recommendations import build_recommendations

    # ATM 24200: rich PE, cheap CE — classic skew tape
    rows = [
        _row(24100, 200, -18, 120, 12, ce_fair=218, pe_fair=108),
        _row(24200, 148, -20, 172, 21, ce_fair=168, pe_fair=151),
        _row(24300, 105, -15, 220, 18, ce_fair=120, pe_fair=202),
    ]
    ideas = build_recommendations(
        rows, underlying="NIFTY", spot=24190.0, atm_strike=24200.0
    )
    assert ideas
    structures = {i["structure"] for i in ideas}
    assert "bull_put_credit" in structures
    put = next(i for i in ideas if i["structure"] == "bull_put_credit")
    assert put["legs"][0]["side"] == "sell"
    assert put["legs"][0]["option_type"] == "PE"
    assert put["net_premium"] > 0
    assert put["max_loss"] == round(put["width"] - put["net_premium"], 4)
    assert "reasoning" in put and put["disclaimer"]
    assert "call_debit" in structures
    call = next(i for i in ideas if i["structure"] == "call_debit")
    assert call["legs"][0]["side"] == "buy"
    assert call["action"] == "debit"


def test_recommendations_skip_illiquid():
    from pricing.recommendations import build_recommendations

    rows = [
        _row(24100, 1.0, -5, 1.0, 5),
        _row(24200, 2.0, -8, 2.0, 8),
        _row(24300, 1.5, -4, 1.5, 4),
    ]
    ideas = build_recommendations(
        rows, underlying="NIFTY", spot=24200.0, atm_strike=24200.0
    )
    assert ideas == []
