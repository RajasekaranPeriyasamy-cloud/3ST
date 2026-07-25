"""Tests for 3ST Supertrend signal computation."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_3st import (
    ThreeSTFilterParams,
    compute_pine_signals,
    compute_signals,
    dashboard_snapshot,
    force_exit_window,
    heikin_ashi,
    in_session,
    params_from_selection,
)


def _sample_ohlc(n: int = 120, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    high = close + rng.uniform(0.2, 1.0, n)
    low = close - rng.uniform(0.2, 1.0, n)
    open_ = close + rng.normal(0, 0.2, n)
    idx = pd.date_range("2026-07-01 09:15", periods=n, freq="3min")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close},
        index=idx,
    )


def test_compute_signals_columns() -> None:
    df = _sample_ohlc()
    sig = compute_signals(df, st_method="regular", adx_enabled=False)
    for col in (
        "st1",
        "st1_upper",
        "st1_lower",
        "dir1",
        "atr1",
        "close",
        "all_bull",
        "bull_filtered",
        "long_entry",
        "long_zone_exit",
        "short_zone_exit",
        "go_long",
        "trade_state",
    ):
        assert col in sig.columns


def test_heikin_ashi_smooths_body() -> None:
    df = _sample_ohlc(30)
    ha = heikin_ashi(df)
    assert "ha_close" in ha.columns
    assert len(ha) == len(df)


def test_supertrend_direction_values() -> None:
    df = _sample_ohlc()
    sig = compute_signals(df, st_method="heikin_ashi")
    dirs = sig["dir1"].dropna().unique()
    assert set(dirs).issubset({-1, 1})


def test_st1_price_zones() -> None:
    df = _sample_ohlc()
    sig = compute_signals(df, adx_enabled=False)
    for _, row in sig.iterrows():
        st1 = row["st1"]
        close = row["close"]
        if pd.isna(st1):
            continue
        assert bool(row["all_bull"]) == bool(close > st1)
        assert bool(row["all_bear"]) == bool(close < st1)
        assert bool(row["long_zone_exit"]) == bool(close < st1)
        assert bool(row["short_zone_exit"]) == bool(close > st1)


def test_st1_only_uses_st1_line() -> None:
    df = _sample_ohlc(80)
    sig = compute_signals(df, st1_only=True, adx_enabled=False)
    for _, row in sig.iterrows():
        assert bool(row["all_bull"]) == bool(row["close"] > row["st1"])


def test_trade_state_and_reentry_columns() -> None:
    df = _sample_ohlc(200, seed=11)
    sig = compute_pine_signals(df, apply_session=False)
    assert "long_reentry" in sig.columns
    assert set(sig["trade_state"].unique()).issubset({-1, 0, 1})


def test_params_from_selection() -> None:
    p = params_from_selection({"atr1": 10, "session_start": "09:20", "force_exit": "15:25"})
    assert p.atr1 == 10
    assert p.session_start == "09:20"
    assert p.force_exit_start == "15:25"


def test_session_helpers() -> None:
    params = ThreeSTFilterParams(session_start="09:20", session_end="23:15")
    assert in_session(pd.Timestamp("2026-07-13 17:00"), params)
    assert not in_session(pd.Timestamp("2026-07-13 08:00"), params)
    params2 = ThreeSTFilterParams(force_exit_start="23:25", force_exit_end="23:30")
    assert force_exit_window(pd.Timestamp("2026-07-13 23:27"), params2)


def test_dashboard_snapshot() -> None:
    sig = compute_signals(_sample_ohlc(120))
    snap = dashboard_snapshot(sig.iloc[-1])
    assert "zone" in snap and "st1" in snap
