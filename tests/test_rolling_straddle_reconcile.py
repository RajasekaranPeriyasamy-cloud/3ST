"""Rolling straddle broker reconciliation and exit-side helpers."""

from __future__ import annotations

from broker.base import Broker
from execution import rolling_straddle as rs
from execution.rolling_straddle_store import reset_daily_state_if_needed


class _FakeBroker(Broker):
    def __init__(self, positions: list[dict], orders: list[dict] | None = None):
        self._positions = positions
        self._orders = orders or []

    def place_order(self, req):
        raise NotImplementedError

    def cancel_order(self, order_id: str):
        raise NotImplementedError

    def positions(self):
        return self._positions

    def orders(self):
        return self._orders

    def ltp(self, exchange: str, tradingsymbol: str) -> float:
        return 100.0


def test_close_transaction_for_qty():
    assert rs._close_transaction_for_qty(65) == "SELL"
    assert rs._close_transaction_for_qty(-65) == "BUY"
    assert rs._close_transaction_for_qty(0) is None


def test_reconcile_reports_manual_kite_position_as_orphan(monkeypatch):
    broker = _FakeBroker(
        [
            {
                "exchange": "NFO",
                "tradingsymbol": "NIFTY2671424250CE",
                "quantity": -390,
                "average_price": 80.6,
            }
        ]
    )
    monkeypatch.setattr(rs, "get_arm_state", lambda: {"mode": "live"})
    cfg = {"underlying": "NIFTY"}
    state = {
        "ce": {"status": "flat"},
        "pe": {"status": "flat"},
    }
    patches, mismatches, orphans = rs._reconcile_broker_legs(cfg, state, broker)
    assert patches == {}
    assert mismatches == []
    assert len(orphans) == 1
    assert orphans[0]["leg_key"] == "ce"
    assert orphans[0]["has_3st_order"] is False


def test_reconcile_updates_algo_managed_leg_qty(monkeypatch):
    broker = _FakeBroker(
        [
            {
                "exchange": "NFO",
                "tradingsymbol": "NIFTY2671424250CE",
                "quantity": 65,
                "average_price": 80.6,
                "last_price": 81.2,
            }
        ]
    )
    monkeypatch.setattr(rs, "get_arm_state", lambda: {"mode": "live"})
    cfg = {"underlying": "NIFTY"}
    state = {
        "ce": {
            "status": "open",
            "managed_by": "algo",
            "tradingsymbol": "NIFTY2671424250CE",
            "exchange": "NFO",
            "entry_at": "2026-07-13T10:00:00",
            "last_action": "long_entry",
        },
        "pe": {"status": "flat"},
    }
    patches, mismatches, orphans = rs._reconcile_broker_legs(cfg, state, broker)
    assert mismatches == []
    assert orphans == []
    assert patches["ce"]["broker_qty"] == 65
    assert patches["ce"]["status"] == "open"
    assert patches["ce"]["entry_price"] == 80.6
    assert patches["ce"]["broker_average_price"] == 80.6
    assert patches["ce"]["ltp"] == 81.2


def test_reconcile_preserves_existing_entry_price(monkeypatch):
    broker = _FakeBroker(
        [
            {
                "exchange": "MCX",
                "tradingsymbol": "CRUDEOILM26AUG8650CE",
                "quantity": -2,
                "average_price": 623.95,
                "last_price": 635.0,
            }
        ]
    )
    monkeypatch.setattr(rs, "get_arm_state", lambda: {"mode": "live"})
    cfg = {"underlying": "CRUDEOILM"}
    state = {
        "ce": {
            "status": "open",
            "managed_by": "algo",
            "tradingsymbol": "CRUDEOILM26AUG8650CE",
            "exchange": "MCX",
            "entry_price": 620.0,
            "entry_at": "2026-07-24T10:00:00",
            "last_action": "short_entry",
        },
        "pe": {"status": "flat"},
    }
    patches, _, _ = rs._reconcile_broker_legs(cfg, state, broker)
    assert patches["ce"]["entry_price"] == 620.0
    assert patches["ce"]["broker_average_price"] == 623.95


def test_short_entry_exit_uses_kite_avg_when_entry_missing():
    cfg = {"entry_exit_enabled": True, "exit_on_bar_close_only": True, "tsl_mode": "Off"}
    leg = {
        "status": "open",
        "managed_by": "algo",
        "entry_side": "SELL",
        "broker_average_price": 623.95,
        "ltp": 635.0,
    }
    signals = {"close": 625.2, "long_zone_exit": False, "short_zone_exit": False, "ts": "2026-07-24 20:05:00"}
    should, reason = rs._should_exit_leg(cfg, signals, leg, force=False, leg_key="ce")
    assert should is True
    assert reason == "entry_exit"


def test_effective_entry_price_prefers_fill_then_avg():
    assert rs._effective_entry_price({"entry_price": 100.0, "broker_average_price": 90.0}) == 100.0
    assert rs._effective_entry_price({"broker_average_price": 623.95}) == 623.95
    assert rs._effective_entry_price({}) is None


def test_reconcile_restores_flat_leg_from_3st_broker_order(monkeypatch):
    broker = _FakeBroker(
        [
            {
                "exchange": "MCX",
                "tradingsymbol": "CRUDEOILM26JUL7050PE",
                "quantity": -1,
                "average_price": 180.05,
            }
        ],
        orders=[
            {
                "exchange": "MCX",
                "tradingsymbol": "CRUDEOILM26JUL7050PE",
                "tag": "3ST-PE-20260713-entr",
                "status": "COMPLETE",
            }
        ],
    )
    monkeypatch.setattr(rs, "get_arm_state", lambda: {"mode": "live"})
    cfg = {"underlying": "CRUDEOILM"}
    state = {
        "ce": {"status": "flat"},
        "pe": {"status": "flat"},
    }
    patches, mismatches, orphans = rs._reconcile_broker_legs(cfg, state, broker)
    assert mismatches == []
    assert orphans == []
    assert patches["pe"]["status"] == "open"
    assert patches["pe"]["tradingsymbol"] == "CRUDEOILM26JUL7050PE"
    assert patches["pe"]["broker_qty"] == -1
    assert patches["pe"]["strike"] == 7050.0
    assert patches["pe"]["last_action"] == "reconcile_restored"
    assert patches["pe"]["entry_price"] == 180.05
    assert patches["pe"]["broker_average_price"] == 180.05


def test_reconcile_flattens_algo_leg_when_broker_closed(monkeypatch):
    broker = _FakeBroker([])
    monkeypatch.setattr(rs, "get_arm_state", lambda: {"mode": "live"})
    logged: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(rs, "append_log", lambda event, detail="", extra=None: logged.append((event, detail, extra or {})))
    cfg = {"underlying": "CRUDEOILM"}
    state = {
        "ce": {
            "status": "open",
            "managed_by": "algo",
            "tradingsymbol": "CRUDEOILM26JUL7150CE",
            "exchange": "MCX",
            "strike": 7150.0,
            "entry_price": 179.95,
            "entry_at": "2026-07-13T21:04:05",
            "entry_order_id": "2076691641883729920",
            "entries_today": 1,
            "broker_qty": 0,
        },
        "pe": {"status": "flat"},
    }
    patches, mismatches, orphans = rs._reconcile_broker_legs(cfg, state, broker)
    assert patches["ce"]["status"] == "flat"
    assert patches["ce"]["last_action"] == "broker_sync_flat"
    assert patches["ce"]["tradingsymbol"] is None
    assert patches["ce"]["broker_qty"] is None
    assert orphans == []
    assert any("synced flat" in m for m in mismatches)
    assert logged and logged[0][0] == "ce_broker_sync"


def test_reset_daily_state_null_session_preserves_legs(tmp_path, monkeypatch):
    from execution import rolling_straddle_store as store

    state_file = tmp_path / "rolling_straddle_state.json"
    monkeypatch.setattr(store, "STATE_FILE", state_file)
    state_file.write_text(
        '{"session_date": null, "runner": "running", "ce": {"status": "open", "managed_by": "algo", "tradingsymbol": "NIFTY2671424100CE"}, "pe": {"status": "flat"}}',
        encoding="utf-8",
    )
    out = reset_daily_state_if_needed("2026-07-14")
    assert out["session_date"] == "2026-07-14"
    assert out["ce"]["status"] == "open"
    assert out["ce"]["tradingsymbol"] == "NIFTY2671424100CE"


def test_unlink_leg_keeps_broker_flat_locally(monkeypatch, tmp_path):
    from execution import rolling_straddle_store as store

    state_file = tmp_path / "rolling_straddle_state.json"
    log_file = tmp_path / "rolling_straddle_log.json"
    monkeypatch.setattr(store, "STATE_FILE", state_file)
    monkeypatch.setattr(store, "LOG_FILE", log_file)
    state_file.write_text(
        '{"session_date": "2026-07-14", "ce": {"status": "open", "managed_by": "algo", "tradingsymbol": "NIFTY2671424100CE", "exchange": "NFO", "entries_today": 2}, "pe": {"status": "flat"}}',
        encoding="utf-8",
    )
    logged: list[str] = []
    monkeypatch.setattr(rs, "append_log", lambda event, detail="", extra=None: logged.append(event))
    result = rs.unlink_leg("ce")
    assert result["ok"] is True
    leg = result["state"]["ce"]
    assert leg["status"] == "flat"
    assert leg["tradingsymbol"] is None
    assert leg["entries_today"] == 2
    assert "ce_unlinked" in logged
