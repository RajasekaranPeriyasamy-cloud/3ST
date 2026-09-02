"""Big-vs-mini cross-contract arbitrage on MCX.

The identity: when a big contract and its mini share an option expiry **and**
that expiry references the same futures month, an option on each at the same
strike is the same claim on the same underlying, quoted in the same unit. The
per-unit premiums must be equal. Any gap, net of charges, is model-free edge —
no volatility model, no greeks, no view.

When the expiries or the referenced futures differ (GOLD/GOLDM and
SILVER/SILVERM today), the gap is mostly futures carry plus an expiry stub.
Those rows are still produced, because watching the spread is useful, but they
are tagged ``tier="B"`` and carry a warning. ``require_clean=True`` (the
scanner default) drops them entirely.

Sizing: one big lot is offset by ``pair.ratio`` mini lots. Both sides go out as
a single order each, so the charge model sees two orders — but the mini order's
turnover is computed on ``ratio x mini_units_per_lot`` units, which is where the
asymmetry actually shows up.
"""

from __future__ import annotations

from typing import Any

from analysis.opt_arb import costs, payoff, universe
from analysis.opt_arb.quotes import Quote, fetch_quotes, quote_key
from analysis.opt_arb.universe import MiniPair

FAMILY = "xcontract"


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
        "expiry": contract["expiry"],
    }


def _direction(
    pair: MiniPair,
    big_c: dict[str, Any],
    mini_c: dict[str, Any],
    big_q: Quote,
    mini_q: Quote,
    *,
    buy_big: bool,
    lots: int,
) -> dict[str, Any] | None:
    """One side of the trade. ``buy_big`` buys the big and sells the minis."""
    big_side = "BUY" if buy_big else "SELL"
    mini_side = "SELL" if buy_big else "BUY"

    big_px = big_q.executable(big_side)
    mini_px = mini_q.executable(mini_side)
    if big_px is None or mini_px is None:
        return None

    # Edge per unit: what you receive minus what you pay, both per unit.
    edge_per_unit = (mini_px - big_px) if buy_big else (big_px - mini_px)
    if edge_per_unit <= 0:
        return None

    mini_lots = int(round(pair.ratio)) * lots
    big_units = pair.big_units_per_lot * lots
    mini_units = pair.mini_units_per_lot * mini_lots
    gross = edge_per_unit * big_units

    legs = [
        _leg(big_c, big_side, big_px, big_units),
        _leg(mini_c, mini_side, mini_px, mini_units),
    ]
    cost = costs.combo_cost(legs, round_trip=True)

    return {
        "direction": f"{big_side} {pair.big} / {mini_side} {pair.mini}",
        "buy_big": buy_big,
        "lots": lots,
        "mini_lots": mini_lots,
        "big_price": round(float(big_px), 4),
        "mini_price": round(float(mini_px), 4),
        "edge_per_unit": round(edge_per_unit, 4),
        "gross": round(gross, 2),
        "cost": cost["total"],
        "net": round(gross - cost["total"], 2),
        "legs": legs,
        "cost_detail": cost,
    }


def _max_lots(
    pair: MiniPair,
    big_c: dict[str, Any],
    mini_c: dict[str, Any],
    big_q: Quote,
    mini_q: Quote,
    *,
    buy_big: bool,
) -> int:
    """Lots the top of book supports on both sides.

    The mini side is the binding constraint in practice — offsetting one big lot
    needs ``ratio`` mini lots, and a thin mini book is exactly why the printed
    spread on these pairs so often cannot be taken.
    """
    big_avail = universe.lots_available(
        big_q.depth_qty("BUY" if buy_big else "SELL"), big_c.get("lot_size")
    )
    mini_avail = universe.lots_available(
        mini_q.depth_qty("SELL" if buy_big else "BUY"), mini_c.get("lot_size")
    )
    ratio = max(int(round(pair.ratio)), 1)
    return max(0, min(big_avail, mini_avail // ratio))


def _priced_cell(
    pair: MiniPair,
    big_c: dict[str, Any],
    mini_c: dict[str, Any],
    big_q: Quote,
    mini_q: Quote,
    *,
    buy_big: bool,
    lots: int,
) -> dict[str, Any] | None:
    """Price one direction whatever the sign.

    ``_direction`` bails out on a non-positive edge because the scanner only
    wants opportunities. A worksheet needs every cell filled, including the
    losing ones — that is what makes the handful of good cells legible.
    """
    big_side = "BUY" if buy_big else "SELL"
    mini_side = "SELL" if buy_big else "BUY"
    big_px = big_q.executable(big_side)
    mini_px = mini_q.executable(mini_side)
    if big_px is None or mini_px is None:
        return None

    edge_per_unit = (mini_px - big_px) if buy_big else (big_px - mini_px)
    mini_lots = int(round(pair.ratio)) * lots
    big_units = pair.big_units_per_lot * lots
    gross = edge_per_unit * big_units
    cost = costs.combo_cost(
        [
            _leg(big_c, big_side, big_px, big_units),
            _leg(mini_c, mini_side, mini_px, pair.mini_units_per_lot * mini_lots),
        ],
        round_trip=True,
    )
    return {
        "edge_per_unit": round(edge_per_unit, 4),
        "gross": round(gross, 2),
        "cost": cost["total"],
        "net": round(gross - cost["total"], 2),
        "big_price": round(float(big_px), 4),
        "mini_price": round(float(mini_px), 4),
        "max_lots": _max_lots(pair, big_c, mini_c, big_q, mini_q, buy_big=buy_big),
    }


def scan_pair(
    pair: MiniPair,
    *,
    expiry: str | None = None,
    lots: int = 1,
    min_net: float = 0.0,
    require_clean: bool = True,
    require_depth: bool = True,
    quotes: dict[str, Quote] | None = None,
) -> dict[str, Any]:
    """Scan one big/mini pair for per-unit premium gaps at shared strikes."""
    status = universe.pair_status(pair)
    shared = status["shared_expiries"]
    clean_expiries = status["clean_expiries"]

    # Pick the expiry, then classify *that* expiry. A pair can be a carry spread
    # in the front month and a true arbitrage further out — GOLD/GOLDM is
    # exactly that — so reusing the pair-level flag here would skip a real
    # opportunity, which is what it did until this was split out.
    if expiry:
        target = expiry
    elif require_clean:
        target = clean_expiries[0] if clean_expiries else None
    else:
        target = shared[0] if shared else None

    if not target:
        return {
            "pair": status,
            "expiry": None,
            "rows": [],
            "skipped": (
                status["reason"]
                if shared
                else "no shared option expiry between the two contracts"
            ),
        }

    leg_status = universe.expiry_status(pair, target, target)
    warnings: list[str] = []
    if not leg_status["clean"]:
        if require_clean:
            return {
                "pair": status,
                "expiry": target,
                "rows": [],
                "skipped": leg_status["reason"],
            }
        warnings.append(f"Tier B — {leg_status['reason']}")

    big_map = universe.strike_map(pair.big, pair.exchange, target)
    mini_map = universe.strike_map(pair.mini, pair.exchange, target)
    common = sorted(set(big_map) & set(mini_map))
    if not common:
        return {"pair": status, "expiry": target, "rows": [], "skipped": "no common strikes"}

    wanted: list[str] = []
    for strike in common:
        for opt in ("CE", "PE"):
            big_c = big_map[strike].get(opt)
            mini_c = mini_map[strike].get(opt)
            if big_c and mini_c:
                wanted.append(quote_key(big_c["exchange"], big_c["tradingsymbol"]))
                wanted.append(quote_key(mini_c["exchange"], mini_c["tradingsymbol"]))

    book = quotes if quotes is not None else fetch_quotes(wanted)

    rows: list[dict[str, Any]] = []
    for strike in common:
        for opt in ("CE", "PE"):
            big_c = big_map[strike].get(opt)
            mini_c = mini_map[strike].get(opt)
            if not big_c or not mini_c:
                continue
            big_q = book.get(quote_key(big_c["exchange"], big_c["tradingsymbol"]))
            mini_q = book.get(quote_key(mini_c["exchange"], mini_c["tradingsymbol"]))
            if big_q is None or mini_q is None:
                continue

            for buy_big in (True, False):
                found = _direction(pair, big_c, mini_c, big_q, mini_q, buy_big=buy_big, lots=lots)
                if not found or found["net"] < min_net:
                    continue

                max_lots = _max_lots(pair, big_c, mini_c, big_q, mini_q, buy_big=buy_big)
                row_warnings = list(warnings)
                if max_lots < lots:
                    if require_depth:
                        continue
                    row_warnings.append(
                        f"top of book supports {max_lots} lot(s), not {lots}"
                    )

                rows.append(
                    {
                        "family": FAMILY,
                        "tier": "A" if leg_status["clean"] else "B",
                        "id": f"{pair.key}:{target}:{strike:g}:{opt}:{'B' if buy_big else 'S'}",
                        "pair_key": pair.key,
                        "label": pair.label,
                        "expiry": target,
                        "strike": strike,
                        "option_type": opt,
                        "unit": pair.unit,
                        "ratio": pair.ratio,
                        "max_lots": max_lots,
                        "big_quote": big_q.as_dict(),
                        "mini_quote": mini_q.as_dict(),
                        "warnings": row_warnings,
                        **found,
                    }
                )

    rows.sort(key=lambda r: r["net"], reverse=True)
    # Derived from the big contract's own book — the pair has no index to quote,
    # and this keeps the payoff chart on the same data the row was priced from.
    from analysis.opt_arb.scanner import implied_spot

    payoff.attach(rows, spot=implied_spot(big_map, book))
    return {"pair": status, "expiry": target, "rows": rows, "skipped": None}


def scan(
    *,
    pair_keys: list[str] | None = None,
    expiry: str | None = None,
    lots: int = 1,
    min_net: float = 0.0,
    require_clean: bool = True,
    require_depth: bool = True,
) -> dict[str, Any]:
    """Scan every configured big/mini pair."""
    selected = (
        [p for p in universe.MINI_PAIRS if p.key in set(pair_keys)]
        if pair_keys
        else list(universe.MINI_PAIRS)
    )
    results = [
        scan_pair(
            pair,
            expiry=expiry if len(selected) == 1 else None,
            lots=lots,
            min_net=min_net,
            require_clean=require_clean,
            require_depth=require_depth,
        )
        for pair in selected
    ]
    rows = [row for r in results for row in r["rows"]]
    rows.sort(key=lambda r: r["net"], reverse=True)
    return {
        "family": FAMILY,
        "pairs": [r["pair"] for r in results],
        "skipped": {r["pair"]["key"]: r["skipped"] for r in results if r["skipped"]},
        "rows": rows,
    }


def _forward(smap: dict[float, dict[str, Any]], book: dict[str, Quote]) -> float | None:
    from analysis.opt_arb.scanner import implied_spot

    return implied_spot(smap, book)


def sheet(
    pair: MiniPair,
    *,
    expiry: str | None = None,
    option_type: str = "CE",
    lots: int = 1,
    threshold: float = 0.0,
    strike_window: int = 12,
) -> dict[str, Any]:
    """Strike grid for one pair and side: BUY and SELL of the spread, per strike.

    The same shape as a vendor big-vs-mini worksheet, with three differences
    that matter:

    * **Cells are net of charges.** The vendor number is the raw spread; on a
      pair whose round trip costs a few hundred rupees that is the difference
      between a green cell and a losing trade.
    * **The header basis is named for what it is.** A vendor sheet prints a
      single number at the top (``-1537.00`` on gold) without saying that on a
      pair whose legs reference different futures months, that number *is* the
      carry — not an opportunity. Here it is measured as the gap between the two
      contracts' own implied forwards and labelled accordingly.
    * **A pair with no shared expiry says so.** GOLD and GOLDM do not expire on
      the same day today, so the grid falls back to each side's front expiry and
      flags that the two columns are not the same contract month.

    ``buy`` is BUY-the-big / SELL-the-minis; ``sell`` is the reverse. Both are
    priced at the side of the book you would actually hit, so the two are not
    mirror images — the gap between them is the round-trip cost.
    """
    opt = str(option_type).upper()
    status = universe.pair_status(pair)
    shared = status["shared_expiries"]
    clean_expiries = status["clean_expiries"]

    if expiry and expiry in shared:
        big_expiry = mini_expiry = expiry
    elif clean_expiries:
        # Default to an expiry where the grid is actually an arbitrage grid.
        big_expiry = mini_expiry = clean_expiries[0]
    elif shared:
        big_expiry = mini_expiry = shared[0]
    else:
        big_expiry = status["front_expiry"]["big"]
        mini_expiry = status["front_expiry"]["mini"]

    if not big_expiry or not mini_expiry:
        return {
            "pair": status,
            "option_type": opt,
            "rows": [],
            "skipped": "neither contract has a listed option expiry",
        }

    big_map = universe.strike_map(pair.big, pair.exchange, big_expiry)
    mini_map = universe.strike_map(pair.mini, pair.exchange, mini_expiry)
    strikes = sorted(set(big_map) & set(mini_map))
    if not strikes:
        return {
            "pair": status,
            "option_type": opt,
            "rows": [],
            "skipped": "no strike is listed on both contracts",
        }

    keys: list[str] = []
    for strike in strikes:
        for smap in (big_map, mini_map):
            leg = smap.get(strike, {}).get(opt)
            if leg:
                keys.append(quote_key(leg["exchange"], leg["tradingsymbol"]))
    # Both sides' full CE+PE books are needed for the forwards, not just `opt`.
    for smap in (big_map, mini_map):
        for legs in smap.values():
            for leg in legs.values():
                keys.append(quote_key(leg["exchange"], leg["tradingsymbol"]))
    book = fetch_quotes(keys)

    big_fwd = _forward(big_map, book)
    mini_fwd = _forward(mini_map, book)
    basis = (big_fwd - mini_fwd) if (big_fwd is not None and mini_fwd is not None) else None

    # Window around the money. Crude lists ~190 strikes; the far ITM ones carry
    # stale, arbitrarily wide books whose cells run to six figures and swamp the
    # handful of rows anyone would trade.
    atm = min(strikes, key=lambda k: abs(k - big_fwd)) if big_fwd is not None else None
    if atm is not None and strike_window > 0:
        centre = strikes.index(atm)
        lo = max(0, centre - strike_window)
        strikes = strikes[lo : centre + strike_window + 1]

    rows: list[dict[str, Any]] = []
    for strike in strikes:
        big_c = big_map[strike].get(opt)
        mini_c = mini_map[strike].get(opt)
        cell: dict[str, Any] = {"strike": strike, "buy": None, "sell": None}
        if big_c and mini_c:
            big_q = book.get(quote_key(big_c["exchange"], big_c["tradingsymbol"]))
            mini_q = book.get(quote_key(mini_c["exchange"], mini_c["tradingsymbol"]))
            if big_q is not None and mini_q is not None:
                for buy_big, key in ((True, "buy"), (False, "sell")):
                    priced = _priced_cell(
                        pair, big_c, mini_c, big_q, mini_q, buy_big=buy_big, lots=lots
                    )
                    if priced is not None:
                        priced["passes"] = priced["net"] >= threshold
                        cell[key] = priced
        rows.append(cell)

    leg_status = universe.expiry_status(pair, big_expiry, mini_expiry)
    return {
        "pair": status,
        "clean": leg_status["clean"],
        "reason": leg_status["reason"],
        "option_type": opt,
        "lots": lots,
        "threshold": threshold,
        "unit": pair.unit,
        "ratio": pair.ratio,
        "expiry": {"big": big_expiry, "mini": mini_expiry, "matched": big_expiry == mini_expiry},
        "forward": {"big": big_fwd, "mini": mini_fwd},
        "basis": {
            "value": round(basis, 2) if basis is not None else None,
            "unit": pair.unit,
            "note": (
                "Both legs reference the same futures month — this gap is a live "
                "dislocation, not carry."
                if leg_status["clean"]
                else "At this expiry the two contracts reference DIFFERENT futures months, "
                "so this gap is mostly carry. It is not the size of the opportunity."
            ),
        },
        "atm_strike": atm,
        "rows": rows,
        "skipped": None,
    }
