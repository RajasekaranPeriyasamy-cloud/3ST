"""Backtest rolling ATM CE/PE entries with independent reentry caps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import pandas as pd

from backtest_engine import (
    BacktestParams,
    _in_window,
    _level,
    _parse_hm,
    force_exit_due,
    run_backtest,
)
from config import INDEX_OPTIONS
from options.chain import atm_strike

TradeMode = Literal["Both", "LongOnly", "ShortOnly"]


@dataclass
class RollingAtmParams(BacktestParams):
    max_reentries_ce: int = 1
    max_reentries_pe: int = 1
    reentry_style: Literal["zone_active", "edge_only"] = "zone_active"
    allow_dual_open: bool = True
    entry_start: str = "09:20"
    underlying: str = "NIFTY"


@dataclass
class RollingAtmTrade:
    leg: str
    side: str
    entry_time: Any
    entry_price: float
    exit_time: Any | None = None
    exit_price: float | None = None
    pnl: float = 0.0
    reason: str = ""
    strike: float = 0.0


@dataclass
class RollingAtmResult:
    trades: list[RollingAtmTrade] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def run_rolling_atm_backtest(df: pd.DataFrame, params: RollingAtmParams) -> RollingAtmResult:
    """
    Simulate index 3ST signals with ATM CE (long) and PE (short) legs.
    Uses index close as proxy for option PnL (simplified — not full option pricing).
    """
    if df is None or df.empty:
        return RollingAtmResult(metrics={"error": "No data"})

    base = run_backtest(df, params)
    sig = base.signals
    if sig is None or sig.empty:
        return RollingAtmResult(metrics={"error": "No signals"})

    step = INDEX_OPTIONS.get(params.underlying, INDEX_OPTIONS["NIFTY"])["strike_step"]
    trades: list[RollingAtmTrade] = []
    ce_open = False
    pe_open = False
    ce_entries = 0
    pe_entries = 0
    ce_entry_px = 0.0
    pe_entry_px = 0.0
    ce_entry_ts = None
    pe_entry_ts = None
    ce_strike = 0.0
    pe_strike = 0.0
    morning_seen = False

    def close_ce(i: int, px: float, reason: str):
        nonlocal ce_open, ce_entry_px, ce_entry_ts, ce_strike
        if not ce_open or ce_entry_ts is None:
            return
        trades.append(
            RollingAtmTrade(
                leg="CE",
                side="long",
                entry_time=ce_entry_ts,
                entry_price=ce_entry_px,
                exit_time=sig.index[i],
                exit_price=px,
                pnl=px - ce_entry_px,
                reason=reason,
                strike=ce_strike,
            )
        )
        ce_open = False

    def close_pe(i: int, px: float, reason: str):
        nonlocal pe_open, pe_entry_px, pe_entry_ts, pe_strike
        if not pe_open or pe_entry_ts is None:
            return
        trades.append(
            RollingAtmTrade(
                leg="PE",
                side="short",
                entry_time=pe_entry_ts,
                entry_price=pe_entry_px,
                exit_time=sig.index[i],
                exit_price=px,
                pnl=pe_entry_px - px,
                reason=reason,
                strike=pe_strike,
            )
        )
        pe_open = False

    max_ce = params.max_reentries_ce + 1
    max_pe = params.max_reentries_pe + 1
    allow_long = params.trade_mode in ("Both", "LongOnly")
    allow_short = params.trade_mode in ("Both", "ShortOnly")

    for i in range(len(sig)):
        row = sig.iloc[i]
        ts = sig.index[i]
        c = float(row["close"])
        in_session = params.system_mode == "Positional" or _in_window(
            ts, params.session_start, params.session_end
        )
        force = params.system_mode == "Intraday" and force_exit_due(
            ts,
            force_exit=params.force_exit,
            session_end=params.session_end,
            system_mode=params.system_mode,
        )
        eh, em = _parse_hm(params.entry_start)
        bar_ok = isinstance(ts, pd.Timestamp) and (ts.hour * 60 + ts.minute) >= eh * 60 + em
        if bar_ok:
            morning_seen = True
        if not morning_seen or not in_session:
            continue

        atm = atm_strike(c, step)

        if ce_open and (force or bool(row.get("long_zone_exit"))):
            close_ce(i, c, "force_exit" if force else "zone_exit")
        if pe_open and (force or bool(row.get("short_zone_exit"))):
            close_pe(i, c, "force_exit" if force else "zone_exit")

        if force:
            continue

        long_entry = bool(row.get("long_entry"))
        short_entry = bool(row.get("short_entry"))
        long_ready = bool(row.get("long_ready"))
        short_ready = bool(row.get("short_ready"))

        if allow_long and not ce_open and ce_entries < max_ce:
            enter = long_entry or (
                params.reentry_style == "zone_active"
                and ce_entries > 0
                and long_ready
                and not long_entry
            )
            if enter:
                if not params.allow_dual_open and pe_open:
                    pass
                else:
                    ce_open = True
                    ce_entries += 1
                    ce_entry_px = c
                    ce_entry_ts = ts
                    ce_strike = atm

        if allow_short and not pe_open and pe_entries < max_pe:
            enter = short_entry or (
                params.reentry_style == "zone_active"
                and pe_entries > 0
                and short_ready
                and not short_entry
            )
            if enter:
                if not params.allow_dual_open and ce_open:
                    pass
                else:
                    pe_open = True
                    pe_entries += 1
                    pe_entry_px = c
                    pe_entry_ts = ts
                    pe_strike = atm

    # TODO: index close proxy is inaccurate for options — replace with actual
    # option pricing or at minimum a delta-adjusted proxy.
    total_pnl = sum(t.pnl for t in trades)
    ce_trades = [t for t in trades if t.leg == "CE"]
    pe_trades = [t for t in trades if t.leg == "PE"]
    return RollingAtmResult(
        trades=trades,
        metrics={
            "total_trades": len(trades),
            "ce_trades": len(ce_trades),
            "pe_trades": len(pe_trades),
            "net_pnl_points": total_pnl,
            "ce_pnl_points": sum(t.pnl for t in ce_trades),
            "pe_pnl_points": sum(t.pnl for t in pe_trades),
            "note": "Index close proxy — not full option premium model",
        },
    )
