"""Kite instruments cache, search, and token resolution."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from config import INDEX_OPTIONS, INSTRUMENTS, KITE_INDEX_LOOKUP
from kite_auth import session_status
from settings import data_dir

CACHE_FILE = data_dir() / "kite_instruments.json"

_DF_CACHE: pd.DataFrame | None = None
_DF_CACHE_MTIME: float | None = None
_DF_LOAD_LOCK = threading.Lock()


def _invalidate_df_cache() -> None:
    global _DF_CACHE, _DF_CACHE_MTIME
    _DF_CACHE = None
    _DF_CACHE_MTIME = None


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="seconds")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj

Segment = Literal["equity", "future", "option"]

_EQUITY_EXCHANGES = {"NSE", "BSE"}
_DERIV_EXCHANGES = {"NFO", "BFO", "NCO", "MCX", "CDS"}


def _cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime).date()
    return mtime == date.today()


def cache_status() -> dict[str, Any]:
    """Disk cache metadata for /health and ops dashboards."""
    if not CACHE_FILE.exists():
        return {"instruments_cache": False, "instruments_cache_fresh": False, "instruments_cache_bytes": 0}
    mtime = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
    age_days = (date.today() - mtime.date()).days
    return {
        "instruments_cache": True,
        "instruments_cache_fresh": _cache_fresh(CACHE_FILE),
        "instruments_cache_age_days": age_days,
        "instruments_cache_bytes": CACHE_FILE.stat().st_size,
        "instruments_cache_updated": mtime.isoformat(timespec="seconds"),
    }


def refresh_instruments(force: bool = False) -> list[dict[str, Any]]:
    if not force and _cache_fresh(CACHE_FILE):
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))

    try:
        from kite_client import _kite_direct_client

        kite = _kite_direct_client()
        rows = kite.instruments()
    except Exception as exc:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        raise RuntimeError(f"Instrument download failed and no cache on disk: {exc}") from exc

    safe_rows = _json_safe(rows)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(safe_rows), encoding="utf-8")
    _invalidate_df_cache()
    return rows


def _load_rows_from_disk() -> list[dict[str, Any]]:
    if not CACHE_FILE.exists():
        if not session_status().get("authenticated"):
            raise RuntimeError(
                "Instrument cache empty. Log in to Kite and refresh instruments."
            )
        return refresh_instruments(force=True)
    return json.loads(CACHE_FILE.read_text(encoding="utf-8"))


def load_instruments(force_refresh: bool = False) -> pd.DataFrame:
    """Load Kite instruments as a DataFrame (cached in memory by file mtime)."""
    global _DF_CACHE, _DF_CACHE_MTIME

    if force_refresh:
        refresh_instruments(force=True)

    with _DF_LOAD_LOCK:
        if (
            not force_refresh
            and _DF_CACHE is not None
            and CACHE_FILE.exists()
            and _DF_CACHE_MTIME == CACHE_FILE.stat().st_mtime
        ):
            return _DF_CACHE

        rows = _load_rows_from_disk()
        df = pd.DataFrame(rows)
        if CACHE_FILE.exists():
            _DF_CACHE_MTIME = CACHE_FILE.stat().st_mtime
        else:
            _DF_CACHE_MTIME = None
        _DF_CACHE = df
        return df


def warm_instruments_cache() -> None:
    """Pre-load instrument cache and expiry lists (call once at API startup)."""
    try:
        load_instruments()
        from options.chain import warm_expiries_cache

        warm_expiries_cache()
    except Exception:
        pass


def _compact_row(row: pd.Series) -> dict[str, Any]:
    expiry = row.get("expiry")
    strike = row.get("strike")
    out: dict[str, Any] = {
        "instrument_token": int(row["instrument_token"]),
        "exchange": str(row["exchange"]),
        "tradingsymbol": str(row["tradingsymbol"]),
        "name": str(row.get("name", "")),
        "segment": str(row.get("segment", row["exchange"])),
        "instrument_type": str(row.get("instrument_type", "")),
        "lot_size": int(row["lot_size"]) if pd.notna(row.get("lot_size")) else 1,
    }
    # Authoritative price increment for the contract — varies more than you would
    # guess (NIFTY 0.10, SENSEX 0.05, CRUDEOIL 1.00), so the volume-profile price
    # lattice reads it here rather than hardcoding a per-underlying map.
    tick = row.get("tick_size")
    if pd.notna(tick):
        out["tick_size"] = float(tick)
    if pd.notna(expiry):
        out["expiry"] = expiry.isoformat() if hasattr(expiry, "isoformat") else str(expiry)
    if pd.notna(strike):
        out["strike"] = float(strike)
    return out


def _filter_segment(df: pd.DataFrame, segment: Segment) -> pd.DataFrame:
    if df.empty:
        return df
    itype = df["instrument_type"].astype(str).str.upper()
    exch = df["exchange"].astype(str).str.upper()

    if segment == "equity":
        return df[(exch.isin(_EQUITY_EXCHANGES)) & (itype == "EQ")]
    if segment == "future":
        return df[(exch.isin(_DERIV_EXCHANGES)) & (itype == "FUT")]
    if segment == "option":
        return df[(exch.isin(_DERIV_EXCHANGES)) & (itype.isin(["CE", "PE"]))]
    return df


def search_instruments(
    q: str,
    segment: Segment = "equity",
    limit: int = 25,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Search cached Kite instruments by tradingsymbol / name."""
    q = (q or "").strip()
    if len(q) < 1:
        return []

    df = load_instruments(force_refresh=force_refresh)
    subset = _filter_segment(df, segment)
    if subset.empty:
        return []

    q_upper = q.upper()
    tsym = subset["tradingsymbol"].astype(str).str.upper()
    name = subset["name"].astype(str).str.upper()

    prefix = subset[tsym.str.startswith(q_upper) | name.str.startswith(q_upper)]
    contains = subset[
        (tsym.str.contains(q_upper, regex=False) | name.str.contains(q_upper, regex=False))
        & ~subset.index.isin(prefix.index)
    ]
    ranked = pd.concat([prefix, contains]).drop_duplicates(subset=["instrument_token"]).head(limit)
    return [_compact_row(ranked.iloc[i]) for i in range(len(ranked))]


def resolve_by_token(instrument_token: int, force_refresh: bool = False) -> dict[str, Any]:
    df = load_instruments(force_refresh=force_refresh)
    hits = df[df["instrument_token"] == instrument_token]
    if hits.empty:
        raise RuntimeError(f"No instrument found for token {instrument_token}")
    row = hits.iloc[0]
    meta = _compact_row(row)
    meta["exchange_token"] = (
        int(row["exchange_token"])
        if "exchange_token" in row and pd.notna(row.get("exchange_token"))
        else None
    )
    return meta


def resolve_by_symbol(
    exchange: str,
    tradingsymbol: str,
    force_refresh: bool = False,
) -> dict[str, Any]:
    df = load_instruments(force_refresh=force_refresh)
    hits = df[
        (df["exchange"].astype(str).str.upper() == exchange.upper())
        & (df["tradingsymbol"].astype(str).str.upper() == tradingsymbol.upper())
    ]
    if hits.empty:
        raise RuntimeError(f"No instrument found for {exchange}:{tradingsymbol}")
    row = hits.iloc[0]
    meta = _compact_row(row)
    meta["exchange_token"] = (
        int(row["exchange_token"])
        if "exchange_token" in row and pd.notna(row.get("exchange_token"))
        else None
    )
    return meta


def _future_candidates(underlying: str, force_refresh: bool = False) -> pd.DataFrame:
    df = load_instruments(force_refresh=force_refresh)
    fut = _filter_segment(df, "future")
    if fut.empty:
        return fut
    u = underlying.strip().upper()
    name = fut["name"].astype(str).str.upper()
    tsym = fut["tradingsymbol"].astype(str).str.upper()

    # Prefer an exact underlying-name match so "NIFTY" does not also pull in
    # "NIFTYNXT50"/sector futures (which share the same monthly expiry dates and
    # would otherwise duplicate the expiry list and risk wrong-contract resolution).
    cand = fut[name == u].copy()
    if cand.empty:
        # Fallback for dumps with blank names: symbols look like NIFTY26JULFUT,
        # so require the underlying immediately followed by a digit (the year).
        cand = fut[tsym.str.match(rf"^{u}\d")].copy()
    if cand.empty:
        return cand

    cand["_exp"] = pd.to_datetime(cand["expiry"], errors="coerce")
    cand = cand.dropna(subset=["_exp"]).sort_values("_exp")
    # One contract per expiry (monthly) — guards against any duplicate rows.
    cand = cand.drop_duplicates(subset=["_exp"], keep="first")
    return cand


def list_future_expiries(underlying: str, force_refresh: bool = False) -> list[str]:
    """Upcoming (and current) monthly future expiries for an index underlying."""
    cand = _future_candidates(underlying, force_refresh=force_refresh)
    if cand.empty:
        return []
    today = pd.Timestamp(date.today())
    upcoming = cand[cand["_exp"] >= today.normalize()]
    rows = upcoming if not upcoming.empty else cand
    return [d.date().isoformat() for d in rows["_exp"].tolist()]


def resolve_future(
    underlying: str,
    expiry: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """
    Resolve a monthly futures contract token for an index underlying.

    Defaults to the front-month (first expiry >= today); pass ``expiry`` (ISO
    YYYY-MM-DD) to pin a specific contract.
    """
    cand = _future_candidates(underlying, force_refresh=force_refresh)
    if cand.empty:
        raise RuntimeError(f"No futures contract found for '{underlying}' — refresh instruments after Kite login.")

    if expiry:
        target = pd.to_datetime(expiry, errors="coerce")
        if pd.isna(target):
            raise ValueError(f"Invalid expiry '{expiry}' (use YYYY-MM-DD)")
        match = cand[cand["_exp"].dt.date == target.date()]
        if match.empty:
            raise RuntimeError(f"No {underlying} future expiring {expiry}")
        row = match.iloc[0]
    else:
        today = pd.Timestamp(date.today()).normalize()
        upcoming = cand[cand["_exp"] >= today]
        row = upcoming.iloc[0] if not upcoming.empty else cand.iloc[-1]

    meta = _compact_row(row)
    meta["expiry"] = row["_exp"].date().isoformat()
    return meta


def resolve_nse_index(
    tradingsymbol: str,
    *,
    name: str | None = None,
    fallbacks: list[str] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Resolve an NSE sector / broad index from the Kite instrument cache."""
    df = load_instruments(force_refresh=force_refresh)
    nse = df[df["exchange"].astype(str).str.upper() == "NSE"].copy()
    if nse.empty:
        raise RuntimeError("Instrument cache has no NSE rows — refresh after Kite login.")

    if "segment" in nse.columns:
        seg = nse["segment"].astype(str).str.upper()
        indices = nse[seg.str.contains("INDICES", na=False)]
        if not indices.empty:
            nse = indices

    needles: list[str] = []
    for item in [tradingsymbol, *(fallbacks or []), *( [name] if name else [])]:
        token = str(item).strip().upper()
        if token and token not in needles:
            needles.append(token)

    tsym = nse["tradingsymbol"].astype(str).str.upper()
    nm = nse["name"].astype(str).str.upper()
    for needle in needles:
        hits = nse[(tsym == needle) | (nm == needle)]
        if hits.empty:
            hits = nse[tsym.str.replace(" ", "", regex=False) == needle.replace(" ", "")]
        if not hits.empty:
            row = hits.iloc[0]
            meta = _compact_row(row)
            meta["exchange_token"] = (
                int(row["exchange_token"])
                if "exchange_token" in row and pd.notna(row.get("exchange_token"))
                else None
            )
            return meta

    raise RuntimeError(
        f"Could not resolve NSE index '{tradingsymbol}'. "
        "Refresh instruments after login."
    )


def resolve_underlying_index_token(underlying: str, force_refresh: bool = False) -> int:
    """Resolve NIFTY / BANKNIFTY / SENSEX to the Kite index instrument_token."""
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")
    key = INDEX_OPTIONS[underlying].get("index_token_key")
    if not key:
        raise RuntimeError(f"No index mapping for {underlying}")
    return int(resolve_instrument(key, force_refresh=force_refresh)["instrument_token"])


def resolve_instrument(key: str, force_refresh: bool = False) -> dict[str, Any]:
    """
    Resolve platform instrument key (NIFTY50 / SENSEX) to Kite instrument_token.
    """
    if key not in INSTRUMENTS:
        raise ValueError(f"Unknown instrument '{key}'. Use {list(INSTRUMENTS)}")

    lookup = KITE_INDEX_LOOKUP[key]
    df = load_instruments(force_refresh=force_refresh)

    exch = lookup["exchange"]
    name = lookup["name"]
    tsym = lookup["tradingsymbol"]

    subset = df[df["exchange"].astype(str).str.upper() == exch.upper()].copy()
    candidates = subset[
        (subset["tradingsymbol"].astype(str).str.upper() == tsym.upper())
        | (subset["name"].astype(str).str.upper() == name.upper())
    ]

    if candidates.empty:
        candidates = subset[
            subset["name"].astype(str).str.upper().str.contains(name.upper(), regex=False)
            | subset["tradingsymbol"].astype(str).str.upper().str.contains(
                tsym.upper().replace(" ", ""), regex=False
            )
        ]

    if candidates.empty:
        raise RuntimeError(
            f"Could not resolve Kite token for {key} ({exch} / {name}). "
            "Refresh instruments after login."
        )

    if "instrument_type" in candidates.columns:
        idx_rows = candidates[candidates["instrument_type"].astype(str).str.upper() == "EQ"]
        exact = candidates[candidates["name"].astype(str).str.upper() == name.upper()]
        pick = exact.iloc[0] if not exact.empty else (idx_rows.iloc[0] if not idx_rows.empty else candidates.iloc[0])
    else:
        pick = candidates.iloc[0]

    return {
        "key": key,
        "instrument_token": int(pick["instrument_token"]),
        "exchange_token": int(pick["exchange_token"]) if "exchange_token" in pick and pd.notna(pick["exchange_token"]) else None,
        "tradingsymbol": str(pick["tradingsymbol"]),
        "name": str(pick.get("name", "")),
        "exchange": str(pick["exchange"]),
        "lot_size": int(pick["lot_size"]) if "lot_size" in pick and pd.notna(pick.get("lot_size")) else 1,
    }


def list_resolved() -> list[dict[str, Any]]:
    out = []
    for key in INSTRUMENTS:
        try:
            out.append(resolve_instrument(key))
        except Exception as e:
            out.append({"key": key, "error": str(e)})
    return out
