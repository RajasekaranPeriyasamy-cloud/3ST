"""Volume Footprint: Measuring Classical Indicators by Math & Geometry.

A faithful Python port of the Pine v6 indicator by ata_sabanci
(https://www.tradingview.com/script/Tm1cGCPD-Volume-Footprint-Measuring-by-Math-Geometry/),
licensed MPL-2.0 like the original.

Quick start
-----------
    from volume_footprint import BarSeries, Settings, VolumeEngine
    from volume_footprint import apply_engine, compute, format_table

    series = BarSeries.from_ohlcv(rows, mintick=0.05, symbol="NIFTY")
    series = apply_engine(series, VolumeEngine.GEOMETRIC)
    res = compute(series, Settings(window_bars=6, view_ticks=8))
    print(format_table(res))

The module layout follows the original's own sections:

    mathkit    the two normal CDFs the script uses, and why there are two
    bars       Bar / BarSeries, the price lattice
    engines    Geometric / Intrabar / Footprint volume splits
    rows       one bar -> one-tick rows, plus diagonal imbalance
    profile    the truncated-normal mixture, OVL, residual self-check
    metrics    POC, value area, balance tilt
    indicator  window search and the assembled result
    render     terminal table, dashboard, ASCII and matplotlib profile
"""

from __future__ import annotations

from .bars import Bar, BarSeries
from .engines import (
    FootprintRow,
    VolumeEngine,
    apply_engine,
    geometric_share,
    geometric_split,
    intrabar_split,
)
from .indicator import FootprintResult, Settings, WindowColumn, compute
from .mathkit import norm_cdf_fast, norm_cdf_precise, norm_pdf
from .metrics import ValueArea, balance_tilt, point_of_control, value_area
from .profile import ProfComp, ProfileModel, band_mass, build_profile, density, sample_curves
from .render import format_dashboard, format_profile, format_table, plot_profile
from .rows import RowSplit, diagonal_imbalance, footprint_view_rows, gauss_view_rows

__version__ = "1.0.0"

__all__ = [
    "Bar",
    "BarSeries",
    "FootprintRow",
    "FootprintResult",
    "ProfComp",
    "ProfileModel",
    "RowSplit",
    "Settings",
    "ValueArea",
    "VolumeEngine",
    "WindowColumn",
    "apply_engine",
    "balance_tilt",
    "band_mass",
    "build_profile",
    "compute",
    "density",
    "diagonal_imbalance",
    "footprint_view_rows",
    "format_dashboard",
    "format_profile",
    "format_table",
    "gauss_view_rows",
    "geometric_share",
    "geometric_split",
    "intrabar_split",
    "norm_cdf_fast",
    "norm_cdf_precise",
    "norm_pdf",
    "plot_profile",
    "point_of_control",
    "sample_curves",
    "value_area",
]
