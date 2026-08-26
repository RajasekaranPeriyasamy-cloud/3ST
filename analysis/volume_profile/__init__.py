"""Volume Footprint desk — 3ST adapter around the vendored engine.

The maths lives in ``vendor/volume_footprint`` (an MPL-2.0 port of a Pine
indicator, kept verbatim so it stays diffable against upstream). Everything in
this package is the 3ST side: Kite candles in, basis alignment, caching, and a
JSON payload out.

Read :mod:`analysis.volume_profile.service` before trusting a reading — in
particular the two caveats it enforces, that the buy/sell split is a *model*
rather than a measurement, and that a thin session reports ``unmeasured``
instead of a confident shape.
"""

from __future__ import annotations

from analysis.volume_profile.service import (
    MIN_PROFILE_BARS,
    PROFILE_CACHE_TTL_SEC,
    compute_volume_profile,
    gamma_levels,
    get_volume_profile,
    list_contracts,
    peek_volume_profile,
    strike_band_volume,
    strike_oi_ladder,
)

__all__ = [
    "MIN_PROFILE_BARS",
    "PROFILE_CACHE_TTL_SEC",
    "compute_volume_profile",
    "gamma_levels",
    "get_volume_profile",
    "list_contracts",
    "peek_volume_profile",
    "strike_band_volume",
    "strike_oi_ladder",
]
