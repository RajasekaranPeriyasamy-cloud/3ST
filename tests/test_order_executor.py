"""Tests for smart-order execution primitives."""

from __future__ import annotations

import threading
import time

from broker.execution_support import (
    acquire_symbol_lock,
    plan_order_to_target,
)
from broker.paper_broker import PaperBroker
from execution.order_executor import place_leg_to_target


def test_plan_order_to_target_short_entry():
    plan = plan_order_to_target(0, -65)
    assert plan.transaction_type == "SELL"
    assert plan.quantity == 65
    assert not plan.noop


def test_plan_order_to_target_flat_exit_from_short():
    plan = plan_order_to_target(-65, 0)
    assert plan.transaction_type == "BUY"
    assert plan.quantity == 65


def test_plan_order_to_target_noop_when_already_at_target():
    plan = plan_order_to_target(-65, -65)
    assert plan.noop
    assert plan.transaction_type is None


def test_place_leg_to_target_paper_short_then_flat(tmp_path, monkeypatch):
    monkeypatch.setattr("broker.paper_broker._PAPER_FILE", tmp_path / "paper.json")
    broker = PaperBroker()
    broker.set_ltp("NFO", "NIFTY26JUL24500PE", 120.0)
    leg = {
        "tradingsymbol": "NIFTY26JUL24500PE",
        "exchange": "NFO",
        "quantity": 65,
    }

    entry = place_leg_to_target(broker, leg, target_qty=-65, product="MIS", tag="3ST-TEST")
    assert entry["ok"]
    assert broker.net_qty("NFO", "NIFTY26JUL24500PE", "MIS") == -65

    again = place_leg_to_target(broker, leg, target_qty=-65, product="MIS", tag="3ST-TEST")
    assert again["ok"]
    assert again["raw"]["noop"] is True

    exit_res = place_leg_to_target(broker, leg, target_qty=0, product="MIS", tag="3ST-TEST-EXIT")
    assert exit_res["ok"]
    assert broker.net_qty("NFO", "NIFTY26JUL24500PE", "MIS") == 0


def test_symbol_lock_serializes_same_instrument():
    order: list[int] = []
    lock = acquire_symbol_lock("NFO", "NIFTY26JUL24500PE", "MIS")

    def worker(n: int) -> None:
        with lock:
            order.append(n)
            time.sleep(0.05)

    t1 = threading.Thread(target=worker, args=(1,))
    t2 = threading.Thread(target=worker, args=(2,))
    t1.start()
    time.sleep(0.01)
    t2.start()
    t1.join()
    t2.join()
    assert order == [1, 2]
