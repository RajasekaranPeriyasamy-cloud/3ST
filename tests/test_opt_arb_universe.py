"""Universe resolution — contract multipliers and big/mini classification.

The classification test is the one that matters. A big/mini pair is only an
arbitrage when both sides share an option expiry *and* reference the same
futures month; treating a carry spread as free money is exactly the mistake
this desk exists to prevent, so both the clean and the dirty case are pinned
here against a fabricated instrument dump rather than against whatever Kite
happens to list today.

All dates derive from ``date.today()`` — a fixture that hardcodes a currently
valid expiry silently stops testing anything the day it lapses.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from analysis.opt_arb import universe


def _iso(days: int) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _row(**kw):
    base = {
        "instrument_token": 0,
        "tradingsymbol": "X",
        "name": "X",
        "expiry": _iso(10),
        "strike": 0.0,
        "lot_size": 1,
        "instrument_type": "CE",
        "exchange": "MCX",
    }
    base.update(kw)
    return base


def _frame() -> pd.DataFrame:
    rows = []
    token = 1

    # CRUDEOIL / CRUDEOILM — same option expiry, same referenced future. Clean.
    for name in ("CRUDEOIL", "CRUDEOILM"):
        for strike in (6000.0, 6100.0, 6200.0):
            for opt in ("CE", "PE"):
                rows.append(
                    _row(
                        instrument_token=token,
                        tradingsymbol=f"{name}26{strike:.0f}{opt}",
                        name=name,
                        expiry=_iso(20),
                        strike=strike,
                        instrument_type=opt,
                    )
                )
                token += 1
        rows.append(
            _row(
                instrument_token=token,
                tradingsymbol=f"{name}26FUT",
                name=name,
                expiry=_iso(22),
                instrument_type="FUT",
            )
        )
        token += 1

    # GOLD / GOLDM — option expiries differ and they point at different futures
    # months. Carry spread, not arbitrage.
    for name, opt_days, fut_days in (("GOLD", 25, 60), ("GOLDM", 21, 31)):
        for strike in (125000.0, 126000.0):
            for opt in ("CE", "PE"):
                rows.append(
                    _row(
                        instrument_token=token,
                        tradingsymbol=f"{name}26{strike:.0f}{opt}",
                        name=name,
                        expiry=_iso(opt_days),
                        strike=strike,
                        instrument_type=opt,
                    )
                )
                token += 1
        rows.append(
            _row(
                instrument_token=token,
                tradingsymbol=f"{name}26FUT",
                name=name,
                expiry=_iso(fut_days),
                instrument_type="FUT",
            )
        )
        token += 1

    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _fake_instruments(monkeypatch: pytest.MonkeyPatch):
    # universe.py does `from instruments import load_instruments` at import time,
    # so patch its own reference, not the definition site.
    frame = _frame()
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: frame)
    # The frame cache is keyed on the real instrument dump's mtime, which does
    # not change when a test swaps the frame underneath it — clear it or one
    # test's fixture leaks into the next.
    universe.clear_caches()
    yield
    universe.clear_caches()


def test_matched_pair_is_clean():
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    status = universe.pair_status(pair)
    assert status["clean"] is True
    assert status["front_expiry"]["matched"] is True
    assert status["referenced_future"]["matched"] is True
    assert status["shared_expiries"] == [_iso(20)]


def test_mismatched_pair_is_flagged_as_carry_not_arbitrage():
    pair = universe.pair_by_key("GOLD_GOLDM")
    status = universe.pair_status(pair)
    assert status["clean"] is False
    assert "different futures months" in status["reason"]
    assert status["referenced_future"]["big"] == _iso(60)
    assert status["referenced_future"]["mini"] == _iso(31)


def test_referenced_future_is_the_first_one_expiring_after_the_option():
    assert universe.referenced_future("GOLD", "MCX", _iso(25)) == _iso(60)
    assert universe.referenced_future("CRUDEOIL", "MCX", _iso(20)) == _iso(22)


def test_past_expiries_are_excluded_by_default():
    listed = universe.option_expiries("CRUDEOIL", "MCX")
    assert all(e >= date.today().isoformat() for e in listed)


def test_mini_ratio_matches_contract_sizes():
    """Wrong multipliers do not break the screen, they mis-price it."""
    expected = {
        "CRUDEOIL_CRUDEOILM": 10.0,  # 100 bbl vs 10 bbl
        "NATURALGAS_NATGASMINI": 5.0,  # 1250 vs 250 mmBtu
        "GOLD_GOLDM": 10.0,  # 1 kg vs 100 g
        "SILVER_SILVERM": 6.0,  # 30 kg vs 5 kg
    }
    assert {p.key: p.ratio for p in universe.MINI_PAIRS} == expected


def test_mcx_units_come_from_the_multiplier_table_not_lot_size():
    """Kite reports lot_size 1 for every MCX option — using it would value a
    commodity leg at one rupee per point."""
    assert universe.units_per_lot("MCX", "CRUDEOIL", 1) == 100.0
    assert universe.units_per_lot("MCX", "GOLDM", 1) == 10.0
    assert universe.units_per_lot("MCX", "NOTLISTED", 1) == 0.0


def test_equity_derivative_units_come_from_lot_size():
    assert universe.units_per_lot("NFO", "NIFTY", 65) == 65.0
    assert universe.units_per_lot("BFO", "SENSEX", 20) == 20.0


def test_cost_segment_separates_index_from_stock_options():
    assert universe.cost_segment("NFO", "NIFTY") == "NFO"
    assert universe.cost_segment("NFO", "RELIANCE") == "NFO_STOCK"
    assert universe.cost_segment("BFO", "SENSEX") == "BFO"
    assert universe.cost_segment("MCX", "GOLD") == "MCX"
    assert universe.is_physically_settled("NFO", "RELIANCE") is True
    assert universe.is_physically_settled("NFO", "NIFTY") is False


def test_strike_map_groups_ce_and_pe():
    smap = universe.strike_map("CRUDEOIL", "MCX", _iso(20))
    assert sorted(smap) == [6000.0, 6100.0, 6200.0]
    assert set(smap[6000.0]) == {"CE", "PE"}


def test_contracts_hands_out_copies_not_the_cached_rows():
    """Detectors enrich contract dicts in place; the cache must not see it."""
    first = universe.contracts("CRUDEOIL", "MCX", _iso(20))
    first[0]["strike"] = -1.0
    second = universe.contracts("CRUDEOIL", "MCX", _iso(20))
    assert second[0]["strike"] != -1.0


def test_clear_caches_lets_a_swapped_dump_take_effect(monkeypatch: pytest.MonkeyPatch):
    """The frame cache is keyed on the dump's mtime, which does not move when a
    caller substitutes the frame — so a swap needs an explicit clear."""
    assert universe.option_expiries("CRUDEOIL", "MCX")

    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: pd.DataFrame())
    assert universe.option_expiries("CRUDEOIL", "MCX"), "stale cache still serving"

    universe.clear_caches()
    assert universe.option_expiries("CRUDEOIL", "MCX") == []


def test_lots_available_normalises_both_depth_conventions():
    """Kite reports NFO depth in underlying units and MCX depth in lots — using
    the raw quantity as lots would inflate an index row by the lot size."""
    assert universe.lots_available(130, 65) == 2  # NFO: 130 units = 2 NIFTY lots
    assert universe.lots_available(64, 65) == 0  # not even one lot
    assert universe.lots_available(7, 1) == 7  # MCX: already lots
    assert universe.lots_available(None, 65) == 0
    assert universe.lots_available(100, 0) == 0  # unknown lot size is not "unlimited"


def test_expiry_status_classifies_one_expiry_not_the_whole_pair():
    """A pair can be carry in the front month and arbitrage further out."""
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    same = universe.expiry_status(pair, _iso(20), _iso(20))
    assert same["clean"] is True

    crossed = universe.expiry_status(pair, _iso(20), _iso(10))
    assert crossed["clean"] is False
    assert crossed["expiry"]["matched"] is False


def test_pair_status_lists_the_expiries_that_are_actually_clean():
    clean = universe.pair_status(universe.pair_by_key("CRUDEOIL_CRUDEOILM"))
    assert clean["clean_expiries"] == [_iso(20)]
    assert clean["front_clean"] is True

    carry = universe.pair_status(universe.pair_by_key("GOLD_GOLDM"))
    assert carry["clean_expiries"] == []
    assert carry["front_clean"] is False
    assert carry["clean"] is False
