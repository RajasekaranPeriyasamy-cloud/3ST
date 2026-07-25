"""Index options chain helpers."""

from __future__ import annotations

import concurrent.futures
import re
from datetime import date
from typing import Any

import pandas as pd

from config import INDEX_OPTIONS
from instruments import CACHE_FILE, load_instruments, resolve_instrument

_EXPIRIES_CACHE: dict[str, tuple[float, list[str]]] = {}
_LTP_TIMEOUT_SEC = 10.0


def tradingsymbol_matches_underlying(tradingsymbol: str, underlying: str) -> bool:
    """True when symbol belongs to this underlying (CRUDEOIL vs CRUDEOILM are distinct)."""
    sym = str(tradingsymbol or "").upper()
    u = str(underlying or "").upper()
    if not sym or not u:
        return False
    return bool(re.match(rf"^{re.escape(u)}\d", sym))


def atm_strike(spot: float, step: int) -> float:
    return round(spot / step) * step


def _underlying_options_df(underlying: str) -> pd.DataFrame:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    meta = INDEX_OPTIONS[underlying]
    exchange = str(meta["exchange"]).upper()
    df = load_instruments()
    if df.empty:
        return df

    subset = df[df["exchange"].astype(str).str.upper() == exchange]
    if subset.empty:
        return subset

    u = underlying.upper()
    name = subset["name"].astype(str).str.upper()
    tsym = subset["tradingsymbol"].astype(str).str.upper()
    itype = subset["instrument_type"].astype(str).str.upper()

    mask = itype.isin(["CE", "PE"]) & ((name == u) | tsym.str.match(rf"^{re.escape(u)}\d", na=False))
    return subset[mask].copy()


def list_expiries(underlying: str) -> list[str]:
    mtime = CACHE_FILE.stat().st_mtime if CACHE_FILE.exists() else 0.0
    cached = _EXPIRIES_CACHE.get(underlying.upper())
    if cached and cached[0] == mtime:
        return cached[1]

    opts = _underlying_options_df(underlying)
    if opts.empty:
        result: list[str] = []
    else:
        expiries = opts["expiry"].dropna().unique()
        parsed: list[date] = []
        for e in expiries:
            if isinstance(e, date):
                parsed.append(e)
            else:
                parsed.append(pd.to_datetime(e).date())
        parsed.sort()
        result = [d.isoformat() for d in parsed]

    _EXPIRIES_CACHE[underlying.upper()] = (mtime, result)
    return result


def warm_expiries_cache() -> None:
    """Pre-compute expiry lists for all index options (API startup warm)."""
    for underlying in INDEX_OPTIONS:
        try:
            list_expiries(underlying)
        except Exception:
            pass


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


def _friendly_ltp_error(exc: Exception) -> str:
    from kite_errors import friendly_kite_message

    return friendly_kite_message(str(exc))


def _fetch_symbol_ltp(
    exchange: str,
    tradingsymbol: str,
    *,
    instrument_token: int | None = None,
) -> tuple[float | None, str | None]:
    """LTP cache first, then Kite REST with a short timeout."""
    try:
        from execution.ltp_cache import get_ltp_cache

        px = get_ltp_cache().get(exchange, tradingsymbol, instrument_token=instrument_token)
        if px is not None:
            return float(px), None
    except Exception:
        pass

    sym = f"{exchange}:{tradingsymbol}"
    try:
        from kite_client import fetch_ltp_batch

        def _call() -> dict[str, Any]:
            return fetch_ltp_batch([sym])

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            data = pool.submit(_call).result(timeout=_LTP_TIMEOUT_SEC)
        return float(data[sym]["last_price"]), None
    except concurrent.futures.TimeoutError:
        return None, f"Kite LTP timed out after {_LTP_TIMEOUT_SEC:.0f}s for {sym}"
    except Exception as exc:
        return None, _friendly_ltp_error(exc)


def get_index_spot_detail(underlying: str) -> tuple[float | None, str | None]:
    """Resolve spot LTP with a human-readable failure reason."""
    meta = INDEX_OPTIONS[underlying]
    spot_source = meta.get("spot_source") or ("index" if meta.get("index_token_key") else "future")

    if spot_source == "future":
        try:
            from instruments import resolve_future

            fut = resolve_future(underlying)
            px, err = _fetch_symbol_ltp(
                str(fut["exchange"]),
                str(fut["tradingsymbol"]),
                instrument_token=int(fut["instrument_token"]),
            )
            if px is not None:
                return px, None
            sym = f"{fut['exchange']}:{fut['tradingsymbol']}"
            return None, err or f"No LTP for {sym}"
        except Exception as exc:
            return None, _friendly_ltp_error(exc)

    key = meta.get("index_token_key")
    if key:
        try:
            resolved = resolve_instrument(key)
            px, err = _fetch_symbol_ltp(
                str(resolved["exchange"]),
                str(resolved["tradingsymbol"]),
                instrument_token=int(resolved["instrument_token"]),
            )
            if px is not None:
                return px, None
            return None, err
        except Exception as exc:
            return None, _friendly_ltp_error(exc)

    if spot_source == "future":
        return None, f"No spot source for {underlying}"

    expiries = list_expiries(underlying)
    if not expiries:
        return None, f"No option expiries for {underlying}"
    today = date.today()
    nearest = next((e for e in expiries if pd.to_datetime(e).date() >= today), expiries[-1])
    chain = get_chain(underlying, nearest)
    strikes = chain.get("strikes") or []
    if not strikes:
        return None, f"Empty chain for {underlying} {nearest}"
    mid = strikes[len(strikes) // 2]["strike"]
    return float(mid), None


def get_index_spot(underlying: str) -> float | None:
    """Resolve spot LTP — index token for NSE/BSE indices, front-month future for MCX."""
    px, _ = get_index_spot_detail(underlying)
    return px


def require_index_spot(underlying: str) -> float:
    """Spot LTP or raise with a user-facing reason (network, session, market data)."""
    from kite_errors import friendly_kite_message

    px, err = get_index_spot_detail(underlying)
    if px is not None:
        return float(px)
    detail = friendly_kite_message(err or f"Index spot unavailable for {underlying}")
    raise RuntimeError(detail)


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


def resolve_expiry(underlying: str, expiry: str | None = None) -> str | None:
    """Return a valid option expiry for underlying; fall back to nearest when missing/invalid."""
    available = list_expiries(underlying)
    if not available:
        return None
    if expiry:
        exp_date = pd.to_datetime(expiry, errors="coerce")
        if pd.notna(exp_date):
            iso = exp_date.date().isoformat()
            if iso in available:
                return iso
    return nearest_expiry(underlying)


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
