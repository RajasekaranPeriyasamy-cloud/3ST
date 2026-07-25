"""Position Ledger — CRUD lifecycle + broker reconciliation."""

from __future__ import annotations

import pytest

from execution import position_ledger as pl
from execution.signal_bus import SignalIntent


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    """Every test gets its own empty ledger file — never touches the real one."""
    monkeypatch.setattr(pl, "LEDGER_FILE", tmp_path / "position_ledger.json")
    monkeypatch.setattr(pl, "_LEDGER", {})


def _enter_intent(**overrides) -> SignalIntent:
    base = dict(
        owner="rolling_straddle",
        instance_id="rs-nifty-2026-07-25",
        leg_key="ce",
        kind="enter",
        exchange="NFO",
        tradingsymbol="NIFTY26JUL24000CE",
        product="MIS",
        target_qty=-75,
        reason="short_ready",
    )
    base.update(overrides)
    return SignalIntent(**base)


def test_upsert_pending_then_open_then_closed_round_trip():
    intent = _enter_intent()
    row = pl.upsert_pending(intent)
    assert row["status"] == "pending"
    assert row["leg_id"] == "rolling_straddle:rs-nifty-2026-07-25:ce"
    assert row["entry_tag"] == intent.tag()

    opened = pl.mark_open(intent.leg_id, quantity=-75, entry_price=123.4, order_id="OID-1")
    assert opened["status"] == "open"
    assert opened["quantity"] == -75
    assert opened["side"] == "short"
    assert opened["opened_at"] is not None

    closed = pl.mark_closed(intent.leg_id, exit_price=110.0, order_id="OID-2", reason="zone_exit")
    assert closed["status"] == "closed"
    assert closed["quantity"] == 0
    assert closed["reason"] == "zone_exit"
    assert closed["closed_at"] is not None


def test_ledger_persists_across_reload(tmp_path, monkeypatch):
    ledger_file = tmp_path / "position_ledger.json"
    monkeypatch.setattr(pl, "LEDGER_FILE", ledger_file)
    monkeypatch.setattr(pl, "_LEDGER", {})

    intent = _enter_intent()
    pl.upsert_pending(intent)
    pl.mark_open(intent.leg_id, quantity=-75, entry_price=100.0, order_id="OID-1")
    assert ledger_file.exists()

    # Simulate an API restart: clear in-memory state, reload from disk.
    monkeypatch.setattr(pl, "_LEDGER", {})
    pl._load()
    row = pl.get(intent.leg_id)
    assert row is not None
    assert row["status"] == "open"
    assert row["quantity"] == -75


def test_has_open_tag_blocks_duplicate():
    intent = _enter_intent()
    assert not pl.has_open_tag(intent.tag())
    pl.upsert_pending(intent)
    assert pl.has_open_tag(intent.tag())

    pl.mark_closed(intent.leg_id)
    assert not pl.has_open_tag(intent.tag())


def test_mark_open_without_pending_raises():
    with pytest.raises(KeyError):
        pl.mark_open("nobody:here:ce", quantity=1, entry_price=1.0, order_id="X")


def test_list_all_filters_by_owner_and_status():
    ce = _enter_intent(leg_key="ce")
    pe = _enter_intent(leg_key="pe", tradingsymbol="NIFTY26JUL24000PE", target_qty=75)
    pl.upsert_pending(ce)
    pl.upsert_pending(pe)
    pl.mark_open(ce.leg_id, quantity=-75, entry_price=100.0, order_id="OID-CE")

    open_rows = pl.list_all(status="open")
    assert len(open_rows) == 1
    assert open_rows[0]["leg_key"] == "ce"

    rs_rows = pl.list_all(owner="rolling_straddle")
    assert len(rs_rows) == 2


class _FakeBroker:
    """Minimal stand-in — reconcile only calls .positions()."""

    def __init__(self, positions=None, *, fail=False):
        self._positions = positions or []
        self._fail = fail

    def positions(self):
        if self._fail:
            raise RuntimeError("Kite unreachable")
        return self._positions


def test_reconcile_updates_drifted_qty_and_price():
    intent = _enter_intent()
    pl.upsert_pending(intent)
    pl.mark_open(intent.leg_id, quantity=-75, entry_price=100.0, order_id="OID-1")

    broker = _FakeBroker(
        [{"exchange": "NFO", "tradingsymbol": "NIFTY26JUL24000CE", "quantity": -75, "average_price": 104.5}]
    )
    report = pl.reconcile_with_broker(broker)
    assert report["ok"]
    assert report["updated"] == [{"leg_id": intent.leg_id, "entry_price": 104.5}]
    assert pl.get(intent.leg_id)["entry_price"] == 104.5


def test_reconcile_closes_stale_when_broker_flat():
    intent = _enter_intent()
    pl.upsert_pending(intent)
    pl.mark_open(intent.leg_id, quantity=-75, entry_price=100.0, order_id="OID-1")

    broker = _FakeBroker([])  # broker flat
    report = pl.reconcile_with_broker(broker)
    assert report["ok"]
    assert len(report["closed_stale"]) == 1
    assert report["closed_stale"][0]["leg_id"] == intent.leg_id
    assert pl.get(intent.leg_id)["status"] == "closed"


def test_reconcile_reports_orphan_for_untracked_broker_position():
    broker = _FakeBroker(
        [{"exchange": "NFO", "tradingsymbol": "NIFTY26JUL24000PE", "quantity": 75, "average_price": 90.0}]
    )
    report = pl.reconcile_with_broker(broker)
    assert report["ok"]
    assert len(report["orphans"]) == 1
    assert report["orphans"][0]["tradingsymbol"] == "NIFTY26JUL24000PE"


def test_reconcile_aborts_on_broker_read_failure():
    intent = _enter_intent()
    pl.upsert_pending(intent)
    pl.mark_open(intent.leg_id, quantity=-75, entry_price=100.0, order_id="OID-1")

    broker = _FakeBroker(fail=True)
    report = pl.reconcile_with_broker(broker)
    assert not report["ok"]
    assert "aborted" in report
    # Nothing changed — still open at the original price.
    row = pl.get(intent.leg_id)
    assert row["status"] == "open"
    assert row["entry_price"] == 100.0
