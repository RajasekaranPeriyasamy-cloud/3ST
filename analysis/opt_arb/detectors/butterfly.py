"""Butterfly convexity — the strongest single-underlying arbitrage check.

Option value is convex in strike. For any three equally spaced strikes on the
same side and expiry::

    C(K-w) - 2*C(K) + C(K+w) >= 0

A long butterfly (buy both wings, sell two bodies) can never be worth less than
zero or more than the wing width ``w``. So there are exactly two violations:

* **buy below zero** — someone pays you to hold a structure that cannot lose
* **sell above the width** — you collect more than the maximum you can owe

Both are model-free. No volatility surface, no rate, no dividend assumption.

The grid returned by :func:`sheet` is the same shape as a combo-butterfly
worksheet: one row per body strike, one BUY and one SELL column per wing width.
The difference from a vendor sheet is that the numbers here are executable —
BUY is priced at the ask on the wings and the bid on the body, SELL the other
way round — and ``net`` is after charges from :mod:`analysis.opt_arb.costs`.
Mid-priced sheets make violations appear roughly where half the bid-ask spread
sits, which is to say almost everywhere.
"""

from __future__ import annotations

from typing import Any

from analysis.opt_arb import costs, universe
from analysis.opt_arb.quotes import Quote, fetch_quotes, quote_key

FAMILY = "butterfly"


def _px(quotes: dict[str, Quote], contract: dict[str, Any] | None, side: str) -> float | None:
    if not contract:
        return None
    quote = quotes.get(quote_key(contract["exchange"], contract["tradingsymbol"]))
    return quote.executable(side) if quote else None  # type: ignore[arg-type]


def _depth(quotes: dict[str, Quote], contract: dict[str, Any] | None, side: str) -> int:
    if not contract:
        return 0
    quote = quotes.get(quote_key(contract["exchange"], contract["tradingsymbol"]))
    return quote.depth_qty(side) if quote else 0  # type: ignore[arg-type]


def _fly_lots(
    quotes: dict[str, Quote],
    lower: dict[str, Any],
    body: dict[str, Any],
    upper: dict[str, Any],
    *,
    long_fly: bool,
) -> int:
    """Lots the book supports — the body needs twice the size of each wing."""
    wing_side = "BUY" if long_fly else "SELL"
    body_side = "SELL" if long_fly else "BUY"
    return min(
        universe.lots_available(_depth(quotes, lower, wing_side), lower.get("lot_size")),
        universe.lots_available(_depth(quotes, upper, wing_side), upper.get("lot_size")),
        universe.lots_available(_depth(quotes, body, body_side), body.get("lot_size")) // 2,
    )


def _legs(
    lower: dict[str, Any],
    body: dict[str, Any],
    upper: dict[str, Any],
    prices: tuple[float, float, float],
    *,
    long_fly: bool,
    units: float,
) -> list[dict[str, Any]]:
    wing_side = "BUY" if long_fly else "SELL"
    body_side = "SELL" if long_fly else "BUY"
    segment = universe.cost_segment(body["exchange"], body["name"])
    return [
        {
            "exchange": lower["exchange"],
            "tradingsymbol": lower["tradingsymbol"],
            "segment": segment,
            "side": wing_side,
            "price": round(prices[0], 4),
            "units": units,
            "strike": lower["strike"],
            "option_type": lower["option_type"],
        },
        {
            "exchange": body["exchange"],
            "tradingsymbol": body["tradingsymbol"],
            "segment": segment,
            "side": body_side,
            "price": round(prices[1], 4),
            "units": units * 2,
            "strike": body["strike"],
            "option_type": body["option_type"],
        },
        {
            "exchange": upper["exchange"],
            "tradingsymbol": upper["tradingsymbol"],
            "segment": segment,
            "side": wing_side,
            "price": round(prices[2], 4),
            "units": units,
            "strike": upper["strike"],
            "option_type": upper["option_type"],
        },
    ]


def _cell(
    lower: dict[str, Any],
    body: dict[str, Any],
    upper: dict[str, Any],
    quotes: dict[str, Quote],
    *,
    width: float,
    units: float,
    lots: int,
) -> dict[str, Any] | None:
    """One (body strike, width) cell: cost to buy and credit to sell the fly."""
    lower_ask = _px(quotes, lower, "BUY")
    upper_ask = _px(quotes, upper, "BUY")
    body_bid = _px(quotes, body, "SELL")
    lower_bid = _px(quotes, lower, "SELL")
    upper_bid = _px(quotes, upper, "SELL")
    body_ask = _px(quotes, body, "BUY")

    buy_cost = (
        lower_ask + upper_ask - 2 * body_bid
        if None not in (lower_ask, upper_ask, body_bid)
        else None
    )
    sell_credit = (
        lower_bid + upper_bid - 2 * body_ask
        if None not in (lower_bid, upper_bid, body_ask)
        else None
    )

    cell: dict[str, Any] = {
        "strike": body["strike"],
        "width": width,
        "buy": round(buy_cost, 4) if buy_cost is not None else None,
        "sell": round(sell_credit, 4) if sell_credit is not None else None,
        "violation": None,
        "net": None,
    }

    qty = units * lots
    # Violation 1 — buying the fly for a credit.
    if buy_cost is not None and buy_cost < 0:
        legs = _legs(lower, body, upper, (lower_ask, body_bid, upper_ask), long_fly=True, units=qty)
        cost = costs.combo_cost(legs, round_trip=True)
        gross = -buy_cost * qty
        cell.update(
            {
                "violation": "buy_below_zero",
                "gross": round(gross, 2),
                "cost": cost["total"],
                "net": round(gross - cost["total"], 2),
                "legs": legs,
                "max_lots": _fly_lots(quotes, lower, body, upper, long_fly=True),
            }
        )
        return cell

    # Violation 2 — selling the fly for more than its maximum liability.
    if sell_credit is not None and sell_credit > width:
        legs = _legs(
            lower, body, upper, (lower_bid, body_ask, upper_bid), long_fly=False, units=qty
        )
        cost = costs.combo_cost(legs, round_trip=True)
        gross = (sell_credit - width) * qty
        cell.update(
            {
                "violation": "sell_above_width",
                "gross": round(gross, 2),
                "cost": cost["total"],
                "net": round(gross - cost["total"], 2),
                "legs": legs,
                "max_lots": _fly_lots(quotes, lower, body, upper, long_fly=False),
            }
        )
    return cell


def sheet(
    name: str,
    exchange: str,
    expiry: str,
    *,
    option_type: str = "CE",
    widths: list[float] | None = None,
    strike_window: int = 20,
    lots: int = 1,
    quotes: dict[str, Quote] | None = None,
) -> dict[str, Any]:
    """Combo-butterfly worksheet: body strikes down, wing widths across.

    ``strike_window`` bounds the sheet to that many strikes either side of the
    middle of the listed range, which is also what bounds the quote budget —
    a full NIFTY expiry is ~240 contracts and a sheet does not need the tails.
    """
    opt = str(option_type).upper()
    smap = universe.strike_map(name, exchange, expiry)
    strikes = sorted(k for k, v in smap.items() if opt in v)
    if len(strikes) < 3:
        return {"family": FAMILY, "rows": [], "widths": [], "skipped": "fewer than 3 strikes"}

    step = min((b - a) for a, b in zip(strikes, strikes[1:], strict=False)) or 0
    if step <= 0:
        return {"family": FAMILY, "rows": [], "widths": [], "skipped": "cannot infer strike step"}

    grid = widths or [step * m for m in (1, 2, 4, 8)]
    grid = sorted({float(w) for w in grid if w > 0})

    mid_index = len(strikes) // 2
    lo = max(0, mid_index - strike_window)
    hi = min(len(strikes), mid_index + strike_window + 1)
    bodies = strikes[lo:hi]

    contract_by_strike = {k: smap[k].get(opt) for k in strikes}
    sample = next((c for c in contract_by_strike.values() if c), None)
    if sample is None:
        return {"family": FAMILY, "rows": [], "widths": [], "skipped": "no contracts"}
    units = universe.units_per_lot(exchange, name, sample.get("lot_size"))
    if units <= 0:
        return {
            "family": FAMILY,
            "rows": [],
            "widths": [],
            "skipped": f"no contract multiplier known for {exchange}:{name}",
        }

    needed: set[str] = set()
    for body in bodies:
        for width in grid:
            for strike in (body - width, body, body + width):
                contract = contract_by_strike.get(strike)
                if contract:
                    needed.add(quote_key(contract["exchange"], contract["tradingsymbol"]))
    book = quotes if quotes is not None else fetch_quotes(sorted(needed))

    rows: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    for body_strike in bodies:
        body = contract_by_strike.get(body_strike)
        if not body:
            continue
        cells: dict[str, Any] = {}
        for width in grid:
            lower = contract_by_strike.get(body_strike - width)
            upper = contract_by_strike.get(body_strike + width)
            if not lower or not upper:
                cells[f"{width:g}"] = None
                continue
            cell = _cell(lower, body, upper, book, width=width, units=units, lots=lots)
            cells[f"{width:g}"] = cell
            if cell and cell["violation"]:
                violations.append(
                    {
                        "family": FAMILY,
                        "tier": "A",
                        "id": f"{name}:{expiry}:{opt}:{body_strike:g}:{width:g}",
                        "underlying": name,
                        "exchange": exchange,
                        "expiry": expiry,
                        "option_type": opt,
                        "strike": body_strike,
                        "width": width,
                        "warnings": _warnings(exchange, name),
                        **{k: v for k, v in cell.items() if k != "strike"},
                    }
                )
        rows.append({"strike": body_strike, "cells": cells})

    violations.sort(key=lambda r: r.get("net") or 0.0, reverse=True)
    return {
        "family": FAMILY,
        "underlying": name,
        "exchange": exchange,
        "expiry": expiry,
        "option_type": opt,
        "strike_step": step,
        "units_per_lot": units,
        "widths": [f"{w:g}" for w in grid],
        "rows": rows,
        "violations": violations,
        "skipped": None,
    }


def _warnings(exchange: str, name: str) -> list[str]:
    if universe.is_physically_settled(exchange, name):
        return ["stock option — physically settled, an unfilled leg becomes a delivery obligation"]
    return []


def scan(
    name: str,
    exchange: str,
    expiry: str,
    *,
    widths: list[float] | None = None,
    strike_window: int = 20,
    lots: int = 1,
    min_net: float = 0.0,
    quotes: dict[str, Quote] | None = None,
) -> dict[str, Any]:
    """Violations only, both option types, ranked by net edge."""
    rows: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    for opt in ("CE", "PE"):
        built = sheet(
            name,
            exchange,
            expiry,
            option_type=opt,
            widths=widths,
            strike_window=strike_window,
            lots=lots,
            quotes=quotes,
        )
        if built.get("skipped"):
            skipped[opt] = built["skipped"]
            continue
        rows.extend(r for r in built["violations"] if (r.get("net") or 0.0) >= min_net)
    rows.sort(key=lambda r: r.get("net") or 0.0, reverse=True)
    return {"family": FAMILY, "rows": rows, "skipped": skipped}
