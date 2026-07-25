"""Order Router — duplicate-tag guard + ledger bookkeeping on top of order_executor."""

from __future__ import annotations

import pytest

from broker.base import OrderResult
from broker.paper_broker import PaperBroker
from execution import order_router as router
from execution import position_ledger as pl
from execution.signal_bus import SignalIntent


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "LEDGER_FILE", tmp_path / "position_ledger.json")
    monkeypatch.setattr(pl, "_LEDGER", {})


@pytest.fixture
def paper_broker(tmp_path, monkeypatch):
    monkeypatch.setattr("broker.paper_broker._PAPER_FILE", tmp_path / "paper.json")
    broker = PaperBroker()
    broker.set_ltp("NFO", "NIFTY26JUL24500PE", 120.0)
    return broker


def _enter_intent(**overrides) -> SignalIntent:
    base = dict(
        owner="rolling_straddle",
        instance_id="rs-nifty-2026-07-25",
        leg_key="pe",
        kind="enter",
        exchange="NFO",
        tradingsymbol="NIFTY26JUL24500PE",
        product="MIS",
        target_qty=-65,
        reason="short_ready",
    )
    base.update(overrides)
    return SignalIntent(**base)


def test_submit_intent_enter_places_order_and_marks_open(paper_broker):
    intent = _enter_intent()
    result = router.submit_intent(paper_broker, intent)

    assert result["ok"]
    assert result["leg_id"] == intent.leg_id
    assert result["ledger"]["status"] == "open"
    assert result["ledger"]["quantity"] == -65
    assert paper_broker.net_qty("NFO", "NIFTY26JUL24500PE", "MIS") == -65


def test_submit_intent_duplicate_tag_blocked(paper_broker):
    intent = _enter_intent()
    first = router.submit_intent(paper_broker, intent)
    assert first["ok"]

    second = router.submit_intent(paper_broker, intent)
    assert not second["ok"]
    assert second["raw"]["duplicate"] is True
    # Broker position unchanged — the duplicate never reached place_order.
    assert paper_broker.net_qty("NFO", "NIFTY26JUL24500PE", "MIS") == -65


def test_submit_intent_exit_marks_closed(paper_broker):
    entry = _enter_intent()
    router.submit_intent(paper_broker, entry)

    exit_intent = _enter_intent(kind="exit", target_qty=0)
    result = router.submit_intent(paper_broker, exit_intent)

    assert result["ok"]
    assert result["ledger"]["status"] == "closed"
    assert paper_broker.net_qty("NFO", "NIFTY26JUL24500PE", "MIS") == 0


class _RejectingBroker:
    """Broker whose place_order always fails — exercises the mark_error path."""

    def positions(self):
        return []

    def orders(self):
        return []

    def place_order(self, req):
        return OrderResult(ok=False, order_id=None, message="Kite: insufficient margin")

    def net_qty(self, exchange, tradingsymbol, product):
        return 0


def test_submit_intent_records_error_when_broker_rejects():
    intent = _enter_intent()
    result = router.submit_intent(_RejectingBroker(), intent)

    assert not result["ok"]
    assert "insufficient margin" in result["message"]
    assert result["ledger"]["status"] == "error"
    # A rejected order must not block a retry — no duplicate-tag lockout.
    assert not pl.has_open_tag(intent.tag())
