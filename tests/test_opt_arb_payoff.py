"""Expiry payoff curves.

The load-bearing property is that the curve is exact at the kinks: every strike
must appear in the sample grid, or a breakeven interpolated across a kink would
be wrong in a way that looks perfectly plausible on a chart.
"""

from __future__ import annotations

import pytest

from analysis.opt_arb import payoff


def _leg(side, option_type, strike, price, units=65.0):
    return {
        "side": side,
        "option_type": option_type,
        "strike": strike,
        "price": price,
        "units": units,
        "exchange": "NFO",
        "tradingsymbol": f"NIFTY{strike:.0f}{option_type}",
    }


def test_cross_contract_identity_is_a_flat_line():
    """Same strike, same expiry, offsetting sizes — no exposure to the spot."""
    legs = [
        _leg("BUY", "PE", 7500, 192.8, units=100),
        _leg("SELL", "PE", 7500, 198.3, units=100),
    ]
    built = payoff.build(legs, spot=7620.0, charges=154.0)
    summary = built["summary"]
    assert summary["flat"] is True
    assert summary["risk_free"] is True
    assert summary["max_profit"] == summary["max_loss"] == pytest.approx(396.0)
    assert summary["breakevens"] == []
    assert {p["net"] for p in built["points"]} == {396.0}


def test_charges_shift_the_curve_without_changing_its_shape():
    legs = [_leg("BUY", "CE", 24000, 200.0)]
    free = payoff.build(legs, spot=24300.0, charges=0.0)
    charged = payoff.build(legs, spot=24300.0, charges=500.0)
    for a, b in zip(free["points"], charged["points"], strict=True):
        assert a["spot"] == b["spot"]
        assert a["gross"] == b["gross"]
        assert b["net"] == pytest.approx(a["net"] - 500.0)


def test_long_call_breakeven_is_strike_plus_premium():
    """Exact only because the strike is guaranteed to be a sample point."""
    legs = [_leg("BUY", "CE", 24000, 200.0)]
    built = payoff.build(legs, spot=24000.0, charges=0.0)
    assert built["summary"]["breakevens"] == [pytest.approx(24200.0, abs=0.02)]


def test_charges_move_the_breakeven():
    legs = [_leg("BUY", "CE", 24000, 200.0)]
    built = payoff.build(legs, spot=24000.0, charges=650.0)  # 10 points on 65 units
    assert built["summary"]["breakevens"] == [pytest.approx(24210.0, abs=0.02)]


def test_every_strike_is_a_sample_point():
    legs = [
        _leg("BUY", "CE", 24200, 200.0),
        _leg("SELL", "CE", 24300, 160.0, units=130.0),
        _leg("BUY", "CE", 24400, 100.0),
    ]
    built = payoff.build(legs, spot=24300.0)
    sampled = {p["spot"] for p in built["points"]}
    assert {24200.0, 24300.0, 24400.0} <= sampled
    assert built["strikes"] == [24200.0, 24300.0, 24400.0]


def test_butterfly_peaks_at_the_body_strike():
    legs = [
        _leg("BUY", "CE", 24200, 200.0),
        _leg("SELL", "CE", 24300, 160.0, units=130.0),
        _leg("BUY", "CE", 24400, 100.0),
    ]
    built = payoff.build(legs, spot=24300.0, charges=150.0)
    best = max(built["points"], key=lambda p: p["net"])
    assert best["spot"] == pytest.approx(24300.0)


def test_a_credit_butterfly_is_risk_free_without_being_flat():
    """The whole premise of the butterfly family — bought below zero it is free
    money, but the curve is a tent, not a line."""
    legs = [
        _leg("BUY", "CE", 24200, 200.0),
        _leg("SELL", "CE", 24300, 160.0, units=130.0),
        _leg("BUY", "CE", 24400, 100.0),
    ]
    summary = payoff.build(legs, spot=24300.0, charges=150.0)["summary"]
    assert summary["flat"] is False
    assert summary["risk_free"] is True
    assert summary["max_loss"] > 0


def test_naked_short_is_flagged_unbounded_with_a_breakeven():
    legs = [_leg("SELL", "CE", 24300, 160.0)]
    summary = payoff.build(legs, spot=24300.0, charges=40.0)["summary"]
    assert summary["unbounded_loss"] is True
    assert summary["risk_free"] is False
    assert summary["max_loss"] < 0
    assert len(summary["breakevens"]) == 1


def test_a_defined_risk_spread_is_not_flagged_unbounded():
    legs = [
        _leg("BUY", "CE", 24200, 200.0),
        _leg("SELL", "CE", 24400, 100.0),
    ]
    summary = payoff.build(legs, spot=24300.0)["summary"]
    assert summary["unbounded_loss"] is False


def test_box_payoff_is_flat_at_width_minus_cash():
    """Long box: long call spread + long put spread on the same two strikes."""
    legs = [
        _leg("BUY", "CE", 24000, 400.0),
        _leg("SELL", "CE", 24200, 230.0),
        _leg("SELL", "PE", 24000, 70.0),
        _leg("BUY", "PE", 24200, 140.0),
    ]
    built = payoff.build(legs, spot=24100.0, charges=0.0)
    # cash paid = 400 - 230 - 70 + 140 = 240; width 200 -> 40 loss per unit
    assert built["summary"]["flat"] is True
    assert built["summary"]["max_profit"] == pytest.approx(-40.0 * 65)


def test_empty_legs_return_an_empty_curve():
    built = payoff.build([], spot=24300.0)
    assert built["points"] == []
    assert built["summary"] == {}


def test_tier_b_cross_contract_says_the_curve_assumes_convergence():
    row = {
        "family": "xcontract",
        "tier": "B",
        "legs": [
            {"exchange": "MCX", "tradingsymbol": "GOLD26AUG", "strike": 125000},
            {"exchange": "MCX", "tradingsymbol": "GOLDM26AUG", "strike": 125000},
        ],
        "warnings": [],
    }
    notes = payoff.row_assumptions(row)
    assert any("DIFFERENT futures months" in n for n in notes)


def test_tier_a_cross_contract_says_the_axis_is_exact():
    row = {
        "family": "xcontract",
        "tier": "A",
        "legs": [
            {"exchange": "MCX", "tradingsymbol": "CRUDEOIL26SEP", "strike": 7500},
            {"exchange": "MCX", "tradingsymbol": "CRUDEOILM26SEP", "strike": 7500},
        ],
        "warnings": [],
    }
    notes = payoff.row_assumptions(row)
    assert any("same futures month" in n for n in notes)


def test_row_warnings_are_carried_into_the_assumptions():
    row = {
        "family": "box",
        "tier": "B",
        "legs": [{"exchange": "NFO", "tradingsymbol": "RELIANCE", "strike": 1400}],
        "warnings": ["stock option — physically settled"],
    }
    assert "stock option — physically settled" in payoff.row_assumptions(row)


def test_attach_uses_each_row_own_charges():
    rows = [
        {
            "family": "vertical",
            "tier": "A",
            "cost": 500.0,
            "warnings": [],
            "legs": [_leg("BUY", "CE", 24000, 200.0)],
        }
    ]
    payoff.attach(rows, spot=24300.0)
    assert rows[0]["payoff"]["charges"] == 500.0
    assert rows[0]["payoff"]["summary"]["breakevens"]


def test_attach_skips_a_row_with_no_legs():
    rows = [{"family": "vertical", "cost": 10.0, "legs": []}]
    payoff.attach(rows, spot=24300.0)
    assert "payoff" not in rows[0]
