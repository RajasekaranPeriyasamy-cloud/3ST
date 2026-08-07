"""Rolling straddle trade_mode entry filters."""

from __future__ import annotations

from execution.rolling_straddle import _can_enter_ce, _can_enter_pe


def _flat_leg() -> dict:
    return {"status": "flat", "entries_today": 0}


def _short_signals() -> dict:
    return {
        "long_entry": False,
        "long_ready": False,
        "short_entry": True,
        "short_ready": True,
    }


def _long_signals() -> dict:
    return {
        "long_entry": True,
        "long_ready": True,
        "short_entry": False,
        "short_ready": False,
    }


def test_short_signals_only_blocks_long_on_ce_and_pe():
    cfg = {"trade_mode": "ShortSignalsOnly", "max_reentries_ce": 1, "max_reentries_pe": 1}
    ce_ok, ce_reason = _can_enter_ce(cfg, _long_signals(), _flat_leg())
    pe_ok, pe_reason = _can_enter_pe(cfg, _long_signals(), _flat_leg())
    assert not ce_ok
    assert not pe_ok
    assert "no CE signal" in ce_reason
    assert "no PE signal" in pe_reason


def test_short_signals_only_allows_short_on_ce_and_pe():
    cfg = {"trade_mode": "ShortSignalsOnly", "max_reentries_ce": 1, "max_reentries_pe": 1}
    ce_ok, ce_reason = _can_enter_ce(cfg, _short_signals(), _flat_leg())
    pe_ok, pe_reason = _can_enter_pe(cfg, _short_signals(), _flat_leg())
    assert ce_ok
    assert pe_ok
    assert "short" in ce_reason
    assert "short" in pe_reason


def test_short_only_still_blocks_ce_leg():
    cfg = {"trade_mode": "ShortOnly", "max_reentries_ce": 1, "max_reentries_pe": 1}
    ce_ok, ce_reason = _can_enter_ce(cfg, _short_signals(), _flat_leg())
    assert not ce_ok
    assert ce_reason == "trade_mode ShortOnly"


def test_long_only_still_blocks_pe_leg():
    cfg = {"trade_mode": "LongOnly", "max_reentries_ce": 1, "max_reentries_pe": 1}
    pe_ok, pe_reason = _can_enter_pe(cfg, _long_signals(), _flat_leg())
    assert not pe_ok
    assert pe_reason == "trade_mode LongOnly"


def test_both_allows_buy_and_short_on_ce_and_pe():
    cfg = {"trade_mode": "Both", "max_reentries_ce": 1, "max_reentries_pe": 1}
    ce_long, ce_long_reason = _can_enter_ce(cfg, _long_signals(), _flat_leg())
    ce_short, ce_short_reason = _can_enter_ce(cfg, _short_signals(), _flat_leg())
    pe_long, pe_long_reason = _can_enter_pe(cfg, _long_signals(), _flat_leg())
    pe_short, pe_short_reason = _can_enter_pe(cfg, _short_signals(), _flat_leg())
    assert ce_long and "long" in ce_long_reason
    assert ce_short and "short" in ce_short_reason
    assert pe_long and "long" in pe_long_reason
    assert pe_short and "short" in pe_short_reason
