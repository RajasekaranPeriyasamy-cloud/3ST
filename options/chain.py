"""Index options chain helpers."""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from config import INDEX_OPTIONS
from instruments import load_instruments, resolve_instrument


def atm_strike(spot: float, step: int) -> float:
    return round(spot / step) * step


def _underlying_options_df(underlying: str) -> pd.DataFrame:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    meta = INDEX_OPTIONS[underlying]
    exchange = meta["exchange"]
    df = load_instruments()
    if df.empty:
        return df

    name = df["name"].astype(str).str.upper()
    tsym = df["tradingsymbol"].astype(str).str.upper()
    u = underlying.upper()
    itype = df["instrument_type"].astype(str).str.upper()
    exch = df["exchange"].astype(str).str.upper()

    mask = (
        (exch == exchange.upper())
        & (itype.isin(["CE", "PE"]))
        & ((name == u) | tsym.str.startswith(u))
    )
    return df[mask].copy()


def list_expiries(underlying: str) -> list[str]:
    opts = _underlying_options_df(underlying)
    if opts.empty:
        return []
    expiries = opts["expiry"].dropna().unique()
    parsed: list[date] = []
    for e in expiries:
        if isinstance(e, date):
            parsed.append(e)
        else:
            parsed.append(pd.to_datetime(e).date())
    parsed.sort()
    return [d.isoformat() for d in parsed]


def get_chain(underlying: str, expiry: str) -> dict[str, Any]:
    """Return CE/PE rows grouped by strike for one expiry."""
    opts = _underlying_options_df(underlying)
    if opts.empty:
        return {"underlying": underlying, "expiry": expiry, "strikes": []}

    exp_date = pd.to_datetime(expiry).date()
    exp_col = opts["expiry"].apply(
        lambda x: x.date() if hasattr(x, "date") else pd.to_datetime(x).date()
    )
    chain = opts[exp_col == exp_date].copy()
    if chain.empty:
        return {"underlying": underlying, "expiry": expiry, "strikes": []}

    meta = INDEX_OPTIONS[underlying]
    strikes_map: dict[float, dict[str, Any]] = {}
    for _, row in chain.iterrows():
        strike = float(row["strike"])
        leg_type = str(row["instrument_type"]).upper()
        entry = {
            "tradingsymbol": str(row["tradingsymbol"]),
            "instrument_token": int(row["instrument_token"]),
            "exchange": str(row["exchange"]),
            "lot_size": int(row["lot_size"]) if pd.notna(row.get("lot_size")) else meta["lot_size"],
        }
        if strike not in strikes_map:
            strikes_map[strike] = {"strike": strike}
        strikes_map[strike][leg_type.lower()] = entry

    strikes = sorted(strikes_map.values(), key=lambda x: x["strike"])
    return {
        "underlying": underlying,
        "expiry": expiry,
        "exchange": meta["exchange"],
        "strike_step": meta["strike_step"],
        "lot_size": meta["lot_size"],
        "strikes": strikes,
    }


def get_index_spot(underlying: str) -> float | None:
    """Resolve index LTP via mapped index token key when available."""
    meta = INDEX_OPTIONS[underlying]
    key = meta.get("index_token_key")
    if key:
        try:
            from kite_auth import get_kite_client

            resolved = resolve_instrument(key)
            kite = get_kite_client()
            sym = f"{resolved['exchange']}:{resolved['tradingsymbol']}"
            data = kite.ltp(sym)
            return float(data[sym]["last_price"])
        except Exception:
            pass

    # Fallback: middle strike of nearest expiry chain (approximate spot)
    expiries = list_expiries(underlying)
    if not expiries:
        return None
    today = date.today()
    nearest = next((e for e in expiries if pd.to_datetime(e).date() >= today), expiries[-1])
    chain = get_chain(underlying, nearest)
    strikes = chain.get("strikes") or []
    if not strikes:
        return None
    mid = strikes[len(strikes) // 2]["strike"]
    return float(mid)


def nearest_expiry(underlying: str) -> str | None:
    """First expiry on or after today."""
    expiries = list_expiries(underlying)
    if not expiries:
        return None
    today = date.today()
    for exp in expiries:
        if pd.to_datetime(exp).date() >= today:
            return exp
    return expiries[-1]


def find_option_leg(
    underlying: str,
    expiry: str,
    strike: float,
    option_type: str,
) -> dict[str, Any] | None:
    chain = get_chain(underlying, expiry)
    opt = option_type.upper()
    for row in chain["strikes"]:
        if abs(row["strike"] - strike) < 0.01:
            leg = row.get(opt.lower())
            if leg:
                return {
                    **leg,
                    "strike": row["strike"],
                    "option_type": opt,
                }
    return None
