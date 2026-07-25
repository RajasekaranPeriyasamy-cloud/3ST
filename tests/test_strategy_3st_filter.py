"""Tests for ST1 price-zone signals (merged in strategy_3st)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy_3st import compute_indicators, compute_pine_signals, compute_signals


def _sample_ohlc(n: int = 200, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 7000 + np.cumsum(rng.normal(0, 8, n))
    high = close + rng.uniform(5, 20, n)
    low = close - rng.uniform(5, 20, n)
    open_ = close + rng.normal(0, 5, n)
    idx = pd.date_range("2026-07-13 15:30", periods=n, freq="3min")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close}, index=idx)


def test_compute_indicators_price_zone_columns() -> None:
    ind = compute_indicators(_sample_ohlc(), apply_session=False)
    for col in ("all_bull", "all_bear", "bull_filtered", "long_zone_exit", "short_zone_exit", "adx"):
        assert col in ind.columns
    assert "ema" not in ind.columns


def test_short_exit_when_close_above_st1() -> None:
    sig = compute_pine_signals(_sample_ohlc(), apply_session=False)
    exits = sig[sig["short_exit"]]
    for _, row in exits.iterrows():
        assert float(row["close"]) > float(row["st1"])


def test_st1_only_crude_example() -> None:
    sig = compute_signals(_sample_ohlc(), st1_only=True, adx_enabled=False)
    row = sig.iloc[-1]
    assert bool(row["all_bull"]) == bool(row["close"] > row["st1"])
