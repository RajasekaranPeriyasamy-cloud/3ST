"""Unit tests for Gamma HHI concentration / conviction helpers."""

from __future__ import annotations

from options.gamma_density import (
    build_gamma_market_read,
    compute_gamma_concentration,
    compute_gamma_conviction,
    hhi_band_cuts,
)

GROSS_COMPRESSED_CUT, GROSS_BALANCED_CUT = hhi_band_cuts("gross")


def _row(strike: float, net_gex: float, density: float = 0.0) -> dict:
    return {
        "strike": strike,
        "net_gex": net_gex,
        "total_density": density,
        "ce_density": 0.0,
        "pe_density": 0.0,
    }


def _sided_row(strike: float, ce_gex: float, pe_gex: float) -> dict:
    return {
        "strike": strike,
        "ce_gex": ce_gex,
        "pe_gex": pe_gex,
        "net_gex": ce_gex + pe_gex,
        "total_density": 0.0,
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
    # Equal shares → HHI = 1/N; need N ≥ 13 to sit under the gross balanced cut
    diffuse = [_row(24000 + i * 50, 100) for i in range(15)]
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
    assert c["band_label"] == "compressed"
    assert d["band"] == "diffuse"
    assert d["band_label"] == "dispersed"
    assert c["dominant_strike"] == 24500
    assert abs(c["top1_share"] - 0.9) < 1e-9
    assert abs(d["top1_share"] - 1 / 15) < 1e-4  # share is rounded to 4dp
    assert abs(sum(abs(r["net_gex"]) for r in concentrated) - 1000) < 1e-9
    assert abs(sum(abs(r["net_gex"]) for r in diffuse) - 1500) < 1e-9
    assert d["hhi"] is not None and d["hhi"] < GROSS_BALANCED_CUT


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


def test_gross_basis_does_not_cancel_balanced_strikes() -> None:
    """A strike with equal CE/PE gamma must not vanish from the index.

    Under the naive sign mode CE is dealer-long and PE dealer-short, so the net
    basis collapses 24500 to zero mass and hands 24600 the entire share.
    """
    rows = [
        _sided_row(24500, ce_gex=500.0, pe_gex=-500.0),  # balanced book
        _sided_row(24600, ce_gex=500.0, pe_gex=0.0),  # one-sided
    ]
    gross = compute_gamma_concentration(
        rows,
        spot=24550,
        atm_strike=24550,
        call_wall=None,
        put_wall=None,
        strike_step=50,
    )
    net = compute_gamma_concentration(
        rows,
        spot=24550,
        atm_strike=24550,
        call_wall=None,
        put_wall=None,
        strike_step=50,
        mass_basis="net",
    )
    # gross: masses 1000 / 500 → shares 2/3, 1/3 → HHI = 5/9
    assert abs(gross["hhi"] - 5 / 9) < 1e-4
    assert gross["mass_basis"] == "gross"
    # net: 24500 cancels entirely → single strike holds 100%
    assert abs(net["hhi"] - 1.0) < 1e-9
    assert net["mass_basis"] == "net"
    assert net["dominant_strike"] == 24600
    # Both bases are reported regardless of which one is selected.
    assert abs(gross["hhi_net"] - 1.0) < 1e-9
    assert abs(net["hhi_gross"] - 5 / 9) < 1e-4


def test_density_fallback_is_aggregate_not_per_row() -> None:
    """One cancelling strike must not contribute a mass in density units.

    density and GEX differ by S²·0.01 (~6e6 at index scale), so a per-row
    fallback silently mixes units across the share denominator.
    """
    rows = [
        _sided_row(24500, ce_gex=0.0, pe_gex=0.0) | {"total_density": 1e9},
        _sided_row(24600, ce_gex=600.0, pe_gex=-200.0),
    ]
    out = compute_gamma_concentration(
        rows,
        spot=24550,
        atm_strike=24550,
        call_wall=None,
        put_wall=None,
        strike_step=50,
    )
    # 24600 holds all the GEX mass; the density-only row must not out-weigh it.
    assert out["dominant_strike"] == 24600
    assert abs(out["top1_share"] - 1.0) < 1e-9


def test_gamma_peaks_read_signed_net_gex() -> None:
    rows = [
        _sided_row(24500, ce_gex=100.0, pe_gex=-900.0),  # net −800 → dealer short
        _sided_row(24600, ce_gex=700.0, pe_gex=-100.0),  # net +600 → dealer long
    ]
    out = compute_gamma_concentration(
        rows,
        spot=24550,
        atm_strike=24550,
        call_wall=None,
        put_wall=None,
        strike_step=50,
    )
    assert out["pos_gamma_peak_strike"] == 24600
    assert out["neg_gamma_peak_strike"] == 24500


def test_daily_session_stats_exclude_today() -> None:
    """D/D and the 5-session mean must compare today against *prior* sessions."""
    from datetime import date, timedelta

    today = date.today()
    series = [
        {"date": (today - timedelta(days=n)).isoformat(), "hhi": hhi}
        for n, hhi in ((4, 0.10), (3, 0.10), (2, 0.10), (1, 0.20))
    ]
    rows = [_sided_row(24500, 900.0, 0.0), _sided_row(24600, 100.0, 0.0)]
    series.append({"date": today.isoformat(), "hhi": 0.82})
    out = compute_gamma_concentration(
        rows,
        spot=24550,
        atm_strike=24550,
        call_wall=None,
        put_wall=None,
        strike_step=50,
        daily_hhi_history=series,
    )
    assert out["hhi_prev_session"] == 0.20
    assert out["hhi_prev_session_date"] == (today - timedelta(days=1)).isoformat()
    # hhi = 0.9² + 0.1² = 0.82; prior mean = (0.1+0.1+0.1+0.2)/4 = 0.125
    assert out["hhi_dod_pct"] == 310.0
    assert out["hhi_mean_5"] == 0.125
    assert out["hhi_vs_mean_pct"] == 556.0
    assert out["hhi_low_30"] == 0.10
    assert out["hhi_high_30"] == 0.82
    assert [r["date"] for r in out["daily_hhi"]] == [r["date"] for r in series]


def test_top5_share_and_full_contributor_list() -> None:
    rows = [_row(24000 + i * 50, 100 * (30 - i)) for i in range(30)]
    out = compute_gamma_concentration(
        rows,
        spot=24000,
        atm_strike=24000,
        call_wall=None,
        put_wall=None,
        strike_step=50,
    )
    contributors = out["top_contributors"]
    # Every strike in the window is returned — the ladder tooltips need the tail.
    assert len(contributors) == 30
    assert abs(out["top5_share"] - sum(c["share"] for c in contributors[:5])) < 1e-3
    assert out["top3_share"] <= out["top5_share"]
    assert abs(contributors[0]["share_sq"] - contributors[0]["share"] ** 2) < 1e-4


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
