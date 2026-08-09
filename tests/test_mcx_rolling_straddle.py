"""MCX commodity support for rolling straddle."""

from __future__ import annotations

import json

import pytest

from execution.rolling_straddle_store import apply_underlying_defaults, lot_size_for, save_config
from options.chain import atm_strike, list_expiries, nearest_expiry, resolve_expiry


def test_crudeoil_expiries_from_instruments_cache():
    expiries = list_expiries("CRUDEOIL")
    assert expiries
    assert all(len(e) == 10 for e in expiries)


def test_naturalgas_strike_step():
    assert atm_strike(312.0, 5) == 310.0
    assert atm_strike(6847.0, 50) == 6850.0


def test_mcx_lot_sizes():
    assert lot_size_for("CRUDEOIL") == 1
    assert lot_size_for("NATURALGAS") == 1


def test_apply_underlying_defaults_forces_nrml_only():
    cfg = apply_underlying_defaults({
        "underlying": "CRUDEOIL",
        "product": "MIS",
        "entry_start": "09:20",
        "force_exit": "23:20",
        "session_end": "23:30",
    })
    assert cfg["product"] == "NRML"
    assert cfg["entry_start"] == "09:20"
    assert cfg["force_exit"] == "23:20"
    assert cfg["session_end"] == "23:30"


def test_crudeoil_mini_expiries():
    expiries = list_expiries("CRUDEOILM")
    assert expiries


def test_crudeoil_mini_nearest_expiry_not_jul14():
    expiries = list_expiries("CRUDEOILM")
    nearest = nearest_expiry("CRUDEOILM")
    assert nearest
    assert nearest in expiries
    assert nearest != "2026-07-14"


def test_resolve_expiry_rejects_invalid_date():
    valid = nearest_expiry("CRUDEOILM")
    assert valid
    assert resolve_expiry("CRUDEOILM", "2026-07-14") == valid
    assert resolve_expiry("CRUDEOILM", valid) == valid


def _rs_config(tmp_path, monkeypatch, body: str):
    from execution import rolling_straddle_store as store

    cfg_path = tmp_path / "rolling_straddle_config.json"
    monkeypatch.setattr(store, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(store, "LOG_FILE", tmp_path / "log.json")
    cfg_path.write_text(body, encoding="utf-8")
    return store


def test_resolve_expiry_rejects_listed_but_past_expiry(monkeypatch):
    """Stale instruments cache keeps dead contracts listed — must not resolve onto one."""
    from datetime import date, timedelta

    from options import chain

    today = date.today()
    past = (today - timedelta(days=7)).isoformat()
    future = (today + timedelta(days=7)).isoformat()
    monkeypatch.setattr(chain, "list_expiries", lambda _u: [past, today.isoformat(), future])

    assert chain.resolve_expiry("NIFTY", past) == today.isoformat()
    # Expiry day itself is still tradeable.
    assert chain.resolve_expiry("NIFTY", today.isoformat()) == today.isoformat()
    assert chain.resolve_expiry("NIFTY", future) == future


def test_save_config_corrects_stale_crudeoilm_expiry(tmp_path, monkeypatch):
    _rs_config(
        tmp_path,
        monkeypatch,
        '{"underlying": "CRUDEOILM", "expiry": "2026-07-14", "size_mode": "lots", "size_value": 1}',
    )
    saved = save_config({})
    assert saved["expiry"] == nearest_expiry("CRUDEOILM")
    assert saved["expiry"] != "2026-07-14"


def test_save_config_corrects_past_expiry_on_unrelated_patch(tmp_path, monkeypatch):
    """A settings save that never mentions expiry must still drop an expired contract."""
    _rs_config(
        tmp_path,
        monkeypatch,
        '{"underlying": "CRUDEOILM", "expiry": "2026-07-14", "size_mode": "lots", "size_value": 1}',
    )
    saved = save_config({"size_value": 2})
    assert saved["size_value"] == 2
    assert saved["expiry"] == nearest_expiry("CRUDEOILM")


def test_save_config_keeps_deliberate_far_month_on_unrelated_patch(tmp_path, monkeypatch):
    """Always-resolving must not yank an operator off a valid far-month contract."""
    expiries = list_expiries("CRUDEOILM")
    far = [e for e in expiries if e != nearest_expiry("CRUDEOILM")]
    if not far:
        pytest.skip("only one CRUDEOILM expiry listed")

    _rs_config(
        tmp_path,
        monkeypatch,
        json.dumps({
            "underlying": "CRUDEOILM",
            "expiry": far[-1],
            "size_mode": "lots",
            "size_value": 1,
        }),
    )
    saved = save_config({"size_value": 3})
    assert saved["expiry"] == far[-1]


def test_crude_symbols_do_not_cross_match():
    from options.chain import tradingsymbol_matches_underlying

    assert tradingsymbol_matches_underlying("CRUDEOIL26JUL6800CE", "CRUDEOIL")
    assert not tradingsymbol_matches_underlying("CRUDEOILM26JUL6800CE", "CRUDEOIL")
    assert tradingsymbol_matches_underlying("CRUDEOILM26JUL6800CE", "CRUDEOILM")


def test_resolve_tick_spot_rejects_chain_mid_glitch(monkeypatch):
    from execution import rolling_straddle as rs

    monkeypatch.setattr("options.chain.get_index_spot_detail", lambda u: (8100.0, None))
    state = {"last_spot": 7085.0}
    spot, err = rs._resolve_tick_spot("CRUDEOILM", state, 50)
    assert spot == 7085.0
    assert err is None


def test_save_state_can_clear_last_spot(tmp_path, monkeypatch):
    from execution import rolling_straddle_store as store

    state_path = tmp_path / "rolling_straddle_state.json"
    monkeypatch.setattr(store, "STATE_FILE", state_path)
    state_path.write_text('{"last_spot": 24035.15, "current_atm": 24050}', encoding="utf-8")
    store.save_state({"last_spot": None, "current_atm": None})
    saved = store.get_state()
    assert saved["last_spot"] is None
    assert saved["current_atm"] is None


def test_save_config_underlying_change_resets_spot_state(tmp_path, monkeypatch):
    from execution import rolling_straddle_store as store

    cfg_path = tmp_path / "rolling_straddle_config.json"
    state_path = tmp_path / "rolling_straddle_state.json"
    monkeypatch.setattr(store, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(store, "STATE_FILE", state_path)
    monkeypatch.setattr(store, "LOG_FILE", tmp_path / "log.json")
    cfg_path.write_text(
        '{"underlying": "NIFTY", "expiry": "2026-07-14", "size_mode": "lots", "size_value": 1}',
        encoding="utf-8",
    )
    state_path.write_text(
        '{"last_spot": 24035.15, "current_atm": 24050, "state_underlying": "NIFTY"}',
        encoding="utf-8",
    )
    saved = save_config({"underlying": "CRUDEOIL", "expiry": "2026-07-16"})
    assert saved["underlying"] == "CRUDEOIL"
    state = store.get_state()
    assert state["last_spot"] is None
    assert state["current_atm"] is None
    assert state["state_underlying"] == "CRUDEOIL"


def test_ensure_state_underlying_heals_legacy_nifty_spot_on_crude(monkeypatch):
    from execution import rolling_straddle as rs

    monkeypatch.setattr(rs, "get_index_spot", lambda u: 7780.0)
    cfg = {"underlying": "CRUDEOIL"}
    state = {"last_spot": 24035.15, "current_atm": 24050}
    healed = rs._ensure_state_underlying(cfg, state)
    assert healed["last_spot"] is None
    assert healed["current_atm"] is None
    assert healed["state_underlying"] == "CRUDEOIL"


def test_ensure_state_underlying_heals_nifty_spot_when_marker_already_crude(monkeypatch):
    """state_underlying=CRUDEOIL but last_spot still NIFTY — must reset."""
    from execution import rolling_straddle as rs

    monkeypatch.setattr(rs, "get_index_spot", lambda u: None)
    cfg = {"underlying": "CRUDEOIL"}
    state = {"last_spot": 24035.15, "current_atm": 24050, "state_underlying": "CRUDEOIL"}
    healed = rs._ensure_state_underlying(cfg, state)
    assert healed["last_spot"] is None
    assert healed["current_atm"] is None


def test_spot_state_stale_ignores_glitchy_jump_not_legacy_clear(monkeypatch):
    """Bad 8100 fetch must not mark state stale when last_spot 7085 is valid CRUDEOIL."""
    from execution import rolling_straddle as rs

    monkeypatch.setattr(rs, "get_index_spot", lambda u: 8100.0)
    cfg = {"underlying": "CRUDEOIL"}
    state = {"last_spot": 7085.0, "state_underlying": None}
    assert rs._spot_state_stale(cfg, state) is False


def test_resolve_tick_spot_uses_last_when_fetch_fails(monkeypatch):
    from execution import rolling_straddle as rs

    monkeypatch.setattr("options.chain.get_index_spot_detail", lambda u: (None, "dns"))
    spot, err = rs._resolve_tick_spot("CRUDEOILM", {"last_spot": 7070.0}, 50)
    assert spot == 7070.0
    assert err is None


def test_save_config_clears_crude_spot_when_nifty_marker_matches(tmp_path, monkeypatch):
    """NIFTY config with crude-scale cached spot must reset even if underlying unchanged."""
    from execution import rolling_straddle_store as store

    cfg_path = tmp_path / "rolling_straddle_config.json"
    state_path = tmp_path / "rolling_straddle_state.json"
    monkeypatch.setattr(store, "CONFIG_FILE", cfg_path)
    monkeypatch.setattr(store, "STATE_FILE", state_path)
    monkeypatch.setattr(store, "LOG_FILE", tmp_path / "log.json")
    cfg_path.write_text(
        '{"underlying": "NIFTY", "expiry": "2026-07-21", "size_mode": "lots", "size_value": 5}',
        encoding="utf-8",
    )
    state_path.write_text(
        '{"last_spot": 7605.0, "current_atm": 7600, "state_underlying": "NIFTY", '
        '"ce": {"signal_strike": 7600}, "pe": {"signal_strike": 7600}}',
        encoding="utf-8",
    )
    save_config({"size_value": 5})
    state = store.get_state()
    assert state["last_spot"] is None
    assert state["current_atm"] is None
    assert state["ce"]["signal_strike"] is None
    assert state["pe"]["signal_strike"] is None


def test_status_bundle_shows_live_nifty_spot_when_cached_is_crude(monkeypatch):
    from execution import rolling_straddle as rs

    monkeypatch.setattr(rs, "get_config", lambda: {"underlying": "NIFTY"})
    monkeypatch.setattr(
        rs,
        "get_state",
        lambda: {
            "last_spot": 7605.0,
            "current_atm": 7600,
            "state_underlying": "NIFTY",
            "ce": {"signal_strike": 7600},
            "pe": {"signal_strike": 7600},
        },
    )
    monkeypatch.setattr(rs, "get_arm_state", lambda: {})
    monkeypatch.setattr(rs, "session_status", lambda: {"authenticated": True})
    monkeypatch.setattr(rs, "order_quantity_from_config", lambda _cfg: 325)
    monkeypatch.setattr(rs, "get_index_spot_detail", lambda u: (24512.5, None))
    bundle = rs.status_bundle(sync_broker=False)
    assert bundle["state"]["last_spot"] == 24512.5
    assert bundle["state"]["current_atm"] == 24500
    assert bundle["state"]["ce"]["signal_strike"] is None
    assert bundle["state"].get("spot_live_only") is True
