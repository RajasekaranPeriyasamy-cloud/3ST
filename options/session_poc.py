"""Session futures volume Point of Control (Fut POC).

Display-only helper: bins today's front-month futures 1-minute volume by
typical price ``(H+L+C)/3`` snapped to the index ``strike_step``.

This is **not** the OI Profile purple POC (max OI-change by strike). Callers
must keep using index LTP for GEX / OI board math — attach ``session_poc`` as
an extra reference level only.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import INDEX_OPTIONS
from instruments import resolve_future
from kite_client import fetch_historical_by_token
from options.gamma_density_history import session_window

IST = ZoneInfo("Asia/Kolkata")

# Light in-process cache so Gamma + OI Movers polls share one Kite pull.
CACHE_TTL_SEC = 45.0

_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_CACHE_LOCK = threading.Lock()


def _now_ist(when: datetime | None = None) -> datetime:
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def _asof_iso(when: datetime | None = None) -> str:
    return _now_ist(when).isoformat(timespec="seconds")


def _strike_step(underlying: str) -> int | None:
    meta = INDEX_OPTIONS.get(underlying.strip().upper())
    if not meta or not meta.get("strike_step"):
        return None
    step = int(meta["strike_step"])
    return step if step > 0 else None


def _bar_ts_iso(value: Any) -> str | None:
    """Normalize a candle timestamp to IST ISO seconds (matches chart series)."""
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        else:
            # pandas Timestamp / numpy datetime64 / string
            import pandas as pd

            dt = pd.Timestamp(value).to_pydatetime()
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    return dt.isoformat(timespec="seconds")


def snap_typical_to_bin(typical: float, bin_step: float) -> float:
    """Snap typical price to nearest strike-step bin."""
    step = float(bin_step)
    if step <= 0:
        raise ValueError("bin_step must be positive")
    return round(float(typical) / step) * step


def poc_from_bars(
    bars: list[dict[str, Any]],
    *,
    bin_step: float,
) -> tuple[float | None, int, list[dict[str, Any]]]:
    """Bin volume by typical price; return ``(poc, total_volume, path)``.

    ``path`` is ``[{t, close}, ...]`` with ISO timestamps when a bar time is
    present. Returns ``(None, 0, path_or_empty)`` when volume is zero.
    """
    step = float(bin_step)
    if step <= 0 or not bars:
        return None, 0, []

    volumes: dict[float, float] = {}
    total = 0.0
    path: list[dict[str, Any]] = []

    for bar in bars:
        try:
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            vol = float(bar.get("volume") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue

        t_iso = _bar_ts_iso(bar.get("date") if "date" in bar else bar.get("t"))
        if t_iso is not None:
            path.append({"t": t_iso, "close": round(close, 2)})

        if vol <= 0:
            continue
        typical = (high + low + close) / 3.0
        level = snap_typical_to_bin(typical, step)
        key = round(float(level), 4)
        volumes[key] = volumes.get(key, 0.0) + vol
        total += vol

    if total <= 0 or not volumes:
        return None, 0, path

    # Strict > keeps the lower price on ties (ascending scan).
    best_lvl = min(volumes.keys())
    best_vol = volumes[best_lvl]
    for lvl in sorted(volumes.keys()):
        v = volumes[lvl]
        if v > best_vol:
            best_vol = v
            best_lvl = lvl

    return float(best_lvl), int(total), path


def clear_session_poc_cache() -> None:
    """Test helper — drop the in-process cache."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _session_fetch_window(
    underlying: str,
    when: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Naive IST ``(start, end]`` for today's session through now / F&O close."""
    now = _now_ist(when)
    start_t, end_t = session_window(underlying)
    start_dt = datetime.combine(now.date(), start_t)  # naive IST
    end_cap = datetime.combine(now.date(), end_t)
    now_naive = now.replace(tzinfo=None)
    fetch_end = min(now_naive, end_cap)
    if fetch_end <= start_dt:
        return None
    return start_dt, fetch_end


def _compute_uncached(
    underlying: str,
    *,
    when: datetime | None = None,
    bars: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    u = underlying.strip().upper()
    step = _strike_step(u)
    if step is None:
        return None

    try:
        fut = resolve_future(u)
    except Exception:
        return None

    token = int(fut.get("instrument_token") or 0)
    symbol = str(fut.get("tradingsymbol") or "")
    if token <= 0 or not symbol:
        return None

    if bars is None:
        window = _session_fetch_window(u, when=when)
        if window is None:
            return None
        start_dt, fetch_end = window
        try:
            df = fetch_historical_by_token(token, "1min", start_dt, fetch_end)
        except Exception:
            return None
        if df is None or getattr(df, "empty", True):
            return None
        bars = []
        for ts, row in df.iterrows():
            bars.append(
                {
                    "date": ts,
                    "high": float(row.high),
                    "low": float(row.low),
                    "close": float(row.close),
                    "volume": float(row.volume) if row.volume == row.volume else 0.0,
                }
            )

    poc, total_volume, path = poc_from_bars(bars, bin_step=step)
    if poc is None or total_volume <= 0:
        return None

    return {
        "poc": float(poc),
        "fut_symbol": symbol,
        "fut_token": token,
        "bin_step": int(step),
        "total_volume": int(total_volume),
        "asof": _asof_iso(when),
        "path": path,
    }


def compute_session_poc(
    underlying: str,
    *,
    when: datetime | None = None,
    bars: list[dict[str, Any]] | None = None,
    use_cache: bool = True,
) -> dict[str, Any] | None:
    """Session futures volume POC for ``underlying``, or ``None``.

    Returns ``None`` when the future cannot be resolved, there are no bars,
    or total session volume is zero. Results are cached ~45s in-process
    (skipped when ``bars`` is injected or ``use_cache`` is False).
    """
    u = underlying.strip().upper()
    # Injected bars / explicit bypass skip the shared Kite cache.
    if bars is not None or not use_cache:
        return _compute_uncached(u, when=when, bars=bars)

    now_mono = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(u)
        if hit is not None:
            ts, payload = hit
            if (now_mono - ts) < CACHE_TTL_SEC:
                return payload

    payload = _compute_uncached(u, when=when, bars=None)
    with _CACHE_LOCK:
        _CACHE[u] = (time.monotonic(), payload)
    return payload
