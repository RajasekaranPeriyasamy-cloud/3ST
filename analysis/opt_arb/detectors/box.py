"""Box spread — put-call parity between two strikes, and the STT that eats it.

A box is a long call spread plus a long put spread on the same two strikes and
expiry. At expiry it is worth exactly the strike difference, whatever the
underlying does::

    (C(K1) - C(K2)) + (P(K2) - P(K1)) = (K2 - K1) * exp(-r * T)

So a long box bought below the discounted width, or a short box sold above it,
is arbitrage — in a market without transaction taxes.

**In India it usually is not, and the reason is exercise STT.** Cash-settled
index options are auto-exercised when in the money and the levy is 0.125% of
*intrinsic*, charged to the holder exercising. A long box always finishes with
a long leg struck at the far strike, whose intrinsic grows without bound as
spot travels. A short box's long legs sit at the near strike, so it is
materially cheaper to carry — an asymmetry this detector prices rather than
ignores (:func:`analysis.opt_arb.costs.box_exercise_cost`).

For **stock options** the problem is different and worse: they are physically
settled, so carrying a box to expiry means delivery obligations on both legs.
Those rows are surfaced with a warning and never with ``tier="A"``.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from analysis.opt_arb import costs, universe
from analysis.opt_arb.quotes import Quote, fetch_quotes, quote_key

FAMILY = "box"

DEFAULT_RATE_PCT = 6.5


def discounted_width(width: float, expiry: str, *, rate_pct: float = DEFAULT_RATE_PCT) -> float:
    """Present value of the box's terminal payoff."""
    try:
        days = (date.fromisoformat(str(expiry)[:10]) - date.today()).days
    except ValueError:
        days = 0
    years = max(days, 0) / 365.0
    return float(width) * math.exp(-(rate_pct / 100.0) * years)


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


def _side_prices(
    quotes: dict[str, Quote], contract: dict[str, Any], side: str
) -> tuple[float | None, int]:
    quote = quotes.get(quote_key(contract["exchange"], contract["tradingsymbol"]))
    if quote is None:
        return None, 0
    return quote.executable(side), quote.depth_qty(side)  # type: ignore[arg-type]


def _evaluate(
    ce_lo: dict[str, Any],
    ce_hi: dict[str, Any],
    pe_lo: dict[str, Any],
    pe_hi: dict[str, Any],
    quotes: dict[str, Quote],
    *,
    long_box: bool,
    pv: float,
    units: float,
    spot: float | None,
) -> dict[str, Any] | None:
    """Long box: buy CE(lo), sell CE(hi), sell PE(lo), buy PE(hi)."""
    sides = (
        {"ce_lo": "BUY", "ce_hi": "SELL", "pe_lo": "SELL", "pe_hi": "BUY"}
        if long_box
        else {"ce_lo": "SELL", "ce_hi": "BUY", "pe_lo": "BUY", "pe_hi": "SELL"}
    )
    contracts = {"ce_lo": ce_lo, "ce_hi": ce_hi, "pe_lo": pe_lo, "pe_hi": pe_hi}

    prices: dict[str, float] = {}
    depths: list[int] = []
    for key, contract in contracts.items():
        price, depth = _side_prices(quotes, contract, sides[key])
        if price is None:
            return None
        prices[key] = price
        depths.append(universe.lots_available(depth, contract.get("lot_size")))

    # Same expression both ways: each price is already taken from the side this
    # direction hits, so it reads as cash paid for a long box and cash received
    # for a short one.
    cash = prices["ce_lo"] - prices["ce_hi"] - prices["pe_lo"] + prices["pe_hi"]
    edge_per_unit = (pv - cash) if long_box else (cash - pv)

    if edge_per_unit <= 0:
        return None

    legs = [_leg(contracts[k], sides[k], prices[k], units) for k in contracts]
    cost = costs.combo_cost(legs, round_trip=False)
    segment = universe.cost_segment(ce_lo["exchange"], ce_lo["name"])
    exercise = (
        costs.box_exercise_cost(
            segment,
            spot=spot,
            lower_strike=ce_lo["strike"],
            upper_strike=ce_hi["strike"],
            units=units,
            long_box=long_box,
        )
        if spot is not None
        else {"applies": False, "reason": "spot unavailable", "intrinsic": 0.0, "stt": 0.0}
    )

    gross = edge_per_unit * units
    total_cost = cost["total"] + float(exercise["stt"])
    return {
        "direction": "long_box" if long_box else "short_box",
        "cash": round(cash, 4),
        "pv_width": round(pv, 4),
        "edge_per_unit": round(edge_per_unit, 4),
        "gross": round(gross, 2),
        "cost": round(total_cost, 2),
        "net": round(gross - total_cost, 2),
        "legs": legs,
        "cost_detail": cost,
        "exercise": exercise,
        "max_lots": min(depths) if depths else 0,
    }


def scan(
    name: str,
    exchange: str,
    expiry: str,
    *,
    spot: float | None = None,
    max_width: float | None = None,
    strike_window: int = 15,
    lots: int = 1,
    min_net: float = 0.0,
    rate_pct: float = DEFAULT_RATE_PCT,
    quotes: dict[str, Quote] | None = None,
) -> dict[str, Any]:
    """Box violations across strike pairs, both directions.

    ``spot`` is only used to estimate exercise STT. Without it the levy is
    reported as not applicable and the net figure is optimistic — the scanner
    passes one in.
    """
    smap = universe.strike_map(name, exchange, expiry)
    complete = sorted(k for k, v in smap.items() if "CE" in v and "PE" in v)
    if len(complete) < 2:
        return {"family": FAMILY, "rows": [], "skipped": "fewer than 2 complete strikes"}

    mid_index = len(complete) // 2
    lo = max(0, mid_index - strike_window)
    hi = min(len(complete), mid_index + strike_window + 1)
    strikes = complete[lo:hi]

    sample = smap[strikes[0]]["CE"]
    units = universe.units_per_lot(exchange, name, sample.get("lot_size")) * lots
    if units <= 0:
        return {
            "family": FAMILY,
            "rows": [],
            "skipped": f"no contract multiplier known for {exchange}:{name}",
        }

    step = min((b - a) for a, b in zip(strikes, strikes[1:], strict=False)) if len(strikes) > 1 else 0
    limit = max_width if max_width is not None else (step * 6 if step else 0)

    needed: set[str] = set()
    for strike in strikes:
        for contract in smap[strike].values():
            needed.add(quote_key(contract["exchange"], contract["tradingsymbol"]))
    book = quotes if quotes is not None else fetch_quotes(sorted(needed))

    physical = universe.is_physically_settled(exchange, name)
    warnings = (
        [
            "stock option — physically settled; a box carried to expiry is a "
            "delivery obligation, not a cash settlement"
        ]
        if physical
        else []
    )

    rows: list[dict[str, Any]] = []
    for i, k1 in enumerate(strikes):
        for k2 in strikes[i + 1 :]:
            width = k2 - k1
            if limit and width > limit:
                break
            pv = discounted_width(width, expiry, rate_pct=rate_pct)
            for long_box in (True, False):
                found = _evaluate(
                    smap[k1]["CE"],
                    smap[k2]["CE"],
                    smap[k1]["PE"],
                    smap[k2]["PE"],
                    book,
                    long_box=long_box,
                    pv=pv,
                    units=units,
                    spot=spot,
                )
                if not found or found["net"] < min_net:
                    continue
                rows.append(
                    {
                        "family": FAMILY,
                        # A physically-settled box is never model-free: you cannot
                        # assume the terminal payoff nets to cash.
                        "tier": "B" if physical else "A",
                        "id": f"{name}:{expiry}:{k1:g}-{k2:g}:{'L' if long_box else 'S'}",
                        "underlying": name,
                        "exchange": exchange,
                        "expiry": expiry,
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
