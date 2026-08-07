"""Portable Straddle Watch analytics — extracted from 3ST ``options/straddle_watch.py``.

Copy this file into other projects. No Kite / FastAPI / 3ST config imports.

Host must supply:
  - CE/PE 1m bars: DataFrame index=timestamp, cols ``close``, ``oi``, ``volume``
  - Chain OI rows: ``[{strike, ce: {oi}, pe: {oi}}, ...]`` for max pain + PCR
  - Optional: current IV + daily IV history for IVR/IVP

IV / BS straddle fair needs separate modules (``implied_volatility``,
``time_to_expiry_years``, ``price_black_scholes``) — not included here.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")


def max_pain_strike(chain_rows: list[dict[str, Any]]) -> float | None:
    """Strike that minimizes total option-writer pain given CE/PE OI."""
    strikes: list[float] = []
    ce_oi: dict[float, float] = {}
    pe_oi: dict[float, float] = {}
    for row in chain_rows:
        try:
            k = float(row["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        strikes.append(k)
        ce = row.get("ce") or {}
        pe = row.get("pe") or {}
        try:
            ce_oi[k] = float(ce.get("oi") or 0)
        except (TypeError, ValueError):
            ce_oi[k] = 0.0
        try:
            pe_oi[k] = float(pe.get("oi") or 0)
        except (TypeError, ValueError):
            pe_oi[k] = 0.0

    if not strikes:
        return None

    strikes = sorted(set(strikes))
    best_k: float | None = None
    best_pain = float("inf")
    for settle in strikes:
        pain = 0.0
        for k in strikes:
            if settle > k:
                pain += (settle - k) * ce_oi.get(k, 0.0)
            if settle < k:
                pain += (k - settle) * pe_oi.get(k, 0.0)
        if pain < best_pain:
            best_pain = pain
            best_k = settle
    return best_k


def chain_pcr(rows: list[dict[str, Any]]) -> float | None:
    """Put OI / Call OI across chain rows."""
    ce_tot = sum(float((r.get("ce") or {}).get("oi") or 0) for r in rows)
    pe_tot = sum(float((r.get("pe") or {}).get("oi") or 0) for r in rows)
    if ce_tot <= 0:
        return None
    return round(pe_tot / ce_tot, 2)


def iv_rank_and_percentile(
    current_iv: float | None,
    history: list[float],
) -> tuple[float | None, float | None]:
    """IV Rank = (cur-min)/(max-min)*100; IV Percentile = % of samples <= current."""
    if current_iv is None or not history:
        return None, None
    vals = [float(v) for v in history if v is not None and float(v) > 0]
    if len(vals) < 5:
        return None, None
    lo = min(vals)
    hi = max(vals)
    cur = float(current_iv)
    if hi > lo:
        ivr = round((cur - lo) / (hi - lo) * 100.0, 2)
    else:
        ivr = 50.0
    below = sum(1 for v in vals if v <= cur)
    ivp = round(below / len(vals) * 100.0, 2)
    return ivr, ivp


def straddle_vwap_series(
    prices: list[float | None],
    volumes: list[float | None],
) -> list[float | None]:
    """Cumulative VWAP of straddle price using combined CE+PE volume."""
    out: list[float | None] = []
    cum_pv = 0.0
    cum_v = 0.0
    for px, vol in zip(prices, volumes):
        if px is None or vol is None or vol <= 0:
            out.append(round(cum_pv / cum_v, 4) if cum_v > 0 else None)
            continue
        cum_pv += float(px) * float(vol)
        cum_v += float(vol)
        out.append(round(cum_pv / cum_v, 4) if cum_v > 0 else None)
    return out


def to_naive_ist_index(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_convert(IST).tz_localize(None)
    return out


def align_leg_frames(ce_df: pd.DataFrame, pe_df: pd.DataFrame) -> pd.DataFrame:
    """Outer-join CE/PE on timestamp; compute straddle close and combined volume."""
    ce = to_naive_ist_index(ce_df)
    pe = to_naive_ist_index(pe_df)
    cols_ce = ce.rename(columns={"close": "call_price", "oi": "call_oi", "volume": "call_vol"})[
        [c for c in ("call_price", "call_oi", "call_vol") if c in ("call_price", "call_oi", "call_vol")]
    ]
    for c, src in (("call_price", "close"), ("call_oi", "oi"), ("call_vol", "volume")):
        if c not in cols_ce.columns and src in ce.columns:
            cols_ce[c] = ce[src]
        if c not in cols_ce.columns:
            cols_ce[c] = pd.NA
    cols_pe = pe.copy()
    for c, src in (("put_price", "close"), ("put_oi", "oi"), ("put_vol", "volume")):
        if src in pe.columns:
            cols_pe[c] = pe[src]
        else:
            cols_pe[c] = pd.NA
    cols_pe = cols_pe[["put_price", "put_oi", "put_vol"]]

    joined = cols_ce[["call_price", "call_oi", "call_vol"]].join(
        cols_pe[["put_price", "put_oi", "put_vol"]],
        how="outer",
    )
    joined = joined.sort_index()
    joined["call_price"] = pd.to_numeric(joined["call_price"], errors="coerce")
    joined["put_price"] = pd.to_numeric(joined["put_price"], errors="coerce")
    joined["call_oi"] = pd.to_numeric(joined["call_oi"], errors="coerce")
    joined["put_oi"] = pd.to_numeric(joined["put_oi"], errors="coerce")
    joined["call_vol"] = pd.to_numeric(joined["call_vol"], errors="coerce").fillna(0.0)
    joined["put_vol"] = pd.to_numeric(joined["put_vol"], errors="coerce").fillna(0.0)
    joined["straddle_price"] = joined["call_price"] + joined["put_price"]
    joined["straddle_vol"] = joined["call_vol"] + joined["put_vol"]
    return joined


def daily_iv_history_for_rank(
    aligned: pd.DataFrame,
    iv_series: list[float | None],
) -> list[float]:
    """Collapse intraday IV to one sample per day (last valid) for IVR/IVP."""
    if aligned.empty or not iv_series:
        return []
    by_day: dict[date, float] = {}
    for ts, iv in zip(aligned.index, iv_series):
        if iv is None:
            continue
        d = ts.date() if hasattr(ts, "date") else date.today()
        by_day[d] = float(iv)
    return list(by_day.values())


def nullable_list(series: pd.Series) -> list[float | None]:
    out: list[float | None] = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
            out.append(None)
        else:
            out.append(round(float(v), 4))
    return out


def atm_strike(spot: float, step: int) -> float:
    return round(spot / step) * step
