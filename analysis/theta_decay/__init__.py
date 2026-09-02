"""Theta-decay desk — how fast premium bleeds, and how much of it you keep.

Analysis-only, like ``analysis/equity_report/``: never imports ``broker/``,
``execution/`` or ``risk/`` and never places orders.

Deliberately has **no collector, store or runner of its own, and requires no
change to the one it reads.** Everything it needs is already archived minute by
minute by ``analysis.delta_velocity``. A second sampler would double the quote
load and duplicate the per-strike IV solve — the slowest analysis work on the
desk — to obtain data that is already on disk.

Theta is recomputed from the archived full-precision ``iv`` on read, which costs
~0.3s per session vectorised and buys two things: the desk works over sessions
collected before it existed, and the greeks are guaranteed to carry this
module's q=0 convention rather than the collector's q=0.012. Storing them in the
snapshot was considered and rejected for exactly that second reason — see
``features.ensure_greeks``.

Read ``features``' module docstring before trusting ``capture_ratio``: burn rate
is solid, decay capture is a session-scale statistic with real limits.
"""

from __future__ import annotations

from analysis.theta_decay.features import (
    DEFAULT_HORIZON_MIN,
    MIN_PREMIUM,
    SMOOTH_N,
    attribution_by_dte,
    black_scholes_greeks,
    burn_rate,
    capture_quality,
    capture_ratio,
    compute_theta_velocity,
    decay_attribution,
    ensure_greeks,
)

__all__ = [
    "DEFAULT_HORIZON_MIN",
    "MIN_PREMIUM",
    "SMOOTH_N",
    "attribution_by_dte",
    "black_scholes_greeks",
    "burn_rate",
    "capture_quality",
    "capture_ratio",
    "compute_theta_velocity",
    "decay_attribution",
    "ensure_greeks",
]
