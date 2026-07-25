"""Regression test for place_watchlist_entry.

``patch["kite_product"]`` used to be computed as
``_product_for_item({**item, **patch})`` while ``patch`` was still being
built — a self-reference that raises ``UnboundLocalError`` on every single
call (Python evaluates a dict literal's values before binding the name).
Found via ``ruff check . --select F821`` (undefined-name). This means every
watchlist activation — manual live BUY/SELL and the taskbar's "ship"/"execute"
action — raised *after* the broker order was already placed, so the order
could fire while the local record never flipped to "active". Fixed by
reusing the ``product`` value already computed (and already used to place
every leg) earlier in the function.
"""

from __future__ import annotations

import pytest

from execution import watchlist_activation as wa
from watchlist_store import add_item


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    monkeypatch.setattr("watchlist_store.WATCHLIST_FILE", tmp_path / "watchlist.json")
    monkeypatch.setattr("broker.paper_broker._PAPER_FILE", tmp_path / "paper.json")
    # Force paper mode regardless of the real data/arm_state.json on disk.
    monkeypatch.setattr(wa, "get_arm_state", lambda: {"mode": "paper", "armed": False})


def _triggered_item(**overrides):
    payload = dict(
        status="waiting",
        signal="long",
        tradingsymbol="NIFTY26JUL24500CE",
        exchange="NFO",
        lot_size=75,
        entry_side="BUY",
    )
    payload.update(overrides)
    item = add_item(payload)
    from watchlist_store import update_item

    return update_item(item["id"], {"status": "triggered"})


def test_place_watchlist_entry_does_not_raise_and_sets_kite_product():
    item = _triggered_item()

    result = wa.place_watchlist_entry(item)

    assert result["status"] == "active"
    assert result["kite_product"] == "MIS"  # default system_mode is Intraday
    assert result["entry_qty"] == 75
    assert result["order_ids"]


def test_place_watchlist_entry_respects_explicit_product_type():
    item = _triggered_item(product_type="NRML")

    result = wa.place_watchlist_entry(item)

    assert result["kite_product"] == "NRML"


def test_activate_watchlist_item_end_to_end():
    item = _triggered_item()
    result = wa.activate_watchlist_item(item["id"])
    assert result["status"] == "active"
