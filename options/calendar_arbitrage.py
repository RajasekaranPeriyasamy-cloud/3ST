"""Futures calendar-spread arbitrage universe (near/next/third month pairs)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

import re

from config import CALENDAR_ARBITRAGE_DEFAULTS
from instruments import load_instruments
from options.oi_var import _quote_batches

MAX_LEGS = 3
DEFAULT_EXCHANGES = ("NFO", "MCX")
SUPPORTED_EXCHANGES = ("NFO", "MCX", "BFO", "CDS")


def calendar_arbitrage_config() -> dict[str, Any]:
    d = CALENDAR_ARBITRAGE_DEFAULTS
    return {
        "default_exchanges": list(d["default_exchanges"]),
        "supported_exchanges": list(SUPPORTED_EXCHANGES),
        "refresh_seconds": d["refresh_seconds"],
        "quote_refresh_seconds": d["quote_refresh_seconds"],
    }


def _parse_expiry(expiry: str) -> datetime:
    if not expiry:
        return datetime.max
    raw = str(expiry).strip()[:10]
    try:
        return datetime.combine(date.fromisoformat(raw), datetime.min.time())
    except ValueError:
        for fmt in ("%d-%b-%y", "%d-%b-%Y"):
            try:
                return datetime.strptime(expiry.strip().upper(), fmt)
            except ValueError:
                continue
    return datetime.max


def _leg(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "symbol": row.get("tradingsymbol") or row.get("symbol"),
        "exchange": row.get("exchange"),
        "expiry": row.get("expiry"),
        "lotsize": row.get("lot_size") or row.get("lotsize"),
        "tick_size": row.get("tick_size"),
    }


def _nearest_futures(exchange: str) -> dict[str, list[dict[str, Any]]]:
    df = load_instruments()
    if df.empty:
        return {}
    exch = exchange.upper()
    fut = df[
        (df["exchange"].astype(str).str.upper() == exch)
        & (df["instrument_type"].astype(str).str.upper() == "FUT")
    ].copy()
    if fut.empty:
        return {}

    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for _, row in fut.iterrows():
        sym = str(row.get("tradingsymbol") or "").upper()
        if not sym.endswith("FUT"):
            continue
        underlying = str(row.get("name") or "").upper()
        if not underlying:
            m = re.match(r"^([A-Z]+)\d", sym)
            underlying = m.group(1) if m else sym.replace("FUT", "")
        expiry = row.get("expiry")
        if not underlying or pd.isna(expiry):
            continue
        exp_s = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
        compact = {
            "tradingsymbol": str(row["tradingsymbol"]),
            "exchange": str(row["exchange"]),
            "expiry": exp_s,
            "lot_size": int(row["lot_size"]) if pd.notna(row.get("lot_size")) else 1,
            "tick_size": float(row["tick_size"]) if pd.notna(row.get("tick_size")) else None,
            "name": underlying,
        }
        grouped.setdefault(underlying, {})[sym] = compact

    nearest: dict[str, list[dict[str, Any]]] = {}
    for underlying, by_sym in grouped.items():
        contracts = sorted(by_sym.values(), key=lambda c: _parse_expiry(str(c.get("expiry") or "")))
        nearest[underlying] = contracts[:MAX_LEGS]
    return nearest


def build_arbitrage_universe(
    exchanges: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    requested = [str(ex).strip().upper() for ex in (exchanges or DEFAULT_EXCHANGES) if str(ex).strip()]
    scan = [ex for ex in requested if ex in SUPPORTED_EXCHANGES]
    if not scan:
        raise ValueError(
            f"No supported exchanges in {requested or 'request'}. Supported: {', '.join(SUPPORTED_EXCHANGES)}"
        )

    pairs: list[dict[str, Any]] = []
    symbols: dict[str, dict[str, str]] = {}
    underlying_count = 0

    for exchange in scan:
        nearest = _nearest_futures(exchange)
        for underlying, contracts in nearest.items():
            if len(contracts) < 2:
                continue
            underlying_count += 1
            near = contracts[0]
            legs_map = {
                "near-next": contracts[1],
                "near-third": contracts[2] if len(contracts) > 2 else None,
            }
            for pair_type, far in legs_map.items():
                if far is None:
                    continue
                pairs.append(
                    {
                        "id": f"{exchange}:{underlying}:{pair_type}",
                        "underlying": underlying,
                        "exchange": exchange,
                        "type": pair_type,
                        "near": _leg(near),
                        "far": _leg(far),
                    }
                )
                for leg in (near, far):
                    key = f"{leg['exchange']}:{leg['tradingsymbol']}"
                    symbols[key] = {"symbol": leg["tradingsymbol"], "exchange": leg["exchange"]}

    symbol_list = list(symbols.values())
    return {
        "pairs": pairs,
        "symbols": symbol_list,
        "counts": {
            "underlyings": underlying_count,
            "pairs": len(pairs),
            "symbols": len(symbol_list),
        },
        "exchanges": scan,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _quote_from_batch(q: dict[str, Any] | None) -> dict[str, Any]:
    if not q:
        return {"ltp": None, "bid": None, "ask": None, "depth": False}
    bid = q.get("depth", {}).get("buy", [{}])[0].get("price") if isinstance(q.get("depth"), dict) else None
    ask = q.get("depth", {}).get("sell", [{}])[0].get("price") if isinstance(q.get("depth"), dict) else None
    if bid is None:
        bid = q.get("buy_price") or q.get("best_bid")
    if ask is None:
        ask = q.get("sell_price") or q.get("best_ask")
    ltp = q.get("last_price")
    return {
        "ltp": float(ltp) if ltp is not None else None,
        "bid": float(bid) if bid is not None else None,
        "ask": float(ask) if ask is not None else None,
        "depth": bid is not None and ask is not None,
    }


def build_arbitrage_snapshot(
    exchanges: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Universe plus REST quote poll (MVP — no WebSocket depth)."""
    universe = build_arbitrage_universe(exchanges)
    keys = [f"{s['exchange']}:{s['symbol']}" for s in universe["symbols"]]
    quotes_raw = _quote_batches(keys) if keys else {}
    quotes: dict[str, dict[str, Any]] = {}
    for key, row in quotes_raw.items():
        quotes[key] = _quote_from_batch(row)

    rows: list[dict[str, Any]] = []
    for pair in universe["pairs"]:
        near_key = f"{pair['near']['exchange']}:{pair['near']['symbol']}"
        far_key = f"{pair['far']['exchange']}:{pair['far']['symbol']}"
        nq = quotes.get(near_key, {})
        fq = quotes.get(far_key, {})

        def _mid(q: dict[str, Any]) -> float | None:
            bid, ask, ltp = q.get("bid"), q.get("ask"), q.get("ltp")
            if bid is not None and ask is not None and bid > 0 and ask > 0:
                return (float(bid) + float(ask)) / 2.0
            return float(ltp) if ltp is not None else None

        near_mid = _mid(nq)
        far_mid = _mid(fq)
        raw_spread = (far_mid - near_mid) if far_mid is not None and near_mid is not None else None
        spread_pct = (raw_spread / near_mid * 100) if raw_spread is not None and near_mid and near_mid > 0 else None

        credit_short = None
        credit_long = None
        if nq.get("bid") is not None and fq.get("ask") is not None:
            credit_short = float(fq["ask"]) - float(nq["bid"])
        if fq.get("bid") is not None and nq.get("ask") is not None:
            credit_long = float(nq["ask"]) - float(fq["bid"])

        direction: str | None = None
        best_credit: float | None = None
        if credit_short is not None and (best_credit is None or credit_short > best_credit):
            best_credit = credit_short
            direction = "SHORT_SPREAD"
        if credit_long is not None and (best_credit is None or credit_long > best_credit):
            best_credit = credit_long
            direction = "LONG_SPREAD"

        rows.append(
            {
                **pair,
                "near_quote": nq,
                "far_quote": fq,
                "near_mid": round(near_mid, 4) if near_mid is not None else None,
                "far_mid": round(far_mid, 4) if far_mid is not None else None,
                "raw_spread": round(raw_spread, 4) if raw_spread is not None else None,
                "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
                "best_credit": round(best_credit, 4) if best_credit is not None else None,
                "direction": direction,
                "liquid": bool(nq.get("depth") and fq.get("depth")),
            }
        )

    rows.sort(
        key=lambda r: (r["spread_pct"] is None, -(r["spread_pct"] or 0)),
    )
    return {
        **universe,
        "quotes": quotes,
        "rows": rows,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
