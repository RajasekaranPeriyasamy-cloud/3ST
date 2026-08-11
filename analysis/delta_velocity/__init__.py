"""Delta-velocity signal engine — minute-level option-state monitoring.

Implements the feature pipeline from Chen & Hybinette, "Velocity- and
Regime-Aware Detection of Intraday Options Market Manipulation"
(arXiv 2608.05373), adapted to what a live Kite session can actually supply.

Analysis-only, like ``analysis/equity_report/``: never imports ``broker/``,
``execution/`` or ``risk/``, never places orders, and runs on its own daemon
thread so a slow computation cannot delay an order-placing tick.

Scope note — the paper trains on three years of archived minute-level option
data. Kite cannot supply that: expired contracts are not resolvable from the
instrument dump, and live strikes only return history back to their listing
date. So this engine builds its own archive forward from the day it is enabled.
Phase 1 collects; it does not detect.
"""

from __future__ import annotations

from analysis.delta_velocity.features import (
    GAP_MAX_MINUTES,
    GAP_MIN_MINUTES,
    SMOOTH_N,
    compute_delta_velocity,
    delta_velocity_series,
)

__all__ = [
    "GAP_MAX_MINUTES",
    "GAP_MIN_MINUTES",
    "SMOOTH_N",
    "compute_delta_velocity",
    "delta_velocity_series",
]
