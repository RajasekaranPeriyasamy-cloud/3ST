"""OI VAR desk — full-chain VAR in Crores with VWAP and EOD OI change rankings."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import INDEX_OPTIONS, OI_VAR_DEFAULTS
from options.chain import get_chain, get_index_spot, nearest_expiry
from options.oi_var_store import CRORE, ensure_eod_baseline

QUOTE_BATCH = 500


def var_config() -> dict[str, Any]:
    d = OI_VAR_DEFAULTS
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "top_n": d["top_n"],
        "refresh_seconds": d["refresh_seconds"],
    }


def var_crores(oi: int | float | None, price: float | None) -> float | None:
    if oi is None or price is None or price <= 0:
        return None
    return round((float(oi) * float(price)) / CRORE, 2)


def moneyness_label(side: str, strike: float, spot: float) -> str:
    if side.lower() in ("call", "ce"):
        return "ITMCE" if strike < spot else "OTMCE"
    return "ITMPE" if strike > spot else "OTMPE"


def _flatten_chain_legs(chain: dict[str, Any]) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    for row in chain.get("strikes") or []:
        strike = float(row["strike"])
        if row.get("ce"):
            legs.append({"side": "call", "strike": strike, **row["ce"]})
        if row.get("pe"):
            legs.append({"side": "put", "strike": strike, **row["pe"]})
    return legs


def _quote_batches(keys: list[str]) -> dict[str, dict[str, Any]]:
    from kite_client import fetch_quote_batch

    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(keys), QUOTE_BATCH):
        chunk = keys[i : i + QUOTE_BATCH]
        try:
            out.update(fetch_quote_batch(chunk))
        except Exception:
            continue
    return out


def _leg_from_quote(
    leg: dict[str, Any],
    quote: dict[str, Any] | None,
    spot: float,
    baseline_oi: int | None,
) -> dict[str, Any] | None:
    if not quote:
        return None

    oi_raw = quote.get("oi")
    if oi_raw is None:
        oi_raw = quote.get("open_interest")
    if oi_raw is None:
        return None

    oi = int(oi_raw)
    ltp = float(quote.get("last_price") or 0)
    if ltp <= 0:
        return None

    atp = quote.get("average_traded_price")
    vwap_fallback = False
    if atp is not None and float(atp) > 0:
        vwap = float(atp)
    else:
        vwap = ltp
        vwap_fallback = True

    side = leg["side"]
    strike = float(leg["strike"])

    var_cr = var_crores(oi, ltp)

    delta_oi: int | None = None
    var_chg_cr: float | None = None
    if baseline_oi is not None:
        delta_oi = oi - baseline_oi
        var_chg_cr = var_crores(abs(delta_oi), ltp)
        if delta_oi < 0 and var_chg_cr is not None:
            var_chg_cr = -var_chg_cr

    return {
        "side": side,
        "strike": strike,
        "symbol": leg.get("tradingsymbol", ""),
        "instrument_token": leg.get("instrument_token"),
        "moneyness": moneyness_label(side, strike, spot),
        "oi": oi,
        "ltp": round(ltp, 2),
        "vwap": round(vwap, 2),
        "vwap_fallback": vwap_fallback,
        "delta_oi": delta_oi,
        "var_cr": var_cr,
        "var_chg_cr": var_chg_cr,
    }


def build_chain_legs(
    legs: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    spot: float,
    oi_baseline: dict[str, int],
) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []
    for leg in legs:
        exchange = leg.get("exchange", "NFO")
        symbol = leg.get("tradingsymbol")
        if not symbol:
            continue
        key = f"{exchange}:{symbol}"
        token = leg.get("instrument_token")
        baseline = oi_baseline.get(str(token)) if token else None
        row = _leg_from_quote(leg, quotes.get(key), spot, baseline)
        if row:
            built.append(row)
    return built


def _footer(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    def _sum(field: str) -> float | None:
        vals = [r[field] for r in rows if r.get(field) is not None]
        if not vals:
            return None
        return round(sum(float(v) for v in vals), 2)

    return {
        "var_cr_total": _sum("var_cr"),
        "var_chg_total": _sum("var_chg_cr"),
    }


def rank_tables(
    chain_legs: list[dict[str, Any]],
    top_n: int = 10,
) -> dict[str, Any]:
    ce = [l for l in chain_legs if l["side"] == "call"]
    pe = [l for l in chain_legs if l["side"] == "put"]

    def _top_oi(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: r.get("var_cr") or 0, reverse=True)[:top_n]

    def _top_chg(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with_chg = [r for r in rows if r.get("var_chg_cr") is not None]
        return sorted(with_chg, key=lambda r: r["var_chg_cr"], reverse=True)[:top_n]

    def _bottom_chg(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with_chg = [r for r in rows if r.get("var_chg_cr") is not None]
        return sorted(with_chg, key=lambda r: r["var_chg_cr"])[:top_n]

    ce_top_oi = _top_oi(ce)
    pe_top_oi = _top_oi(pe)
    ce_top_chg = _top_chg(ce)
    pe_top_chg = _top_chg(pe)
    ce_bottom = _bottom_chg(ce)
    pe_bottom = _bottom_chg(pe)

    return {
        "calls": {
            "top_oi": ce_top_oi,
            "top_chg": ce_top_chg,
            "bottom_chg": ce_bottom,
            "footer": {
                "top_oi": _footer(ce_top_oi),
                "top_chg": _footer(ce_top_chg),
                "bottom_chg": _footer(ce_bottom),
            },
        },
        "puts": {
            "top_oi": pe_top_oi,
            "top_chg": pe_top_chg,
            "bottom_chg": pe_bottom,
            "footer": {
                "top_oi": _footer(pe_top_oi),
                "top_chg": _footer(pe_top_chg),
                "bottom_chg": _footer(pe_bottom),
            },
        },
    }


def build_var_snapshot(
    underlying: str,
    expiry: str | None = None,
    *,
    top_n: int | None = None,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    defaults = OI_VAR_DEFAULTS
    n = top_n if top_n is not None else int(defaults["top_n"])

    exp = expiry or nearest_expiry(underlying)
    if not exp:
        raise RuntimeError(f"No expiries found for {underlying}")

    spot = get_index_spot(underlying)
    if spot is None:
        raise RuntimeError(f"Index spot unavailable for {underlying}")

    chain = get_chain(underlying, exp)
    raw_legs = _flatten_chain_legs(chain)
    if not raw_legs:
        raise RuntimeError(f"No option legs for {underlying} expiry {exp}")

    baseline_date, oi_baseline = ensure_eod_baseline(underlying, exp, raw_legs)

    quote_keys = [
        f"{leg.get('exchange', chain.get('exchange', 'NFO'))}:{leg['tradingsymbol']}"
        for leg in raw_legs
        if leg.get("tradingsymbol")
    ]
    quotes = _quote_batches(quote_keys)

    chain_legs = build_chain_legs(raw_legs, quotes, float(spot), oi_baseline)
    ranked = rank_tables(chain_legs, top_n=n)

    return {
        "underlying": underlying,
        "expiry": exp,
        "spot": float(spot),
        "updated_at": datetime.now().astimezone().isoformat(),
        "baseline_date": baseline_date,
        "chain_legs_quoted": len(chain_legs),
        "chain_legs_total": len(raw_legs),
        "top_n": n,
        **ranked,
    }
