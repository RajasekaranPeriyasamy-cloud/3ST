"""Minute sampler: one batch quote -> IV -> Delta -> archive.

Deliberately does not use the historical API. Kite serves option candles only
back to a contract's listing date, so history cannot supply the corpus this
engine needs; a single ``fetch_quote_batch`` call (500-instrument ceiling) can
cover every tracked leg across all three underlyings each minute, and writing
those down is the only way to accumulate what the paper had.

Phase 0 finding baked into the defaults: with one or two expiries per
underlying, days-to-expiry is confounded with calendar date, so ``EXPIRIES``
defaults to 3 — the same session then appears at several DTEs and the two can
be separated.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import (
    DEFAULT_SESSION,
    INDEX_OPTIONS,
    KITE_INDEX_LOOKUP,
    PRICING_ENGINE_DEFAULTS,
)
from instruments import load_instruments
from kite_client import fetch_quote_batch
from options.greeks_engine import compute_greeks
from options.iv import implied_volatility, time_to_expiry_years
from utils.logging import get_logger

IST = ZoneInfo("Asia/Kolkata")
logger = get_logger("delta_velocity.collector")

UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "SENSEX")

# Strikes each side of ATM. 5 -> 11 strikes x 2 types x 3 expiries x 3
# underlyings = 198 legs, comfortably inside the 500-instrument quote ceiling.
STRIKE_WIDTH = 5
EXPIRIES = 3

RISK_FREE = float(PRICING_ENGINE_DEFAULTS.get("risk_free_rate", 0.065))

_QUOTE_CEILING = 500


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def in_session(now: datetime | None = None) -> bool:
    """Cash-market hours. MCX underlyings are out of scope for this engine."""
    ts = now or _now_ist()
    if ts.weekday() >= 5:
        return False
    start = str(DEFAULT_SESSION["session_start"])
    end = str(DEFAULT_SESSION["session_end"])
    hhmm = ts.strftime("%H:%M")
    return start <= hhmm <= end


def index_quote_key(underlying: str) -> str | None:
    meta = INDEX_OPTIONS.get(str(underlying).upper()) or {}
    lookup = KITE_INDEX_LOOKUP.get(str(meta.get("index_token_key") or "")) or {}
    if not lookup:
        return None
    return f"{lookup['exchange']}:{lookup['tradingsymbol']}"


def tracked_expiries(underlying: str, *, count: int = EXPIRIES) -> list[str]:
    """The nearest ``count`` listed expiries, nearest first."""
    u = str(underlying).upper()
    meta = INDEX_OPTIONS.get(u) or {}
    exchange = str(meta.get("exchange") or "NFO").upper()
    df = load_instruments()
    if df.empty:
        return []
    sub = df[
        (df["exchange"].astype(str).str.upper() == exchange)
        & (df["name"].astype(str).str.upper() == u)
        & (df["instrument_type"].astype(str).str.upper().isin(["CE", "PE"]))
    ]
    today = _now_ist().date().isoformat()
    expiries = sorted({str(e) for e in sub["expiry"].dropna().unique() if str(e) >= today})
    return expiries[:count]


def tracked_legs(underlying: str, spot: float, *, width: int = STRIKE_WIDTH) -> list[dict[str, Any]]:
    """Resolve ATM +/- ``width`` strikes across the tracked expiries."""
    u = str(underlying).upper()
    meta = INDEX_OPTIONS.get(u) or {}
    exchange = str(meta.get("exchange") or "NFO").upper()
    step = int(meta.get("strike_step") or 50)
    atm = round(float(spot) / step) * step
    wanted = {float(atm + i * step) for i in range(-width, width + 1)}

    df = load_instruments()
    if df.empty:
        return []
    sub = df[
        (df["exchange"].astype(str).str.upper() == exchange)
        & (df["name"].astype(str).str.upper() == u)
        & (df["instrument_type"].astype(str).str.upper().isin(["CE", "PE"]))
        & (df["expiry"].astype(str).isin(tracked_expiries(u)))
        & (df["strike"].astype(float).isin(wanted))
    ]
    legs: list[dict[str, Any]] = []
    for _, row in sub.iterrows():
        legs.append(
            {
                "key": f"{exchange}:{row['tradingsymbol']}",
                "tradingsymbol": str(row["tradingsymbol"]),
                "exchange": exchange,
                "expiry": str(row["expiry"]),
                "strike": float(row["strike"]),
                "option_type": str(row["instrument_type"]).upper(),
            }
        )
    return legs


def _mid_or_last(quote: dict[str, Any]) -> float | None:
    """Prefer the bid/ask midpoint; fall back to last traded price.

    Mid is the better IV input — last price can be stale or off-book on a
    thin strike — but a strike with one-sided depth has no usable mid.
    """
    depth = quote.get("depth") or {}
    buy = (depth.get("buy") or [{}])[0].get("price")
    sell = (depth.get("sell") or [{}])[0].get("price")
    try:
        if buy and sell and float(buy) > 0 and float(sell) > 0:
            return (float(buy) + float(sell)) / 2.0
    except (TypeError, ValueError):
        pass
    try:
        last = float(quote.get("last_price") or 0)
        return last if last > 0 else None
    except (TypeError, ValueError):
        return None


def build_snapshot(
    underlying: str,
    spot: float,
    legs: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One minute of option state. Delta uses *this* minute's spot, not a session constant."""
    ts = now or _now_ist()
    out_legs: list[dict[str, Any]] = []
    for leg in legs:
        quote = quotes.get(leg["key"])
        if not quote:
            continue
        price = _mid_or_last(quote)
        tte = time_to_expiry_years(leg["expiry"], as_of=ts)
        iv = delta = None
        if price and tte and tte > 0:
            iv = implied_volatility(price, float(spot), leg["strike"], tte, leg["option_type"], RISK_FREE)
            if iv and iv > 0:
                greeks = compute_greeks(
                    spot=float(spot),
                    strike=leg["strike"],
                    tte_years=tte,
                    iv=iv,
                    option_type=leg["option_type"],
                    risk_free_rate=RISK_FREE,
                )
                delta = greeks.get("delta")
        out_legs.append(
            {
                "tradingsymbol": leg["tradingsymbol"],
                "expiry": leg["expiry"],
                "strike": leg["strike"],
                "option_type": leg["option_type"],
                "ltp": price,
                "oi": quote.get("oi"),
                "volume": quote.get("volume") or quote.get("volume_traded"),
                "iv": iv,
                "delta": delta,
            }
        )
    return {
        "ts": ts.replace(microsecond=0).isoformat(),
        "session_date": ts.date().isoformat(),
        "underlying": str(underlying).upper(),
        "spot": float(spot),
        "legs": out_legs,
        "legs_valid": sum(1 for leg in out_legs if leg.get("delta") is not None),
    }


def sample_once(underlyings: tuple[str, ...] = UNDERLYINGS, *, now: datetime | None = None) -> dict[str, Any]:
    """Sample every underlying in one batch quote. Returns a per-underlying report.

    Never raises: a failure on one underlying must not stop the others, and a
    collector that dies silently is worse than one that logs and continues.
    """
    from analysis.delta_velocity import store

    ts = now or _now_ist()
    plan: dict[str, dict[str, Any]] = {}
    keys: list[str] = []

    for u in underlyings:
        idx_key = index_quote_key(u)
        if not idx_key:
            plan[u] = {"error": "no index quote key"}
            continue
        plan[u] = {"index_key": idx_key}
        keys.append(idx_key)

    if not keys:
        return {"ok": False, "error": "no underlyings resolvable"}

    try:
        index_quotes = fetch_quote_batch(keys)
    except Exception as exc:
        logger.warning("delta_velocity index quote failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    all_legs: dict[str, list[dict[str, Any]]] = {}
    leg_keys: list[str] = []
    for u, info in plan.items():
        if "error" in info:
            continue
        quote = index_quotes.get(info["index_key"]) or {}
        spot = quote.get("last_price")
        if not spot:
            info["error"] = "no spot"
            continue
        info["spot"] = float(spot)
        legs = tracked_legs(u, float(spot))
        all_legs[u] = legs
        leg_keys.extend(leg["key"] for leg in legs)

    if len(leg_keys) > _QUOTE_CEILING:
        logger.warning(
            "delta_velocity tracking %d legs, above the %d quote ceiling — truncating",
            len(leg_keys),
            _QUOTE_CEILING,
        )
        leg_keys = leg_keys[:_QUOTE_CEILING]

    try:
        leg_quotes = fetch_quote_batch(leg_keys) if leg_keys else {}
    except Exception as exc:
        logger.warning("delta_velocity leg quote failed: %s", exc)
        return {"ok": False, "error": str(exc)}

    report: dict[str, Any] = {"ok": True, "ts": ts.replace(microsecond=0).isoformat(), "underlyings": {}}
    for u, info in plan.items():
        if "error" in info:
            report["underlyings"][u] = {"error": info["error"]}
            continue
        snapshot = build_snapshot(u, info["spot"], all_legs.get(u) or [], leg_quotes, now=ts)
        try:
            store.append_snapshot(u, snapshot)
        except OSError as exc:
            report["underlyings"][u] = {"error": f"write failed: {exc}"}
            continue
        report["underlyings"][u] = {
            "spot": snapshot["spot"],
            "legs": len(snapshot["legs"]),
            "legs_valid": snapshot["legs_valid"],
        }
    return report


def last_session_date() -> date:
    return _now_ist().date()
