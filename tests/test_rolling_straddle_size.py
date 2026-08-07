"""Rolling straddle order size and algo ownership."""

from __future__ import annotations

import pytest

from execution.algo_ownership import leg_is_algo_managed
from execution.rolling_straddle_store import lot_size_for, order_quantity_from_config


def test_order_quantity_lots_mode():
    cfg = {"underlying": "NIFTY", "size_mode": "lots", "size_value": 2}
    assert order_quantity_from_config(cfg) == lot_size_for("NIFTY") * 2


def test_order_quantity_qty_mode():
    cfg = {"underlying": "NIFTY", "size_mode": "qty", "size_value": 130}
    assert order_quantity_from_config(cfg) == 130


def test_order_quantity_rejects_invalid_multiple():
    with pytest.raises(ValueError, match="multiple"):
        order_quantity_from_config({"underlying": "NIFTY", "size_mode": "qty", "size_value": 70})


def test_leg_is_algo_managed():
    assert leg_is_algo_managed({"status": "open", "managed_by": "algo"})
    assert not leg_is_algo_managed({"status": "open", "managed_by": "manual"})
    assert not leg_is_algo_managed({"status": "open", "last_action": "paper_sync"})
    assert leg_is_algo_managed(
        {
            "status": "open",
            "entry_at": "2026-07-13T10:00:00",
            "last_action": "short_entry",
            "entry_order_id": "12345",
        }
    )
