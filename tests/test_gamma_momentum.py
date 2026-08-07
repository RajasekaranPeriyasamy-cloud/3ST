"""Tests for Gamma Squeeze / Momentum scoring (v1)."""

from __future__ import annotations

from options.gamma_momentum import (
    WEIGHT_GEX,
    WEIGHT_IV,
    WEIGHT_OI_FLOW,
    WEIGHT_SQUEEZE,
    WEIGHT_STRUCTURE,
    compute_gamma_momentum,
)


def test_weights_sum_to_100() -> None:
    total = WEIGHT_GEX + WEIGHT_SQUEEZE + WEIGHT_OI_FLOW + WEIGHT_IV + WEIGHT_STRUCTURE
    assert abs(total - 100.0) < 1e-9


def test_near_call_wall_neg_gex_elevated_bullish() -> None:
    """Spot near call wall + negative GEX → elevated bullish / squeeze-up score."""
    out = compute_gamma_momentum(
        {
            "spot": 24580,
            "total_gex": -5e7,
            "call_wall": 24600,
            "put_wall": 24400,
            "strike_step": 50,
            "atm_iv": 12.0,
            "distance_to_flip": 200,
            "strikes": [
                {"strike": 24550, "ce_doi": 5000, "pe_doi": -1000},
                {"strike": 24600, "ce_doi": 8000, "pe_doi": -500},
            ],
            "concentration": {
                "hhi": 0.35,
                "band": "concentrated",
                "pin_strike": 24600,
                "cliff_strike": 24600,
            },
            "history": [
                {"total_gex": -2e7},
                {"total_gex": -4e7},
            ],
        }
    )
    assert out["score"] > 60
    assert out["label"] == "bullish"
    assert out["components"]["squeeze"] > 60
    assert out["components"]["gex"] > 50
    assert any("call wall" in d or "neg GEX" in d or "pin" in d for d in out["drivers"])


def test_diffuse_far_walls_neutral() -> None:
    """Diffuse HHI + spot far from walls → near-neutral score."""
    out = compute_gamma_momentum(
        {
            "spot": 24500,
            "total_gex": 1e6,  # small pos GEX
            "call_wall": 25200,  # far
            "put_wall": 23800,  # far
            "strike_step": 50,
            "atm_iv": 11.0,
            "distance_to_flip": 400,
            "strikes": [{"strike": 24500}],  # no doi
            "concentration": {
                "hhi": 0.08,
                "band": "diffuse",
                "pin_strike": None,
                "cliff_strike": None,
            },
            "history": [],
        }
    )
    assert 40 <= out["score"] <= 60
    assert out["label"] == "neutral"
    assert abs(out["components"]["oi_flow"] - 50.0) < 1e-6
    assert abs(out["components"]["iv"] - 50.0) < 1e-6
    assert abs(out["components"]["squeeze"] - 50.0) < 1e-6


def test_null_safe_minimal_inputs() -> None:
    """Missing walls/history/DOI does not raise; returns bounded score."""
    out = compute_gamma_momentum({})
    assert 0.0 <= out["score"] <= 100.0
    assert out["label"] in ("bullish", "neutral", "bearish")
    comps = out["components"]
    for key in ("gex", "squeeze", "oi_flow", "iv", "structure"):
        assert key in comps
        assert 0.0 <= comps[key] <= 100.0
    assert isinstance(out["drivers"], list)


def test_near_put_wall_pos_gex_bearish_tilt() -> None:
    out = compute_gamma_momentum(
        {
            "spot": 24420,
            "total_gex": 8e7,
            "call_wall": 24800,
            "put_wall": 24400,
            "strike_step": 50,
            "strikes": [
                {"strike": 24400, "ce_doi": -2000, "pe_doi": 9000},
            ],
            "concentration": {
                "hhi": 0.30,
                "band": "concentrated",
                "pin_strike": 24400,
            },
            "history": [{"total_gex": 6e7}],
        }
    )
    assert out["score"] < 50
    assert out["components"]["squeeze"] < 40
    assert out["label"] in ("bearish", "neutral")


def test_kwargs_and_dict_merge() -> None:
    out = compute_gamma_momentum({"spot": 100}, total_gex=-1e6, strike_step=50)
    assert "score" in out
    assert out["components"]["gex"] > 50
