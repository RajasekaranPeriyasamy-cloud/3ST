"""Tests for broker-as-source-of-truth reconciliation (NAUTILUS_IMPROVEMENTS #1)."""

from __future__ import annotations

from typing import Any

import pytest

from broker.base import Broker, OrderRequest, OrderResult
from execution import reconcile


class FakeBroker(Broker):
    def __init__(self, positions: list[dict[str, Any]], orders: list[dict[str, Any]]):
        self._positions = positions
        self._orders = orders

    def place_order(self, req: OrderRequest) -> OrderResult:  # pragma: no cover - unused
        raise AssertionError("reconcile must never place orders")

    def cancel_order(self, order_id: str) -> OrderResult:  # pragma: no cover - unused
        raise AssertionError("reconcile must never cancel orders")

    def positions(self) -> list[dict[str, Any]]:
        return self._positions

    def orders(self) -> list[dict[str, Any]]:
        return self._orders

    def ltp(self, exchange: str, tradingsymbol: str) -> float:  # pragma: no cover - unused
        return 0.0


def _pos(sym: str, qty: int, avg: float = 110.0, exch: str = "NFO") -> dict[str, Any]:
    return {
        "tradingsymbol": sym,
        "exchange": exch,
        "quantity": qty,
        "average_price": avg,
        "last_price": avg,
        "pnl": 0.0,
        "product": "NRML",
    }


def _order(sym: str, exch: str = "NFO", tag: str = "3ST-CE-x-entry") -> dict[str, Any]:
    return {
        "order_id": "1",
        "tradingsymbol": sym,
        "exchange": exch,
        "transaction_type": "SELL",
        "quantity": 75,
        "product": "NRML",
        "order_type": "MARKET",
        "status": "OPEN",
        "tag": tag,
    }


def _item(sym: str, *, trade_mode: str = "live", exch: str = "NFO", **over: Any) -> dict[str, Any]:
    base = {"id": f"item-{sym}", "status": "active", "exchange": exch, "tradingsymbol": sym, "trade_mode": trade_mode}
    base.update(over)
    return base


@pytest.fixture
def capture(monkeypatch):
    calls: dict[str, list] = {"update": [], "add": []}

    def fake_update(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        calls["update"].append((item_id, patch))
        return {"id": item_id, **patch}

    def fake_add(payload: dict[str, Any]) -> dict[str, Any]:
        item = {"id": f"new-{len(calls['add'])}", **payload}
        calls["add"].append(payload)
        return item

    monkeypatch.setattr(reconcile, "update_item", fake_update)
    monkeypatch.setattr(reconcile, "add_item", fake_add)
    return calls


def test_closes_stale_when_broker_flat(capture) -> None:
    broker = FakeBroker(positions=[], orders=[])
    report = reconcile.reconcile_from_broker(broker, items=[_item("NIFTY25JUL24000CE")], global_mode="live")

    assert [c["id"] for c in report["closed_stale"]] == ["item-NIFTY25JUL24000CE"]
    assert capture["update"][0][0] == "item-NIFTY25JUL24000CE"
    assert capture["update"][0][1]["status"] == "closed"
    assert capture["update"][0][1]["exit_reason"].startswith("reconcile")


def test_matched_position_refreshes_entry(capture) -> None:
    broker = FakeBroker(positions=[_pos("NIFTY25JUL24000CE", -75, avg=110.0)], orders=[])
    item = _item("NIFTY25JUL24000CE", entry_qty=50, entry_price=100.0)
    report = reconcile.reconcile_from_broker(broker, items=[item], global_mode="live")

    assert report["matched"] and report["matched"][0]["quantity"] == -75
    assert not report["closed_stale"]
    patch = dict(capture["update"][0][1])
    assert patch["entry_qty"] == 75
    assert patch["entry_price"] == 110.0


def test_pending_order_keeps_active(capture) -> None:
    broker = FakeBroker(positions=[], orders=[_order("NIFTY25JUL24000CE")])
    report = reconcile.reconcile_from_broker(broker, items=[_item("NIFTY25JUL24000CE")], global_mode="live")

    assert report["pending_orders"] and report["pending_orders"][0]["key"] == "NFO:NIFTY25JUL24000CE"
    assert not report["closed_stale"]
    assert not capture["update"]


def test_paper_items_never_closed(capture) -> None:
    broker = FakeBroker(positions=[], orders=[])
    report = reconcile.reconcile_from_broker(
        broker, items=[_item("NIFTY25JUL24000CE", trade_mode="paper")], global_mode="live"
    )
    assert not report["closed_stale"]
    assert not capture["update"]


def test_orphan_reported_not_adopted_by_default(capture) -> None:
    broker = FakeBroker(positions=[_pos("BANKNIFTY25JUL50000PE", -30)], orders=[])
    report = reconcile.reconcile_from_broker(broker, items=[], global_mode="live")
    assert len(report["orphan_positions"]) == 1
    assert report["orphan_positions"][0]["direction"] == "SHORT"
    assert not report["adopted"]
    assert not capture["add"]


def test_orphan_adopted_when_enabled(capture) -> None:
    broker = FakeBroker(positions=[_pos("BANKNIFTY25JUL50000PE", -30, avg=95.0)], orders=[])
    report = reconcile.reconcile_from_broker(broker, items=[], global_mode="live", adopt_orphans=True)
    assert len(report["adopted"]) == 1
    assert capture["add"], "add_item should be called to adopt the orphan"
    # adopted item is promoted to active with a signal derived from position side
    promote = capture["update"][0][1]
    assert promote["status"] == "active"
    assert promote["signal"] == "short"
    assert promote["entry_qty"] == 30


def test_dry_run_does_not_mutate(capture) -> None:
    broker = FakeBroker(positions=[], orders=[])
    report = reconcile.reconcile_from_broker(
        broker, items=[_item("NIFTY25JUL24000CE")], global_mode="live", apply_changes=False
    )
    assert report["closed_stale"]  # still reported
    assert not capture["update"]  # but nothing written


def test_global_paper_mode_skips_untagged_items(capture) -> None:
    broker = FakeBroker(positions=[], orders=[])
    # item has no trade_mode; global paper -> treated as paper -> not closed
    item = {"id": "x", "status": "active", "exchange": "NFO", "tradingsymbol": "NIFTY25JUL24000CE"}
    report = reconcile.reconcile_from_broker(broker, items=[item], global_mode="paper")
    assert not report["closed_stale"]


class PositionsFailBroker(FakeBroker):
    def positions(self) -> list:
        raise RuntimeError("Incorrect `api_key` or `access_token`.")


class OrdersFailBroker(FakeBroker):
    def orders(self) -> list:
        raise RuntimeError("network blip")


def test_positions_read_failure_aborts_without_closing(capture) -> None:
    broker = PositionsFailBroker(positions=[], orders=[])
    report = reconcile.reconcile_from_broker(
        broker, items=[_item("NIFTY25JUL24000CE")], global_mode="live"
    )
    # A broker read failure must NOT be read as "broker flat".
    assert report["ok"] is False
    assert "aborted" in report
    assert not report["closed_stale"]
    assert not capture["update"]


def test_orders_read_failure_skips_close(capture) -> None:
    broker = OrdersFailBroker(positions=[], orders=[])
    report = reconcile.reconcile_from_broker(
        broker, items=[_item("NIFTY25JUL24000CE")], global_mode="live"
    )
    assert report["ok"] is False
    assert not report["closed_stale"]
    assert report["close_skipped"] and report["close_skipped"][0]["reason"] == "orders unavailable"
    assert not capture["update"]
