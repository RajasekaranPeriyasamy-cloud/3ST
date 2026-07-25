"""Tests for OI VAR session flow regime + shift detection."""

from __future__ import annotations

from options.oi_var import detect_flow_shifts, flow_polarity, side_flow_regime


def test_flow_polarity() -> None:
    assert flow_polarity("long_build") == 1
    assert flow_polarity("short_cover") == 1
    assert flow_polarity("short_build") == -1
    assert flow_polarity("long_unwind") == -1
    assert flow_polarity("flat") == 0


def test_side_flow_regime_ce_long() -> None:
    legs = [
        {"side": "call", "flow_tag_session": "long_build", "delta_oi_session": 500},
        {"side": "call", "flow_tag_session": "short_cover", "delta_oi_session": -200},
        {"side": "call", "flow_tag_session": "short_build", "delta_oi_session": 50},
        {"side": "put", "flow_tag_session": "short_build", "delta_oi_session": 900},
    ]
    ce = side_flow_regime(legs, "call")
    assert ce["regime"] == "long"
    pe = side_flow_regime(legs, "put")
    assert pe["regime"] == "short"


def test_detect_ce_short_to_long_and_pe_long_to_short() -> None:
    history = [
        {"t": "2026-07-24T09:30:00+05:30", "spot": 23650, "ce_flow_regime": "short", "pe_flow_regime": "long"},
        {"t": "2026-07-24T09:45:00+05:30", "spot": 23620, "ce_flow_regime": "short", "pe_flow_regime": "long"},
        {"t": "2026-07-24T10:10:00+05:30", "spot": 23580, "ce_flow_regime": "long", "pe_flow_regime": "short"},
        {"t": "2026-07-24T10:12:00+05:30", "spot": 23610, "ce_flow_regime": "long", "pe_flow_regime": "short"},
        {"t": "2026-07-24T10:15:00+05:30", "spot": 23640, "ce_flow_regime": "long", "pe_flow_regime": "short"},
    ]
    shifts = detect_flow_shifts(history, min_hold_ticks=2)
    labels = {s["label"] for s in shifts}
    assert "CE short → long" in labels
    assert "PE long → short" in labels
