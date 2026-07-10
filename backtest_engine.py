"""Event-driven backtest engine for 3ST · ADX."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from strategy_3st import StMethod, compute_signals

TradeMode = Literal["Both", "LongOnly", "ShortOnly"]
RiskMode = Literal["Off", "%", "Pts", "ATR"]


@dataclass
class BacktestParams:
    atr1: int = 21
    factor1: float = 1.0
    atr2: int = 14
    factor2: float = 2.0
    atr3: int = 7
    factor3: float = 3.0
    st1_enabled: bool = True
    st2_enabled: bool = True
    st3_enabled: bool = True
    adx_enabled: bool = True
    adx_period: int = 14
    adx_threshold: float = 20.0
    st_method: StMethod = "heikin_ashi"
    trade_mode: TradeMode = "Both"
    system_mode: Literal["Intraday", "Positional"] = "Intraday"
    session_start: str = "09:15"
    session_end: str = "15:30"
    force_exit: str = "15:20"
    tgt_mode: RiskMode = "Off"
    tgt_value: float = 1.0
    sl_mode: RiskMode = "Off"
    sl_value: float = 1.0
    tsl_mode: RiskMode = "Off"
    tsl_value: float = 1.5
    lot_size: float = 1.0
    use_lot_multiplier: bool = False


@dataclass
class Trade:
    side: str
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    pnl_points: float = 0.0
    pnl: float = 0.0
    reason: str = ""
    bars_held: int = 0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity: pd.Series | None = None
    signals: pd.DataFrame | None = None
    metrics: dict = field(default_factory=dict)


def _hm(ts: pd.Timestamp) -> tuple[int, int]:
    return int(ts.hour), int(ts.minute)


def _parse_hm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    return int(parts[0]), int(parts[1])


def _in_window(ts: pd.Timestamp, start: str, end: str) -> bool:
    h, m = _hm(ts)
    sh, sm = _parse_hm(start)
    eh, em = _parse_hm(end)
    t = h * 60 + m
    return (sh * 60 + sm) <= t <= (eh * 60 + em)


def _level(price: float, mode: RiskMode, value: float, side: str, kind: str) -> float | None:
    if mode == "Off" or mode == "ATR":
        return None
    if kind == "tgt":
        if mode == "%":
            return price * (1 + value / 100) if side == "long" else price * (1 - value / 100)
        return price + value if side == "long" else price - value
    if mode == "%":
        return price * (1 - value / 100) if side == "long" else price * (1 + value / 100)
    return price - value if side == "long" else price + value


def _pnl_multiplier(params: BacktestParams) -> float:
    if params.use_lot_multiplier and params.lot_size > 0:
        return float(params.lot_size)
    return 1.0


def run_backtest(df: pd.DataFrame, params: BacktestParams) -> BacktestResult:
    if df is None or df.empty:
        return BacktestResult(metrics={"error": "No candle data"})

    sig = compute_signals(
        df,
        atr1=params.atr1,
        factor1=params.factor1,
        atr2=params.atr2,
        factor2=params.factor2,
        atr3=params.atr3,
        factor3=params.factor3,
        st1_enabled=params.st1_enabled,
        st2_enabled=params.st2_enabled,
        st3_enabled=params.st3_enabled,
        adx_enabled=params.adx_enabled,
        adx_period=params.adx_period,
        adx_threshold=params.adx_threshold,
        st_method=params.st_method,
    )

    allow_long = params.trade_mode in ("Both", "LongOnly")
    allow_short = params.trade_mode in ("Both", "ShortOnly")
    mult = _pnl_multiplier(params)

    trades: list[Trade] = []
    equity = np.zeros(len(sig), dtype=float)
    cum_pnl = 0.0

    pos = 0
    entry_price = 0.0
    entry_time = None
    sl = None
    tgt = None
    bars_in_trade = 0

    def close_trade(i: int, price: float, reason: str):
        nonlocal pos, cum_pnl, entry_price, entry_time, sl, tgt, bars_in_trade
        if pos == 0 or entry_time is None:
            return
        side = "long" if pos == 1 else "short"
        raw_pts = (price - entry_price) if pos == 1 else (entry_price - price)
        pnl = raw_pts * mult
        cum_pnl += pnl
        trades.append(
            Trade(
                side=side,
                entry_time=entry_time,
                entry_price=entry_price,
                exit_time=sig.index[i],
                exit_price=price,
                pnl_points=raw_pts,
                pnl=pnl,
                reason=reason,
                bars_held=bars_in_trade,
            )
        )
        pos = 0
        sl = None
        tgt = None
        bars_in_trade = 0

    for i in range(len(sig)):
        row = sig.iloc[i]
        ts = sig.index[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])

        in_session = params.system_mode == "Positional" or _in_window(
            ts, params.session_start, params.session_end
        )
        force = params.system_mode == "Intraday" and _in_window(ts, params.force_exit, params.session_end)

        if pos != 0:
            bars_in_trade += 1

            if params.tsl_mode == "ATR" and not np.isnan(row.get("atr1", np.nan)):
                atr_v = float(row["atr1"]) * params.tsl_value
                if pos == 1:
                    trail = c - atr_v
                    if sl is None or trail > sl:
                        sl = trail
                else:
                    trail = c + atr_v
                    if sl is None or trail < sl:
                        sl = trail
            elif params.tsl_mode != "Off" and i > 0:
                prev = sig.iloc[i - 1]
                if pos == 1:
                    trail = (
                        float(prev["high"]) * (1 - params.tsl_value / 100)
                        if params.tsl_mode == "%"
                        else float(prev["high"]) - params.tsl_value
                    )
                    if sl is None or trail > sl:
                        sl = trail
                else:
                    trail = (
                        float(prev["low"]) * (1 + params.tsl_value / 100)
                        if params.tsl_mode == "%"
                        else float(prev["low"]) + params.tsl_value
                    )
                    if sl is None or trail < sl:
                        sl = trail

            hit_sl = sl is not None and ((pos == 1 and l <= sl) or (pos == -1 and h >= sl))
            hit_tgt = tgt is not None and ((pos == 1 and h >= tgt) or (pos == -1 and l <= tgt))

            if hit_sl and hit_tgt:
                close_trade(i, float(sl), "SL (same bar as TGT)")
            elif hit_sl:
                close_trade(i, float(sl), "SL/TSL")
            elif hit_tgt:
                close_trade(i, float(tgt), "Target")

            if pos == 1 and bool(row["long_zone_exit"]):
                close_trade(i, c, "Zone Exit (ST1)")
            elif pos == -1 and bool(row["short_zone_exit"]):
                close_trade(i, c, "Zone Exit (ST1)")

            if force and pos != 0:
                close_trade(i, c, "Force Exit")

        if pos == 0 and in_session and not force:
            if allow_long and bool(row["long_entry"]):
                pos = 1
                entry_price = c
                entry_time = ts
                bars_in_trade = 0
                sl = _level(entry_price, params.sl_mode, params.sl_value, "long", "sl")
                tgt = _level(entry_price, params.tgt_mode, params.tgt_value, "long", "tgt")
            elif allow_short and bool(row["short_entry"]):
                pos = -1
                entry_price = c
                entry_time = ts
                bars_in_trade = 0
                sl = _level(entry_price, params.sl_mode, params.sl_value, "short", "sl")
                tgt = _level(entry_price, params.tgt_mode, params.tgt_value, "short", "tgt")

        mtm = cum_pnl
        if pos == 1:
            mtm += (c - entry_price) * mult
        elif pos == -1:
            mtm += (entry_price - c) * mult
        equity[i] = mtm

    if pos != 0:
        close_trade(len(sig) - 1, float(sig.iloc[-1]["close"]), "EOD Flatten")

    eq = pd.Series(equity, index=sig.index, name="equity")
    metrics = summarize(trades, eq)
    if len(df):
        metrics["start_open"] = float(df.iloc[0]["open"])
        metrics["end_close"] = float(df.iloc[-1]["close"])
    return BacktestResult(trades=trades, equity=eq, signals=sig, metrics=metrics)


def summarize(trades: list[Trade], equity: pd.Series) -> dict:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "net_points": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": 0.0,
            "avg_pnl": 0.0,
            "avg_points": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "return_pct": 0.0,
            "long_trades": 0,
            "short_trades": 0,
            "long_points": 0.0,
            "short_points": 0.0,
        }

    pnls = np.array([t.pnl for t in trades], dtype=float)
    pts = np.array([t.pnl_points for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0
    net = float(pnls.sum())
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    peak = equity.cummax()
    dd = equity - peak
    max_dd = float(dd.min()) if len(dd) else 0.0
    max_dd_pct = 0.0
    if len(dd) and peak.max() != 0:
        max_dd_pct = float((dd / peak.replace(0, np.nan)).min() * 100)

    return {
        "trades": int(len(trades)),
        "wins": int(len(wins)),
        "losses": int(len(losses)),
        "win_rate": float(len(wins) / len(trades) * 100),
        "net_pnl": net,
        "net_points": float(pts.sum()),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": pf if pf != float("inf") else 999.0,
        "avg_pnl": float(pnls.mean()),
        "avg_points": float(pts.mean()),
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "return_pct": 0.0,
        "long_trades": sum(1 for t in trades if t.side == "long"),
        "short_trades": sum(1 for t in trades if t.side == "short"),
        "long_points": float(sum(t.pnl_points for t in trades if t.side == "long")),
        "short_points": float(sum(t.pnl_points for t in trades if t.side == "short")),
    }


def trades_to_df(trades: list[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(
            columns=[
                "side",
                "entry_time",
                "entry_price",
                "exit_time",
                "exit_price",
                "pnl_points",
                "pnl",
                "reason",
                "bars_held",
            ]
        )
    return pd.DataFrame([t.__dict__ for t in trades])
