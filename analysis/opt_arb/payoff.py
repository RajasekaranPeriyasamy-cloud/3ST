"""Expiry payoff curve for a scanned structure.

Computed here rather than in the browser for the same reason the delta-velocity
aggregation is: it is arithmetic with edge cases (breakevens, unbounded tails,
cross-contract convergence) and it should be testable, not reimplemented in
TypeScript.

The curve is **piecewise linear with kinks only at the strikes**, so the sample
grid always includes every strike. Between two consecutive samples the payoff is
exactly linear, which makes the interpolated breakevens exact rather than
approximate.

Two lines are returned. ``gross`` is the structure's own payoff; ``net`` is that
minus the charges the scanner already computed. For a model-free arbitrage the
net line sits **flat and above zero** across the whole range — that flat line is
the whole point of the picture, and a payoff that dips below zero anywhere is
the fastest way to see that a row is not the risk-free trade it was ranked as.

**Cross-contract rows carry an assumption.** A big and a mini leg settle against
their own futures contracts. When those are the same month (Tier A) they
converge and one shared price axis is correct. When they are not (Tier B — gold
and silver today) the flat line is a fiction, so ``assumptions`` says so and the
caller is expected to surface it.
"""

from __future__ import annotations

from typing import Any

# Points added between kinks. The curve is exact at the kinks regardless; this
# only controls how smooth the tails look.
GRID_POINTS = 61


def leg_intrinsic(leg: dict[str, Any], spot: float) -> float:
    """Value of one leg at expiry, per unit, before its premium."""
    strike = float(leg.get("strike") or 0.0)
    option_type = str(leg.get("option_type") or "").upper()
    if option_type == "CE":
        return max(spot - strike, 0.0)
    if option_type == "PE":
        return max(strike - spot, 0.0)
    # A future or unknown leg tracks the underlying one-for-one.
    return spot - strike


def leg_pnl(leg: dict[str, Any], spot: float) -> float:
    """Signed rupee P&L for one leg at an expiry price."""
    sign = 1.0 if str(leg.get("side") or "BUY").upper() == "BUY" else -1.0
    units = float(leg.get("units") or 0.0)
    price = float(leg.get("price") or 0.0)
    return sign * (leg_intrinsic(leg, spot) - price) * units


def payoff_at(legs: list[dict[str, Any]], spot: float) -> float:
    return sum(leg_pnl(leg, spot) for leg in legs)


def _price_range(
    legs: list[dict[str, Any]], spot: float | None
) -> tuple[float, float, list[float]]:
    strikes = sorted({float(leg["strike"]) for leg in legs if leg.get("strike")})
    if not strikes:
        anchor = float(spot or 100.0)
        return anchor * 0.8, anchor * 1.2, []

    lo_strike, hi_strike = strikes[0], strikes[-1]
    anchor = float(spot) if spot else 0.5 * (lo_strike + hi_strike)
    # Wide enough that both tails are visibly flat or visibly diverging, and
    # never narrower than the strike span itself.
    pad = max(0.12 * anchor, (hi_strike - lo_strike) * 1.5, anchor * 0.02)
    return max(lo_strike - pad, 0.0), hi_strike + pad, strikes


def _breakevens(points: list[dict[str, float]], key: str) -> list[float]:
    """Zero crossings, exact because the curve is linear between samples."""
    out: list[float] = []
    for a, b in zip(points, points[1:], strict=False):
        y0, y1 = a[key], b[key]
        if y0 == 0.0:
            out.append(a["spot"])
            continue
        if (y0 < 0) != (y1 < 0):
            span = y1 - y0
            if span == 0:
                continue
            out.append(a["spot"] + (b["spot"] - a["spot"]) * (-y0 / span))
    if points and points[-1][key] == 0.0:
        out.append(points[-1]["spot"])
    return [round(x, 2) for x in sorted(set(out))]


def build(
    legs: list[dict[str, Any]],
    *,
    spot: float | None = None,
    charges: float = 0.0,
    assumptions: list[str] | None = None,
) -> dict[str, Any]:
    """Expiry payoff curve plus the summary a trader reads first.

    ``charges`` shifts the whole curve down by a constant — costs do not depend
    on where the underlying finishes, so the net line is the gross line moved
    down, never a different shape.
    """
    legs = [leg for leg in (legs or []) if leg]
    if not legs:
        return {
            "points": [],
            "strikes": [],
            "spot": spot,
            "charges": round(float(charges), 2),
            "summary": {},
            "assumptions": list(assumptions or []),
        }

    lo, hi, strikes = _price_range(legs, spot)
    grid = {lo, hi, *strikes}
    if spot:
        grid.add(float(spot))
    step = (hi - lo) / max(GRID_POINTS - 1, 1)
    grid.update(lo + step * i for i in range(GRID_POINTS))

    cost = float(charges)
    points = [
        {
            "spot": round(s, 2),
            "gross": round(payoff_at(legs, s), 2),
            "net": round(payoff_at(legs, s) - cost, 2),
        }
        for s in sorted(grid)
    ]

    nets = [p["net"] for p in points]
    lowest, highest = min(nets), max(nets)
    # "Flat" to the paisa: a genuine arbitrage has no exposure to where the
    # underlying finishes, and that is exactly what should be visible.
    flat = (highest - lowest) < 0.01

    # The tails are where an unbounded structure shows itself. Comparing the two
    # end samples against their neighbours tells you whether the curve is still
    # moving at the edge of the plotted range.
    left_slope = points[1]["net"] - points[0]["net"] if len(points) > 1 else 0.0
    right_slope = points[-1]["net"] - points[-2]["net"] if len(points) > 1 else 0.0
    unbounded_loss = left_slope > 0.01 or right_slope < -0.01

    return {
        "points": points,
        "strikes": [round(s, 2) for s in strikes],
        "spot": round(float(spot), 2) if spot else None,
        "charges": round(cost, 2),
        "summary": {
            "flat": flat,
            "max_profit": round(highest, 2),
            "max_loss": round(lowest, 2),
            "profit_at_spot": (
                round(payoff_at(legs, float(spot)) - cost, 2) if spot else None
            ),
            "breakevens": _breakevens(points, "net"),
            # Risk-free means the curve never dips to or below zero anywhere in
            # range — NOT that it is flat. A butterfly bought below zero has a
            # tent shape and is still free money; requiring flatness here would
            # report the whole butterfly family as risky.
            "risk_free": lowest > 0,
            "unbounded_loss": unbounded_loss,
            "range": [points[0]["spot"], points[-1]["spot"]],
        },
        "assumptions": list(assumptions or []),
    }


def row_assumptions(row: dict[str, Any]) -> list[str]:
    """What the reader has to accept for this row's curve to be true."""
    notes: list[str] = []
    exchanges = {str(leg.get("exchange") or "").upper() for leg in row.get("legs") or []}
    symbols = {str(leg.get("tradingsymbol") or "")[:6] for leg in row.get("legs") or []}

    if row.get("family") == "xcontract":
        if row.get("tier") == "A":
            notes.append(
                "Both legs settle against the same futures month, so one price axis is exact."
            )
        else:
            notes.append(
                "The two legs settle against DIFFERENT futures months — this curve assumes a "
                "convergence that will not happen. Read it as indicative only."
            )
    if len(exchanges) > 1:
        notes.append(f"Legs span {', '.join(sorted(exchanges))} — settlement rules differ.")
    elif row.get("family") == "xcontract" and len(symbols) > 1:
        notes.append("Plotted against a single underlying price shared by both contracts.")

    if row.get("family") == "box":
        exercise = row.get("exercise") or {}
        if exercise.get("applies"):
            notes.append(
                "Curve is net of the estimated exercise levy at the current spot; the actual "
                "levy moves with where the underlying finishes."
            )
    for warning in row.get("warnings") or []:
        notes.append(str(warning))
    return notes


def attach(rows: list[dict[str, Any]], *, spot: float | None = None) -> list[dict[str, Any]]:
    """Add a ``payoff`` block to every row, in place."""
    for row in rows:
        legs = row.get("legs") or []
        if not legs:
            continue
        row["payoff"] = build(
            legs,
            spot=spot,
            charges=float(row.get("cost") or 0.0),
            assumptions=row_assumptions(row),
        )
    return rows
