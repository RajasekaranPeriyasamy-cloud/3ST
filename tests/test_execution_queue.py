"""Tests for unified execution queue aggregation."""

from __future__ import annotations

from execution import execution_queue as eq


def test_build_execution_queue_aggregates_rs_active(monkeypatch):
    monkeypatch.setattr(
        eq,
        "rs_status_bundle",
        lambda: {
            "config": {"underlying": "NIFTY", "expiry": "2026-07-14", "execution_mode": "auto"},
            "state": {
                "runner": "running",
                "ce": {
                    "status": "open",
                    "tradingsymbol": "NIFTY2671424100CE",
                    "exchange": "NFO",
                    "position_side": "short",
                    "broker_qty": -65,
                    "entry_price": 39.8,
                },
                "pe": {"status": "flat"},
            },
            "orphans": [],
            "broker_mismatches": [],
        },
    )
    monkeypatch.setattr(
        eq,
        "build_active_trades_view",
        lambda: {"trades": [], "orphans": [], "mode": "paper"},
    )
    monkeypatch.setattr(eq, "get_arm_state", lambda: {"mode": "paper", "armed": False})
    monkeypatch.setattr(eq, "session_status", lambda: {"authenticated": True})

    out = eq.build_execution_queue()
    assert out["summary"]["active_count"] == 1
    assert out["active"][0]["leg_id"] == "rs:ce"
    assert out["active"][0]["qty"] == -65
    assert "close" in out["active"][0]["actions"]


def test_build_execution_queue_rs_orphans(monkeypatch):
    monkeypatch.setattr(
        eq,
        "rs_status_bundle",
        lambda: {
            "config": {"underlying": "NIFTY", "expiry": "2026-07-14"},
            "state": {"runner": "running", "ce": {"status": "flat"}, "pe": {"status": "flat"}},
            "orphans": [
                {
                    "leg_key": "pe",
                    "exchange": "NFO",
                    "tradingsymbol": "NIFTY2671424100PE",
                    "quantity": -130,
                    "average_price": 59.18,
                    "has_3st_order": True,
                }
            ],
            "broker_mismatches": [],
        },
    )
    monkeypatch.setattr(eq, "build_active_trades_view", lambda: {"trades": [], "orphans": [], "mode": "live"})
    monkeypatch.setattr(eq, "get_arm_state", lambda: {"mode": "live", "armed": True})
    monkeypatch.setattr(eq, "session_status", lambda: {"authenticated": True})

    out = eq.build_execution_queue()
    assert out["summary"]["orphan_count"] == 1
    assert out["orphans"][0]["leg_id"] == "rs:orphan:pe"
    assert out["orphans"][0]["actions"] == ["adopt", "close"]


def test_build_execution_queue_pending_confirm_mode(monkeypatch):
    monkeypatch.setattr(
        eq,
        "rs_status_bundle",
        lambda: {
            "config": {"underlying": "NIFTY", "expiry": "2026-07-14", "execution_mode": "confirm"},
            "state": {
                "runner": "running",
                "ce": {"status": "flat", "short_ready": True, "blocked": False},
                "pe": {"status": "flat"},
            },
            "orphans": [],
            "broker_mismatches": [],
        },
    )
    monkeypatch.setattr(eq, "build_active_trades_view", lambda: {"trades": [], "orphans": []})
    monkeypatch.setattr(eq, "get_arm_state", lambda: {"mode": "live", "armed": True})
    monkeypatch.setattr(eq, "session_status", lambda: {"authenticated": True})

    out = eq.build_execution_queue()
    assert out["summary"]["pending_count"] == 1
    assert out["pending"][0]["leg_id"] == "rs:pending:ce"
    assert out["pending"][0]["actions"] == ["ship", "dismiss"]


def test_queue_action_routes_rs_unlink(monkeypatch):
    called: list[str] = []

    monkeypatch.setattr(eq, "unlink_leg", lambda leg: called.append(leg) or {"ok": True})

    result = eq.queue_action("rs:ce", "unlink")
    assert called == ["ce"]
    assert result["ok"] is True


def test_queue_action_routes_rs_pending_ship(monkeypatch):
    monkeypatch.setattr(eq, "ship_leg_entry", lambda leg: {"ok": True, "leg": leg})

    result = eq.queue_action("rs:pending:pe", "ship")
    assert result["ok"] is True
