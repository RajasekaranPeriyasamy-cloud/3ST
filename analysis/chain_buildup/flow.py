"""Underlying order flow for the Chain Build-Up time axis.

The strike ladder gets per-strike traded volume straight from the archive (see
``features._side_row``). This module answers the other half of the question: what
did the *underlying* do over the same buckets — a strip under the grid, on the
same time axis.

The index itself has no volume, so this reads the **front-month future**, which
is what the Volume Footprint desk and ``options/session_poc.py`` already use for
the same reason.

Cost, and why this is not in ``/buildup/grid``
---------------------------------------------
One ``fetch_historical_by_token`` call per (underlying, timeframe, session) —
one instrument, not four hundred — cached per session. Cheap, but still a Kite
call, so it lives behind its own endpoint on the same principle as
``levels.py``: the grid stays a pure archive read, and a futures-feed problem
greys one strip instead of blanking the ladder.

Delta is absent HERE, and only here
-----------------------------------
The strike ladder does classify direction: it has an archived book per leg per
minute, and on 2026-08-31 the quote rule left only 0.2% of volume unattributed.
This strip cannot, for a different reason — it is built from Kite OHLCV candles
for the future, which carry no bid or ask to classify against. Same desk, same
session, two different data shapes.

A "delta" derived from where a bar closed in its range — the geometric estimator
in ``vendor/volume_footprint/engines.py`` — would work on exactly this data and
is deliberately NOT used, because its own docstring is the reason: it "cannot
know that price ran up and got sold back into". Volume here is a measurement;
a geometric delta would be a guess sitting next to a measured one, which is the
worst possible pairing on one screen.

Giving this strip a real delta means archiving the future's top of book the way
``delta_velocity.collector`` archives the options' — a collector change, not a
change here.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from utils.logging import get_logger, log_event

IST = ZoneInfo("Asia/Kolkata")
logger = get_logger("chain_buildup.flow")

_TIMEFRAME_KEY = {5: "5min", 15: "15min", 30: "30min", 60: "60min"}

_CACHE_LOCK = threading.RLock()
#: ``(underlying, timeframe_min, session) -> list[bar dict]``
_BARS_CACHE: dict[tuple[str, int, str], list[dict[str, Any]]] = {}


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _fetch_bars(underlying: str, timeframe_min: int, session_date: date) -> list[dict[str, Any]]:
    key = (underlying.upper(), int(timeframe_min), session_date.isoformat())
    with _CACHE_LOCK:
        hit = _BARS_CACHE.get(key)
    if hit is not None:
        return hit

    from instruments import resolve_future
    from kite_client import fetch_historical_by_token

    fut = resolve_future(underlying)
    frame = fetch_historical_by_token(
        int(fut["instrument_token"]),
        _TIMEFRAME_KEY[timeframe_min],
        datetime.combine(session_date, time(9, 0)),
        datetime.combine(session_date, time(15, 40)),
    )
    bars: list[dict[str, Any]] = []
    if frame is not None and not frame.empty:
        reset = frame.reset_index()
        stamp = reset.columns[0]
        for record in reset.to_dict("records"):
            bars.append(
                {
                    "date": record.get(stamp),
                    "open": _num(record.get("open")),
                    "high": _num(record.get("high")),
                    "low": _num(record.get("low")),
                    "close": _num(record.get("close")),
                    "volume": _num(record.get("volume")),
                }
            )
    with _CACHE_LOCK:
        _BARS_CACHE[key] = bars
    return bars


def _bar_close(value: Any, timeframe_min: int) -> datetime | None:
    """Kite stamps a candle by its OPEN; the grid buckets by close."""
    from datetime import timedelta

    ts = value
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            return None
    if not isinstance(ts, datetime):
        try:
            ts = ts.to_pydatetime()  # pandas Timestamp
        except AttributeError:
            return None
    if ts.tzinfo is not None:
        ts = ts.astimezone(IST).replace(tzinfo=None)
    return ts + timedelta(minutes=timeframe_min)


def _empty(
    underlying: str, session_date: date, timeframe_min: int, bucket_ends: list[datetime], reason: str
) -> dict[str, Any]:
    """An unavailable strip, in the SAME shape as an available one.

    A failure path that drops keys turns a degraded feed into an AttributeError
    two layers up — which is exactly what happened the first time this was run
    against an expired Kite token.
    """
    return {
        "underlying": underlying,
        "session_date": session_date.isoformat(),
        "timeframe_min": timeframe_min,
        "available": False,
        "reason": reason,
        "coverage": 0,
        "buckets": len(bucket_ends),
        "max_volume": None,
        "total_volume": None,
        "points": [
            {"key": e.strftime("%H:%M"), "volume": None, "cum_volume": None, "close": None}
            for e in bucket_ends
        ],
    }


def underlying_flow(
    underlying: str,
    session_date: date,
    bucket_ends: list[datetime],
    *,
    timeframe_min: int = 5,
) -> dict[str, Any]:
    """Front-month future volume per bucket, aligned to the ladder's time axis."""
    u = str(underlying).upper()
    if timeframe_min not in _TIMEFRAME_KEY:
        raise ValueError(f"Unsupported timeframe {timeframe_min}. Use {list(_TIMEFRAME_KEY)}")

    try:
        bars = _fetch_bars(u, timeframe_min, session_date)
    except Exception as exc:
        log_event(logger, logging.WARNING, "chain_buildup_flow_failed",
                  underlying=u, error=str(exc)[:200])
        return _empty(u, session_date, timeframe_min, bucket_ends, "futures_bars_unavailable")

    by_close: dict[datetime, dict[str, Any]] = {}
    for bar in bars:
        close_at = _bar_close(bar.get("date"), timeframe_min)
        if close_at is not None:
            by_close[close_at] = bar

    points: list[dict[str, Any]] = []
    running = 0.0
    covered = 0
    for end in bucket_ends:
        bar = by_close.get(end)
        volume = bar.get("volume") if bar else None
        if volume is not None:
            running += volume
            covered += 1
        points.append(
            {
                "key": end.strftime("%H:%M"),
                "volume": volume,
                # Running total across the buckets actually rendered, so the
                # strip reads against this grid rather than against a session
                # the grid may only partly cover.
                "cum_volume": running if volume is not None else None,
                "close": bar.get("close") if bar else None,
            }
        )

    vols = [p["volume"] for p in points if p["volume"] is not None]
    return {
        "underlying": u,
        "session_date": session_date.isoformat(),
        "timeframe_min": timeframe_min,
        "available": bool(vols),
        "reason": None if vols else "no_bars_for_session",
        # Reported for the same reason the level track reports it: a strip with
        # gaps must say whether the feed was quiet or absent.
        "coverage": covered,
        "buckets": len(points),
        "max_volume": max(vols) if vols else None,
        "total_volume": sum(vols) if vols else None,
        "points": points,
    }


def reset_cache() -> None:
    with _CACHE_LOCK:
        _BARS_CACHE.clear()
