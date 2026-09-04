"""Price-based stops on the Rolling Straddle runner.

The runner used to honour ``tsl_mode == "ATR"`` only, while the config (and the
Stock Selection page, which Rolling Straddle copies from) can set ``%`` or
``Pts``. Those were accepted, persisted, rendered in the UI — and silently did
nothing. These tests pin all three modes, and pin that a mode the runner cannot
act on is refused rather than ignored.
"""

from __future__ import annotations

import pytest

from execution import rolling_straddle as rs

SIGNALS = {
    "close": 100.0,
    "atr1": 4.0,
    "long_zone_exit": False,
    "short_zone_exit": False,
    "ts": "2026-09-03 10:05:00",
}


def _leg(**over):
    leg = {"status": "open", "managed_by": "algo", "entry_side": "BUY"}
    leg.update(over)
    return leg


# --------------------------------------------------------------------------- #
# The band per mode — the only thing that differs between the three
# --------------------------------------------------------------------------- #


def test_trail_band_atr():
    assert rs._trail_band("ATR", ref=100.0, atr1=4.0, value=1.5) == 6.0


def test_trail_band_points():
    assert rs._trail_band("Pts", ref=100.0, atr1=None, value=8.0) == 8.0


def test_trail_band_percent():
    assert rs._trail_band("%", ref=200.0, atr1=None, value=5.0) == 10.0


def test_trail_band_atr_needs_an_atr():
    assert rs._trail_band("ATR", ref=100.0, atr1=None, value=1.5) is None
    assert rs._trail_band("ATR", ref=100.0, atr1=0.0, value=1.5) is None


def test_trail_band_unknown_mode_is_none():
    assert rs._trail_band("Off", ref=100.0, atr1=4.0, value=1.5) is None


# --------------------------------------------------------------------------- #
# Percent / points trails now actually arm — this was the silent no-op
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "mode,value,expected_trail",
    [
        ("ATR", 1.5, 94.0),  # 100 - 4*1.5
        ("Pts", 8.0, 92.0),  # 100 - 8
        ("%", 5.0, 95.0),  # 100 - 5%
    ],
)
def test_long_trail_arms_for_every_mode(mode, value, expected_trail):
    cfg = {"tsl_mode": mode, "tsl_value": value}
    patch = rs._live_exit_fields(cfg, SIGNALS, _leg(), side="long", ltp=100.0)
    assert patch["atr_trail"] == expected_trail
    assert patch["trail_mode"] == mode


@pytest.mark.parametrize(
    "mode,value,expected_trail",
    [
        ("ATR", 1.5, 106.0),
        ("Pts", 8.0, 108.0),
        ("%", 5.0, 105.0),
    ],
)
def test_short_trail_arms_for_every_mode(mode, value, expected_trail):
    cfg = {"tsl_mode": mode, "tsl_value": value}
    patch = rs._live_exit_fields(cfg, SIGNALS, _leg(entry_side="SELL"), side="short", ltp=100.0)
    assert patch["atr_trail"] == expected_trail


def test_trail_off_arms_nothing():
    patch = rs._live_exit_fields({"tsl_mode": "Off"}, SIGNALS, _leg(), side="long", ltp=100.0)
    assert "atr_trail" not in patch


def test_percent_trail_ratchets_and_never_loosens():
    """A trail may only move in the favourable direction."""
    cfg = {"tsl_mode": "%", "tsl_value": 5.0}
    leg = _leg()

    first = rs._live_exit_fields(cfg, SIGNALS, leg, side="long", ltp=100.0)
    assert first["atr_trail"] == 95.0

    # Price up -> trail ratchets up.
    leg = {**leg, **first}
    up = rs._live_exit_fields(cfg, SIGNALS, leg, side="long", ltp=120.0)
    assert up["atr_trail"] == 114.0

    # Price back down -> trail holds, never widens.
    leg = {**leg, **up}
    down = rs._live_exit_fields(cfg, SIGNALS, leg, side="long", ltp=100.0)
    assert down["atr_trail"] == 114.0


@pytest.mark.parametrize("mode,value", [("ATR", 1.5), ("Pts", 8.0), ("%", 5.0)])
def test_exit_fires_when_price_breaks_the_trail(mode, value, monkeypatch):
    monkeypatch.setattr(rs, "_leg_ltp", lambda _leg: 94.0)
    cfg = {"tsl_mode": mode, "tsl_value": value, "exit_on_bar_close_only": False}
    leg = _leg(atr_trail=95.0)
    should, reason = rs._should_exit_leg(cfg, SIGNALS, leg, force=False, leg_key="ce")
    assert should is True
    assert reason == "atr_exit"


def test_trail_does_not_fire_while_price_holds_above_it(monkeypatch):
    monkeypatch.setattr(rs, "_leg_ltp", lambda _leg: 96.0)
    cfg = {"tsl_mode": "%", "tsl_value": 5.0, "exit_on_bar_close_only": False}
    should, _ = rs._should_exit_leg(
        cfg, SIGNALS, _leg(atr_trail=95.0), force=False, leg_key="ce"
    )
    assert should is False


def test_trail_off_never_fires_even_with_a_stale_trail(monkeypatch):
    """Turning the trail off must disarm it, not leave the last level live."""
    monkeypatch.setattr(rs, "_leg_ltp", lambda _leg: 10.0)
    cfg = {"tsl_mode": "Off", "tsl_value": 5.0, "exit_on_bar_close_only": False}
    should, reason = rs._should_exit_leg(
        cfg, SIGNALS, _leg(atr_trail=95.0), force=False, leg_key="ce"
    )
    assert reason != "atr_exit"


def test_percent_trail_shows_up_in_the_exit_ladder():
    """A configured trail the operator cannot see is as good as no trail."""
    cfg = {"tsl_mode": "%", "tsl_value": 5.0, "timeframe": "5min", "entry_exit_enabled": False}
    leg = _leg(atr_trail=95.0, atr_live_ref=100.0, ltp=99.0, signal_close=99.0)
    params = rs._leg_exit_params("ce", cfg, leg)
    row = next(r for r in params["exit_levels"] if str(r["category"]).startswith("Trail"))
    assert row["price"] == 95.0
    assert "5.0%" in row["rule"]
    assert params["trail_enabled"] is True


# --------------------------------------------------------------------------- #
# A mode the runner cannot honour is refused, not ignored
# --------------------------------------------------------------------------- #


def _base_cfg(**over):
    cfg = {
        "underlying": "NIFTY",
        "size_mode": "lots",
        "size_value": 1,
        "sl_mode": "Off",
        "sl_value": 1.0,
        "tgt_mode": "Off",
        "tgt_value": 1.0,
        "tsl_mode": "Off",
        "tsl_value": 1.5,
    }
    cfg.update(over)
    return cfg


def test_atr_stop_loss_is_refused():
    """_level() returns None for ATR, so an ATR SL is no stop at all."""
    from execution.rolling_straddle_store import validate_stop_modes

    with pytest.raises(ValueError, match="sl_mode"):
        validate_stop_modes(_base_cfg(sl_mode="ATR", sl_value=1.5))


def test_atr_target_is_refused():
    from execution.rolling_straddle_store import validate_stop_modes

    with pytest.raises(ValueError, match="tgt_mode"):
        validate_stop_modes(_base_cfg(tgt_mode="ATR", tgt_value=1.5))


def test_atr_trailing_stop_is_allowed():
    from execution.rolling_straddle_store import validate_stop_modes

    validate_stop_modes(_base_cfg(tsl_mode="ATR", tsl_value=1.5))


@pytest.mark.parametrize("mode", ["%", "Pts"])
def test_percent_and_points_allowed_everywhere(mode):
    from execution.rolling_straddle_store import validate_stop_modes

    validate_stop_modes(
        _base_cfg(sl_mode=mode, sl_value=5, tgt_mode=mode, tgt_value=5, tsl_mode=mode, tsl_value=5)
    )


def test_zero_distance_stop_is_refused():
    """A stop at the entry price would fire the moment it is armed."""
    from execution.rolling_straddle_store import validate_stop_modes

    with pytest.raises(ValueError, match="sl_value"):
        validate_stop_modes(_base_cfg(sl_mode="Pts", sl_value=0))


def test_all_off_is_valid():
    """Running without a stop stays allowed — it is reported, not blocked."""
    from execution.rolling_straddle_store import validate_stop_modes

    validate_stop_modes(_base_cfg())


# --------------------------------------------------------------------------- #
# "No hard stop" must never be a silent condition
# --------------------------------------------------------------------------- #


def test_summary_reports_no_price_stop():
    out = rs.price_stop_summary(_base_cfg(force_exit="15:20", timeframe="5min"))
    assert out["has_price_stop"] is False
    assert out["armed"] == []
    assert any("entry_exit" in r for r in out["remaining_exits"])
    assert any("ST1" in r for r in out["remaining_exits"])
    assert any("force_exit" in r for r in out["remaining_exits"])


def test_summary_reports_an_armed_stop():
    out = rs.price_stop_summary(_base_cfg(sl_mode="Pts", sl_value=25))
    assert out["has_price_stop"] is True
    assert out["armed"] == ["SL Pts 25"]


def test_trailing_stop_alone_counts_as_a_price_stop():
    out = rs.price_stop_summary(_base_cfg(tsl_mode="ATR", tsl_value=1.5))
    assert out["has_price_stop"] is True


def test_target_alone_is_not_a_price_stop():
    """A target caps the upside; it does nothing for the downside."""
    out = rs.price_stop_summary(_base_cfg(tgt_mode="Pts", tgt_value=25))
    assert out["has_price_stop"] is False
