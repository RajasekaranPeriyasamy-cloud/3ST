"""Tests for the typed domain model + fail-fast validation (NAUTILUS_IMPROVEMENTS #2)."""

from __future__ import annotations

import math

import pytest

from domain import (
    Fill,
    Instrument,
    InvalidMarketData,
    InvalidOrder,
    Order,
    Position,
    Quote,
    open_orders_from_kite,
    open_positions_from_kite,
    safe_price,
    validate_price,
    validate_quantity,
    validate_signed_quantity,
)


# --------------------------------------------------------------------- validation


@pytest.mark.parametrize("bad", [None, "", "abc", float("nan"), float("inf"), -float("inf"), 0, -5, True, False])
def test_safe_price_rejects_invalid(bad) -> None:
    assert safe_price(bad) is None


@pytest.mark.parametrize("good,expected", [(1, 1.0), ("110.5", 110.5), (242.0, 242.0)])
def test_safe_price_accepts_valid(good, expected) -> None:
    assert safe_price(good) == expected


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0, -1])
def test_validate_price_raises(bad) -> None:
    with pytest.raises(InvalidMarketData):
        validate_price(bad)


def test_validate_price_message_includes_field() -> None:
    with pytest.raises(InvalidMarketData, match="NIFTY last_price"):
        validate_price(float("nan"), field="NIFTY last_price")


def test_validate_quantity() -> None:
    assert validate_quantity(75) == 75
    assert validate_quantity("75") == 75
    with pytest.raises(InvalidOrder):
        validate_quantity(0)
    assert validate_quantity(0, allow_zero=True) == 0
    with pytest.raises(InvalidOrder):
        validate_quantity(-1)
    with pytest.raises(InvalidOrder):
        validate_quantity(True)


def test_validate_signed_quantity() -> None:
    assert validate_signed_quantity(-75) == -75
    assert validate_signed_quantity(0) == 0
    with pytest.raises(InvalidOrder):
        validate_signed_quantity("x")


# ------------------------------------------------------------------------ models


def test_instrument_normalizes_and_keys() -> None:
    inst = Instrument(exchange="nfo", tradingsymbol="nifty25jul24000ce")
    assert inst.exchange == "NFO"
    assert inst.tradingsymbol == "NIFTY25JUL24000CE"
    assert inst.key == "NFO:NIFTY25JUL24000CE"


def test_instrument_requires_fields() -> None:
    with pytest.raises(InvalidOrder):
        Instrument(exchange="", tradingsymbol="X")
    with pytest.raises(InvalidOrder):
        Instrument(exchange="NSE", tradingsymbol="")


def test_quote_rejects_bad_price() -> None:
    inst = Instrument("NSE", "RELIANCE")
    assert Quote(inst, 100.0).last_price == 100.0
    with pytest.raises(InvalidMarketData):
        Quote(inst, float("nan"))
    with pytest.raises(InvalidMarketData):
        Quote(inst, 0)


def test_fill_validates() -> None:
    inst = Instrument("NFO", "NIFTY25JUL24000CE")
    fill = Fill(inst, "buy", 75, 110.5)
    assert fill.side == "BUY" and fill.quantity == 75 and fill.price == 110.5
    with pytest.raises(InvalidMarketData):
        Fill(inst, "BUY", 75, 0)
    with pytest.raises(InvalidOrder):
        Fill(inst, "HOLD", 75, 110.5)


# ------------------------------------------------------------------- kite parsers


def _kite_position(**over):
    row = {
        "tradingsymbol": "NIFTY25JUL24000CE",
        "exchange": "NFO",
        "instrument_token": 12345,
        "quantity": -75,
        "average_price": 110.0,
        "last_price": 95.0,
        "pnl": 1125.0,
        "product": "NRML",
    }
    row.update(over)
    return row


def test_position_from_kite() -> None:
    pos = Position.from_kite(_kite_position())
    assert pos.instrument.key == "NFO:NIFTY25JUL24000CE"
    assert pos.quantity == -75
    assert pos.direction == "SHORT"
    assert pos.is_open
    assert pos.average_price == 110.0
    assert pos.net_value == 75 * 110.0
    assert pos.pnl == 1125.0


def test_position_zero_prices_become_none() -> None:
    pos = Position.from_kite(_kite_position(quantity=0, average_price=0, last_price=0))
    assert not pos.is_open
    assert pos.direction == "FLAT"
    assert pos.average_price is None
    assert pos.last_price is None
    assert pos.net_value is None


def test_position_negative_pnl_preserved() -> None:
    pos = Position.from_kite(_kite_position(pnl=-500.0))
    assert pos.pnl == -500.0


def _kite_order(**over):
    row = {
        "order_id": "2607120001",
        "tradingsymbol": "NIFTY25JUL24000CE",
        "exchange": "NFO",
        "transaction_type": "SELL",
        "quantity": 75,
        "filled_quantity": 0,
        "product": "NRML",
        "order_type": "MARKET",
        "status": "OPEN",
        "price": 0,
        "average_price": 0,
        "tag": "3ST-CE-20260712-entry",
    }
    row.update(over)
    return row


def test_order_from_kite() -> None:
    order = Order.from_kite(_kite_order())
    assert order.order_id == "2607120001"
    assert order.side == "SELL"
    assert order.is_open
    assert order.is_3st
    assert order.price is None  # 0 → None
    assert order.average_price is None


def test_open_orders_filters_3st_and_status() -> None:
    rows = [
        _kite_order(order_id="1", status="OPEN", tag="3ST-CE-x-entry"),
        _kite_order(order_id="2", status="COMPLETE", tag="3ST-CE-x-entry"),
        _kite_order(order_id="3", status="OPEN", tag="MANUAL"),
        {"garbage": True},  # unparseable → skipped
    ]
    orders = open_orders_from_kite(rows)
    assert [o.order_id for o in orders] == ["1"]
    # without the 3ST filter, the manual open order is included too
    assert {o.order_id for o in open_orders_from_kite(rows, only_3st=False)} == {"1", "3"}


def test_open_positions_skips_flat_and_bad_rows() -> None:
    rows = [
        _kite_position(tradingsymbol="A", quantity=-75),
        _kite_position(tradingsymbol="B", quantity=0),  # flat → skipped
        {"exchange": "", "tradingsymbol": ""},  # bad → skipped
    ]
    positions = open_positions_from_kite(rows)
    assert [p.instrument.tradingsymbol for p in positions] == ["A"]


# ------------------------------------------------------- ltp_cache fail-fast adoption


def test_ltp_cache_drops_bad_ws_ticks() -> None:
    from execution.ltp_cache import LtpCache

    cache = LtpCache()
    cache.register("NFO", "NIFTY25JUL24000CE", 111)
    cache.register("NFO", "NIFTY25JUL24500PE", 222)
    cache.ingest_ws_ticks(
        [
            {"instrument_token": 111, "last_price": 110.5},
            {"instrument_token": 222, "last_price": float("nan")},  # dropped
        ]
    )
    assert cache.get("NFO", "NIFTY25JUL24000CE", allow_rest=False) == 110.5
    assert cache.get("NFO", "NIFTY25JUL24500PE", allow_rest=False) is None
    assert not any(math.isnan(e.price) for e in cache._by_token.values())
