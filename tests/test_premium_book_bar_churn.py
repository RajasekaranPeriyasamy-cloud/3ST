"""Premium Book candle-close gates (mirrors Rolling Straddle anti-whipsaw)."""

from __future__ import annotations

from execution.premium_book_runner import (
    _desk_bar_guard,
    _evaluate_package_exit,
    _evaluate_short_leg_exit,
    _reentry_allowed_after_exit,
    _same_bar_action_blocked,
    _short_leg_adverse_zone,
    _st1_hit_package,
    _st1_hit_short_leg,
)


def _signals(
    *,
    ts: str = "2026-07-20 21:40:00",
    long_zone_exit: bool = False,
    short_zone_exit: bool = False,
    long_ready: bool = False,
    short_ready: bool = False,
) -> dict:
    return {
        "ts": ts,
        "close": 6200.0,
        "st1": 6180.0,
        "exit_line": 6180.0,
        "atr1": 25.0,
        "long_zone_exit": long_zone_exit,
        "short_zone_exit": short_zone_exit,
        "long_entry": False,
        "long_ready": long_ready,
        "short_entry": False,
        "short_ready": short_ready,
        "adx_ok": True,
    }


def test_same_bar_action_blocked_after_entry() -> None:
    holder = {"last_action_bar_ts": "2026-07-20 21:40:00"}
    assert _same_bar_action_blocked(holder, _signals(ts="2026-07-20 21:40:00"))
    assert not _same_bar_action_blocked(holder, _signals(ts="2026-07-20 21:45:00"))


def test_reentry_cooldown_blocks_until_next_bar() -> None:
    holder = {
        "last_exit_bar_ts": "2026-07-20 21:40:00",
        "last_action_bar_ts": "2026-07-20 21:40:00",
    }
    ok, reason = _reentry_allowed_after_exit(holder, _signals(ts="2026-07-20 21:40:00"))
    assert not ok
    assert reason == "reentry_cooldown"

    ok, reason = _reentry_allowed_after_exit(holder, _signals(ts="2026-07-20 21:45:00"))
    assert ok
    assert reason == ""


def test_desk_guard_merges_package_and_leg_stamps() -> None:
    state = {
        "last_action_bar_ts": None,
        "last_exit_bar_ts": None,
        "package": {
            "last_exit_bar_ts": "2026-07-20 21:40:00",
            "last_action_bar_ts": "2026-07-20 21:40:00",
        },
        "ce": {"last_exit_bar_ts": None},
        "pe": {"last_exit_bar_ts": None},
    }
    guard = _desk_bar_guard(state, _signals(ts="2026-07-20 21:40:00"))
    assert guard["last_exit_bar_ts"] == "2026-07-20 21:40:00"
    assert _same_bar_action_blocked(guard, _signals(ts="2026-07-20 21:40:00"))
    ok, reason = _reentry_allowed_after_exit(guard, _signals(ts="2026-07-20 21:40:00"))
    assert not ok and reason == "reentry_cooldown"


def test_st1_package_exit_skipped_same_bar(monkeypatch) -> None:
    cfg = {"exit_on_bar_close_only": True, "tsl_mode": "Off", "entry_exit_enabled": False}
    pkg = {
        "status": "open",
        "structure": "bull_put",
        "legs": [],
        "last_action_bar_ts": "2026-07-20 21:40:00",
    }
    desk = {"last_action_bar_ts": "2026-07-20 21:40:00"}
    signals = _signals(ts="2026-07-20 21:40:00", short_zone_exit=True, short_ready=True)

    reason, _ = _evaluate_package_exit(cfg, signals, pkg, False, desk_guard=desk)
    assert reason == "skipped_same_bar"

    next_bar = _signals(ts="2026-07-20 21:45:00", short_zone_exit=True, short_ready=True)
    reason, _ = _evaluate_package_exit(cfg, signals=next_bar, pkg=pkg, force=False, desk_guard=desk)
    assert reason == "st1_exit"


def test_atr_exit_still_fires_same_bar(monkeypatch) -> None:
    """ATR/SL remain tick-responsive even when ST1 is bar-gated."""
    cfg = {
        "exit_on_bar_close_only": True,
        "tsl_mode": "ATR",
        "tsl_value": 1.0,
        "entry_exit_enabled": False,
        "sl_mode": "Off",
    }
    leg = {
        "status": "open",
        "option_type": "CE",
        "exchange": "MCX",
        "tradingsymbol": "CRUDEOILM25JUL8000CE",
        "entry_price": 50.0,
        "atr_trail": 55.0,
        "atr_extreme": 60.0,
        "last_action_bar_ts": "2026-07-20 21:40:00",
    }
    desk = {"last_action_bar_ts": "2026-07-20 21:40:00"}
    signals = _signals(ts="2026-07-20 21:40:00")
    signals["atr1"] = 100.0

    monkeypatch.setattr(
        "execution.premium_book_runner._leg_ltp",
        lambda *_a, **_k: 70.0,
    )

    reason, patch = _evaluate_short_leg_exit(cfg, signals, leg, False, desk_guard=desk)
    assert reason == "atr_exit"
    assert patch.get("atr_trail") is not None


def test_force_exit_bypasses_same_bar() -> None:
    cfg = {"exit_on_bar_close_only": True, "tsl_mode": "Off", "entry_exit_enabled": False}
    pkg = {
        "status": "open",
        "structure": "bear_call",
        "legs": [],
        "last_action_bar_ts": "2026-07-20 21:40:00",
    }
    desk = {"last_action_bar_ts": "2026-07-20 21:40:00"}
    reason, _ = _evaluate_package_exit(
        cfg, _signals(ts="2026-07-20 21:40:00"), pkg, True, desk_guard=desk
    )
    assert reason == "force_exit"


def test_st1_hit_package_bull_put_on_short_zone() -> None:
    assert _st1_hit_package(
        "bull_put",
        _signals(short_zone_exit=True),
    )
    assert not _st1_hit_package(
        "bull_put",
        _signals(long_ready=True),
    )


def test_naked_short_ce_st1_uses_adverse_zone_not_below_st1() -> None:
    """Regression: flat/whipsaw + below ST1 must not ST1-exit short CE (CE churn)."""
    ce = {"status": "open", "option_type": "CE"}
    pe = {"status": "open", "option_type": "PE"}

    below = _signals(long_zone_exit=True, short_zone_exit=False)
    assert not _st1_hit_short_leg(ce, below)
    assert _st1_hit_short_leg(pe, below)
    assert not _short_leg_adverse_zone("CE", below)
    assert _short_leg_adverse_zone("PE", below)

    above = _signals(long_zone_exit=False, short_zone_exit=True)
    assert _st1_hit_short_leg(ce, above)
    assert not _st1_hit_short_leg(pe, above)


def test_short_ce_st1_exit_same_bar_then_reentry_blocked(monkeypatch) -> None:
    """After CE ST1 cover, same-bar re-entry cooldown must hold (desk guard)."""
    cfg = {
        "exit_on_bar_close_only": True,
        "tsl_mode": "Off",
        "sl_mode": "Off",
        "entry_exit_enabled": False,
    }
    leg = {
        "status": "open",
        "option_type": "CE",
        "exchange": "MCX",
        "tradingsymbol": "CRUDEOILM26AUG8000CE",
        "entry_price": 495.70,
        "last_action_bar_ts": "2026-07-20 21:40:00",
    }
    desk = {
        "last_action_bar_ts": "2026-07-20 21:40:00",
        "last_exit_bar_ts": None,
    }
    monkeypatch.setattr(
        "execution.premium_book_runner._leg_ltp",
        lambda *_a, **_k: 497.0,
    )

    # Same bar as entry — ST1 deferred even if adverse zone is on.
    same = _signals(ts="2026-07-20 21:40:00", short_zone_exit=True)
    reason, _ = _evaluate_short_leg_exit(cfg, same, leg, False, desk_guard=desk)
    assert reason == "skipped_same_bar"

    # Next bar — adverse zone fires ST1 on CE.
    nxt = _signals(ts="2026-07-20 21:45:00", short_zone_exit=True)
    reason, _ = _evaluate_short_leg_exit(cfg, nxt, leg, False, desk_guard=desk)
    assert reason == "st1_exit"

    after_exit = {
        "last_action_bar_ts": "2026-07-20 21:45:00",
        "last_exit_bar_ts": "2026-07-20 21:45:00",
    }
    ok, why = _reentry_allowed_after_exit(after_exit, nxt)
    assert not ok and why == "reentry_cooldown"
    # Still adverse → dual-open path must not sell CE again this bar.
    assert _short_leg_adverse_zone("CE", nxt)
