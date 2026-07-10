"""Kite instruments cache, search, and token resolution."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from config import INDEX_OPTIONS, INSTRUMENTS, KITE_INDEX_LOOKUP
from kite_auth import get_kite_client, session_status
from settings import data_dir

CACHE_FILE = data_dir() / "kite_instruments.json"


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


def refresh_instruments(force: bool = False) -> list[dict[str, Any]]:
    if not force and _cache_fresh(CACHE_FILE):
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))

    kite = get_kite_client()
    rows = kite.instruments()
    safe_rows = _json_safe(rows)
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(safe_rows), encoding="utf-8")
    return rows


def load_instruments(force_refresh: bool = False) -> pd.DataFrame:
    if force_refresh:
        rows = refresh_instruments(force=True)
    elif _cache_fresh(CACHE_FILE):
        rows = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    elif CACHE_FILE.exists():
        rows = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    else:
        if not session_status().get("authenticated"):
            raise RuntimeError(
                "Instrument cache empty. Log in to Kite and refresh instruments."
            )
        rows = refresh_instruments(force=True)
    df = pd.DataFrame(rows)
    return df


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
