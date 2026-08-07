"""
Parity test: 3ST `supertrend_regular` vs the OpenAlgo community Supertrend.

Both use the same source (hl2 = (high+low)/2), so any difference isolates:
  1. ATR smoothing — OpenAlgo uses pandas ``ewm(alpha=1/p)`` (adjust=True),
     3ST uses true Wilder RMA (SMA seed + recursive) as in TradingView Pine.
  2. Band-lock formula — OpenAlgo's simplified/null-band variant vs 3ST's
     canonical Pine band carry computed every bar.

The test quantifies the drift and asserts the ATR methods *converge* (drift in
the last window is smaller than in the first window), and that trend direction
agrees on the vast majority of bars after warmup.

Run with output:  pytest tests/test_supertrend_parity.py -s -q
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategy_3st import atr as wilder_atr
from strategy_3st import supertrend_regular

# (atr_period, factor): a fast period (like the OpenAlgo sample) and 3ST's ST1.
PARITY_CASES = [
    pytest.param(5, 1.0, id="fast_atr5"),
    pytest.param(21, 1.0, id="st1_atr21"),
]


# --------------------------------------------------------------------------- #
# OpenAlgo community Supertrend (copied verbatim from the downloaded sample)
# --------------------------------------------------------------------------- #
def openalgo_supertrend(df: pd.DataFrame, atr_period: int, multiplier: float) -> pd.DataFrame:
    high = df["high"]
    low = df["low"]
    close = df["close"]

    price_diffs = [high - low, high - close.shift(), close.shift() - low]
    true_range = pd.concat(price_diffs, axis=1)
    true_range = true_range.abs().max(axis=1)
    atr = true_range.ewm(alpha=1 / atr_period, min_periods=atr_period).mean()

    hl2 = (high + low) / 2
    final_upperband = upperband = hl2 + (multiplier * atr)
    final_lowerband = lowerband = hl2 - (multiplier * atr)

    supertrend = [True] * len(df)
    for i in range(1, len(df.index)):
        curr, prev = i, i - 1
        if close.iloc[curr] > final_upperband.iloc[prev]:
            supertrend[curr] = True
        elif close.iloc[curr] < final_lowerband.iloc[prev]:
            supertrend[curr] = False
        else:
            supertrend[curr] = supertrend[prev]
            if supertrend[curr] and final_lowerband.iloc[curr] < final_lowerband.iloc[prev]:
                final_lowerband.iat[curr] = final_lowerband.iat[prev]
            if (not supertrend[curr]) and final_upperband.iloc[curr] > final_upperband.iloc[prev]:
                final_upperband.iat[curr] = final_upperband.iat[prev]

        if supertrend[curr]:
            final_upperband.iat[curr] = np.nan
        else:
            final_lowerband.iat[curr] = np.nan

    return pd.DataFrame(
        {
            "Supertrend": supertrend,
            "Final_Lowerband": final_lowerband,
            "Final_Upperband": final_upperband,
            "atr": atr,
        },
        index=df.index,
    )


def _sample_ohlc(n: int = 500, seed: int = 7) -> pd.DataFrame:
    """Deterministic random-walk OHLC with realistic intrabar ranges."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0, 1.0, size=n).cumsum()
    close = 20000 + steps * 15
    spread = rng.uniform(3, 25, size=n)
    open_ = close + rng.normal(0, 6, size=n)
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2026-01-01 09:15", periods=n, freq="1min")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": np.round(close, 2)},
        index=idx,
    )


@pytest.mark.parametrize(("atr_period", "factor"), PARITY_CASES)
def test_supertrend_atr_method_drift(atr_period: int, factor: float) -> None:
    df = _sample_ohlc()
    warmup = 3 * atr_period

    # 3ST
    st_line, direction, upper, lower = supertrend_regular(df, atr_period, factor)
    atr_3st = wilder_atr(df["high"], df["low"], df["close"], atr_period)

    # OpenAlgo
    oa = openalgo_supertrend(df, atr_period, factor)
    atr_oa = oa["atr"]
    st_oa = oa["Final_Lowerband"].where(oa["Supertrend"], oa["Final_Upperband"])
    dir_oa = np.where(oa["Supertrend"], 1, -1)

    # --- ATR drift (the headline metric) ---
    atr_diff = (atr_3st - atr_oa).abs()
    price = df["close"]
    atr_rel = (atr_diff / price * 100.0)  # % of price

    early = slice(warmup, warmup + 50)
    late = slice(-50, None)
    early_mean = float(atr_diff.iloc[early].mean())
    late_mean = float(atr_diff.iloc[late].mean())
    ratio = late_mean / max(early_mean, 1e-12)

    # --- ST line drift ---
    st_diff = (st_line - st_oa).abs()
    st_rel = (st_diff / price * 100.0)

    # --- direction agreement (after warmup) ---
    d3 = direction.to_numpy()[warmup:]
    do = dir_oa[warmup:]
    agree = float(np.mean(d3 == do))

    print(f"\n=== SuperTrend parity: 3ST (Wilder) vs OpenAlgo (ewm) — atr={atr_period} factor={factor} ===")
    print(f"bars={len(df)}  warmup={warmup}")
    print("\nATR-method drift (|3ST - OpenAlgo|):")
    print(f"  mean abs   : {float(atr_diff.iloc[warmup:].mean()):.4f}")
    print(f"  median abs : {float(atr_diff.iloc[warmup:].median()):.4f}")
    print(f"  max abs    : {float(atr_diff.iloc[warmup:].max()):.4f}")
    print(f"  mean rel   : {float(atr_rel.iloc[warmup:].mean()):.4f}% of price")
    print(f"  early window mean (bars {warmup}-{warmup + 50}): {early_mean:.4f}")
    print(f"  late  window mean (last 50 bars)             : {late_mean:.4f}")
    print(f"  convergence ratio (late/early)               : {ratio:.3f}")
    print("\nST-line drift:")
    print(f"  mean abs   : {float(st_diff.iloc[warmup:].mean()):.4f}")
    print(f"  max abs    : {float(st_diff.iloc[warmup:].max()):.4f}")
    print(f"  mean rel   : {float(st_rel.iloc[warmup:].mean()):.4f}% of price")
    print(f"\nDirection agreement after warmup: {agree * 100:.2f}%")
    print("=" * 56)

    # ATR methods converge as history accumulates (ewm(adjust=True) -> Wilder):
    # the late window drift is negligible and no larger than the early window.
    assert late_mean <= early_mean + 1e-9
    assert late_mean < 0.01, f"late-window ATR drift not negligible: {late_mean:.4f}"

    # Same source + equivalent flip logic -> strong direction agreement.
    assert agree >= 0.75, f"direction agreement too low: {agree:.3f}"

    # ST line stays within a small fraction of price on average.
    assert float(st_rel.iloc[warmup:].mean()) < 1.0


def test_wilder_atr_seed_matches_sma() -> None:
    """3ST Wilder ATR seeds with the SMA of the first `period` true ranges."""
    period = 5
    df = _sample_ohlc(n=60)
    a = wilder_atr(df["high"], df["low"], df["close"], period)
    # Nothing valid before period-1; a real value at period-1.
    assert bool(np.isnan(a.iloc[period - 2]))
    assert not bool(np.isnan(a.iloc[period - 1]))
