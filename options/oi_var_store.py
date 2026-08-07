"""EOD OI baseline cache for OI VAR desk (prev session close OI per token)."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from settings import data_dir

IST = ZoneInfo("Asia/Kolkata")
BASELINE_FILE = data_dir() / "oi_var_eod_baseline.json"
CRORE = 10_000_000.0


def _cache_key(underlying: str, expiry: str) -> str:
    return f"{underlying.upper()}|{expiry}"


def prev_trading_date(from_day: date | None = None) -> date:
    d = (from_day or date.today()) - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def load_baseline_file() -> dict[str, Any]:
    if not BASELINE_FILE.exists():
        return {"entries": {}}
    try:
        data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"entries": {}}
    if not isinstance(data, dict):
        return {"entries": {}}
    data.setdefault("entries", {})
    return data


def save_baseline_file(data: dict[str, Any]) -> None:
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_cached_baseline(underlying: str, expiry: str) -> tuple[str | None, dict[str, int]]:
    data = load_baseline_file()
    entry = data.get("entries", {}).get(_cache_key(underlying, expiry), {})
    if not isinstance(entry, dict):
        return None, {}
    baseline_date = entry.get("baseline_date")
    oi_map = entry.get("oi_by_token") or {}
    normalized: dict[str, int] = {}
    for k, v in oi_map.items():
        if v is not None:
            normalized[str(k)] = int(v)
    return (str(baseline_date) if baseline_date else None), normalized


def is_baseline_valid(underlying: str, expiry: str) -> bool:
    expected = prev_trading_date().isoformat()
    cached_date, oi_map = get_cached_baseline(underlying, expiry)
    return cached_date == expected and len(oi_map) > 0


def save_baseline(underlying: str, expiry: str, baseline_date: str, oi_by_token: dict[int | str, int]) -> None:
    data = load_baseline_file()
    entries = data.setdefault("entries", {})
    entries[_cache_key(underlying, expiry)] = {
        "baseline_date": baseline_date,
        "underlying": underlying.upper(),
        "expiry": expiry,
        "oi_by_token": {str(k): int(v) for k, v in oi_by_token.items()},
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_baseline_file(data)


def fetch_session_close_oi(instrument_token: int, session_day: date) -> int | None:
    """Last minute OI on a given session date (15:25–15:40 IST, CAS/F&O close)."""
    from kite_client import _kite_direct_client

    start = datetime.combine(session_day, time(15, 25), tzinfo=IST)
    end = datetime.combine(session_day, time(15, 40), tzinfo=IST)
    try:
        kite = _kite_direct_client()
        raw = kite.historical_data(
            instrument_token=int(instrument_token),
            from_date=start,
            to_date=end,
            interval="minute",
            continuous=False,
            oi=True,
        )
    except Exception:
        return None

    if not raw:
        return None
    last = raw[-1]
    if isinstance(last, dict):
        oi = last.get("oi")
    else:
        oi = last[6] if len(last) > 6 else None
    return int(oi) if oi is not None else None


def ensure_eod_baseline(
    underlying: str,
    expiry: str,
    legs: list[dict[str, Any]],
    *,
    force_refresh: bool = False,
) -> tuple[str, dict[str, int]]:
    """
    Return baseline_date and oi_by_token (string keys).
    Builds cache from prev trading session close if missing.
    """
    baseline_date = prev_trading_date().isoformat()
    if not force_refresh and is_baseline_valid(underlying, expiry):
        _, cached = get_cached_baseline(underlying, expiry)
        return baseline_date, cached

    session_day = prev_trading_date()
    oi_by_token: dict[str, int] = {}
    for leg in legs:
        token = leg.get("instrument_token")
        if not token:
            continue
        oi = fetch_session_close_oi(int(token), session_day)
        if oi is not None:
            oi_by_token[str(token)] = oi

    if oi_by_token:
        save_baseline(underlying, expiry, baseline_date, oi_by_token)
    return baseline_date, oi_by_token
