"""Sweep orchestration: universe -> quotes -> detectors -> net-of-cost ranking.

One quote fetch per underlying/expiry feeds every single-underlying detector,
because the binding constraint on this desk is the quote budget, not CPU. Kite's
REST ``quote()`` takes 500 instruments per call and is rate limited; a nearest
NIFTY expiry alone is ~240 contracts. Fetching once and passing the book into
each detector keeps a full three-family sweep of one expiry at a single call.

Nothing here places an order. Rows carry the exact legs at the exact prices they
were priced on, which is what an execution path would need — but wiring that up
is a separate, operator-approved change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from analysis.opt_arb import payoff, store, universe
from analysis.opt_arb.detectors import box, butterfly, vertical, xcontract
from analysis.opt_arb.quotes import Quote, fetch_quotes, quote_key

IST = ZoneInfo("Asia/Kolkata")

SINGLE_UNDERLYING_FAMILIES = ("butterfly", "vertical", "box")


def implied_spot(
    smap: dict[float, dict[str, dict[str, Any]]], quotes: dict[str, Quote]
) -> float | None:
    """Synthetic forward from the strike where CE and PE mids are closest.

    ``F = K + CE - PE`` at the strike with the smallest |CE - PE|, i.e. the
    strike nearest at-the-money. Derived from the book already fetched rather
    than from a separate index/future quote — one less API call, and it works
    the same way for an index, a stock and a commodity.
    """
    best: tuple[float, float] | None = None
    for strike, legs in smap.items():
        ce, pe = legs.get("CE"), legs.get("PE")
        if not ce or not pe:
            continue
        ce_q = quotes.get(quote_key(ce["exchange"], ce["tradingsymbol"]))
        pe_q = quotes.get(quote_key(pe["exchange"], pe["tradingsymbol"]))
        if ce_q is None or pe_q is None:
            continue
        ce_mid, pe_mid = ce_q.mid, pe_q.mid
        if ce_mid is None or pe_mid is None:
            continue
        gap = abs(ce_mid - pe_mid)
        if best is None or gap < best[0]:
            best = (gap, strike + ce_mid - pe_mid)
    return round(best[1], 2) if best else None


def _window_keys(
    smap: dict[float, dict[str, dict[str, Any]]], strike_window: int
) -> tuple[list[str], dict[float, dict[str, dict[str, Any]]]]:
    strikes = sorted(smap)
    mid = len(strikes) // 2
    lo = max(0, mid - strike_window)
    hi = min(len(strikes), mid + strike_window + 1)
    windowed = {k: smap[k] for k in strikes[lo:hi]}
    keys = [
        quote_key(c["exchange"], c["tradingsymbol"])
        for legs in windowed.values()
        for c in legs.values()
    ]
    return keys, windowed


def apply_depth_gate(
    rows: list[dict[str, Any]], *, lots: int, require_depth: bool
) -> list[dict[str, Any]]:
    """Drop — or annotate — rows the top of book cannot fill at the asked size.

    The single-underlying detectors report ``max_lots`` but do not gate on it,
    because the butterfly sheet wants every cell whether or not it is fillable.
    The gate belongs here, where it applies to all four families identically:
    without it the ranking is led by deep-ITM strikes whose "violation" is one
    stale quote nobody will trade against.
    """
    kept: list[dict[str, Any]] = []
    for row in rows:
        available = int(row.get("max_lots") or 0)
        if available >= lots:
            kept.append(row)
            continue
        if require_depth:
            continue
        row = dict(row)
        row["warnings"] = [
            *(row.get("warnings") or []),
            f"top of book supports {available} lot(s), not {lots}",
        ]
        kept.append(row)
    return kept


def scan_underlying(
    name: str,
    exchange: str,
    expiry: str | None = None,
    *,
    families: list[str] | None = None,
    lots: int | None = None,
    min_net: float | None = None,
    strike_window: int | None = None,
    widths: list[float] | None = None,
    require_depth: bool | None = None,
    quotes: dict[str, Quote] | None = None,
) -> dict[str, Any]:
    """Run every single-underlying family against one expiry, on one quote fetch."""
    cfg = store.config()
    fams = [f for f in (families or cfg["families"]) if f in SINGLE_UNDERLYING_FAMILIES]
    lot_count = int(lots or cfg["lots"])
    floor = float(min_net if min_net is not None else cfg["min_net_rs"])
    window = int(strike_window or cfg["strike_window"])

    expiries = universe.option_expiries(name, exchange)
    target = expiry or (expiries[0] if expiries else None)
    if not target:
        return {
            "underlying": name,
            "exchange": exchange,
            "expiry": None,
            "rows": [],
            "skipped": {"universe": f"no listed option expiry for {exchange}:{name}"},
        }

    smap = universe.strike_map(name, exchange, target)
    if not smap:
        return {
            "underlying": name,
            "exchange": exchange,
            "expiry": target,
            "rows": [],
            "skipped": {"universe": f"no contracts for {exchange}:{name} {target}"},
        }

    keys, _ = _window_keys(smap, window)
    book = quotes if quotes is not None else fetch_quotes(keys)
    spot = implied_spot(smap, book)

    rows: list[dict[str, Any]] = []
    skipped: dict[str, Any] = {}

    if "butterfly" in fams:
        result = butterfly.scan(
            name,
            exchange,
            target,
            widths=widths,
            strike_window=window,
            lots=lot_count,
            min_net=floor,
            quotes=book,
        )
        rows.extend(result["rows"])
        if result.get("skipped"):
            skipped["butterfly"] = result["skipped"]

    if "vertical" in fams:
        result = vertical.scan(
            name,
            exchange,
            target,
            strike_window=window,
            lots=lot_count,
            min_net=floor,
            quotes=book,
        )
        rows.extend(result["rows"])
        if result.get("skipped"):
            skipped["vertical"] = result["skipped"]

    if "box" in fams:
        result = box.scan(
            name,
            exchange,
            target,
            spot=spot,
            strike_window=min(window, 15),
            lots=lot_count,
            min_net=floor,
            rate_pct=float(cfg["rate_pct"]),
            quotes=book,
        )
        rows.extend(result["rows"])
        if result.get("skipped"):
            skipped["box"] = result["skipped"]

    rows = apply_depth_gate(
        rows,
        lots=lot_count,
        require_depth=cfg["require_depth"] if require_depth is None else bool(require_depth),
    )
    rows.sort(key=lambda r: r.get("net") or 0.0, reverse=True)
    payoff.attach(rows, spot=spot)
    return {
        "underlying": name,
        "exchange": exchange,
        "expiry": target,
        "expiries": expiries,
        "implied_spot": spot,
        "quotes_fetched": len(book),
        "rows": rows,
        "skipped": skipped,
    }


def scan_all(
    *,
    families: list[str] | None = None,
    underlyings: list[dict[str, str]] | None = None,
    lots: int | None = None,
    min_net: float | None = None,
    require_clean: bool | None = None,
    require_depth: bool | None = None,
) -> dict[str, Any]:
    """Full sweep: cross-contract pairs plus every configured underlying.

    Underlying failures are per-underlying, not fatal: a name whose instruments
    are missing costs you that name's rows, not the sweep.
    """
    cfg = store.config()
    fams = list(families or cfg["families"])
    targets = underlyings if underlyings is not None else cfg["underlyings"]

    rows: list[dict[str, Any]] = []
    skipped: dict[str, Any] = {}
    pairs: list[dict[str, Any]] = []
    quote_calls = 0

    if "xcontract" in fams:
        result = xcontract.scan(
            lots=int(lots or cfg["lots"]),
            min_net=float(min_net if min_net is not None else cfg["min_net_rs"]),
            require_clean=cfg["require_clean"] if require_clean is None else bool(require_clean),
            require_depth=cfg["require_depth"] if require_depth is None else bool(require_depth),
        )
        rows.extend(result["rows"])
        pairs = result["pairs"]
        if result["skipped"]:
            skipped["xcontract"] = result["skipped"]

    per_underlying: list[dict[str, Any]] = []
    if any(f in SINGLE_UNDERLYING_FAMILIES for f in fams):
        for target in targets:
            name = str(target.get("name") or "").upper()
            exchange = str(target.get("exchange") or "").upper()
            if not name or not exchange:
                continue
            try:
                result = scan_underlying(
                    name,
                    exchange,
                    target.get("expiry"),
                    families=fams,
                    lots=lots,
                    min_net=min_net,
                    require_depth=require_depth,
                )
            except Exception as exc:  # one bad underlying must not kill the sweep
                skipped[f"{exchange}:{name}"] = str(exc)
                continue
            quote_calls += result.get("quotes_fetched", 0)
            rows.extend(result["rows"])
            if result.get("skipped"):
                skipped[f"{exchange}:{name}"] = result["skipped"]
            per_underlying.append(
                {
                    "underlying": name,
                    "exchange": exchange,
                    "expiry": result["expiry"],
                    "implied_spot": result.get("implied_spot"),
                    "rows": len(result["rows"]),
                }
            )

    rows.sort(key=lambda r: r.get("net") or 0.0, reverse=True)
    return {
        "generated_at": datetime.now(tz=IST).isoformat(timespec="seconds"),
        "families": fams,
        "pairs": pairs,
        "underlyings": per_underlying,
        "counts": {
            "rows": len(rows),
            "tier_a": sum(1 for r in rows if r.get("tier") == "A"),
            "tier_b": sum(1 for r in rows if r.get("tier") == "B"),
            "instruments_quoted": quote_calls,
        },
        "rows": rows,
        "skipped": skipped,
    }
