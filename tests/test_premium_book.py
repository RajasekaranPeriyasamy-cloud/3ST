"""Unit tests for Premium Book strikes, exit ladder, max-loss, and SL convert."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from execution.premium_book_runner import (
    exit_ladder_reason,
    pick_auto_structure,
    should_convert_sl_to_spread,
    structure_entry_ok,
)
from config import INDEX_OPTIONS, MCX_SESSION
from execution.premium_book_store import (
    BUY_STRUCTURES,
    DEFAULT_BUY_STRUCTURE,
    DEFAULT_CONFIG,
    DEFAULT_SELL_STRUCTURE,
    PREMIUM_BOOK_UNDERLYINGS,
    SELL_STRUCTURES,
    TRADE_BIAS_BUY,
    TRADE_BIAS_SELL,
    apply_underlying_defaults,
    is_buy_structure,
    lot_size_for,
    trade_bias_for_structure,
)
from options.chain import atm_strike
from options.spreads import (
    _max_loss_estimate,
    build_legs,
    hedge_wing_for_short_leg,
    preview_spread,
)


def test_exit_ladder_force_before_atr_before_st1() -> None:
    assert (
        exit_ladder_reason(force=True, atr_hit=True, st1_hit=True, sl_hit=True)
        == "force_exit"
    )
    assert (
        exit_ladder_reason(force=False, atr_hit=True, st1_hit=True, sl_hit=True)
        == "atr_exit"
    )
    assert (
        exit_ladder_reason(force=False, atr_hit=False, st1_hit=True, sl_hit=True)
        == "sl_exit"
    )
    assert (
        exit_ladder_reason(force=False, atr_hit=False, st1_hit=True, sl_hit=False)
        == "st1_exit"
    )
    assert (
        exit_ladder_reason(force=False, atr_hit=False, st1_hit=False, sl_hit=False)
        is None
    )


def test_entry_exit_skipped_when_disabled() -> None:
    assert (
        exit_ladder_reason(
            force=False,
            atr_hit=False,
            st1_hit=True,
            entry_exit_hit=True,
            entry_exit_enabled=False,
        )
        == "st1_exit"
    )
    assert (
        exit_ladder_reason(
            force=False,
            atr_hit=False,
            st1_hit=False,
            entry_exit_hit=True,
            entry_exit_enabled=True,
        )
        == "entry_exit"
    )


def test_hedge_wing_ce_becomes_bear_call() -> None:
    d = hedge_wing_for_short_leg(
        option_type="CE", short_strike=24500, width_steps=1, strike_step=50
    )
    assert d["template"] == "bear_call"
    assert d["strike"] == 24550
    assert d["side"] == "BUY"
    assert d["option_type"] == "CE"


def test_hedge_wing_pe_becomes_bull_put() -> None:
    d = hedge_wing_for_short_leg(
        option_type="PE", short_strike=24500, width_steps=2, strike_step=50
    )
    assert d["template"] == "bull_put"
    assert d["strike"] == 24400
    assert d["side"] == "BUY"
    assert d["option_type"] == "PE"


def test_should_convert_sl_to_spread_decision() -> None:
    assert should_convert_sl_to_spread(
        structure="short_straddle",
        convert_enabled=True,
        exit_reason="atr_exit",
        leg_has_wing=False,
    )
    assert should_convert_sl_to_spread(
        structure="short_strangle",
        convert_enabled=True,
        exit_reason="sl_exit",
        leg_has_wing=False,
    )
    assert not should_convert_sl_to_spread(
        structure="short_straddle",
        convert_enabled=True,
        exit_reason="force_exit",
        leg_has_wing=False,
    )
    assert not should_convert_sl_to_spread(
        structure="bull_put",
        convert_enabled=True,
        exit_reason="atr_exit",
        leg_has_wing=False,
    )
    assert not should_convert_sl_to_spread(
        structure="short_straddle",
        convert_enabled=True,
        exit_reason="atr_exit",
        leg_has_wing=True,
    )
    assert not should_convert_sl_to_spread(
        structure="short_straddle",
        convert_enabled=False,
        exit_reason="atr_exit",
        leg_has_wing=False,
    )


def test_convert_sl_to_spread_default_on() -> None:
    assert DEFAULT_CONFIG["convert_sl_to_spread"] is True
    assert DEFAULT_CONFIG["entry_exit_enabled"] is False
    assert DEFAULT_CONFIG["tsl_mode"] == "ATR"
    assert DEFAULT_CONFIG["tsl_value"] == 1.2
    assert DEFAULT_CONFIG["auto_structure"] is True
    assert "sideways_structure" not in DEFAULT_CONFIG
    assert "allow_dual_open" not in DEFAULT_CONFIG
    assert "short_strangle" not in SELL_STRUCTURES
    assert "short_straddle" not in SELL_STRUCTURES


def test_vertical_max_loss_credit() -> None:
    # width 50, credit 20 → max loss 30 (net_debit = -20)
    assert _max_loss_estimate("bull_put", 1, "NIFTY", -20.0) == 30.0
    assert _max_loss_estimate("bear_call", 1, "NIFTY", -15.0) == 35.0
    assert _max_loss_estimate("short_straddle", 1, "NIFTY", -100.0) is None
    assert _max_loss_estimate("short_strangle", 1, "NIFTY", -80.0) is None


def _fake_leg(underlying, expiry, strike, option_type):
    return {
        "tradingsymbol": f"{underlying}{int(strike)}{option_type}",
        "exchange": "NFO",
        "instrument_token": int(strike),
        "strike": float(strike),
        "option_type": option_type,
    }


@patch("options.spreads.get_index_spot", return_value=24512.0)
@patch("options.spreads.find_option_leg", side_effect=_fake_leg)
def test_build_legs_atm_offsets(_find, _spot) -> None:
    # NIFTY step 50 → ATM 24500
    bull = build_legs("NIFTY", "2026-07-23", "bull_put", width_steps=1, otm_offset=1, spot=24512)
    # sell ATM-1-otm = 24500 - 50 - 50 = 24400; buy 24350
    assert bull[0]["side"] == "SELL" and bull[0]["strike"] == 24400
    assert bull[1]["side"] == "BUY" and bull[1]["strike"] == 24350

    bear = build_legs("NIFTY", "2026-07-23", "bear_call", width_steps=2, otm_offset=1, spot=24512)
    # sell 24500+50+50=24600; buy +100 = 24700
    assert bear[0]["strike"] == 24600 and bear[1]["strike"] == 24700

    sd = build_legs("NIFTY", "2026-07-23", "short_straddle", width_steps=1, otm_offset=0, spot=24512)
    assert {lg["strike"] for lg in sd} == {24500.0}
    assert {lg["option_type"] for lg in sd} == {"CE", "PE"}

    sg = build_legs("NIFTY", "2026-07-23", "short_strangle", width_steps=1, otm_offset=1, spot=24512)
    strikes = {lg["option_type"]: lg["strike"] for lg in sg}
    assert strikes["CE"] == 24600  # ATM+1+otm
    assert strikes["PE"] == 24400


@patch("options.spreads.get_index_spot", return_value=24512.0)
@patch("options.spreads.find_option_leg", side_effect=_fake_leg)
def test_preview_passes_otm_offset(_find, _spot) -> None:
    prev = preview_spread(
        "NIFTY",
        "2026-07-23",
        "bear_call",
        width_steps=1,
        spot=24512,
        otm_offset=2,
        ltp_fn=lambda _e, _s: 10.0,
    )
    assert prev["otm_offset"] == 2
    assert prev["net_credit"] > 0 or prev["net_debit"] >= 0
    # sell ATM+step+2*step = 24500+50+100=24650; buy 24700
    assert prev["legs"][0]["strike"] == 24650
    assert prev["max_loss_estimate"] is not None


def test_rolling_straddle_module_untouched_signature() -> None:
    """Smoke: RS public surface still importable — Premium Book must not alter RS."""
    from execution import rolling_straddle as rs

    assert callable(rs.tick)
    assert callable(rs.start_runner)
    assert callable(rs.stop_runner)
    assert callable(rs.close_all)


def test_crude_index_options_meta() -> None:
    for u in ("CRUDEOIL", "CRUDEOILM"):
        meta = INDEX_OPTIONS[u]
        assert meta["exchange"] == "MCX"
        assert meta["strike_step"] == 50
        assert meta["lot_size"] == 1
        assert meta.get("spot_source") == "future"
        assert lot_size_for(u) == 1
    assert atm_strike(6847.0, 50) == 6850.0
    assert atm_strike(7105.0, INDEX_OPTIONS["CRUDEOILM"]["strike_step"]) == 7100.0


def test_apply_underlying_defaults_mcx_session_and_nrml() -> None:
    cfg = apply_underlying_defaults(
        {
            "underlying": "CRUDEOIL",
            "product": "MIS",
            "session_start": "09:15",
            "session_end": "15:30",
            "force_exit": "15:20",
            "entry_start": "09:20",
        },
        previous_underlying="NIFTY",
    )
    assert cfg["product"] == "NRML"
    assert cfg["session_start"] == "09:00"
    assert cfg["session_end"] == MCX_SESSION["session_end"]
    assert cfg["force_exit"] == MCX_SESSION["force_exit"]
    assert cfg["entry_start"] == MCX_SESSION["entry_start"]

    # Same underlying save must not clobber a custom force_exit / entry_start,
    # but market session stays locked.
    kept = apply_underlying_defaults(
        {**cfg, "force_exit": "22:45", "session_start": "10:00", "session_end": "20:00"},
        previous_underlying="CRUDEOIL",
    )
    assert kept["force_exit"] == "22:45"
    assert kept["session_start"] == "09:00"
    assert kept["session_end"] == "23:30"
    assert kept["product"] == "NRML"


def test_apply_underlying_defaults_back_to_nifty() -> None:
    cfg = apply_underlying_defaults(
        {
            "underlying": "NIFTY",
            "product": "NRML",
            "session_end": "23:30",
            "force_exit": "23:20",
        },
        previous_underlying="CRUDEOILM",
    )
    assert cfg["product"] == "MIS"
    assert cfg["session_end"] == "15:40"
    assert cfg["force_exit"] == "15:20"


@patch("options.spreads.get_index_spot", return_value=7085.0)
@patch("options.spreads.find_option_leg", side_effect=_fake_leg)
def test_build_legs_crudeoil_strike_step(_find, _spot) -> None:
    # CRUDEOIL step 50 → ATM 7100; bull_put sell ATM-1-otm = 7100-50-50=7000; buy 6950
    bull = build_legs(
        "CRUDEOIL",
        "2026-07-21",
        "bull_put",
        width_steps=1,
        otm_offset=1,
        spot=7085.0,
    )
    assert bull[0]["side"] == "SELL" and bull[0]["strike"] == 7000
    assert bull[1]["side"] == "BUY" and bull[1]["strike"] == 6950
    assert _max_loss_estimate("bull_put", 1, "CRUDEOIL", -20.0) == 30.0


def test_chart_token_uses_future_for_crude(monkeypatch) -> None:
    from execution import premium_book_runner as pbr

    monkeypatch.setattr(
        pbr,
        "resolve_future",
        lambda u: {"instrument_token": 999001, "tradingsymbol": f"{u}26JULFUT", "exchange": "MCX"},
    )
    monkeypatch.setattr(
        pbr,
        "resolve_underlying_index_token",
        lambda u: (_ for _ in ()).throw(RuntimeError(f"no index for {u}")),
    )
    assert pbr._chart_token_for_underlying("CRUDEOIL") == 999001
    assert pbr._chart_token_for_underlying("CRUDEOILM") == 999001


def test_mcx_underlyings_listed_for_premium_book() -> None:
    assert "CRUDEOIL" in PREMIUM_BOOK_UNDERLYINGS
    assert "CRUDEOILM" in PREMIUM_BOOK_UNDERLYINGS
    assert "NIFTY" in PREMIUM_BOOK_UNDERLYINGS


def test_buy_hold_modes_present_and_default_off() -> None:
    assert DEFAULT_CONFIG["trade_bias"] == TRADE_BIAS_SELL
    assert DEFAULT_BUY_STRUCTURE == "bull_call"
    assert "bull_call" in BUY_STRUCTURES
    assert "bear_put" in BUY_STRUCTURES
    assert "long_call" in BUY_STRUCTURES
    assert trade_bias_for_structure("bull_call") == TRADE_BIAS_BUY
    assert trade_bias_for_structure("bull_put") == TRADE_BIAS_SELL
    assert is_buy_structure("long_put")
    assert not is_buy_structure("short_straddle")
    for s in SELL_STRUCTURES:
        assert not is_buy_structure(s)


def test_save_state_can_clear_active_structure(tmp_path, monkeypatch) -> None:
    """Sit-out must persist active_structure=None (not leave a stale short_strangle)."""
    from execution import premium_book_store as store

    monkeypatch.setattr(store, "STATE_FILE", tmp_path / "pb_state.json")
    store.save_state({"active_structure": "short_strangle", "auto_structure_reason": "stale"})
    assert store.get_state()["active_structure"] == "short_strangle"
    store.save_state({"active_structure": None, "auto_structure_reason": "auto_flat_no_entry"})
    st = store.get_state()
    assert st["active_structure"] is None
    assert st["auto_structure_reason"] == "auto_flat_no_entry"


def test_pick_auto_structure_sell_book() -> None:
    above = {
        "long_ready": True,
        "long_entry": True,
        "short_ready": False,
        "short_entry": False,
        "entry_require_st1_st2": True,
        "dir1": 1,
        "dir2": 1,
    }
    below = {
        "long_ready": False,
        "long_entry": False,
        "short_ready": True,
        "short_entry": True,
        "entry_require_st1_st2": True,
        "dir1": -1,
        "dir2": -1,
    }
    flat = {
        "long_ready": False,
        "long_entry": False,
        "short_ready": False,
        "short_entry": False,
        "entry_require_st1_st2": True,
        "dir1": 0,
        "dir2": 0,
    }
    assert pick_auto_structure(above)[0] == "bull_put"
    assert pick_auto_structure(below)[0] == "bear_call"
    picked_flat, why_flat = pick_auto_structure(flat)
    assert picked_flat is None
    assert why_flat == "auto_flat_no_entry"
    conflict = {**above, "short_ready": True, "dir1": 1, "dir2": -1}
    # long_ok fails if dir disagree; short_ok fails → flat sit-out (or skip_whipsaw if both ok)
    picked, why = pick_auto_structure(conflict)
    assert picked is None
    assert why in ("auto_flat_no_entry", "skip_whipsaw", "auto_conflict_st1_st2")


def test_buy_hold_entry_direction_long_side() -> None:
    long_sig = {
        "long_ready": True,
        "long_entry": False,
        "short_ready": False,
        "short_entry": False,
        "adx_ok": True,
        "entry_require_st1_st2": True,
        "dir1": 1,
        "dir2": 1,
    }
    short_sig = {
        "long_ready": False,
        "long_entry": False,
        "short_ready": True,
        "short_entry": False,
        "adx_ok": True,
        "entry_require_st1_st2": True,
        "dir1": -1,
        "dir2": -1,
    }
    flat = {
        "long_ready": False,
        "long_entry": False,
        "short_ready": False,
        "short_entry": False,
        "adx_ok": True,
        "entry_require_st1_st2": True,
        "dir1": 1,
        "dir2": -1,
    }
    # ST1 long ready but ST2 not bullish → no entry
    st1_only_long = {**long_sig, "dir2": -1}
    assert not structure_entry_ok("bull_put", st1_only_long)[0]
    assert not structure_entry_ok("long_call", st1_only_long)[0]

    ok, why = structure_entry_ok("long_call", long_sig)
    assert ok and "st1_st2" in why
    assert structure_entry_ok("bull_call", long_sig)[0]
    assert structure_entry_ok("bull_put", long_sig)[0]
    assert not structure_entry_ok("long_call", short_sig)[0]
    assert not structure_entry_ok("bull_call", flat)[0]

    ok_put, why_put = structure_entry_ok("long_put", short_sig)
    assert ok_put and "st1_st2" in why_put
    assert structure_entry_ok("bear_put", short_sig)[0]
    assert structure_entry_ok("bear_call", short_sig)[0]
    assert not structure_entry_ok("long_put", long_sig)[0]


def test_convert_sl_not_for_buy_structures() -> None:
    assert not should_convert_sl_to_spread(
        structure="bull_call",
        convert_enabled=True,
        exit_reason="atr_exit",
        leg_has_wing=False,
    )
    assert not should_convert_sl_to_spread(
        structure="long_call",
        convert_enabled=True,
        exit_reason="sl_exit",
        leg_has_wing=False,
    )


@patch("options.spreads.get_index_spot", return_value=24512.0)
@patch("options.spreads.find_option_leg", side_effect=_fake_leg)
def test_build_legs_long_call_and_bull_call(_find, _spot) -> None:
    lc = build_legs("NIFTY", "2026-07-23", "long_call", width_steps=1, otm_offset=1, spot=24512)
    assert len(lc) == 1
    assert lc[0]["side"] == "BUY" and lc[0]["option_type"] == "CE"
    assert lc[0]["strike"] == 24550  # ATM 24500 + 50

    bc = build_legs("NIFTY", "2026-07-23", "bull_call", width_steps=1, otm_offset=0, spot=24512)
    assert bc[0]["side"] == "BUY" and bc[0]["strike"] == 24500
    assert bc[1]["side"] == "SELL" and bc[1]["strike"] == 24550


def test_revoke_buy_hold_flattens_and_switches(tmp_path, monkeypatch) -> None:
    from execution import premium_book_runner as pbr
    from execution import premium_book_store as store

    cfg_path = tmp_path / "premium_book_config.json"
    state_path = tmp_path / "premium_book_state.json"
    log_path = tmp_path / "premium_book_log.json"
    monkeypatch.setattr(store, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(store, "STATE_FILE", state_path)
    monkeypatch.setattr(store, "LOG_FILE", log_path)

    cfg_path.write_text(
        '{"underlying":"NIFTY","trade_bias":"buy_hold","structure":"bull_call",'
        '"size_mode":"lots","size_value":1,"st1_enabled":true,"expiry":"2026-07-23"}',
        encoding="utf-8",
    )
    state_path.write_text(
        '{"package":{"status":"open","structure":"bull_call","legs":[],"net_debit":12},'
        '"ce":{"status":"flat"},"pe":{"status":"flat"},"runner":"stopped"}',
        encoding="utf-8",
    )

    closed: list[str] = []

    def _fake_close(cfg, reason):
        closed.append(reason)
        store.save_state({"package": store.flat_package()})
        return store.get_state()["package"]

    monkeypatch.setattr(pbr, "_close_package", _fake_close)
    # resolve_expiry needs chain — stub via save_config path
    monkeypatch.setattr(
        "options.chain.resolve_expiry",
        lambda u, e=None: e or "2026-07-23",
    )
    monkeypatch.setattr(
        "options.chain.nearest_expiry",
        lambda u: "2026-07-23",
    )

    out = pbr.revoke_buy_hold()
    assert out["ok"] is True
    assert out["closed_package"] is True
    assert closed == ["revoke_buy_hold"]
    assert out["config"]["trade_bias"] == TRADE_BIAS_SELL
    assert out["config"]["structure"] == DEFAULT_SELL_STRUCTURE
    assert store.get_state()["package"]["status"] == "flat"


def test_close_short_leg_reconciles_when_disarmed_and_broker_flat(tmp_path, monkeypatch) -> None:
    """Manual Kite flatten + DISARM must clear local CE without sending an order."""
    from broker.base import Broker, OrderRequest, OrderResult
    from execution import premium_book_runner as pbr
    from execution import premium_book_store as store

    cfg_path = tmp_path / "premium_book_config.json"
    state_path = tmp_path / "premium_book_state.json"
    log_path = tmp_path / "premium_book_log.json"
    monkeypatch.setattr(store, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(store, "STATE_FILE", state_path)
    monkeypatch.setattr(store, "LOG_FILE", log_path)

    cfg_path.write_text(
        '{"underlying":"CRUDEOILM","product":"NRML","structure":"bear_call",'
        '"size_mode":"lots","size_value":1}',
        encoding="utf-8",
    )
    state_path.write_text(
        '{"package":{"status":"flat"},"pe":{"status":"flat"},'
        '"ce":{"status":"open","tradingsymbol":"CRUDEOILM26AUG8000CE",'
        '"exchange":"MCX","strike":8000,"option_type":"CE","broker_qty":1,'
        '"managed_by":"algo"},"runner":"running"}',
        encoding="utf-8",
    )

    class _FlatBroker(Broker):
        def place_order(self, req: OrderRequest) -> OrderResult:
            raise AssertionError("must not place orders while DISARMED")

        def cancel_order(self, order_id: str) -> OrderResult:
            raise AssertionError("must not cancel while DISARMED")

        def positions(self):
            return []

        def orders(self):
            return []

        def ltp(self, exchange: str, tradingsymbol: str) -> float:
            return 100.0

    monkeypatch.setattr(pbr, "_broker", lambda: _FlatBroker())
    monkeypatch.setattr(
        pbr, "get_arm_state", lambda: {"mode": "live", "armed": False}
    )

    out = pbr._close_short_leg("ce", store.get_config(), "atr_exit")
    assert out["status"] == "flat"
    assert store.get_state()["ce"]["status"] == "flat"
    events = [row["event"] for row in store.get_log(20)]
    assert "ce_exit_reconciled_broker_flat" in events
    assert "ce_exit_blocked_disarm" not in events
