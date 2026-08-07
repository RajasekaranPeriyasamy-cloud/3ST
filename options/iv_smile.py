"""IV smile — CE/PE implied vol across strikes for one expiry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import INDEX_OPTIONS, IV_SMILE_DEFAULTS
from options.chain import atm_strike, get_chain, list_expiries, require_index_spot
from options.iv import implied_volatility, time_to_expiry_years
from options.oi_var import _quote_batches


def iv_smile_config() -> dict[str, Any]:
    d = IV_SMILE_DEFAULTS
    return {
        "underlyings": list(d.get("index_underlyings") or ["NIFTY", "BANKNIFTY", "SENSEX"]),
        "strike_count": d["strike_count"],
        "refresh_seconds": d["refresh_seconds"],
    }


def _strike_window(spot: float, step: int, strike_count: int) -> list[float]:
    atm = atm_strike(spot, step)
    half = max(1, strike_count // 2)
    return [float(atm + i * step) for i in range(-half, half + 1)]


def _iv_skew(chain: list[dict[str, Any]], atm_strike_val: float) -> float | None:
    if not atm_strike_val or not chain:
        return None
    otm_distance = atm_strike_val * 0.05
    put_iv = None
    for item in sorted(chain, key=lambda x: abs(x["strike"] - (atm_strike_val - otm_distance))):
        if item["strike"] < atm_strike_val and item.get("pe_iv") is not None:
            put_iv = item["pe_iv"]
            break
    call_iv = None
    for item in sorted(chain, key=lambda x: abs(x["strike"] - (atm_strike_val + otm_distance))):
        if item["strike"] > atm_strike_val and item.get("ce_iv") is not None:
            call_iv = item["ce_iv"]
            break
    if put_iv is not None and call_iv is not None:
        return round(put_iv - call_iv, 2)
    return None


def build_iv_smile(
    underlying: str,
    expiry: str,
    *,
    strike_count: int | None = None,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS.keys())}")

    available = list_expiries(underlying)
    if expiry not in available:
        raise RuntimeError(f"Expiry {expiry} not available for {underlying}")

    defaults = IV_SMILE_DEFAULTS
    n = int(strike_count if strike_count is not None else defaults["strike_count"])
    n = max(5, min(n, 41))
    step = int(INDEX_OPTIONS[underlying]["strike_step"])
    r = float(defaults.get("risk_free_rate", 0.065))

    spot = require_index_spot(underlying)
    atm = atm_strike(spot, step)
    strikes = _strike_window(spot, step, n)

    chain_data = get_chain(underlying, expiry)
    smap = {float(r["strike"]): r for r in chain_data.get("strikes", [])}
    exch_default = chain_data.get("exchange", INDEX_OPTIONS[underlying]["exchange"])

    quote_keys: list[str] = []
    leg_by_strike: dict[float, dict[str, str]] = {}
    for k in strikes:
        row = smap.get(k)
        if not row:
            continue
        leg_by_strike[k] = {}
        for otype in ("ce", "pe"):
            leg = row.get(otype)
            if leg and leg.get("tradingsymbol"):
                key = f"{leg.get('exchange', exch_default)}:{leg['tradingsymbol']}"
                leg_by_strike[k][otype] = key
                quote_keys.append(key)

    quotes = _quote_batches(quote_keys)
    tte = time_to_expiry_years(expiry)
    if tte is None:
        raise RuntimeError(f"Expiry {expiry} has no time to expiry")

    iv_chain: list[dict[str, Any]] = []
    atm_ce_iv: float | None = None
    atm_pe_iv: float | None = None

    for k in strikes:
        ce_iv: float | None = None
        pe_iv: float | None = None
        legs = leg_by_strike.get(k, {})
        if "ce" in legs:
            q = quotes.get(legs["ce"])
            if q:
                ltp = float(q.get("last_price") or 0)
                iv = implied_volatility(ltp, spot, k, tte, "CE", r)
                if iv is not None:
                    ce_iv = round(iv * 100, 2)
        if "pe" in legs:
            q = quotes.get(legs["pe"])
            if q:
                ltp = float(q.get("last_price") or 0)
                iv = implied_volatility(ltp, spot, k, tte, "PE", r)
                if iv is not None:
                    pe_iv = round(iv * 100, 2)
        if k == atm:
            atm_ce_iv = ce_iv
            atm_pe_iv = pe_iv
        iv_chain.append({"strike": k, "ce_iv": ce_iv, "pe_iv": pe_iv})

    atm_iv: float | None = None
    if atm_ce_iv is not None and atm_pe_iv is not None:
        atm_iv = round((atm_ce_iv + atm_pe_iv) / 2, 2)
    elif atm_ce_iv is not None:
        atm_iv = atm_ce_iv
    elif atm_pe_iv is not None:
        atm_iv = atm_pe_iv

    return {
        "underlying": underlying,
        "expiry": expiry,
        "spot": round(float(spot), 2),
        "atm_strike": float(atm),
        "atm_iv": atm_iv,
        "skew": _iv_skew(iv_chain, float(atm)),
        "chain": iv_chain,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
