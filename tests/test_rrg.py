"""Tests for RRG calculations."""

from __future__ import annotations

import numpy as np
import pandas as pd

from analysis.rrg import (
    _regime_summary,
    _sector_id_from_symbol,
    calculate_rs_momentum,
    calculate_rs_ratio,
    color_for,
    quadrant_for,
    rrg_config,
)
from config import RRG_PRESETS, RRG_SECTOR_INDICES


def _weekly_series(n: int = 120, seed: int = 0) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-07", periods=n, freq="W-SUN")
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.Series(close, index=idx)


def test_quadrant_and_color() -> None:
    assert quadrant_for(101, 101) == "leading"
    assert quadrant_for(101, 99) == "weakening"
    assert quadrant_for(99, 99) == "lagging"
    assert quadrant_for(99, 101) == "improving"
    assert color_for(101, 101) == "#008217"


def test_rs_ratio_momentum_nonempty() -> None:
    stock = _weekly_series(120, seed=1)
    bench = _weekly_series(120, seed=2)
    rsr = calculate_rs_ratio(stock, bench, window=14)
    rsm = calculate_rs_momentum(rsr, window=14, period=52)
    assert len(rsr) > 20
    assert len(rsm) > 10
    assert 80 < float(rsr.iloc[-1]) < 120


def test_sector_id_from_symbol() -> None:
    assert _sector_id_from_symbol("NIFTY_IT") == "NIFTY_IT"
    assert _sector_id_from_symbol("nifty it") == "NIFTY_IT"
    assert _sector_id_from_symbol("RELIANCE") is None


def test_rrg_config_sector_rotation_preset() -> None:
    cfg = rrg_config()
    assert len(cfg["sectors"]) == len(RRG_SECTOR_INDICES)
    preset = next(p for p in cfg["presets"] if p["id"] == "sector_rotation")
    assert preset["benchmark"] == "NIFTY50"
    assert len(preset["symbols"]) == len(RRG_SECTOR_INDICES)
    assert preset["symbols"] == list(RRG_SECTOR_INDICES.keys())


def test_regime_summary() -> None:
    rows = [
        {"quadrant": "leading"},
        {"quadrant": "leading"},
        {"quadrant": "lagging"},
        {"quadrant": "improving"},
    ]
    summary = _regime_summary(rows)
    assert summary["leading"] == 2
    assert summary["lagging"] == 1
    assert summary["improving"] == 1
    assert summary["weakening"] == 0
