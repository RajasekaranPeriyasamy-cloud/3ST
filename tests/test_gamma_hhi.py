"""Unit tests for Gamma HHI concentration / conviction helpers."""

from __future__ import annotations

from options.gamma_density import (
    build_gamma_market_read,
    compute_gamma_concentration,
    compute_gamma_conviction,
)


def _row(strike: float, net_gex: float, density: float = 0.0) -> dict:
    return {
        "strike": strike,
        "net_gex": net_gex,
        "total_density": density,
        "ce_density": 0.0,
        "pe_density": 0.0,
    }


def test_same_total_gex_different_hhi() -> None:
    # Same Σ|net_gex| = 1000, different shape
    concentrated = [
        _row(24500, 900),
        _row(24550, 50),
        _row(24600, 50),
    ]
    # Equal shares → HHI = 1/N; need N≥9 for band "diffuse" (<0.12)
    diffuse = [_row(24400 + i * 50, 100) for i in range(10)]
    c = compute_gamma_concentration(
        concentrated,
        spot=24500,
        atm_strike=24500,
        call_wall=24600,
        put_wall=24400,
        strike_step=50,
    )
    d = compute_gamma_concentration(
        diffuse,
        spot=24500,
        atm_strike=24500,
        call_wall=24600,
        put_wall=24400,
        strike_step=50,
    )
    assert c["hhi"] is not None and d["hhi"] is not None
    assert c["hhi"] > d["hhi"]
    assert c["band"] == "concentrated"
    assert d["band"] == "diffuse"
    assert c["dominant_strike"] == 24500
    assert abs(c["top1_share"] - 0.9) < 1e-9
    assert abs(d["top1_share"] - 0.1) < 1e-9
    assert abs(sum(abs(r["net_gex"]) for r in concentrated) - 1000) < 1e-9
    assert abs(sum(abs(r["net_gex"]) for r in diffuse) - 1000) < 1e-9
    assert d["hhi"] is not None and d["hhi"] < 0.12


def test_dominant_and_pin_from_top_share() -> None:
    rows = [_row(24000, 100), _row(24100, 800), _row(24200, 100)]
    out = compute_gamma_concentration(
        rows,
        spot=24100,
        atm_strike=24100,
        call_wall=24200,
        put_wall=24000,
        strike_step=100,
        pin_threshold=0.18,
    )
    assert out["dominant_strike"] == 24100
    assert out["pin_strike"] == 24100
    assert out["pin_share"] == out["top1_share"]
    assert out["top3_share"] == 1.0
    assert out["effective_strikes"] is not None
    assert out["effective_strikes"] > 1


def test_pin_falls_back_to_wall_midpoint_when_diffuse() -> None:
    rows = [_row(k, 100) for k in (24000, 24100, 24200, 24300, 24400)]
    out = compute_gamma_concentration(
        rows,
        spot=24200,
        atm_strike=24200,
        call_wall=24400,
        put_wall=24000,
        strike_step=100,
        pin_threshold=0.5,  # force fallback (top1=0.2)
    )
    assert out["pin_strike"] == 24200  # midpoint of 24000/24400


def test_empty_and_zero_mass_null_safe() -> None:
    empty = compute_gamma_concentration(
        [],
        spot=100,
        atm_strike=100,
        call_wall=None,
        put_wall=None,
        strike_step=50,
    )
    assert empty["hhi"] is None
    assert empty["band"] is None
    assert empty["dominant_strike"] is None

    zeros = compute_gamma_concentration(
        [_row(100, 0, 0), _row(150, 0, 0)],
        spot=100,
        atm_strike=100,
        call_wall=None,
        put_wall=None,
        strike_step=50,
    )
    assert zeros["hhi"] is None


def test_density_fallback_when_gex_zero() -> None:
    rows = [_row(100, 0, density=900), _row(150, 0, density=100)]
    out = compute_gamma_concentration(
        rows,
        spot=100,
        atm_strike=100,
        call_wall=None,
        put_wall=None,
        strike_step=50,
    )
    assert out["hhi"] is not None
    assert out["dominant_strike"] == 100
    assert abs(out["top1_share"] - 0.9) < 1e-9


def test_pin_stability_from_history() -> None:
    rows = [_row(24500, 900), _row(24550, 100)]
    hist = [{"pin_strike": 24500} for _ in range(8)] + [{"pin_strike": 24600} for _ in range(2)]
    out = compute_gamma_concentration(
        rows,
        spot=24500,
        atm_strike=24500,
        call_wall=None,
        put_wall=None,
        strike_step=50,
        history=hist,
    )
    assert out["pin_strike"] == 24500
    assert out["pin_stable"] is True
    assert out["pin_stability_pct"] == 80.0


def test_conviction_and_market_read() -> None:
    conc = compute_gamma_concentration(
        [_row(24500, 900), _row(24550, 100)],
        spot=24500,
        atm_strike=24500,
        call_wall=24600,
        put_wall=24400,
        strike_step=50,
        history=[{"conviction": 40}],
    )
    conv = compute_gamma_conviction(
        total_gex=5e7,
        gamma_regime="positive",
        concentration=conc,
        distance_to_flip=80,
        spot=24500,
        expected_move={"sigma1_pts": 100},
        history=[{"conviction": 40}],
    )
    assert conv["score"] is not None
    assert 0 <= conv["score"] <= 100
    assert conv["delta"] is not None
    assert conv["direction"] in ("rising", "falling", "flat")

    read = build_gamma_market_read(
        gamma_regime="positive",
        concentration=conc,
        conviction=conv,
        flip_level=24580,
        distance_to_flip=80,
        call_wall=24600,
        put_wall=24400,
    )
    assert "Positive" in read["regime_line"]
    assert "HHI" in read["shape_line"]
    assert "flip" in read["change_line"].lower()
    assert "Dominant" in read["levels_line"]
