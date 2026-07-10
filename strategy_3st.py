"""3ST indicators — Triple SuperTrend + ADX (no EMA filter)."""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

StMethod = Literal["heikin_ashi", "regular", "hybrid"]


def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Manual HA OHLC from regular candles (matches Pine logic)."""
    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    l = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    n = len(df)

    ha_close = (o + h + l + c) / 4.0
    ha_open = np.empty(n, dtype=float)
    ha_open[0] = (o[0] + c[0]) / 2.0
    for i in range(1, n):
        ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

    ha_high = np.maximum(h, np.maximum(ha_open, ha_close))
    ha_low = np.minimum(l, np.minimum(ha_open, ha_close))

    out = df.copy()
    out["ha_open"] = ha_open
    out["ha_high"] = ha_high
    out["ha_low"] = ha_low
    out["ha_close"] = ha_close
    return out


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.roll(close, 1)
    prev_close[0] = close[0]
    return np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))


def atr(series_high: pd.Series, series_low: pd.Series, series_close: pd.Series, period: int) -> pd.Series:
    tr = _true_range(
        series_high.to_numpy(dtype=float),
        series_low.to_numpy(dtype=float),
        series_close.to_numpy(dtype=float),
    )
    atr_vals = np.full(len(tr), np.nan, dtype=float)
    if len(tr) < period:
        return pd.Series(atr_vals, index=series_close.index)
    atr_vals[period - 1] = np.mean(tr[:period])
    alpha = 1.0 / period
    for i in range(period, len(tr)):
        atr_vals[i] = atr_vals[i - 1] * (1 - alpha) + tr[i] * alpha
    return pd.Series(atr_vals, index=series_close.index)


def _supertrend_core(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    src: np.ndarray,
    atr_s: np.ndarray,
    factor: float,
) -> tuple[np.ndarray, np.ndarray]:
    upper_basic = src + factor * atr_s
    lower_basic = src - factor * atr_s
    n = len(close)
    upper = np.full(n, np.nan)
    lower = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)
    st_line = np.full(n, np.nan)

    for i in range(n):
        if np.isnan(atr_s[i]):
            direction[i] = direction[i - 1] if i else 1
            continue

        prev_lower = lower[i - 1] if i and not np.isnan(lower[i - 1]) else lower_basic[i]
        prev_upper = upper[i - 1] if i and not np.isnan(upper[i - 1]) else upper_basic[i]
        prev_close = close[i - 1] if i else close[i]

        if lower_basic[i] > prev_lower or prev_close < prev_lower:
            lower[i] = lower_basic[i]
        else:
            lower[i] = prev_lower

        if upper_basic[i] < prev_upper or prev_close > prev_upper:
            upper[i] = upper_basic[i]
        else:
            upper[i] = prev_upper

        dir_prev = int(direction[i - 1]) if i else 1
        if dir_prev == 1 and close[i] < lower[i]:
            direction[i] = -1
        elif dir_prev == -1 and close[i] > upper[i]:
            direction[i] = 1
        else:
            direction[i] = dir_prev

        st_line[i] = lower[i] if direction[i] == 1 else upper[i]

    return st_line, direction


def supertrend_ha(df: pd.DataFrame, atr_period: int, factor: float) -> tuple[pd.Series, pd.Series]:
    """SuperTrend on HA mid; ATR and direction flips use regular OHLC."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ha_high = df["ha_high"].to_numpy(dtype=float)
    ha_low = df["ha_low"].to_numpy(dtype=float)
    atr_s = atr(df["high"], df["low"], df["close"], atr_period).to_numpy(dtype=float)
    src = (ha_high + ha_low) / 2.0
    st_line, direction = _supertrend_core(high, low, close, src, atr_s, factor)
    return (
        pd.Series(st_line, index=df.index, name="st"),
        pd.Series(direction, index=df.index, name="dir"),
    )


def supertrend_regular(df: pd.DataFrame, atr_period: int, factor: float) -> tuple[pd.Series, pd.Series]:
    """SuperTrend on regular candle mid (high+low)/2."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr_s = atr(df["high"], df["low"], df["close"], atr_period).to_numpy(dtype=float)
    src = (high + low) / 2.0
    st_line, direction = _supertrend_core(high, low, close, src, atr_s, factor)
    return (
        pd.Series(st_line, index=df.index, name="st"),
        pd.Series(direction, index=df.index, name="dir"),
    )


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX on regular OHLC."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    n = len(df)

    up = np.diff(high, prepend=high[0])
    down = -np.diff(low, prepend=low[0])
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = _true_range(high, low, close)

    def wilder_smooth(x: np.ndarray) -> np.ndarray:
        out = np.full(n, np.nan)
        if n < period:
            return out
        out[period - 1] = np.sum(x[:period])
        for i in range(period, n):
            out[i] = out[i - 1] - (out[i - 1] / period) + x[i]
        return out

    atr_s = wilder_smooth(tr)
    plus_s = wilder_smooth(plus_dm)
    minus_s = wilder_smooth(minus_dm)

    plus_di = np.where(atr_s > 0, 100.0 * plus_s / atr_s, np.nan)
    minus_di = np.where(atr_s > 0, 100.0 * minus_s / atr_s, np.nan)
    dx = np.where(
        (plus_di + minus_di) > 0,
        100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di),
        np.nan,
    )

    adx_vals = np.full(n, np.nan)
    start = 2 * period - 1
    if n > start:
        adx_vals[start] = np.nanmean(dx[period - 1 : start + 1])
        for i in range(start + 1, n):
            if np.isnan(dx[i]) or np.isnan(adx_vals[i - 1]):
                adx_vals[i] = adx_vals[i - 1]
            else:
                adx_vals[i] = (adx_vals[i - 1] * (period - 1) + dx[i]) / period

    return pd.Series(adx_vals, index=df.index, name="adx")


def _compute_st(
    df: pd.DataFrame,
    st_method: StMethod,
    atr1: int,
    factor1: float,
    atr2: int,
    factor2: float,
    atr3: int,
    factor3: float,
) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series, pd.Series]:
    """Return working frame + st1..3 and dir1..3."""
    if st_method in ("heikin_ashi", "hybrid"):
        work = heikin_ashi(df)
        st_fn = supertrend_ha
    else:
        work = df.copy()
        st_fn = supertrend_regular

    st1, d1 = st_fn(work, atr1, factor1)
    st2, d2 = st_fn(work, atr2, factor2)
    st3, d3 = st_fn(work, atr3, factor3)
    return work, st1, st2, st3, d1, d2, d3


def _st_entry_mask(
    close: pd.Series,
    st1: pd.Series,
    st2: pd.Series,
    st3: pd.Series,
    *,
    st1_enabled: bool,
    st2_enabled: bool,
    st3_enabled: bool,
    side: Literal["long", "short"],
) -> pd.Series:
    """Price must be above/below every enabled SuperTrend line."""
    masks: list[pd.Series] = []
    if st1_enabled:
        masks.append(close > st1 if side == "long" else close < st1)
    if st2_enabled:
        masks.append(close > st2 if side == "long" else close < st2)
    if st3_enabled:
        masks.append(close > st3 if side == "long" else close < st3)
    if not masks:
        return pd.Series(False, index=close.index)
    result = masks[0]
    for mask in masks[1:]:
        result = result & mask
    return result


def _zone_exit_line(
    st1: pd.Series,
    st2: pd.Series,
    st3: pd.Series,
    *,
    st1_enabled: bool,
    st2_enabled: bool,
    st3_enabled: bool,
) -> tuple[pd.Series, str]:
    """Zone exit uses the slowest enabled ST (ST1, else ST2, else ST3)."""
    if st1_enabled:
        return st1, "ST1"
    if st2_enabled:
        return st2, "ST2"
    if st3_enabled:
        return st3, "ST3"
    return st1, "ST1"


def compute_signals(
    df: pd.DataFrame,
    atr1: int = 21,
    factor1: float = 1.0,
    atr2: int = 14,
    factor2: float = 2.0,
    atr3: int = 7,
    factor3: float = 3.0,
    st1_enabled: bool = True,
    st2_enabled: bool = True,
    st3_enabled: bool = True,
    adx_enabled: bool = True,
    adx_period: int = 14,
    adx_threshold: float = 20.0,
    st_method: StMethod = "heikin_ashi",
) -> pd.DataFrame:
    """
    3ST + ADX signals.

    Entries (bar close fill):
      Long  — first bar where regular close > each enabled ST AND ADX confirms
      Short — first bar where regular close < each enabled ST AND ADX confirms

    Zone exits (bar close):
      Long  — close < slowest enabled ST (ST1 preferred)
      Short — close > slowest enabled ST (ST1 preferred)
    """
    out = df.copy()
    _, st1, st2, st3, d1, d2, d3 = _compute_st(
        out, st_method, atr1, factor1, atr2, factor2, atr3, factor3
    )

    out["st1"] = st1
    out["st2"] = st2
    out["st3"] = st3
    out["dir1"] = d1
    out["dir2"] = d2
    out["dir3"] = d3
    out["atr1"] = atr(out["high"], out["low"], out["close"], atr1)

    close = out["close"]
    above_all = _st_entry_mask(
        close, st1, st2, st3,
        st1_enabled=st1_enabled,
        st2_enabled=st2_enabled,
        st3_enabled=st3_enabled,
        side="long",
    )
    below_all = _st_entry_mask(
        close, st1, st2, st3,
        st1_enabled=st1_enabled,
        st2_enabled=st2_enabled,
        st3_enabled=st3_enabled,
        side="short",
    )
    exit_line, _ = _zone_exit_line(
        st1, st2, st3,
        st1_enabled=st1_enabled,
        st2_enabled=st2_enabled,
        st3_enabled=st3_enabled,
    )

    out["adx"] = adx(out, adx_period)
    adx_ok = (~adx_enabled) | (out["adx"] > adx_threshold)

    out["above_all"] = above_all
    out["below_all"] = below_all
    out["adx_ok"] = adx_ok

    long_ready = above_all & adx_ok
    short_ready = below_all & adx_ok
    out["long_ready"] = long_ready
    out["short_ready"] = short_ready

    prev_long = long_ready.shift(1).fillna(False)
    prev_short = short_ready.shift(1).fillna(False)
    out["long_entry"] = long_ready & ~prev_long
    out["short_entry"] = short_ready & ~prev_short

    out["long_zone_exit"] = close < exit_line
    out["short_zone_exit"] = close > exit_line

    # Legacy columns for compatibility
    out["all_bull"] = above_all
    out["all_bear"] = below_all
    out["bull_filtered"] = long_ready
    out["bear_filtered"] = short_ready

    return out
