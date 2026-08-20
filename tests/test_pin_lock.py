"""Unit tests for pin strength (gates + components, no blended score)."""

from __future__ import annotations

from datetime import datetime, timedelta

from options.pin_lock import (
    PIN_WINDOWS,
    compute_pin_lock,
    normalize_pin_window,
)
from options.gamma_density_history import IST

BASE = datetime(2026, 8, 20, 12, 0, tzinfo=IST)


def _ticks(n: int, *, pin: float = 24600.0, gex: float = 1e12, step_min: int = 1):
    """GEX history ticks ending at BASE."""
    return [
        {
            "t": (BASE - timedelta(minutes=(n - 1 - i) * step_min)).isoformat(timespec="seconds"),
            "pin_strike": pin,
            "total_gex": gex,
            "gamma_regime": "positive" if gex >= 0 else "negative",
        }
        for i in range(n)
    ]


def _minutes(spots: list[float]):
    """Minute chart rows ending at BASE."""
    n = len(spots)
    return [
        {
            "t": (BASE - timedelta(minutes=n - 1 - i)).isoformat(timespec="seconds"),
            "spot": s,
        }
        for i, s in enumerate(spots)
    ]


def _kw(**over):
    base = dict(
        pin_strike=24600.0,
        pin_source="dominant",
        spot=24605.0,
        strike_step=50.0,
        flip_level=24800.0,
        sigma1_pts=60.0,
        now=BASE,
    )
    base.update(over)
    return base


def test_window_normalization() -> None:
    assert normalize_pin_window(None) == "30m"
    assert normalize_pin_window("bogus") == "30m"
    assert normalize_pin_window("SESSION") == "session"
    assert PIN_WINDOWS["session"] is None


def test_gates_pass_on_a_real_pinned_book() -> None:
    out = compute_pin_lock(
        history=_ticks(30),
        chart_series=_minutes([24600 + (2 if i % 2 else -2) for i in range(30)]),
        **_kw(),
    )
    assert out["gates"]["pin_is_dominant"] is True
    assert out["gates"]["dealers_long_gamma"] is True
    assert out["gates"]["passed"] is True
    assert out["pin_mode"] == 24600.0
    assert out["components"]["stability_pct"] == 100.0
    assert out["components"]["containment_pct"] == 100.0
    # Oscillating across the pin is the mechanism, not noise.
    assert out["components"]["crossings"] == 29
    assert out["reasons"] == []


def test_atm_placeholder_fails_the_gate_however_steady_it_looks() -> None:
    """The regression this whole feature exists for.

    An ATM pin tracks spot, so stability and containment are perfect — and it is
    still not a pin.
    """
    out = compute_pin_lock(
        history=_ticks(30),
        chart_series=_minutes([24600.0] * 30),
        **_kw(pin_source="atm"),
    )
    assert out["components"]["stability_pct"] == 100.0
    assert out["components"]["containment_pct"] == 100.0
    assert out["gates"]["pin_is_dominant"] is False
    assert out["gates"]["passed"] is False
    assert "atm pin is not a gamma pin" in out["reasons"]


def test_short_gamma_fails_the_gate() -> None:
    out = compute_pin_lock(
        history=_ticks(30, gex=-1e12),
        chart_series=_minutes([24600.0] * 30),
        **_kw(),
    )
    assert out["gates"]["dealers_long_gamma"] is False
    assert out["gates"]["long_gamma_share"] == 0.0
    assert out["gates"]["passed"] is False
    assert "dealers are short gamma" in out["reasons"]


def test_unknown_is_not_failed() -> None:
    """No history must read as 'cannot tell', never as a failed pin."""
    out = compute_pin_lock(history=[], chart_series=[], **_kw())
    assert out["gates"]["dealers_long_gamma"] is None
    assert out["gates"]["passed"] is None
    assert out["components"]["stability_pct"] is None
    assert out["components"]["containment_pct"] is None
    assert out["samples"] == {"ticks": 0, "minutes": 0}
    assert "not enough session history yet" in out["reasons"]
    # Falls back to the caller's pin so the panel can still name a level.
    assert out["pin"] == 24600.0


def test_window_actually_trims_and_defaults_to_newest_sample() -> None:
    """A stale trail must not be scored as current."""
    stale = _ticks(10)
    for row in stale:  # push the whole trail two hours into the past
        row["t"] = (
            datetime.fromisoformat(row["t"]) - timedelta(hours=2)
        ).isoformat(timespec="seconds")
    # now=BASE → nothing inside a 30m window
    assert compute_pin_lock(history=stale, chart_series=[], **_kw())["samples"]["ticks"] == 0
    # now=None → window anchors on the newest row present, so it measures again
    out = compute_pin_lock(history=stale, chart_series=[], **_kw(now=None))
    assert out["samples"]["ticks"] == 10

    # session window keeps everything regardless
    assert (
        compute_pin_lock(history=stale, chart_series=[], window="session", **_kw())[
            "samples"
        ]["ticks"]
        == 10
    )


def test_modal_pin_survives_a_wandering_tail() -> None:
    """Anchor on the modal pin, not the latest one — a pin that just moved
    should not retroactively mark the whole window unstable."""
    history = _ticks(20) + _ticks(4, pin=24700.0)
    out = compute_pin_lock(history=history, chart_series=[], **_kw())
    assert out["pin_mode"] == 24600.0
    # 20 of 24 ticks sit on the modal pin
    assert out["components"]["stability_pct"] == round(100 * 20 / 24, 1)


def test_containment_uses_minutes_and_a_step_tolerance() -> None:
    # Half the minutes sit 120 pts away — well outside one 50-pt step.
    spots = [24600.0] * 15 + [24720.0] * 15
    out = compute_pin_lock(history=_ticks(30), chart_series=_minutes(spots), **_kw())
    assert out["components"]["containment_pct"] == 50.0
    assert out["components"]["containment_steps"] == 1.0


def test_flip_room_and_breaker_direction() -> None:
    out = compute_pin_lock(history=_ticks(30), chart_series=[], **_kw())
    # |24605 - 24800| / 60 = 3.25σ of room
    assert out["components"]["flip_room_sigma"] == 3.25
    assert out["components"]["flip_room_ok"] is True
    assert out["breaker"]["direction"] == "above"
    assert "24,800" in out["breaker"]["label"]

    tight = compute_pin_lock(
        history=_ticks(30), chart_series=[], **_kw(flip_level=24620.0)
    )
    assert tight["components"]["flip_room_sigma"] == 0.25
    assert tight["components"]["flip_room_ok"] is False

    none_flip = compute_pin_lock(
        history=_ticks(30), chart_series=[], **_kw(flip_level=None)
    )
    assert none_flip["breaker"]["level"] is None
    assert none_flip["components"]["flip_room_sigma"] is None


def test_pin_doi_reads_the_wall_at_the_pin_strike() -> None:
    strikes = [
        {"strike": 24550.0, "ce_doi": 999999, "pe_doi": 999999},
        {"strike": 24600.0, "ce_doi": -300000, "pe_doi": 100000},
    ]
    out = compute_pin_lock(history=_ticks(30), chart_series=[], strikes=strikes, **_kw())
    assert out["components"]["pin_doi"] == -200000
    assert out["components"]["pin_doi_direction"] == "unwinding"
    assert "pin OI is unwinding" in out["reasons"]

    # No baseline on the pin row → unknown, not zero.
    blank = compute_pin_lock(
        history=_ticks(30),
        chart_series=[],
        strikes=[{"strike": 24600.0, "ce_doi": None, "pe_doi": None}],
        **_kw(),
    )
    assert blank["components"]["pin_doi"] is None
    assert blank["components"]["pin_doi_direction"] is None


def test_no_blended_score_is_emitted() -> None:
    """Guards the design decision: components stay unweighted until calibrated."""
    out = compute_pin_lock(history=_ticks(30), chart_series=_minutes([24600.0] * 30), **_kw())
    flat = str(out)
    assert "score" not in flat
    assert set(out["gates"]) == {
        "pin_is_dominant",
        "dealers_long_gamma",
        "long_gamma_share",
        "passed",
    }
