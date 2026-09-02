"""Detector behaviour, driven by synthetic books.

Every family gets a **negative control**: a book that satisfies put-call parity
and the convexity bounds exactly, priced with a real bid-ask, must produce zero
rows. A screen that fires on a fair book is worse than no screen, because the
noise is indistinguishable from the signal — and half-spread artifacts are
exactly how a mid-priced sheet manufactures violations.

Then each family gets one deliberately mispriced strike and has to find it.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd
import pytest

from analysis.opt_arb import costs, universe
from analysis.opt_arb.detectors import box, butterfly, vertical, xcontract
from analysis.opt_arb.quotes import Quote, quote_key

SPOT = 24300.0
STRIKES = [24000.0 + 100.0 * i for i in range(7)]  # 24000 .. 24600
LOT = 65
HALF_SPREAD = 0.5


def _expiry(days: int = 7) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _fair(strike: float, opt: str, spot: float = SPOT) -> float:
    """Parity-consistent prices: CE - PE == spot - strike at every strike."""
    intrinsic = max(spot - strike, 0.0) if opt == "CE" else max(strike - spot, 0.0)
    time_value = 120.0 * math.exp(-(((strike - spot) / 700.0) ** 2))
    return round(intrinsic + time_value, 2)


def _nifty_frame(expiry: str) -> pd.DataFrame:
    rows = []
    token = 1
    for strike in STRIKES:
        for opt in ("CE", "PE"):
            rows.append(
                {
                    "instrument_token": token,
                    "tradingsymbol": f"NIFTY{strike:.0f}{opt}",
                    "name": "NIFTY",
                    "expiry": expiry,
                    "strike": strike,
                    "lot_size": LOT,
                    "instrument_type": opt,
                    "exchange": "NFO",
                }
            )
            token += 1
    return pd.DataFrame(rows)


def _mcx_frame(expiry: str, fut_expiry: str) -> pd.DataFrame:
    rows = []
    token = 500
    for name in ("CRUDEOIL", "CRUDEOILM"):
        for strike in (6000.0, 6100.0, 6200.0):
            for opt in ("CE", "PE"):
                rows.append(
                    {
                        "instrument_token": token,
                        "tradingsymbol": f"{name}{strike:.0f}{opt}",
                        "name": name,
                        "expiry": expiry,
                        "strike": strike,
                        "lot_size": 1,
                        "instrument_type": opt,
                        "exchange": "MCX",
                    }
                )
                token += 1
        rows.append(
            {
                "instrument_token": token,
                "tradingsymbol": f"{name}FUT",
                "name": name,
                "expiry": fut_expiry,
                "strike": 0.0,
                "lot_size": 1,
                "instrument_type": "FUT",
                "exchange": "MCX",
            }
        )
        token += 1
    return pd.DataFrame(rows)


def _book(
    name: str,
    exchange: str,
    expiry: str,
    price_of,
    *,
    depth: int = 10_000,
) -> dict[str, Quote]:
    quotes: dict[str, Quote] = {}
    for strike, legs in universe.strike_map(name, exchange, expiry).items():
        for opt, contract in legs.items():
            mid = price_of(strike, opt)
            key = quote_key(contract["exchange"], contract["tradingsymbol"])
            quotes[key] = Quote(
                key=key,
                bid=round(mid - HALF_SPREAD, 2),
                ask=round(mid + HALF_SPREAD, 2),
                bid_qty=depth,
                ask_qty=depth,
                ltp=mid,
                oi=100_000,
            )
    return quotes


@pytest.fixture(autouse=True)
def _pristine_rates():
    costs.reset_rates()
    universe.clear_caches()
    yield
    costs.reset_rates()
    universe.clear_caches()


@pytest.fixture
def nifty(monkeypatch: pytest.MonkeyPatch):
    expiry = _expiry()
    frame = _nifty_frame(expiry)
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: frame)
    universe.clear_caches()
    return expiry


@pytest.fixture
def crude(monkeypatch: pytest.MonkeyPatch):
    expiry = _expiry(20)
    frame = _mcx_frame(expiry, _expiry(22))
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: frame)
    universe.clear_caches()
    return expiry


# --------------------------------------------------------------------------
# Negative controls
# --------------------------------------------------------------------------


def test_fair_book_produces_no_butterfly_violations(nifty):
    quotes = _book("NIFTY", "NFO", nifty, _fair)
    result = butterfly.scan("NIFTY", "NFO", nifty, quotes=quotes)
    assert result["rows"] == []


def test_fair_book_produces_no_vertical_violations(nifty):
    quotes = _book("NIFTY", "NFO", nifty, _fair)
    result = vertical.scan("NIFTY", "NFO", nifty, quotes=quotes)
    assert result["rows"] == []


def test_parity_exact_book_produces_no_box_violations(nifty):
    """The half-spread must not read as edge — this is where a mid-priced
    screen would light up on every strike pair."""
    quotes = _book("NIFTY", "NFO", nifty, _fair)
    result = box.scan("NIFTY", "NFO", nifty, spot=SPOT, quotes=quotes)
    assert result["rows"] == []


def test_matched_big_mini_prices_produce_no_xcontract_rows(crude):
    quotes = _book("CRUDEOIL", "MCX", crude, lambda k, o: _fair(k, o, spot=6100.0))
    quotes.update(_book("CRUDEOILM", "MCX", crude, lambda k, o: _fair(k, o, spot=6100.0)))
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    result = xcontract.scan_pair(pair, quotes=quotes)
    assert result["rows"] == []


# --------------------------------------------------------------------------
# Butterfly
# --------------------------------------------------------------------------


def test_rich_body_makes_the_fly_free_to_buy(nifty):
    """Two over-priced bodies against fair wings pays you to hold the fly."""

    def priced(strike, opt):
        bump = 80.0 if (strike == 24300.0 and opt == "CE") else 0.0
        return _fair(strike, opt) + bump

    quotes = _book("NIFTY", "NFO", nifty, priced)
    result = butterfly.scan("NIFTY", "NFO", nifty, quotes=quotes)
    hits = [r for r in result["rows"] if r["strike"] == 24300.0 and r["option_type"] == "CE"]
    assert hits, "an over-priced body must violate convexity"
    assert hits[0]["violation"] == "buy_below_zero"
    assert hits[0]["net"] > 0
    assert hits[0]["net"] < hits[0]["gross"]  # charges were deducted


def test_cheap_strike_shows_up_on_both_sides_of_the_bound(nifty):
    """A single under-priced strike is a cheap body to one fly and a cheap
    wing to its neighbour, so both violations should fire."""

    def priced(strike, opt):
        bump = -40.0 if (strike == 24300.0 and opt == "CE") else 0.0
        return max(_fair(strike, opt) + bump, 0.05)

    quotes = _book("NIFTY", "NFO", nifty, priced)
    result = butterfly.scan("NIFTY", "NFO", nifty, quotes=quotes)
    kinds = {r["violation"] for r in result["rows"]}
    assert "sell_above_width" in kinds  # the cheap strike as a body
    assert "buy_below_zero" in kinds  # the cheap strike as a wing


def test_sheet_grid_has_one_cell_per_body_and_width(nifty):
    quotes = _book("NIFTY", "NFO", nifty, _fair)
    grid = butterfly.sheet("NIFTY", "NFO", nifty, widths=[100.0, 200.0], quotes=quotes)
    assert grid["widths"] == ["100", "200"]
    assert grid["units_per_lot"] == LOT
    row = next(r for r in grid["rows"] if r["strike"] == 24300.0)
    assert set(row["cells"]) == {"100", "200"}
    # A fair book still prints a buy/sell pair in every cell — it just has no
    # violation. That is what makes the sheet readable as a worksheet.
    assert row["cells"]["100"]["buy"] is not None
    assert row["cells"]["100"]["violation"] is None


# --------------------------------------------------------------------------
# Vertical
# --------------------------------------------------------------------------


def test_call_cheaper_than_a_higher_strike_is_a_vertical_violation(nifty):
    """Strike monotonicity is subsumed by the vertical debit bound."""

    def priced(strike, opt):
        bump = -120.0 if (strike == 24200.0 and opt == "CE") else 0.0
        return max(_fair(strike, opt) + bump, 0.05)

    quotes = _book("NIFTY", "NFO", nifty, priced)
    result = vertical.scan("NIFTY", "NFO", nifty, quotes=quotes)
    hits = [
        r
        for r in result["rows"]
        if r["option_type"] == "CE" and r["lower_strike"] == 24200.0
    ]
    assert hits
    assert hits[0]["violation"] == "debit_below_zero"
    assert hits[0]["net"] < hits[0]["gross"]


def test_credit_above_width_is_flagged(nifty):
    def priced(strike, opt):
        bump = 250.0 if (strike == 24200.0 and opt == "CE") else 0.0
        return _fair(strike, opt) + bump

    quotes = _book("NIFTY", "NFO", nifty, priced)
    result = vertical.scan("NIFTY", "NFO", nifty, quotes=quotes)
    assert any(r["violation"] == "credit_above_width" for r in result["rows"])


# --------------------------------------------------------------------------
# Box
# --------------------------------------------------------------------------


def test_box_violation_includes_exercise_stt_in_its_cost(nifty):
    def priced(strike, opt):
        bump = 60.0 if (strike == 24500.0 and opt == "CE") else 0.0
        return _fair(strike, opt) + bump

    quotes = _book("NIFTY", "NFO", nifty, priced)
    result = box.scan("NIFTY", "NFO", nifty, spot=SPOT, quotes=quotes)
    assert result["rows"]
    row = result["rows"][0]
    assert row["exercise"]["applies"] is True
    assert row["cost"] > row["cost_detail"]["total"]  # the levy was added on top
    assert row["net"] == pytest.approx(row["gross"] - row["cost"], abs=0.01)


def test_box_without_spot_reports_the_levy_as_unknown(nifty):
    def priced(strike, opt):
        bump = 60.0 if (strike == 24500.0 and opt == "CE") else 0.0
        return _fair(strike, opt) + bump

    quotes = _book("NIFTY", "NFO", nifty, priced)
    result = box.scan("NIFTY", "NFO", nifty, spot=None, quotes=quotes)
    assert result["rows"]
    assert result["rows"][0]["exercise"]["applies"] is False


def test_discounted_width_is_below_face_value_for_a_future_expiry():
    far = box.discounted_width(200.0, _expiry(365))
    assert far < 200.0
    assert box.discounted_width(200.0, date.today().isoformat()) == pytest.approx(200.0)


# --------------------------------------------------------------------------
# Cross-contract
# --------------------------------------------------------------------------


def _crude_quotes(expiry: str, mini_bump: float, *, depth: int = 10_000):
    quotes = _book("CRUDEOIL", "MCX", expiry, lambda k, o: _fair(k, o, spot=6100.0), depth=depth)
    quotes.update(
        _book(
            "CRUDEOILM",
            "MCX",
            expiry,
            lambda k, o: _fair(k, o, spot=6100.0) + mini_bump,
            depth=depth,
        )
    )
    return quotes


def test_rich_mini_produces_a_buy_big_sell_mini_row(crude):
    quotes = _crude_quotes(crude, mini_bump=8.0)
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    result = xcontract.scan_pair(pair, quotes=quotes)
    assert result["rows"]
    row = result["rows"][0]
    assert row["buy_big"] is True
    assert row["tier"] == "A"
    # 1 big lot (100 bbl) is offset by 10 mini lots (10 x 10 bbl).
    assert row["mini_lots"] == 10
    assert row["legs"][0]["units"] == 100.0
    assert row["legs"][1]["units"] == 100.0
    assert row["edge_per_unit"] == pytest.approx(8.0 - 2 * HALF_SPREAD, abs=0.01)
    assert row["gross"] == pytest.approx(row["edge_per_unit"] * 100.0, abs=0.01)
    assert row["net"] < row["gross"]


def test_cheap_mini_produces_a_sell_big_buy_mini_row(crude):
    quotes = _crude_quotes(crude, mini_bump=-8.0)
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    result = xcontract.scan_pair(pair, quotes=quotes)
    assert result["rows"]
    assert result["rows"][0]["buy_big"] is False


def test_thin_mini_book_is_dropped_by_the_depth_gate(crude):
    # 5 lots of mini depth cannot offset one big lot, which needs 10.
    quotes = _crude_quotes(crude, mini_bump=8.0, depth=5)
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    assert xcontract.scan_pair(pair, quotes=quotes, require_depth=True)["rows"] == []

    relaxed = xcontract.scan_pair(pair, quotes=quotes, require_depth=False)
    assert relaxed["rows"]
    assert any("top of book supports" in w for w in relaxed["rows"][0]["warnings"])


def test_carry_pair_is_skipped_when_clean_is_required(monkeypatch: pytest.MonkeyPatch):
    frame = pd.concat(
        [
            _mcx_frame(_expiry(20), _expiry(22)),
            pd.DataFrame(
                [
                    {
                        "instrument_token": 900 + i,
                        "tradingsymbol": f"{name}{strike:.0f}{opt}",
                        "name": name,
                        "expiry": _expiry(opt_days),
                        "strike": strike,
                        "lot_size": 1,
                        "instrument_type": opt,
                        "exchange": "MCX",
                    }
                    for i, (name, opt_days, strike, opt) in enumerate(
                        [
                            ("GOLD", 25, 125000.0, "CE"),
                            ("GOLD", 25, 125000.0, "PE"),
                            ("GOLDM", 21, 125000.0, "CE"),
                            ("GOLDM", 21, 125000.0, "PE"),
                        ]
                    )
                ]
            ),
        ],
        ignore_index=True,
    )
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: frame)
    universe.clear_caches()

    pair = universe.pair_by_key("GOLD_GOLDM")
    strict = xcontract.scan_pair(pair, quotes={}, require_clean=True)
    assert strict["rows"] == []
    assert "no shared option expiry" in strict["skipped"]


def _gold_frame(rows_spec):
    """rows_spec: (name, option expiry days, future expiry days) tuples."""
    rows = []
    for name, opt_days, fut_days in rows_spec:
        for days in opt_days:
            for strike in (125000.0, 126000.0):
                for opt in ("CE", "PE"):
                    rows.append(
                        {
                            "instrument_token": 900 + len(rows),
                            "tradingsymbol": f"{name}{days}{strike:.0f}{opt}",
                            "name": name,
                            "expiry": _expiry(days),
                            "strike": strike,
                            "lot_size": 1,
                            "instrument_type": opt,
                            "exchange": "MCX",
                        }
                    )
        for days in fut_days:
            rows.append(
                {
                    "instrument_token": 900 + len(rows),
                    "tradingsymbol": f"{name}{days}FUT",
                    "name": name,
                    "expiry": _expiry(days),
                    "strike": 0.0,
                    "lot_size": 1,
                    "instrument_type": "FUT",
                    "exchange": "MCX",
                }
            )
    return pd.DataFrame(rows)


def test_scan_prefers_a_clean_later_expiry_over_a_dirty_front_month(
    monkeypatch: pytest.MonkeyPatch,
):
    """The GOLD/GOLDM shape, and the bug it exposed.

    Front months differ (25d vs 21d) and point at different futures (60d vs
    31d), but both sides also list a 40-day option that references the same
    70-day future. Classifying the *pair* by its front month skipped that
    expiry entirely and hid a real Tier A trade; classification has to be per
    expiry.
    """
    frame = _gold_frame(
        [
            # Front options differ (25d vs 21d) and resolve to different futures
            # (30d vs 31d); the shared 40d option resolves to the same 70d future.
            ("GOLD", [25, 40], [30, 70]),
            ("GOLDM", [21, 40], [31, 70]),
        ]
    )
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: frame)
    universe.clear_caches()

    pair = universe.pair_by_key("GOLD_GOLDM")
    status = universe.pair_status(pair)
    assert status["front_clean"] is False
    assert status["clean_expiries"] == [_expiry(40)]

    quotes = _book("GOLD", "MCX", _expiry(40), lambda k, o: _fair(k, o, spot=125500.0))
    quotes.update(
        _book(
            "GOLDM",
            "MCX",
            _expiry(40),
            lambda k, o: _fair(k, o, spot=125500.0) + 30.0,
        )
    )
    result = xcontract.scan_pair(pair, quotes=quotes, require_clean=True)
    assert result["expiry"] == _expiry(40), "must trade the clean expiry, not the front"
    assert result["rows"]
    assert all(r["tier"] == "A" for r in result["rows"])


def test_grid_defaults_to_a_clean_expiry_when_the_front_month_is_carry(
    monkeypatch: pytest.MonkeyPatch,
):
    frame = _gold_frame(
        [
            # Front options differ (25d vs 21d) and resolve to different futures
            # (30d vs 31d); the shared 40d option resolves to the same 70d future.
            ("GOLD", [25, 40], [30, 70]),
            ("GOLDM", [21, 40], [31, 70]),
        ]
    )
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: frame)
    universe.clear_caches()
    monkeypatch.setattr(xcontract, "fetch_quotes", lambda keys: {})

    grid = xcontract.sheet(universe.pair_by_key("GOLD_GOLDM"), option_type="CE")
    assert grid["expiry"]["big"] == _expiry(40)
    assert grid["clean"] is True
    assert "not carry" in grid["basis"]["note"]


# --------------------------------------------------------------------------
# Big-vs-mini strike grid
# --------------------------------------------------------------------------


def _patch_fetch(monkeypatch: pytest.MonkeyPatch, quotes: dict[str, Quote]) -> None:
    """xcontract.sheet fetches its own book — hand it a canned one."""
    monkeypatch.setattr(xcontract, "fetch_quotes", lambda keys: quotes)


def test_sheet_fills_every_strike_including_the_losing_ones(
    crude, monkeypatch: pytest.MonkeyPatch
):
    """A worksheet needs every cell. The scan drops non-positive edges; the grid
    must not, or the good cells have nothing to stand out against."""
    _patch_fetch(monkeypatch, _crude_quotes(crude, mini_bump=0.0))
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    grid = xcontract.sheet(pair, option_type="CE")

    assert [r["strike"] for r in grid["rows"]] == [6000.0, 6100.0, 6200.0]
    assert all(r["buy"] and r["sell"] for r in grid["rows"])
    # Fairly priced with a real spread, both directions lose after charges.
    assert all(r["buy"]["net"] < 0 and r["sell"]["net"] < 0 for r in grid["rows"])


def test_sheet_buy_and_sell_are_not_mirror_images(crude, monkeypatch: pytest.MonkeyPatch):
    """The gap between the two directions is the round-trip cost of crossing the
    spread on both legs — a mid-priced sheet would show them as exact negatives."""
    _patch_fetch(monkeypatch, _crude_quotes(crude, mini_bump=0.0))
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    row = xcontract.sheet(pair, option_type="CE")["rows"][0]
    assert row["buy"]["gross"] != pytest.approx(-row["sell"]["gross"])
    assert row["buy"]["gross"] + row["sell"]["gross"] < 0


def test_sheet_marks_cells_that_clear_the_threshold(crude, monkeypatch: pytest.MonkeyPatch):
    _patch_fetch(monkeypatch, _crude_quotes(crude, mini_bump=8.0))
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    grid = xcontract.sheet(pair, option_type="CE", threshold=0.0)
    assert any(r["buy"]["passes"] for r in grid["rows"])

    strict = xcontract.sheet(pair, option_type="CE", threshold=1e9)
    assert not any(r["buy"]["passes"] for r in strict["rows"])


def test_sheet_basis_is_labelled_as_a_dislocation_on_a_clean_pair(
    crude, monkeypatch: pytest.MonkeyPatch
):
    _patch_fetch(monkeypatch, _crude_quotes(crude, mini_bump=0.0))
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    grid = xcontract.sheet(pair, option_type="CE")
    assert grid["expiry"]["matched"] is True
    assert "not carry" in grid["basis"]["note"]
    assert grid["basis"]["value"] == pytest.approx(0.0, abs=1.0)


def test_sheet_basis_is_labelled_as_carry_when_the_futures_months_differ(
    monkeypatch: pytest.MonkeyPatch,
):
    """The number a vendor sheet prints at the top of a gold panel."""
    rows = []
    for i, (name, opt_days, fut_days) in enumerate((("GOLD", 25, 60), ("GOLDM", 21, 31))):
        for strike in (125000.0, 130000.0):
            for opt in ("CE", "PE"):
                rows.append(
                    {
                        "instrument_token": 700 + len(rows),
                        "tradingsymbol": f"{name}{strike:.0f}{opt}",
                        "name": name,
                        "expiry": _expiry(opt_days),
                        "strike": strike,
                        "lot_size": 1,
                        "instrument_type": opt,
                        "exchange": "MCX",
                    }
                )
        rows.append(
            {
                "instrument_token": 800 + i,
                "tradingsymbol": f"{name}FUT",
                "name": name,
                "expiry": _expiry(fut_days),
                "strike": 0.0,
                "lot_size": 1,
                "instrument_type": "FUT",
                "exchange": "MCX",
            }
        )
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: pd.DataFrame(rows))
    universe.clear_caches()
    monkeypatch.setattr(xcontract, "fetch_quotes", lambda keys: {})

    pair = universe.pair_by_key("GOLD_GOLDM")
    grid = xcontract.sheet(pair, option_type="CE")
    assert grid["expiry"]["matched"] is False
    assert grid["expiry"]["big"] != grid["expiry"]["mini"]
    assert "mostly carry" in grid["basis"]["note"]
    assert "not the size of the opportunity" in grid["basis"]["note"]


def test_sheet_reports_skipped_when_no_strike_is_listed_on_both(
    monkeypatch: pytest.MonkeyPatch,
):
    rows = [
        {
            "instrument_token": 1,
            "tradingsymbol": "CRUDEOIL6000CE",
            "name": "CRUDEOIL",
            "expiry": _expiry(20),
            "strike": 6000.0,
            "lot_size": 1,
            "instrument_type": "CE",
            "exchange": "MCX",
        },
        {
            "instrument_token": 2,
            "tradingsymbol": "CRUDEOILM9999CE",
            "name": "CRUDEOILM",
            "expiry": _expiry(20),
            "strike": 9999.0,
            "lot_size": 1,
            "instrument_type": "CE",
            "exchange": "MCX",
        },
    ]
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: pd.DataFrame(rows))
    universe.clear_caches()
    pair = universe.pair_by_key("CRUDEOIL_CRUDEOILM")
    grid = xcontract.sheet(pair, option_type="CE")
    assert grid["rows"] == []
    assert "no strike is listed on both" in grid["skipped"]
