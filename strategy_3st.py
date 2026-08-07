"""
3ST strategy — core SuperTrend math + ST1 price zones (close vs ST line).

All engine consumers import from this module only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

StMethod = Literal["heikin_ashi", "regular", "hybrid"]
TradeMode = Literal["Both", "LongOnly", "ShortOnly"]
SystemMode = Literal["Intraday", "Positional"]


@dataclass
class ThreeSTFilterParams:
    """Stock Selection + session fields (no EMA / PRS webhook)."""

    atr1: int = 21
    factor1: float = 1.0
    atr2: int = 14
    factor2: float = 2.0
    atr3: int = 7
    factor3: float = 3.0
    st_method: StMethod = "heikin_ashi"
    st1_enabled: bool = True
    st2_enabled: bool = True
    st3_enabled: bool = True
    adx_enabled: bool = True
    adx_period: int = 14
    adx_threshold: float = 20.0
    system_mode: SystemMode = "Intraday"
    session_start: str = "09:15"
    session_end: str = "15:40"
    force_exit_start: str = "15:20"
    force_exit_end: str = "15:30"
    trade_mode: TradeMode = "Both"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def params_from_selection(sel: dict[str, Any]) -> ThreeSTFilterParams:
    """Build params from ``selection_store`` / ``/selection`` payload."""
    force = str(sel.get("force_exit") or "15:20")
    end = str(sel.get("session_end") or "15:40")
    return ThreeSTFilterParams(
        atr1=int(sel.get("atr1") or 21),
        factor1=float(sel.get("factor1") or 1.0),
        atr2=int(sel.get("atr2") or 14),
        factor2=float(sel.get("factor2") or 2.0),
        atr3=int(sel.get("atr3") or 7),
        factor3=float(sel.get("factor3") or 3.0),
        st_method=sel.get("st_method") or "heikin_ashi",  # type: ignore[arg-type]
        st1_enabled=bool(sel.get("st1_enabled", True)),
        st2_enabled=bool(sel.get("st2_enabled", True)),
        st3_enabled=bool(sel.get("st3_enabled", True)),
        adx_enabled=bool(sel.get("adx_enabled", True)),
        adx_period=int(sel.get("adx_period") or 14),
        adx_threshold=float(sel.get("adx_threshold") or 20.0),
        system_mode=sel.get("system_mode") or "Intraday",  # type: ignore[arg-type]
        session_start=str(sel.get("session_start") or "09:15"),
        session_end=end,
        force_exit_start=force,
        force_exit_end=end,
    )


# --------------------------------------------------------------------------- #
# Core SuperTrend / HA / ADX
# --------------------------------------------------------------------------- #


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
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
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

    return st_line, direction, upper, lower


def supertrend_ha(df: pd.DataFrame, atr_period: int, factor: float) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """SuperTrend on HA mid; ATR and direction flips use regular OHLC."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    ha_high = df["ha_high"].to_numpy(dtype=float)
    ha_low = df["ha_low"].to_numpy(dtype=float)
    atr_s = atr(df["high"], df["low"], df["close"], atr_period).to_numpy(dtype=float)
    src = (ha_high + ha_low) / 2.0
    st_line, direction, upper, lower = _supertrend_core(high, low, close, src, atr_s, factor)
    return (
        pd.Series(st_line, index=df.index, name="st"),
        pd.Series(direction, index=df.index, name="dir"),
        pd.Series(upper, index=df.index, name="upper"),
        pd.Series(lower, index=df.index, name="lower"),
    )


def supertrend_regular(df: pd.DataFrame, atr_period: int, factor: float) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """SuperTrend on regular candle mid (high+low)/2."""
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    atr_s = atr(df["high"], df["low"], df["close"], atr_period).to_numpy(dtype=float)
    src = (high + low) / 2.0
    st_line, direction, upper, lower = _supertrend_core(high, low, close, src, atr_s, factor)
    return (
        pd.Series(st_line, index=df.index, name="st"),
        pd.Series(direction, index=df.index, name="dir"),
        pd.Series(upper, index=df.index, name="upper"),
        pd.Series(lower, index=df.index, name="lower"),
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
) -> tuple[
    pd.DataFrame,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
    pd.Series,
]:
    """Return working frame + st1..3, dir1..3, and upper/lower bands for ST1..3."""
    if st_method in ("heikin_ashi", "hybrid"):
        work = heikin_ashi(df)
        st_fn = supertrend_ha
    else:
        work = df.copy()
        st_fn = supertrend_regular

    st1, d1, u1, l1 = st_fn(work, atr1, factor1)
    st2, d2, u2, l2 = st_fn(work, atr2, factor2)
    st3, d3, u3, l3 = st_fn(work, atr3, factor3)
    return work, st1, st2, st3, d1, d2, d3, u1, l1, u2, l2, u3, l3


# --------------------------------------------------------------------------- #
# Session helpers
# --------------------------------------------------------------------------- #


def _parse_hm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    return int(parts[0]), int(parts[1])


def _clock_minutes(ts: pd.Timestamp) -> int:
    return int(ts.hour) * 60 + int(ts.minute)


def in_session(ts: pd.Timestamp, params: ThreeSTFilterParams) -> bool:
    if params.system_mode == "Positional":
        return True
    start_m = _parse_hm(params.session_start)[0] * 60 + _parse_hm(params.session_start)[1]
    end_m = _parse_hm(params.session_end)[0] * 60 + _parse_hm(params.session_end)[1]
    return start_m <= _clock_minutes(ts) <= end_m


def force_exit_window(ts: pd.Timestamp, params: ThreeSTFilterParams) -> bool:
    if params.system_mode != "Intraday":
        return False
    start_m = _parse_hm(params.force_exit_start)[0] * 60 + _parse_hm(params.force_exit_start)[1]
    end_m = _parse_hm(params.force_exit_end)[0] * 60 + _parse_hm(params.force_exit_end)[1]
    now_m = _clock_minutes(ts)
    if start_m <= end_m:
        return start_m <= now_m <= end_m
    return now_m >= start_m


# --------------------------------------------------------------------------- #
# ST1 price zones — close vs ST line (CE/PE entry & exit)
# --------------------------------------------------------------------------- #


def _signal_st_line(
    st1: pd.Series,
    st2: pd.Series,
    st3: pd.Series,
    *,
    st1_enabled: bool,
    st2_enabled: bool,
    st3_enabled: bool,
) -> pd.Series:
    """Signal line for entry/exit — ST1 when enabled, else slowest enabled ST."""
    if st1_enabled:
        return st1
    if st2_enabled:
        return st2
    if st3_enabled:
        return st3
    return st1


def _direction_zone(
    d1: pd.Series,
    d2: pd.Series,
    d3: pd.Series,
    *,
    st1_enabled: bool,
    st2_enabled: bool,
    st3_enabled: bool,
    side: Literal["bull", "bear"],
) -> pd.Series:
    """True when every enabled ST direction matches ``side``."""
    target = 1 if side == "bull" else -1
    masks: list[pd.Series] = []
    if st1_enabled:
        masks.append(d1 == target)
    if st2_enabled:
        masks.append(d2 == target)
    if st3_enabled:
        masks.append(d3 == target)
    if not masks:
        return pd.Series(False, index=d1.index)
    out = masks[0]
    for mask in masks[1:]:
        out = out & mask
    return out


def compute_indicators(
    df: pd.DataFrame,
    params: ThreeSTFilterParams | None = None,
    *,
    st1_only: bool = False,
    apply_session: bool = False,
) -> pd.DataFrame:
    """
    HA/regular ST×3, ADX, ST1 price zones, optional session flags.

    Entry: close above ST1 (long) / close below ST1 (short) + optional ADX.
    Exit:  opposite — long exits below ST1, short exits above ST1.

    ``st1_only`` forces ST1 as the signal line (Rolling Straddle CE/PE).
    """
    if df is None or df.empty:
        return pd.DataFrame()

    p = params or ThreeSTFilterParams()
    st1_on = bool(p.st1_enabled) or st1_only
    st2_on = bool(p.st2_enabled) and not st1_only
    st3_on = bool(p.st3_enabled) and not st1_only

    out = df.copy()
    _, st1, st2, st3, d1, d2, d3, u1, l1, u2, l2, u3, l3 = _compute_st(
        out,
        p.st_method,
        p.atr1,
        p.factor1,
        p.atr2,
        p.factor2,
        p.atr3,
        p.factor3,
    )

    out["st1"] = st1
    out["st2"] = st2
    out["st3"] = st3
    out["st1_upper"] = u1
    out["st1_lower"] = l1
    out["st2_upper"] = u2
    out["st2_lower"] = l2
    out["st3_upper"] = u3
    out["st3_lower"] = l3
    out["dir1"] = d1
    out["dir2"] = d2
    out["dir3"] = d3
    out["atr1"] = atr(out["high"], out["low"], out["close"], p.atr1)

    signal_st = _signal_st_line(
        st1, st2, st3,
        st1_enabled=st1_on,
        st2_enabled=st2_on,
        st3_enabled=st3_on,
    )
    out["signal_st"] = signal_st

    close = out["close"]
    above_st = close > signal_st
    below_st = close < signal_st

    # Direction alignment kept for dashboard / diagnostics only
    out["dir_all_bull"] = _direction_zone(
        d1, d2, d3,
        st1_enabled=st1_on,
        st2_enabled=st2_on,
        st3_enabled=st3_on,
        side="bull",
    )
    out["dir_all_bear"] = _direction_zone(
        d1, d2, d3,
        st1_enabled=st1_on,
        st2_enabled=st2_on,
        st3_enabled=st3_on,
        side="bear",
    )

    out["adx"] = adx(out, p.adx_period)
    adx_ok = (out["adx"] > p.adx_threshold) if p.adx_enabled else True
    out["adx_ok"] = adx_ok

    out["all_bull"] = above_st
    out["all_bear"] = below_st
    out["bull_filtered"] = above_st & adx_ok
    out["bear_filtered"] = below_st & adx_ok
    out["long_zone_exit"] = below_st
    out["short_zone_exit"] = above_st

    if apply_session:
        out["in_session"] = [in_session(ts, p) for ts in out.index]
        out["force_exit"] = [force_exit_window(ts, p) for ts in out.index]
    else:
        out["in_session"] = True
        out["force_exit"] = False

    out["allow_long"] = p.trade_mode in ("Both", "LongOnly")
    out["allow_short"] = p.trade_mode in ("Both", "ShortOnly")
    return out


def apply_trade_state(ind: pd.DataFrame, params: ThreeSTFilterParams | None = None) -> pd.DataFrame:
    """Entry on ST zone edge; re-entry while flat; exit on opposite ST cross."""
    if ind is None or ind.empty:
        return pd.DataFrame()

    p = params or ThreeSTFilterParams()
    out = ind.copy()
    n = len(out)
    allow_long = p.trade_mode in ("Both", "LongOnly")
    allow_short = p.trade_mode in ("Both", "ShortOnly")

    long_entry = np.zeros(n, dtype=bool)
    long_reentry = np.zeros(n, dtype=bool)
    short_entry = np.zeros(n, dtype=bool)
    short_reentry = np.zeros(n, dtype=bool)
    long_exit = np.zeros(n, dtype=bool)
    short_exit = np.zeros(n, dtype=bool)
    force_exit_signal = np.zeros(n, dtype=bool)
    trade_state = np.zeros(n, dtype=int)

    state = 0
    prev_long_ready = False
    prev_short_ready = False

    for i in range(n):
        row = out.iloc[i]
        long_ready = bool(row["bull_filtered"])
        short_ready = bool(row["bear_filtered"])
        long_zone_exit = bool(row["long_zone_exit"])
        short_zone_exit = bool(row["short_zone_exit"])
        sess = bool(row["in_session"])
        force = bool(row["force_exit"])

        le = allow_long and sess and long_ready and not prev_long_ready
        lre = allow_long and sess and long_ready and state == 0 and not le
        se = allow_short and sess and short_ready and not prev_short_ready
        sre = allow_short and sess and short_ready and state == 0 and not se
        lx = state == 1 and long_zone_exit
        sx = state == -1 and short_zone_exit

        if le or lre:
            state = 1
        elif se or sre:
            state = -1
        elif lx or sx:
            state = 0

        long_entry[i] = le
        long_reentry[i] = lre
        short_entry[i] = se
        short_reentry[i] = sre
        long_exit[i] = lx
        short_exit[i] = sx
        force_exit_signal[i] = force and state != 0
        trade_state[i] = state
        prev_long_ready = long_ready
        prev_short_ready = short_ready

    out["long_entry"] = long_entry
    out["long_reentry"] = long_reentry
    out["short_entry"] = short_entry
    out["short_reentry"] = short_reentry
    out["long_exit"] = long_exit
    out["short_exit"] = short_exit
    out["force_exit_signal"] = force_exit_signal
    out["trade_state"] = trade_state
    out["go_long"] = out["long_entry"] | out["long_reentry"]
    out["go_short"] = out["short_entry"] | out["short_reentry"]
    out["go_exit"] = out["long_exit"] | out["short_exit"] | out["force_exit_signal"]
    return out


def _attach_legacy_aliases(out: pd.DataFrame) -> pd.DataFrame:
    """Keep API/UI column names stable during migration."""
    out["above_all"] = out["all_bull"]
    out["below_all"] = out["all_bear"]
    out["long_ready"] = out["bull_filtered"]
    out["short_ready"] = out["bear_filtered"]
    return out


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
    *,
    params: ThreeSTFilterParams | None = None,
    st1_only: bool = False,
    apply_session: bool = False,
) -> pd.DataFrame:
    """
    Full 3ST signal pipeline.

    Long  entry — close crosses above ST1 (+ ADX when enabled).
    Short entry — close crosses below ST1 (+ ADX when enabled).
    Long  exit  — close below ST1.
    Short exit  — close above ST1.

    Set ``st1_only=True`` for Rolling Straddle CE/PE legs.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    p = params or ThreeSTFilterParams(
        atr1=atr1,
        factor1=factor1,
        atr2=atr2,
        factor2=factor2,
        atr3=atr3,
        factor3=factor3,
        st_method=st_method,
        st1_enabled=st1_enabled,
        st2_enabled=st2_enabled,
        st3_enabled=st3_enabled,
        adx_enabled=adx_enabled,
        adx_period=adx_period,
        adx_threshold=adx_threshold,
    )

    ind = compute_indicators(df, p, st1_only=st1_only, apply_session=apply_session)
    if ind.empty:
        return ind
    out = apply_trade_state(ind, p)
    return _attach_legacy_aliases(out)


def compute_pine_signals(
    df: pd.DataFrame,
    params: ThreeSTFilterParams | None = None,
    *,
    st1_only: bool = False,
    apply_session: bool = True,
) -> pd.DataFrame:
    """Alias for selection-driven pipelines with session filtering enabled."""
    p = params or ThreeSTFilterParams()
    return compute_signals(
        df,
        params=p,
        st1_only=st1_only,
        apply_session=apply_session,
    )


def dashboard_snapshot(row: pd.Series, params: ThreeSTFilterParams | None = None) -> dict[str, Any]:
    """PRS-style dashboard row for the latest bar."""
    zone = (
        "LONG ZONE"
        if bool(row.get("bull_filtered"))
        else "SHORT ZONE"
        if bool(row.get("bear_filtered"))
        else "ABOVE ST1"
        if bool(row.get("all_bull"))
        else "BELOW ST1"
        if bool(row.get("all_bear"))
        else "NO TRADE"
    )
    adx_val = row.get("adx")
    adx_status = (
        "OFF"
        if params and not params.adx_enabled
        else "TREND"
        if pd.notna(adx_val) and float(adx_val) > (params.adx_threshold if params else 20.0)
        else "WEAK"
    )
    return {
        "st1": {"dir": "BULL" if int(row["dir1"]) == 1 else "BEAR", "value": float(row["st1"])},
        "st2": {"dir": "BULL" if int(row["dir2"]) == 1 else "BEAR", "value": float(row["st2"])},
        "st3": {"dir": "BULL" if int(row["dir3"]) == 1 else "BEAR", "value": float(row["st3"])},
        "adx": {
            "status": adx_status,
            "value": float(adx_val) if pd.notna(adx_val) else None,
        },
        "zone": zone,
        "session": "ACTIVE" if bool(row.get("in_session")) else "CLOSED",
        "trade_state": int(row.get("trade_state", 0)),
        "alert": (
            "LONG READY"
            if bool(row.get("bull_filtered"))
            else "SHORT READY"
            if bool(row.get("bear_filtered"))
            else "WAITING"
        ),
    }
