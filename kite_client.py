"""Kite Connect market data helpers — historical candles."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from config import KITE_INTERVALS, KITE_MAX_DAYS
from instruments import resolve_instrument
from kite_auth import get_kite_client, session_status


def _to_dt(d: date | datetime) -> datetime:
    if isinstance(d, datetime):
        return d
    return datetime.combine(d, datetime.min.time()).replace(hour=9, minute=15)


def _candles_to_df(rows: list[list[Any]]) -> pd.DataFrame:
    """
    Kite candle: [timestamp, open, high, low, close, volume] (+ oi optional)
    """
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi"])

    records = []
    for r in rows:
        ts = pd.to_datetime(r[0])
        rec = {
            "datetime": ts,
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]) if len(r) > 5 else 0.0,
            "oi": float(r[6]) if len(r) > 6 else 0.0,
        }
        records.append(rec)

    df = pd.DataFrame(records).set_index("datetime").sort_index()
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_localize(None)
    return df[~df.index.duplicated(keep="last")]


def kite_max_lookback_days(timeframe: str) -> int:
    return int(KITE_MAX_DAYS.get(timeframe, 400))


def default_kite_date_range(timeframe: str) -> tuple[date, date]:
    end = date.today()
    start = end - timedelta(days=kite_max_lookback_days(timeframe))
    return start, end


def fetch_historical_by_token(
    instrument_token: int,
    timeframe: str,
    start: date | datetime,
    end: date | datetime,
    chunk_days: int = 60,
) -> pd.DataFrame:
    """Fetch OHLC from Kite for any instrument token."""
    if timeframe not in KITE_INTERVALS:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Use {list(KITE_INTERVALS)}")

    interval = KITE_INTERVALS[timeframe]
    start_dt = _to_dt(start)
    end_dt = _to_dt(end)
    if end_dt.hour == 9 and end_dt.minute == 15 and isinstance(end, date) and not isinstance(end, datetime):
        end_dt = end_dt.replace(hour=15, minute=30)

    if start_dt >= end_dt:
        raise ValueError("start must be before end")

    from kite_auth import _is_proxy_error

    last_err: Exception | None = None
    for force_direct in (False, True):
        try:
            kite = get_kite_client() if not force_direct else _kite_direct_client()
            return _fetch_historical_chunks(
                kite, instrument_token, interval, start_dt, end_dt, chunk_days
            )
        except Exception as e:
            last_err = e
            if force_direct or not _is_proxy_error(e):
                raise
    if last_err:
        raise last_err
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi"])


def _kite_direct_client():
    from kite_auth import _kite, load_session

    sess = load_session()
    if not sess:
        raise RuntimeError("Not logged in. Complete Kite login and POST /auth/session.")
    kite = _kite(use_proxy=False)
    kite.set_access_token(sess["access_token"])
    return kite


def _fetch_historical_chunks(
    kite,
    instrument_token: int,
    interval: str,
    start_dt: datetime,
    end_dt: datetime,
    chunk_days: int,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    cursor = start_dt
    while cursor < end_dt:
        chunk_end = min(cursor + timedelta(days=chunk_days), end_dt)
        raw = kite.historical_data(
            instrument_token=instrument_token,
            from_date=cursor,
            to_date=chunk_end,
            interval=interval,
            continuous=False,
            oi=False,
        )
        if raw and isinstance(raw[0], dict):
            rows = [
                [
                    r["date"],
                    r["open"],
                    r["high"],
                    r["low"],
                    r["close"],
                    r.get("volume", 0),
                    r.get("oi", 0),
                ]
                for r in raw
            ]
        else:
            rows = raw or []
        part = _candles_to_df(rows)
        if not part.empty:
            frames.append(part)
        cursor = chunk_end + timedelta(seconds=1)

    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi"])

    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="last")]


def fetch_historical(
    instrument_key: str,
    timeframe: str,
    start: date | datetime,
    end: date | datetime,
    chunk_days: int = 60,
) -> pd.DataFrame:
    """
    Fetch OHLC from Kite historical API for NIFTY50 / SENSEX or by token key alias.
    """
    meta = resolve_instrument(instrument_key)
    return fetch_historical_by_token(
        meta["instrument_token"], timeframe, start, end, chunk_days=chunk_days
    )


def fetch_historical_for_selection(
    instrument_token: int | None,
    instrument_key: str | None,
    timeframe: str,
    start: date | datetime,
    end: date | datetime,
    chunk_days: int = 60,
) -> pd.DataFrame:
    if instrument_token is not None:
        return fetch_historical_by_token(instrument_token, timeframe, start, end, chunk_days)
    if instrument_key:
        return fetch_historical(instrument_key, timeframe, start, end, chunk_days)
    raise ValueError("Provide instrument_token or instrument_key")


def profile() -> dict[str, Any]:
    kite = get_kite_client()
    return kite.profile()


def margins() -> dict[str, Any]:
    kite = get_kite_client()
    return kite.margins()


def status_bundle() -> dict[str, Any]:
    st = session_status()
    if not st.get("authenticated"):
        return st
    try:
        st["profile"] = profile()
    except Exception as e:
        st["profile_error"] = str(e)
    return st


def fetch_minute_candles(instrument_token: int, minutes: int = 40) -> list[dict[str, Any]]:
    """Fetch minute candles with OHLC, volume, and OI for the last N minutes."""
    return fetch_minute_oi(instrument_token, minutes=minutes)


def fetch_minute_oi(instrument_token: int, minutes: int = 40) -> list[dict[str, Any]]:
    """Fetch minute candles with Open Interest for the last N minutes."""
    from kite_auth import _is_proxy_error

    to_date = datetime.now()
    from_date = to_date - timedelta(minutes=minutes)
    last_err: Exception | None = None
    for force_direct in (False, True):
        try:
            kite = _kite_direct_client() if force_direct else get_kite_client()
            raw = kite.historical_data(
                instrument_token=instrument_token,
                from_date=from_date,
                to_date=to_date,
                interval="minute",
                continuous=False,
                oi=True,
            )
            break
        except Exception as e:
            last_err = e
            if force_direct or not _is_proxy_error(e):
                raise
    else:
        if last_err:
            raise last_err
        return []
    if not raw:
        return []
    if isinstance(raw[0], dict):
        return raw
    # Legacy list format
    out: list[dict[str, Any]] = []
    for r in raw:
        out.append(
            {
                "date": r[0],
                "open": r[1],
                "high": r[2],
                "low": r[3],
                "close": r[4],
                "volume": r[5] if len(r) > 5 else 0,
                "oi": r[6] if len(r) > 6 else 0,
            }
        )
    return out


def fetch_index_minute_spot(underlying: str, minutes: int = 40) -> list[dict[str, Any]]:
    """Fetch index minute candles for spot history used in IV back-calculation."""
    from config import INDEX_OPTIONS

    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'")
    key = INDEX_OPTIONS[underlying].get("index_token_key")
    if not key:
        return []
    meta = resolve_instrument(key)
    return fetch_minute_candles(int(meta["instrument_token"]), minutes=minutes)


def fetch_quote_batch(instruments: list[str]) -> dict[str, dict[str, Any]]:
    """Retrieve full market quotes for up to 500 instruments in one call."""
    if not instruments:
        return {}
    from kite_auth import _is_proxy_error

    last_err: Exception | None = None
    for force_direct in (False, True):
        try:
            kite = _kite_direct_client() if force_direct else get_kite_client()
            return dict(kite.quote(*instruments))
        except Exception as e:
            last_err = e
            if force_direct or not _is_proxy_error(e):
                raise
    if last_err:
        raise last_err
    return {}
