"""Yahoo Finance data client — no broker login required.

Yahoo intraday limits (approx):
  5m  → ~60 days
  15m → ~60 days
  30m → ~60 days
Daily → max available history
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from config import INSTRUMENTS, TIMEFRAMES, YAHOO_MAX_DAYS

# Yahoo interval codes
_YF_INTERVAL = {
    "1min": "1m",
    "3min": "5m",
    "5min": "5m",
    "15min": "15m",
    "30min": "30m",
    "60min": "60m",
}


def max_lookback_days(timeframe: str) -> int:
    return int(YAHOO_MAX_DAYS.get(timeframe, 60))


def default_date_range(timeframe: str) -> tuple[date, date]:
    """Return (start, end) covering Yahoo's maximum for this timeframe."""
    end = date.today()
    start = end - timedelta(days=max_lookback_days(timeframe))
    return start, end


def _normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi"])

    # yfinance may return MultiIndex columns
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    colmap = {c: str(c).lower() for c in df.columns}
    df = df.rename(columns=colmap)

    # Ensure timezone-naive index in local/IST-friendly form
    if getattr(df.index, "tz", None) is not None:
        df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)

    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["oi"] = 0.0
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df[["open", "high", "low", "close", "volume", "oi"]]


def fetch_candles(
    instrument: str,
    timeframe: str,
    start: date | datetime | None = None,
    end: date | datetime | None = None,
    use_max: bool = True,
) -> pd.DataFrame:
    """
    Fetch OHLC from Yahoo Finance.
    instrument: NIFTY50 | SENSEX
    timeframe: 5min | 15min | 30min
    use_max=True ignores start/end and pulls Yahoo's maximum window for the TF.
    """
    if instrument not in INSTRUMENTS:
        raise ValueError(f"Unknown instrument '{instrument}'. Use {list(INSTRUMENTS)}")
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Use {list(TIMEFRAMES)}")

    meta = INSTRUMENTS[instrument]
    ticker = meta["yahoo_symbol"]
    interval = _YF_INTERVAL[timeframe]

    if use_max or start is None or end is None:
        start_d, end_d = default_date_range(timeframe)
    else:
        start_d = start.date() if isinstance(start, datetime) else start
        end_d = end.date() if isinstance(end, datetime) else end

    # Yahoo end is exclusive for some intervals — add 1 day
    end_exclusive = end_d + timedelta(days=1)

    # Cap to Yahoo max window so requests don't silently return empty
    earliest = date.today() - timedelta(days=max_lookback_days(timeframe))
    if start_d < earliest:
        start_d = earliest

    t = yf.Ticker(ticker)
    raw = t.history(
        start=start_d.isoformat(),
        end=end_exclusive.isoformat(),
        interval=interval,
        auto_adjust=False,
        actions=False,
    )

    # Fallback: period= string if history empty
    if raw is None or raw.empty:
        period = "60d" if timeframe in {"5min", "15min", "30min", "60min"} else "max"
        raw = t.history(period=period, interval=interval, auto_adjust=False, actions=False)

    out = _normalize_ohlc(raw)
    if out.empty:
        raise RuntimeError(
            f"Yahoo returned no candles for {ticker} ({timeframe}). "
            "Try again later or pick another timeframe."
        )
    return out


def instrument_label(instrument: str) -> str:
    return INSTRUMENTS[instrument]["label"]
