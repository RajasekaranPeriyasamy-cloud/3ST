"""Rolling straddle bar-close exits and re-entry cooldown (anti-whipsaw)."""

from __future__ import annotations

from execution.rolling_straddle import (
    _can_enter_pe,
    _reentry_allowed_after_exit,
    _same_bar_action_blocked,
    _should_exit_pe,
)


def _short_signals(*, ts: str = "2026-07-14 12:50:00", zone_exit: bool = False) -> dict:
    return {
        "ts": ts,
        "close": 49.0,
        "st1": 61.0,
        "exit_line": 61.0,
        "atr1": 10.0,
        "long_zone_exit": False,
        "short_zone_exit": zone_exit,
        "long_entry": False,
        "long_ready": False,
        "short_entry": False,
        "short_ready": True,
    }


def _open_pe_short(*, signal_bar_ts: str = "2026-07-14 12:50:00") -> dict:
    return {
        "status": "open",
        "managed_by": "algo",
        "entry_side": "SELL",
        "position_side": "short",
        "entry_price": 55.0,
        "signal_bar_ts": signal_bar_ts,
        "last_action_bar_ts": "2026-07-14 12:45:00",
    }


def test_exit_deferred_ltp_when_bar_close_only(monkeypatch):
    cfg = {"exit_on_bar_close_only": True, "sl_mode": "Off", "tgt_mode": "Off", "tsl_mode": "Off"}
    leg = _open_pe_short()
    signals = _short_signals(zone_exit=False)

    monkeypatch.setattr(
        "execution.rolling_straddle._leg_ltp",
        lambda _leg: 62.0,
    )

    should_x, reason = _should_exit_pe(cfg, signals, leg, False)
    assert not should_x
    assert reason == "exit_deferred_ltp"


def test_exit_on_bar_close_when_zone_exit_set():
    cfg = {"exit_on_bar_close_only": True, "sl_mode": "Off", "tgt_mode": "Off", "tsl_mode": "Off"}
    leg = _open_pe_short()
    signals = _short_signals(zone_exit=True)

    should_x, reason = _should_exit_pe(cfg, signals, leg, False)
    assert should_x
    assert reason == "short_zone_exit"


def test_reentry_cooldown_blocks_zone_active_until_next_bar():
    leg = {
        "status": "flat",
        "entries_today": 1,
        "last_exit_bar_ts": "2026-07-14 12:50:00",
        "last_action_bar_ts": "2026-07-14 12:50:00",
    }
    same_bar = _short_signals(ts="2026-07-14 12:50:00")
    next_bar = _short_signals(ts="2026-07-14 12:55:00")

    ok, reason = _reentry_allowed_after_exit(leg, same_bar)
    assert not ok
    assert reason == "reentry_cooldown"

    ok, reason = _reentry_allowed_after_exit(leg, next_bar)
    assert ok


def test_can_enter_pe_respects_reentry_cooldown_with_zone_active():
    cfg = {
        "trade_mode": "ShortSignalsOnly",
        "max_reentries_pe": 20,
        "reentry_style": "zone_active",
    }
    leg = {
        "status": "flat",
        "entries_today": 2,
        "last_exit_bar_ts": "2026-07-14 12:50:00",
    }
    signals = _short_signals(ts="2026-07-14 12:50:00")

    ok, reason = _can_enter_pe(cfg, signals, leg)
    assert not ok
    assert reason == "reentry_cooldown"


def test_can_enter_pe_allows_after_next_bar_with_zone_active():
    cfg = {
        "trade_mode": "ShortSignalsOnly",
        "max_reentries_pe": 20,
        "reentry_style": "zone_active",
    }
    leg = {
        "status": "flat",
        "entries_today": 2,
        "last_exit_bar_ts": "2026-07-14 12:50:00",
        "last_action_bar_ts": "2026-07-14 12:50:00",
    }
    signals = _short_signals(ts="2026-07-14 12:55:00")

    ok, reason = _can_enter_pe(cfg, signals, leg)
    assert ok
    assert "short" in reason


def test_same_bar_action_blocked_after_entry():
    leg = {
        "status": "open",
        "last_action_bar_ts": "2026-07-14 12:50:00",
    }
    signals = _short_signals(ts="2026-07-14 12:50:00")
    assert _same_bar_action_blocked(leg, signals)

    signals_next = _short_signals(ts="2026-07-14 12:55:00")
    assert not _same_bar_action_blocked(leg, signals_next)
