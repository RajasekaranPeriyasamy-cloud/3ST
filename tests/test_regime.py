"""Tests for the structural regime classifier, confluence and σ levels."""

from __future__ import annotations

import pytest

from options.regime import (
    CONTAINMENT_HOLDS_PCT,
    FLIP_NEAR_SIGMA,
    build_regime_block,
    classify_regime,
    compute_confluence,
    levels_in_sigma,
    regime_features,
)


def _features(**over):
    base = dict(
        gamma_regime="negative",
        spot=24252.65,
        sigma1_pts=174.0,
        flip_level=24362.66,
        call_wall=24300.0,
        put_wall=24200.0,
        pin=24300.0,
        pin_source="dominant",
        pin_gates_passed=False,
        containment_pct=96.7,
        hhi_band="balanced",
        overlap_pct=73.2,
        confluence=None,
    )
    base.update(over)
    return regime_features(**base)


# ── levels in σ ────────────────────────────────────────────────────────────


def test_levels_in_sigma_are_signed_and_scaled() -> None:
    out = levels_in_sigma(24252.65, 174.0, {"flip": 24362.66, "put_wall": 24200.0})
    assert out["flip"]["pts"] == pytest.approx(110.01, abs=0.01)
    assert out["flip"]["sigma"] == pytest.approx(0.632, abs=0.005)
    # Below spot must read negative, not absolute.
    assert out["put_wall"]["pts"] < 0
    assert out["put_wall"]["sigma"] < 0


def test_levels_without_sigma_report_points_only() -> None:
    """No expected move is not an excuse to invent one."""
    out = levels_in_sigma(24252.65, None, {"flip": 24362.66})
    assert out["flip"]["pts"] == pytest.approx(110.01, abs=0.01)
    assert out["flip"]["sigma"] is None

    missing = levels_in_sigma(None, 174.0, {"flip": 24362.66})
    assert missing["flip"]["pts"] is None


# ── confluence ─────────────────────────────────────────────────────────────


def test_confluence_measures_pin_against_poc() -> None:
    """Today's live shape: POC one full strike below the gamma pin."""
    out = compute_confluence(
        pin=24300.0, poc=24252.28, vah=24260.80, val=24225.87, strike_step=50.0
    )
    assert out["gap_pts"] == pytest.approx(-47.72, abs=0.01)
    assert out["gap_steps"] == pytest.approx(-0.95, abs=0.01)
    # Within one step, so the levels are "aligned" even though the pin sits
    # outside the (very narrow) value area — the two questions differ.
    assert out["aligned"] is True
    assert out["pin_in_value"] is False
    assert out["value_area"]["width_pts"] == pytest.approx(34.93, abs=0.01)


def test_confluence_flags_a_structural_only_magnet() -> None:
    out = compute_confluence(
        pin=24500.0, poc=24200.0, vah=24240.0, val=24160.0, strike_step=50.0
    )
    assert out["gap_steps"] == pytest.approx(-6.0, abs=0.01)
    assert out["aligned"] is False
    assert out["pin_in_value"] is False


def test_confluence_missing_side_is_none_not_agreement() -> None:
    """'Could not compare' must never render as 'they agree'."""
    out = compute_confluence(pin=24300.0, poc=None, vah=None, val=None, strike_step=50.0)
    assert out["gap_pts"] is None
    assert out["aligned"] is None
    assert out["pin_in_value"] is None
    assert out["value_area"] is None


# ── features ───────────────────────────────────────────────────────────────


def test_features_capture_the_live_book() -> None:
    f = _features()
    assert f["gamma_sign"] == "negative"
    assert f["flip_sigma"] == pytest.approx(0.632, abs=0.005)
    assert f["flip_near"] is True  # 0.63σ <= 0.75
    assert f["box_pts"] == 100.0
    assert f["spot_in_box"] is True
    assert f["pin_is_dominant"] is True
    assert f["containment_holds"] is True  # 96.7 >= 80
    assert f["volume_balanced"] is False  # OVL 73.2 < 75


def test_unknown_gamma_sign_is_not_guessed() -> None:
    f = _features(gamma_regime=None)
    assert f["gamma_sign"] is None
    assert classify_regime(f)["state"] == "unmeasured"


# ── classification ─────────────────────────────────────────────────────────


def test_live_book_classifies_as_a_coiled_box() -> None:
    """Contained, but by positioning rather than by dealer hedging."""
    v = classify_regime(_features())
    assert v["state"] == "coiled_box"
    assert "dealers short gamma" in v["evidence"]
    assert any("flip" in e for e in v["evidence"])


def test_long_gamma_at_a_held_dominant_strike_is_pinned() -> None:
    v = classify_regime(
        _features(gamma_regime="positive", pin_gates_passed=True, containment_pct=95.0)
    )
    assert v["state"] == "pinned"
    assert "dealers long gamma" in v["evidence"]


def test_short_gamma_with_room_and_no_containment_trends() -> None:
    v = classify_regime(
        _features(containment_pct=20.0, flip_level=24252.65 + 174.0 * 2.5)
    )
    assert v["state"] == "short_gamma_trend"


def test_near_the_flip_without_containment_is_a_transition() -> None:
    v = classify_regime(_features(gamma_regime="positive", containment_pct=10.0))
    # Long gamma, no containment, flip within FLIP_NEAR_SIGMA → boundary state.
    assert v["state"] == "transition"


def test_long_gamma_far_from_everything_drifts() -> None:
    v = classify_regime(
        _features(
            gamma_regime="positive",
            containment_pct=10.0,
            flip_level=24252.65 + 174.0 * 3,
        )
    )
    assert v["state"] == "long_gamma_drift"


def test_every_state_carries_evidence_and_no_recommendation() -> None:
    """Guards the module's boundary: it describes, it does not advise.

    Matched on whole words and advisory *phrasing* rather than bare nouns —
    "positioning" and "dealers sell rallies" are descriptions of the book, while
    "you should" and named structures are recommendations. An earlier version of
    this test flagged the word "positioning" and was wrong to.
    """
    import re

    banned_phrases = (
        r"\byou should\b", r"\brecommend", r"\bsuggest", r"\bwe advise\b",
        r"\bgo (long|short)\b", r"\b(buy|sell) (a|the) \b", r"\benter (a|the) \b",
        r"\bstrangle\b", r"\bstraddle\b", r"\biron condor\b", r"\bbutterfly\b",
    )
    for f in (
        _features(),
        _features(gamma_regime="positive", pin_gates_passed=True),
        _features(containment_pct=5.0),
        _features(gamma_regime=None),
    ):
        v = classify_regime(f)
        assert v["evidence"], v
        assert v["label"] and v["description"]
        blob = (v["description"] + " " + " ".join(v["evidence"])).lower()
        for pat in banned_phrases:
            assert not re.search(pat, blob), f"{v['state']} leaked advice: {pat}"


# ── assembly ───────────────────────────────────────────────────────────────


def test_build_regime_block_assembles_from_snapshot_pieces() -> None:
    out = build_regime_block(
        gamma_regime="negative",
        spot=24252.65,
        sigma1_pts=174.0,
        flip_level=24362.66,
        call_wall=24300.0,
        put_wall=24200.0,
        strike_step=50.0,
        concentration={
            "pin_strike": 24300.0,
            "pin_source": "dominant",
            "band_label": "balanced",
            "pos_gamma_peak_strike": 24500.0,
            "neg_gamma_peak_strike": 24200.0,
        },
        pin_lock={
            "pin_mode": 24300.0,
            "gates": {"passed": False},
            "components": {"containment_pct": 96.7},
        },
        volume_profile={"available": True, "poc": 24252.28, "vah": 24260.8,
                        "val": 24225.87, "overlap_pct": 73.2},
    )
    assert out["state"] == "coiled_box"
    assert out["confluence"]["gap_steps"] == pytest.approx(-0.95, abs=0.01)
    assert out["levels"]["flip"]["sigma"] == pytest.approx(0.632, abs=0.005)
    assert out["levels"]["neg_gamma_peak"]["sigma"] < 0
    assert out["features"]["flip_near"] is True
    assert out["sigma1_pts"] == 174.0


def test_build_regime_block_survives_a_missing_volume_profile() -> None:
    out = build_regime_block(
        gamma_regime="negative", spot=24252.65, sigma1_pts=174.0,
        flip_level=24362.66, call_wall=24300.0, put_wall=24200.0, strike_step=50.0,
        concentration={"pin_strike": 24300.0, "pin_source": "dominant"},
        pin_lock=None, volume_profile=None,
    )
    assert out["confluence"]["aligned"] is None
    assert out["levels"]["poc"]["level"] is None
    assert out["state"] in {"transition", "short_gamma_trend", "coiled_box"}


def test_thresholds_are_named_not_magic() -> None:
    assert 0 < FLIP_NEAR_SIGMA < 2
    assert 50 <= CONTAINMENT_HOLDS_PCT <= 100
