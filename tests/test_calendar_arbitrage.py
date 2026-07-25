"""Tests for calendar arbitrage universe (instruments only)."""

from __future__ import annotations

import pandas as pd

from options import calendar_arbitrage as arb


def _sample_df() -> pd.DataFrame:
    rows = [
        {
            "instrument_token": 1,
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26JULFUT",
            "name": "NIFTY",
            "instrument_type": "FUT",
            "expiry": pd.Timestamp("2026-07-31"),
            "lot_size": 65,
            "tick_size": 0.05,
        },
        {
            "instrument_token": 2,
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26AUGFUT",
            "name": "NIFTY",
            "instrument_type": "FUT",
            "expiry": pd.Timestamp("2026-08-28"),
            "lot_size": 65,
            "tick_size": 0.05,
        },
        {
            "instrument_token": 3,
            "exchange": "NFO",
            "tradingsymbol": "NIFTY26SEPFUT",
            "name": "NIFTY",
            "instrument_type": "FUT",
            "expiry": pd.Timestamp("2026-09-25"),
            "lot_size": 65,
            "tick_size": 0.05,
        },
    ]
    return pd.DataFrame(rows)


def test_config():
    cfg = arb.calendar_arbitrage_config()
    assert "NFO" in cfg["default_exchanges"]


def test_universe_pairs(monkeypatch):
    monkeypatch.setattr(arb, "load_instruments", lambda force_refresh=False: _sample_df())
    out = arb.build_arbitrage_universe(["NFO"])
    assert out["counts"]["pairs"] == 2
    assert out["pairs"][0]["underlying"] == "NIFTY"
    assert out["pairs"][0]["type"] == "near-next"


def test_snapshot_sorts(monkeypatch):
    monkeypatch.setattr(arb, "load_instruments", lambda force_refresh=False: _sample_df())
    monkeypatch.setattr(
        arb,
        "_quote_batches",
        lambda keys: {k: {"last_price": 100.0, "depth": {"buy": [{"price": 99}], "sell": [{"price": 101}]}} for k in keys},
    )
    snap = arb.build_arbitrage_snapshot(["NFO"])
    assert "rows" in snap
    assert len(snap["rows"]) >= 1
