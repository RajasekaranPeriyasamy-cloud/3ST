"""Vertical spread bounds — the cheapest model-free check there is.

For two strikes ``K1 < K2`` on the same side and expiry, a vertical spread is
worth between zero and the strike difference::

    0 <= C(K1) - C(K2) <= K2 - K1
    0 <= P(K2) - P(K1) <= K2 - K1

Two violations follow, and both are arbitrage:

* **debit below zero** — the spread that can only pay you trades at a credit.
  This is also the strike-monotonicity check (a call cheaper than a
  higher-struck call, a put cheaper than a lower-struck one), so monotonicity
  needs no separate detector.
* **credit above the width** — you are paid more than the most you can owe.

Pairs are bounded by ``max_width`` because a violation between two strikes
hundreds of points apart is almost always one stale quote on an untraded wing
rather than something you can lift.
"""

from __future__ import annotations

from typing import Any

from analysis.opt_arb import costs, universe
from analysis.opt_arb.quotes import Quote, fetch_quotes, quote_key

FAMILY = "vertical"


def _quote(quotes: dict[str, Quote], contract: dict[str, Any]) -> Quote | None:
    return quotes.get(quote_key(contract["exchange"], contract["tradingsymbol"]))


def _leg(contract: dict[str, Any], side: str, price: float, units: float) -> dict[str, Any]:
    return {
        "exchange": contract["exchange"],
        "tradingsymbol": contract["tradingsymbol"],
        "segment": universe.cost_segment(contract["exchange"], contract["name"]),
        "side": side,
        "price": round(float(price), 4),
        "units": units,
        "strike": contract["strike"],
        "option_type": contract["option_type"],
    }


def _check(
    long_contract: dict[str, Any],
    short_contract: dict[str, Any],
    long_q: Quote,
    short_q: Quote,
    *,
    width: float,
    units: float,
) -> dict[str, Any] | None:
    """``long_contract`` is the leg you buy in the debit version of the spread."""
    long_ask = long_q.executable("BUY")
    short_bid = short_q.executable("SELL")
    long_bid = long_q.executable("SELL")
    short_ask = short_q.executable("BUY")

    if long_ask is not None and short_bid is not None:
        debit = long_ask - short_bid
        if debit < 0:
            legs = [
                _leg(long_contract, "BUY", long_ask, units),
                _leg(short_contract, "SELL", short_bid, units),
            ]
            cost = costs.combo_cost(legs, round_trip=True)
            gross = -debit * units
            return {
                "violation": "debit_below_zero",
                "price": round(debit, 4),
                "bound": 0.0,
                "gross": round(gross, 2),
                "cost": cost["total"],
                "net": round(gross - cost["total"], 2),
                "legs": legs,
                "max_lots": min(
                    universe.lots_available(long_q.depth_qty("BUY"), long_contract.get("lot_size")),
                    universe.lots_available(short_q.depth_qty("SELL"), short_contract.get("lot_size")),
                ),
            }

    if long_bid is not None and short_ask is not None:
        credit = long_bid - short_ask
        if credit > width:
            legs = [
                _leg(long_contract, "SELL", long_bid, units),
                _leg(short_contract, "BUY", short_ask, units),
            ]
            cost = costs.combo_cost(legs, round_trip=True)
            gross = (credit - width) * units
            return {
                "violation": "credit_above_width",
                "price": round(credit, 4),
                "bound": width,
                "gross": round(gross, 2),
                "cost": cost["total"],
                "net": round(gross - cost["total"], 2),
                "legs": legs,
                "max_lots": min(
                    universe.lots_available(long_q.depth_qty("SELL"), long_contract.get("lot_size")),
                    universe.lots_available(short_q.depth_qty("BUY"), short_contract.get("lot_size")),
                ),
            }
    return None


def scan(
    name: str,
    exchange: str,
    expiry: str,
    *,
    max_width: float | None = None,
    strike_window: int = 25,
    lots: int = 1,
    min_net: float = 0.0,
    quotes: dict[str, Quote] | None = None,
) -> dict[str, Any]:
    """Every strike pair within ``max_width``, both option types."""
    smap = universe.strike_map(name, exchange, expiry)
    all_strikes = sorted(smap)
    if len(all_strikes) < 2:
        return {"family": FAMILY, "rows": [], "skipped": "fewer than 2 strikes"}

    mid_index = len(all_strikes) // 2
    lo = max(0, mid_index - strike_window)
    hi = min(len(all_strikes), mid_index + strike_window + 1)
    strikes = all_strikes[lo:hi]

    sample = next((c for v in smap.values() for c in v.values()), None)
    units = universe.units_per_lot(exchange, name, (sample or {}).get("lot_size")) * lots
    if units <= 0:
        return {
            "family": FAMILY,
            "rows": [],
            "skipped": f"no contract multiplier known for {exchange}:{name}",
        }

    step = min((b - a) for a, b in zip(strikes, strikes[1:], strict=False)) if len(strikes) > 1 else 0
    limit = max_width if max_width is not None else (step * 10 if step else 0)

    needed: set[str] = set()
    for strike in strikes:
        for contract in smap[strike].values():
            needed.add(quote_key(contract["exchange"], contract["tradingsymbol"]))
    book = quotes if quotes is not None else fetch_quotes(sorted(needed))

    warnings = (
        ["stock option — physically settled, an unfilled leg becomes a delivery obligation"]
        if universe.is_physically_settled(exchange, name)
        else []
    )

    rows: list[dict[str, Any]] = []
    for i, k1 in enumerate(strikes):
        for k2 in strikes[i + 1 :]:
            width = k2 - k1
            if limit and width > limit:
                break
            for opt in ("CE", "PE"):
                c1 = smap[k1].get(opt)
                c2 = smap[k2].get(opt)
                if not c1 or not c2:
                    continue
                q1 = _quote(book, c1)
                q2 = _quote(book, c2)
                if q1 is None or q2 is None:
                    continue
                # The debit leg is the lower strike for calls, the higher for puts.
                long_c, short_c = (c1, c2) if opt == "CE" else (c2, c1)
                long_q, short_q = (q1, q2) if opt == "CE" else (q2, q1)
                found = _check(long_c, short_c, long_q, short_q, width=width, units=units)
                if not found or found["net"] < min_net:
                    continue
                rows.append(
                    {
                        "family": FAMILY,
                        "tier": "A",
                        "id": f"{name}:{expiry}:{opt}:{k1:g}-{k2:g}",
                        "underlying": name,
                        "exchange": exchange,
                        "expiry": expiry,
                        "option_type": opt,
                        "lower_strike": k1,
                        "upper_strike": k2,
                        "width": width,
                        "lots": lots,
                        "warnings": warnings,
                        **found,
                    }
                )

    rows.sort(key=lambda r: r["net"], reverse=True)
    return {"family": FAMILY, "rows": rows, "skipped": None}
