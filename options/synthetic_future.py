"""ATM synthetic future from nearest-expiry CE/PE mids.

Display-only: ``F = K + CE_mid − PE_mid`` (LTP fallback when mid missing).
Does not feed GEX / OI / spot math.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import INDEX_OPTIONS
from kite_client import fetch_quote_batch
from options.chain import atm_strike, get_chain, get_index_spot, nearest_expiry

IST = ZoneInfo("Asia/Kolkata")

# Light cache — CAS desk polls ~8s; avoid a fresh chain+quote every tick.
CACHE_TTL_SEC = 40.0

_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_CACHE_LOCK = threading.Lock()


def _now_ist(when: datetime | None = None) -> datetime:
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def _asof_iso(when: datetime | None = None) -> str:
    return _now_ist(when).isoformat(timespec="seconds")


def mid_or_ltp(quote: dict[str, Any] | None) -> tuple[float | None, str]:
    """Prefer bid/ask mid; fall back to LTP. Returns ``(price, source)``."""
    if not quote:
        return None, "none"

    depth = quote.get("depth") if isinstance(quote.get("depth"), dict) else {}
    buy = depth.get("buy") or []
    sell = depth.get("sell") or []
    bid = buy[0].get("price") if buy else None
    ask = sell[0].get("price") if sell else None
    if bid is None:
        bid = quote.get("buy_price") or quote.get("best_bid")
    if ask is None:
        ask = quote.get("sell_price") or quote.get("best_ask")

    try:
        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        bid_f, ask_f = None, None

    if bid_f is not None and ask_f is not None and bid_f > 0 and ask_f > 0 and ask_f >= bid_f:
        mid = 0.5 * (bid_f + ask_f)
        if mid > 0:
            return mid, "mid"

    ltp = quote.get("last_price")
    try:
        ltp_f = float(ltp) if ltp is not None else None
    except (TypeError, ValueError):
        ltp_f = None
    if ltp_f is not None and ltp_f > 0:
        return ltp_f, "ltp"
    return None, "none"


def synthetic_from_prices(
    atm: float,
    ce_px: float,
    pe_px: float,
) -> float:
    """``F = K + CE − PE``."""
    return float(atm) + float(ce_px) - float(pe_px)


def _quote_for_key(quotes: dict[str, Any], key: str) -> dict[str, Any] | None:
    if not key or not quotes:
        return None
    if key in quotes:
        return quotes[key]
    suffix = key.split(":", 1)[-1]
    for k, v in quotes.items():
        if str(k) == key or str(k).endswith(suffix):
            return v
    return None


def clear_synthetic_future_cache() -> None:
    """Test helper — drop the in-process cache."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _compute_uncached(
    underlying: str,
    *,
    when: datetime | None = None,
    spot: float | None = None,
    indicative: float | None = None,
    chain: dict[str, Any] | None = None,
    quotes: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    u = underlying.strip().upper()
    meta = INDEX_OPTIONS.get(u)
    if not meta:
        return None

    step = int(meta.get("strike_step") or 0)
    if step <= 0:
        return None

    spot_px = float(spot) if spot is not None else get_index_spot(u)
    if spot_px is None:
        return None

    expiry = None
    if chain and chain.get("expiry"):
        expiry = str(chain["expiry"])
    else:
        expiry = nearest_expiry(u)
    if not expiry:
        return None

    ch = chain if chain is not None else get_chain(u, expiry)
    strikes = ch.get("strikes") or []
    if not strikes:
        return None

    atm = atm_strike(float(spot_px), step)
    row = next((s for s in strikes if float(s.get("strike") or 0) == float(atm)), None)
    if row is None:
        # Nearest listed strike if exact ATM missing.
        row = min(strikes, key=lambda s: abs(float(s.get("strike") or 0) - atm))
        atm = float(row.get("strike") or atm)

    ce = row.get("ce") if isinstance(row.get("ce"), dict) else None
    pe = row.get("pe") if isinstance(row.get("pe"), dict) else None
    if not ce or not pe:
        return None

    ce_ex = str(ce.get("exchange") or meta.get("exchange") or "NFO")
    pe_ex = str(pe.get("exchange") or meta.get("exchange") or "NFO")
    ce_sym = str(ce.get("tradingsymbol") or "")
    pe_sym = str(pe.get("tradingsymbol") or "")
    if not ce_sym or not pe_sym:
        return None

    ce_key = f"{ce_ex}:{ce_sym}"
    pe_key = f"{pe_ex}:{pe_sym}"

    qmap = quotes
    if qmap is None:
        try:
            qmap = fetch_quote_batch([ce_key, pe_key])
        except Exception:
            return None

    ce_q = _quote_for_key(qmap or {}, ce_key)
    pe_q = _quote_for_key(qmap or {}, pe_key)
    ce_px, ce_src = mid_or_ltp(ce_q)
    pe_px, pe_src = mid_or_ltp(pe_q)
    if ce_px is None or pe_px is None:
        return None

    f_px = synthetic_from_prices(atm, ce_px, pe_px)
    if ce_src == pe_src:
        price_source = ce_src
    else:
        price_source = "mixed"

    basis_vs_spot = round(f_px - float(spot_px), 4)
    basis_vs_indicative = None
    if indicative is not None:
        try:
            ind = float(indicative)
            if math.isfinite(ind):
                basis_vs_indicative = round(f_px - ind, 4)
        except (TypeError, ValueError):
            basis_vs_indicative = None

    return {
        "F": round(f_px, 4),
        "atm_strike": float(atm),
        "expiry": str(expiry),
        "ce_symbol": ce_sym,
        "pe_symbol": pe_sym,
        "ce_price": round(ce_px, 4),
        "pe_price": round(pe_px, 4),
        "ce_source": ce_src,
        "pe_source": pe_src,
        "price_source": price_source,
        "spot": float(spot_px),
        "basis_vs_spot": basis_vs_spot,
        "basis_vs_indicative": basis_vs_indicative,
        "asof": _asof_iso(when),
    }


def compute_synthetic_future(
    underlying: str,
    *,
    when: datetime | None = None,
    spot: float | None = None,
    indicative: float | None = None,
    chain: dict[str, Any] | None = None,
    quotes: dict[str, Any] | None = None,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    """Nearest-expiry ATM synthetic future, or ``None`` on failure.

    Cached ~40s in-process (skipped when ``chain``/``quotes`` injected or
    ``use_cache`` is False). Pass ``indicative`` to fill ``basis_vs_indicative``.
    """
    u = underlying.strip().upper()
    injected = chain is not None or quotes is not None
    if injected or not use_cache:
        return _compute_uncached(
            u,
            when=when,
            spot=spot,
            indicative=indicative,
            chain=chain,
            quotes=quotes,
        )

    now_mono = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(u)
        if hit is not None:
            ts, payload = hit
            if (now_mono - ts) < CACHE_TTL_SEC:
                if payload is None:
                    return None
                # Refresh basis vs live indicative without a new quote pull.
                out = dict(payload)
                if indicative is not None and out.get("F") is not None:
                    try:
                        out["basis_vs_indicative"] = round(float(out["F"]) - float(indicative), 4)
                    except (TypeError, ValueError):
                        out["basis_vs_indicative"] = None
                elif "basis_vs_indicative" not in out:
                    out["basis_vs_indicative"] = None
                return out

    payload = _compute_uncached(u, when=when, spot=spot, indicative=indicative)
    with _CACHE_LOCK:
        _CACHE[u] = (time.monotonic(), payload)
    return dict(payload) if payload is not None else None
