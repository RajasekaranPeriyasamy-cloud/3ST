"""OI Profile — index-futures candles + OI-by-price butterfly + daily OI change.

Pulls historical futures candles *with open interest* (Kite ``oi=True``) for a
monthly index-future contract, then derives three views from the same series:

1. **Candles + OI** — per-bar OHLC/volume/OI and bar-over-bar OI change, for a
   candlestick panel with an OI overlay.
2. **OI-by-price butterfly** — buckets bars by price and splits OI *buildup*
   (bars where OI rose) from OI *unwinding* (bars where OI fell) at each price
   level, giving a two-sided horizontal profile plus the point-of-control (the
   price where the most OI changed hands) and the strongest buildup "walls".
3. **Daily OI change** — day-over-day close/OI deltas classified into the four
   classic interpretations (Long buildup / Short buildup / Short covering /
   Long unwinding).

Reuses ``instruments.resolve_future`` for contract resolution and the OI-aware
``kite_client.fetch_historical_by_token`` for data, so everything tracks Kite.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

from config import INDEX_OPTIONS, OI_PROFILE_DEFAULTS
from instruments import list_future_expiries, resolve_future
from kite_client import fetch_historical_by_token

# Strike step per index, used to snap the OI-by-price profile onto real strikes.
_STRIKE_STEP: dict[str, int] = {"NIFTY": 50, "BANKNIFTY": 100, "FINNIFTY": 50, "SENSEX": 100}


def _strike_step(underlying: str) -> int:
    u = underlying.upper()
    meta = INDEX_OPTIONS.get(u)
    if meta and meta.get("strike_step"):
        return int(meta["strike_step"])
    return _STRIKE_STEP.get(u, 50)


def oi_profile_config() -> dict[str, Any]:
    d = OI_PROFILE_DEFAULTS
    return {
        "underlyings": list(d["underlyings"]),
        "intervals": list(d["intervals"]),
        "default_interval": d["default_interval"],
        "default_days": d["default_days"],
        "max_days": d["max_days"],
        "price_buckets": d["price_buckets"],
        "refresh_seconds": d["refresh_seconds"],
    }


def _classify(price_chg: float, oi_chg: float) -> str:
    """Classic price/OI buildup interpretation."""
    if oi_chg > 0:
        return "Long buildup" if price_chg > 0 else "Short buildup"
    if oi_chg < 0:
        return "Short covering" if price_chg > 0 else "Long unwinding"
    return "Neutral"


def _build_price_profile(df: pd.DataFrame, step: float) -> tuple[list[dict[str, Any]], float | None]:
    """Two-sided OI-by-strike histogram: buildup (OI up) vs unwinding (OI down).

    Each bar is snapped to the nearest **strike level** (multiple of ``step``) so
    the profile's price axis lands on real strikes (e.g. 24500, 24550) rather than
    arbitrary bucket midpoints.
    """
    close = df["close"].to_numpy(dtype=float)
    oi_chg = df["oi_change"].to_numpy(dtype=float)
    if close.size == 0 or step <= 0:
        return [], None

    levels = np.round(close / step) * step
    agg: dict[float, list[float]] = {}
    for lvl, d_oi in zip(levels, oi_chg):
        key = round(float(lvl), 2)
        bu = agg.setdefault(key, [0.0, 0.0])
        if d_oi > 0:
            bu[0] += d_oi
        elif d_oi < 0:
            bu[1] += -d_oi

    rows: list[dict[str, Any]] = []
    poc_price: float | None = None
    best = 0.0
    for lvl in sorted(agg.keys(), reverse=True):
        buildup, unwind = agg[lvl]
        if buildup == 0 and unwind == 0:
            continue
        rows.append(
            {
                "price_low": round(lvl - step / 2.0, 2),
                "price_high": round(lvl + step / 2.0, 2),
                "price_mid": lvl,  # the strike level itself
                "buildup": round(buildup, 2),
                "unwind": round(unwind, 2),
                "net": round(buildup - unwind, 2),
            }
        )
        if buildup + unwind > best:
            best = buildup + unwind
            poc_price = lvl
    return rows, poc_price


def _daily_oi_change(df: pd.DataFrame) -> list[dict[str, Any]]:
    g = df.groupby(df.index.normalize())
    daily = pd.DataFrame(
        {
            "open": g["open"].first(),
            "high": g["high"].max(),
            "low": g["low"].min(),
            "close": g["close"].last(),
            "volume": g["volume"].sum(),
            "oi": g["oi"].last(),
        }
    ).sort_index()

    daily["prev_close"] = daily["close"].shift(1)
    daily["prev_oi"] = daily["oi"].shift(1)

    out: list[dict[str, Any]] = []
    for ts, row in daily.iterrows():
        prev_close = row["prev_close"]
        prev_oi = row["prev_oi"]
        price_chg = float(row["close"] - prev_close) if pd.notna(prev_close) else 0.0
        price_chg_pct = (
            float(price_chg / prev_close * 100.0) if pd.notna(prev_close) and prev_close else 0.0
        )
        oi_chg = float(row["oi"] - prev_oi) if pd.notna(prev_oi) else 0.0
        oi_chg_pct = float(oi_chg / prev_oi * 100.0) if pd.notna(prev_oi) and prev_oi else 0.0
        has_prev = pd.notna(prev_close) and pd.notna(prev_oi)
        out.append(
            {
                "date": ts.date().isoformat(),
                "close": round(float(row["close"]), 2),
                "price_chg": round(price_chg, 2),
                "price_chg_pct": round(price_chg_pct, 2),
                "volume": int(row["volume"]) if pd.notna(row["volume"]) else 0,
                "oi": int(row["oi"]) if pd.notna(row["oi"]) else 0,
                "oi_chg": int(oi_chg),
                "oi_chg_pct": round(oi_chg_pct, 2),
                "interpretation": _classify(price_chg, oi_chg) if has_prev else "—",
            }
        )
    return out


def oi_profile_snapshot(
    underlying: str,
    *,
    expiry: str | None = None,
    interval: str | None = None,
    days: int | None = None,
) -> dict[str, Any]:
    d = OI_PROFILE_DEFAULTS
    interval = interval or d["default_interval"]
    if interval not in d["intervals"]:
        raise ValueError(f"Invalid interval '{interval}'. Allowed: {', '.join(d['intervals'])}")
    days = int(days or d["default_days"])
    days = max(1, min(days, int(d["max_days"])))

    fut = resolve_future(underlying, expiry)
    token = int(fut["instrument_token"])
    resolved_expiry = fut.get("expiry")
    step = _strike_step(underlying)

    end = date.today()
    start = end - timedelta(days=days)
    df = fetch_historical_by_token(token, interval, start, end, oi=True)

    meta = {
        "underlying": underlying,
        "fut_symbol": fut.get("tradingsymbol"),
        "fut_token": token,
        "exchange": fut.get("exchange"),
        "lot_size": fut.get("lot_size"),
        "expiry": resolved_expiry,
        "interval": interval,
        "days": days,
        "available_expiries": list_future_expiries(underlying),
        "price_buckets": int(d["price_buckets"]),
        "strike_step": step,
    }

    if df.empty:
        return {
            "ok": True,
            "empty": True,
            "message": "No futures candles returned for this contract/interval.",
            "meta": meta,
            "candles": [],
            "profile": [],
            "poc_price": None,
            "daily": [],
            "stats": {},
        }

    df = df.copy()
    df["oi_change"] = df["oi"].diff().fillna(0.0)

    candles = [
        {
            "t": ts.isoformat(),
            "open": round(float(r.open), 2),
            "high": round(float(r.high), 2),
            "low": round(float(r.low), 2),
            "close": round(float(r.close), 2),
            "volume": int(r.volume),
            "oi": int(r.oi),
            "oi_change": int(r.oi_change),
        }
        for ts, r in df.iterrows()
    ]

    profile, poc_price = _build_price_profile(df, step)
    daily = _daily_oi_change(df)

    last = df.iloc[-1]
    total_buildup = float(df["oi_change"].clip(lower=0).sum())
    total_unwind = float(-df["oi_change"].clip(upper=0).sum())
    walls = sorted(
        (p for p in profile if p["buildup"] > 0),
        key=lambda p: p["buildup"],
        reverse=True,
    )[:3]

    stats = {
        "current_price": round(float(last.close), 2),
        "current_oi": int(last.oi),
        "session_oi_change": int(df["oi_change"].sum()),
        "total_buildup": int(total_buildup),
        "total_unwind": int(total_unwind),
        "poc_price": poc_price,
        "oi_walls": [w["price_mid"] for w in walls],
        "last_bar": df.index[-1].isoformat(),
        "day_interpretation": daily[-1]["interpretation"] if daily else "—",
    }

    return {
        "ok": True,
        "empty": False,
        "meta": meta,
        "candles": candles,
        "profile": profile,
        "poc_price": poc_price,
        "daily": daily,
        "stats": stats,
    }
