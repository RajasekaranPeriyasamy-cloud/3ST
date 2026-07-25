"""Tests for the gamma density engine (no Kite; synthetic chain + BS-priced quotes)."""

from __future__ import annotations

import pytest
from vollib.black_scholes import black_scholes as bs_price

from options import gamma_density as gd
from options.gamma_density_provider import StaticGammaDensityDataProvider

R = 0.065
SPOT = 20000.0
STEP = 50
TTE = 5.0 / 365.0  # ~5 calendar days
IV = 0.14
EXPIRY = "2026-07-16"


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
        # Include depth so mid-path is exercised
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


@pytest.fixture
def static_provider():
    chain = _fake_chain("NIFTY", EXPIRY)
    return StaticGammaDensityDataProvider(
        chain=chain,
        spot=SPOT,
        quotes=_fake_chain.quotes,  # type: ignore[attr-defined]
        expiries=[EXPIRY, "2026-07-23"],
    )


@pytest.fixture
def patched(monkeypatch, static_provider):
    monkeypatch.setattr(gd, "time_to_expiry_years", lambda e: TTE)
    return static_provider


def _snap(provider, **kwargs):
    return gd.build_gamma_snapshot(
        "NIFTY",
        provider=provider,
        include_multi_expiry=False,
        include_history=False,
        **kwargs,
    )


def test_gamma_config():
    cfg = gd.gamma_config()
    assert "NIFTY" in cfg["underlyings"]
    assert cfg["refresh_seconds"] > 0
    assert cfg["strike_window"] > 0
    assert cfg["provider"] == "kite"
    assert cfg["requires_session"] is True
    assert "dividend_yield" in cfg
    assert "naive" in cfg["sign_modes"]


def test_unknown_underlying_raises():
    with pytest.raises(ValueError):
        gd.build_gamma_snapshot("FOO")


def test_snapshot_shape(patched):
    snap = _snap(patched)
    assert snap["underlying"] == "NIFTY"
    assert snap["spot"] == SPOT
    assert snap["strikes"], "expected resolvable strikes"
    assert snap["atm_strike"] == SPOT
    assert snap["chain_legs_quoted"] > 0
    for key in (
        "total_gex",
        "gamma_regime",
        "call_wall",
        "put_wall",
        "expected_move",
        "gex_profile",
        "hedge_flow",
        "distance_to_flip",
    ):
        assert key in snap
    assert snap["expected_move"]["source"] in ("straddle", "atm_iv")
    assert len(snap["gex_profile"]) > 10
    assert len(snap["hedge_flow"]) >= 2


def test_gex_sign_convention(patched):
    snap = _snap(patched, sign_mode="naive")
    for row in snap["strikes"]:
        assert row["ce_gex"] >= 0, "call GEX must be non-negative (naive)"
        assert row["pe_gex"] <= 0, "put GEX must be non-positive (naive)"
        assert row["ce_density"] >= 0 and row["pe_density"] >= 0


def test_customer_sign_flips(patched):
    naive = _snap(patched, sign_mode="naive")
    cust = _snap(patched, sign_mode="customer")
    assert naive["total_gex"] == pytest.approx(-cust["total_gex"], rel=1e-6)


def test_mid_price_stats(patched):
    snap = _snap(patched)
    assert snap["price_source_stats"]["mid"] > 0


def test_expected_move_bands(patched):
    snap = _snap(patched)
    bands = snap["expected_move"]
    assert bands is not None
    assert bands["sigma1_up"] > SPOT > bands["sigma1_dn"]
    assert bands["sigma2_up"] > bands["sigma1_up"]


def test_strike_window_limits(patched):
    snap = _snap(patched, strike_window=2)
    assert len(snap["strikes"]) <= 5


def test_total_gex_sign_flips_across_spot():
    legs = [
        (19500.0, 100000, 65, "PE", 0.14),
        (20000.0, 100000, 65, "CE", 0.14),
        (20000.0, 100000, 65, "PE", 0.14),
        (20500.0, 100000, 65, "CE", 0.14),
    ]
    low = gd.total_gex_at_spot(legs, 19000.0, TTE)
    high = gd.total_gex_at_spot(legs, 21000.0, TTE)
    assert low < 0 < high or high < 0 < low


def test_gamma_flip_level_scan():
    legs = [
        (19800.0, 150000, 65, "PE", 0.14),
        (20000.0, 120000, 65, "CE", 0.14),
        (20000.0, 120000, 65, "PE", 0.14),
        (20200.0, 150000, 65, "CE", 0.14),
    ]
    flip = gd.gamma_flip_level(legs, SPOT, TTE, 19000.0, 21000.0)
    if flip is not None:
        assert 19000.0 <= flip <= 21000.0


def test_snapshot_flip_positive(patched):
    snap = _snap(patched)
    if snap["flip_level"] is not None:
        assert snap["flip_level"] > 0
    assert "flip_sticky_delta" in snap


def test_magnet_walls_present(patched):
    snap = _snap(patched)
    assert snap["call_wall"] is not None
    assert snap["put_wall"] is not None
    assert snap["call_wall_magnet"] is not None


def test_vanna_strip(patched):
    snap = gd.build_gamma_snapshot(
        "NIFTY",
        provider=patched,
        include_multi_expiry=False,
        include_history=False,
        include_vanna_strip=True,
    )
    assert snap["vanna_strip"] is not None
    assert "joint_read" in snap["vanna_strip"]


def test_quote_price_prefers_mid():
    q = {
        "last_price": 100.0,
        "depth": {"buy": [{"price": 99.0}], "sell": [{"price": 101.0}]},
    }
    px, src = gd._quote_price(q, 0.12)
    assert src == "mid"
    assert px == pytest.approx(100.0)
