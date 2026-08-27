"""Charge model — the gate every arbitrage row has to clear.

These assertions pin arithmetic that is easy to get subtly wrong and hard to
notice: GST applies to the service fees but never to STT or stamp duty, STT is
sell-side only, stamp duty is buy-side only, and exercise STT depends on which
direction of a box you hold.
"""

from __future__ import annotations

import pytest

from analysis.opt_arb import costs


@pytest.fixture(autouse=True)
def _pristine_rates():
    costs.reset_rates()
    yield
    costs.reset_rates()


def test_sell_leg_pays_stt_and_no_stamp():
    leg = costs.leg_cost("NFO", "SELL", price=100.0, units=75.0)
    assert leg.turnover == pytest.approx(7500.0)
    assert leg.stt == pytest.approx(7500.0 * 0.001, abs=0.01)  # 0.1% of premium
    assert leg.stamp == 0.0


def test_buy_leg_pays_stamp_and_no_stt():
    leg = costs.leg_cost("NFO", "BUY", price=100.0, units=75.0)
    assert leg.stt == 0.0
    assert leg.stamp == pytest.approx(7500.0 * 0.00003, abs=0.01)


def test_gst_excludes_stt_and_stamp():
    """GST is on brokerage + exchange/regulator fees only, never on the levies."""
    leg = costs.leg_cost("NFO", "SELL", price=100.0, units=75.0)
    fee_base = leg.brokerage + leg.txn + leg.ipft + leg.sebi
    assert leg.gst == pytest.approx(fee_base * 0.18, abs=0.02)
    assert leg.total == pytest.approx(fee_base + leg.gst + leg.stt + leg.stamp, abs=0.02)


def test_mcx_uses_ctt_rate_not_stt():
    nfo = costs.leg_cost("NFO", "SELL", price=100.0, units=100.0)
    mcx = costs.leg_cost("MCX", "SELL", price=100.0, units=100.0)
    assert mcx.stt == pytest.approx(10000.0 * 0.0005, abs=0.01)  # 0.05%
    assert mcx.stt < nfo.stt


def test_combo_round_trip_charges_the_exit_too():
    legs = [
        {"segment": "NFO", "side": "BUY", "price": 400.0, "units": 65.0},
        {"segment": "NFO", "side": "SELL", "price": 230.0, "units": 65.0},
    ]
    one_way = costs.combo_cost(legs, round_trip=False)
    both = costs.combo_cost(legs, round_trip=True)
    assert one_way["leg_count"] == 2
    assert both["leg_count"] == 4
    assert both["total"] > one_way["total"]
    assert both["entry_total"] == one_way["total"]


def test_four_leg_index_box_costs_several_points():
    """A NIFTY box round-trip is a few index points before any edge exists.

    This is the number that decides whether the desk is worth running at all —
    if it ever collapses toward zero, the rate card has been zeroed out.
    """
    legs = [
        {"segment": "NFO", "side": "BUY", "price": 400.0, "units": 65.0},
        {"segment": "NFO", "side": "SELL", "price": 230.0, "units": 65.0},
        {"segment": "NFO", "side": "SELL", "price": 70.0, "units": 65.0},
        {"segment": "NFO", "side": "BUY", "price": 140.0, "units": 65.0},
    ]
    total = costs.combo_cost(legs, round_trip=True)["total"]
    points = total / 65.0
    assert 2.0 < points < 12.0


def test_long_box_exercise_stt_is_measured_to_the_far_strike():
    far = costs.box_exercise_cost(
        "NFO", spot=25000.0, lower_strike=24000.0, upper_strike=24200.0, units=65.0, long_box=True
    )
    near = costs.box_exercise_cost(
        "NFO", spot=25000.0, lower_strike=24000.0, upper_strike=24200.0, units=65.0, long_box=False
    )
    assert far["intrinsic"] == pytest.approx(1000.0)  # spot - lower strike
    assert near["intrinsic"] == pytest.approx(800.0)  # spot - upper strike
    assert far["stt"] > near["stt"]


def test_short_box_between_strikes_has_no_itm_long_leg():
    between = costs.box_exercise_cost(
        "NFO", spot=24100.0, lower_strike=24000.0, upper_strike=24200.0, units=65.0, long_box=False
    )
    assert between["intrinsic"] == 0.0
    assert between["stt"] == 0.0


def test_mcx_has_no_intrinsic_exercise_levy():
    result = costs.box_exercise_cost(
        "MCX", spot=6500.0, lower_strike=6000.0, upper_strike=6200.0, units=100.0
    )
    assert result["applies"] is False
    assert result["stt"] == 0.0


def test_rate_override_applies_and_resets():
    costs.set_rates("NFO", {"txn_pct": 0.0})
    assert costs.rates_for("NFO").txn_pct == 0.0
    costs.reset_rates()
    assert costs.rates_for("NFO").txn_pct == pytest.approx(0.0495)


def test_rate_override_ignores_unknown_and_non_numeric_fields():
    before = costs.rates_for("NFO")
    costs.set_rates("NFO", {"nonsense": 1, "label": "hacked", "txn_pct": "not-a-number"})
    after = costs.rates_for("NFO")
    assert after.label == before.label
    assert after.txn_pct == before.txn_pct


def test_stock_options_are_flagged_physically_settled():
    assert costs.rates_for("NFO_STOCK").physical_settlement is True
    assert costs.rates_for("NFO").physical_settlement is False
