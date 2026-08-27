"""Tests for the Expiry Magnet pressure kernel, time boost and pin state."""

from __future__ import annotations

import math

import pytest

from options.expiry_magnet import (
    DOMINANT_MARGIN,
    MAX_TIME_BOOST,
    TIME_BOOST_REFERENCE_DTE,
    build_expiry_magnet,
    classify_pin_state,
    leader_stability_pct,
    pressure_by_strike,
    time_boost,
)

SPOT = 24280.5
SIGMA = 140.0


def _row(strike: float, ce: float, pe: float) -> dict:
    return {"strike": strike, "ce_gex": ce, "pe_gex": pe, "net_gex": ce + pe}


def _book() -> list[dict]:
    """Gross gamma chosen to reproduce the observed live table (in ₹Cr × 1e5)."""
    L = 1e5
    return [
        _row(24300, 16.98 * L / 2, -16.98 * L / 2),
        _row(24350, 8.71 * L / 2, -8.71 * L / 2),
        _row(24250, 7.04 * L / 2, -7.04 * L / 2),
        _row(24400, 5.03 * L / 2, -5.03 * L / 2),
        _row(24450, 2.10 * L / 2, -2.10 * L / 2),
    ]


# ── pressure kernel ────────────────────────────────────────────────────────


def test_pressure_matches_the_gaussian_kernel() -> None:
    """P(K) ∝ Γ(K)·exp(−(K−S)²/2σ²), normalised to the peak."""
    rows = {r["strike"]: r for r in pressure_by_strike(_book(), SPOT, SIGMA)}

    def expected(k: float, gamma_l: float) -> float:
        d = k - SPOT
        return gamma_l * math.exp(-(d * d) / (2 * SIGMA * SIGMA))

    peak = expected(24300, 16.98)
    assert rows[24300]["pressure"] == pytest.approx(1.0, abs=1e-4)
    assert rows[24350]["pressure"] == pytest.approx(expected(24350, 8.71) / peak, abs=1e-3)
    assert rows[24250]["pressure"] == pytest.approx(expected(24250, 7.04) / peak, abs=1e-3)
    assert rows[24400]["pressure"] == pytest.approx(expected(24400, 5.03) / peak, abs=1e-3)
    assert rows[24450]["pressure"] == pytest.approx(expected(24450, 2.10) / peak, abs=1e-3)


def test_pressure_reproduces_the_observed_live_values() -> None:
    """Pins the kernel against the numbers the design was derived from."""
    rows = {r["strike"]: r["pressure"] for r in pressure_by_strike(_book(), SPOT, SIGMA)}
    for strike, want in ((24300, 1.000), (24350, 0.457), (24250, 0.409),
                         (24400, 0.208), (24450, 0.060)):
        assert rows[strike] == pytest.approx(want, abs=0.002), strike


def test_pressure_and_raw_gamma_can_invert() -> None:
    """The reason ranking on pressure is not ranking on gamma.

    A near strike with less gamma must be able to outrank a far one with more.
    """
    book = [
        _row(24300, 4.0e5, -4.0e5),    # 8L gross, right at spot
        _row(24800, 6.0e5, -6.0e5),    # 12L gross, 3.7σ away
    ]
    ranked = pressure_by_strike(book, SPOT, SIGMA)
    assert ranked[0]["strike"] == 24300
    # ...even though the far strike carries the larger raw gamma.
    assert ranked[1]["gamma"] > ranked[0]["gamma"]
    assert ranked[1]["pressure"] < 0.05


def test_gross_magnitude_and_signed_net_are_both_reported() -> None:
    """A magnet on short dealer gamma is a different read from one on long."""
    ranked = pressure_by_strike([_row(24300, 3.0e5, -8.0e5)], SPOT, SIGMA)
    r = ranked[0]
    assert r["gamma"] == pytest.approx(1.1e6)   # |CE| + |PE|
    assert r["net_gamma"] == pytest.approx(-5.0e5)  # sign survives
    assert r["net_gamma"] < 0 < r["gamma"]


def test_no_sigma_means_no_pressure_rather_than_a_guess() -> None:
    assert pressure_by_strike(_book(), SPOT, None) == []
    assert pressure_by_strike(_book(), SPOT, 0) == []
    assert pressure_by_strike(_book(), None, SIGMA) == []


# ── time boost ─────────────────────────────────────────────────────────────


def test_time_boost_is_root_t() -> None:
    assert time_boost(TIME_BOOST_REFERENCE_DTE) == pytest.approx(1.0, abs=0.01)
    # 1 DTE against a six-session reference — the 2.43x seen on the live desk.
    assert time_boost(1) == pytest.approx(math.sqrt(6.0), abs=0.01)
    assert time_boost(4) < time_boost(1)


def test_expiry_day_is_floored_not_infinite() -> None:
    """√(6/0) is not a number a desk should print."""
    assert time_boost(0) is not None
    assert time_boost(0) <= MAX_TIME_BOOST
    assert time_boost(None) is None


# ── stability and state ────────────────────────────────────────────────────


def test_leader_stability_counts_ticks_within_one_step() -> None:
    hist = [{"pin_strike": 24300}] * 8 + [{"pin_strike": 24500}] * 2
    assert leader_stability_pct(hist, 24300, 50.0) == 80.0
    # One step of tolerance, so an adjacent strike still counts as held.
    assert leader_stability_pct([{"pin_strike": 24350}], 24300, 50.0) == 100.0
    # No history is unmeasured, not unstable.
    assert leader_stability_pct([], 24300, 50.0) is None
    assert leader_stability_pct(None, 24300, 50.0) is None


def test_pin_state_ladder() -> None:
    assert classify_pin_state(leader_share=0.05, margin=0.9, stability_pct=100) == "no_pin"
    assert classify_pin_state(leader_share=0.4, margin=0.9, stability_pct=20) == "shifting"
    assert classify_pin_state(leader_share=0.4, margin=0.05, stability_pct=95) == "stable"
    assert classify_pin_state(leader_share=0.4, margin=DOMINANT_MARGIN, stability_pct=95) == "locked"


# ── assembly ───────────────────────────────────────────────────────────────


def test_build_assembles_a_locked_pin() -> None:
    out = build_expiry_magnet(
        strikes=_book(),
        spot=SPOT,
        sigma_pts=SIGMA,
        dte=1,
        strike_step=50.0,
        history=[{"pin_strike": 24300}] * 20,
    )
    assert out["pin"] == 24300
    assert out["runner_up"] == 24350
    assert out["margin"] == pytest.approx(1.0 - 0.457, abs=0.01)
    assert out["state"] == "locked"
    assert out["stability_pct"] == 100.0
    assert out["time_boost"] == pytest.approx(math.sqrt(6.0), abs=0.01)
    assert len(out["top"]) == 5
    assert out["top"][0]["rank"] == 1


def test_conviction_is_scored_but_flagged_uncalibrated() -> None:
    """The score exists here (unlike pin_lock) — but never unlabelled."""
    out = build_expiry_magnet(
        strikes=_book(), spot=SPOT, sigma_pts=SIGMA, dte=1, strike_step=50.0,
        history=[{"pin_strike": 24300}] * 20,
    )
    c = out["conviction"]
    assert 0 <= c["score"] <= 100
    assert c["calibrated"] is False
    # Every input and weight travels with the number.
    assert set(c["parts"]) == {"margin", "stability", "proximity", "time"}
    assert pytest.approx(sum(c["weights"].values()), abs=1e-9) == 1.0


def test_missing_component_reweights_rather_than_scoring_zero() -> None:
    """No history must not drag conviction down as if the pin were unstable."""
    with_hist = build_expiry_magnet(
        strikes=_book(), spot=SPOT, sigma_pts=SIGMA, dte=1, strike_step=50.0,
        history=[{"pin_strike": 24300}] * 20,
    )
    without = build_expiry_magnet(
        strikes=_book(), spot=SPOT, sigma_pts=SIGMA, dte=1, strike_step=50.0, history=None,
    )
    assert without["conviction"]["parts"]["stability"] is None
    assert without["conviction"]["score"] is not None
    # Stability was 1.0 with history, so dropping it can only lower the score a
    # little — never to the ~0.7x a zero-fill would produce.
    assert without["conviction"]["score"] > 0.8 * with_hist["conviction"]["score"]


def test_build_returns_none_when_pressure_is_unmeasurable() -> None:
    assert build_expiry_magnet(
        strikes=_book(), spot=SPOT, sigma_pts=None, dte=1, strike_step=50.0
    ) is None
