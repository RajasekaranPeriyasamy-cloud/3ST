"""Delta-velocity feature — equation 1 of arXiv 2608.05373.

Pure computation: no I/O, no Kite, no clock. Everything here is unit-testable
and deterministic, which matters because every downstream number depends on it.

For each ``(session_date, underlying, expiry, strike, option_type)`` group, let
``dbar15(t)`` be the trailing mean of 15 *available* Delta observations and
``tau_t`` the observation timestamp:

    v_t = | dbar15(t) - dbar15(t-1) | / ((tau_t - tau_t-1) / 1 minute)

Values are blanked when adjacent observations are less than 0.5 or more than
2.0 minutes apart, and grouping by session date prevents an overnight gap from
producing a spurious velocity.

Two implementation points that are easy to get wrong:

* Delta must be computed against **that minute's spot**. Holding spot constant
  across a session removes the spot term entirely and the feature then measures
  option-price drift instead. Measured 2026-08-10 on live data, a frozen-spot
  series correlates only 0.28-0.40 with the correct one and understates median
  velocity by 3x (NIFTY) to 11x (BANKNIFTY).
* Use unrounded Delta. ``options.greeks.option_greeks`` rounds to 4dp, which at
  typical v_t magnitudes (~3e-4) is several percent of the quantity measured.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

SMOOTH_N = 15
GAP_MIN_MINUTES = 0.5
GAP_MAX_MINUTES = 2.0

GROUP_KEYS = ("session_date", "underlying", "expiry", "strike", "option_type")

BLANK_REASONS = (
    "no_delta",
    "insufficient_window",
    "short_gap",
    "long_gap",
)


def delta_velocity_series(
    timestamps: pd.Series,
    delta: pd.Series,
    *,
    smooth_n: int = SMOOTH_N,
) -> tuple[pd.Series, dict[str, int]]:
    """v_t for one group, plus a count of why observations were dropped.

    ``timestamps`` and ``delta`` must be aligned and already sorted ascending.
    Returns a Series indexed like the input (blanked entries absent) and a
    per-reason blank tally, so a thin-data artefact is distinguishable from a
    genuine property of the feature.
    """
    counts = dict.fromkeys(BLANK_REASONS, 0)

    frame = pd.DataFrame({"ts": pd.to_datetime(timestamps), "delta": delta}).sort_values("ts")
    available = frame.dropna(subset=["delta"])
    counts["no_delta"] = int(len(frame) - len(available))

    if len(available) < smooth_n + 1:
        counts["insufficient_window"] = int(len(available))
        return pd.Series(dtype=float), counts

    dbar = available["delta"].rolling(smooth_n).mean()
    counts["insufficient_window"] = int(dbar.isna().sum())

    gap = available["ts"].diff().dt.total_seconds() / 60.0
    velocity = dbar.diff().abs() / gap

    too_short = gap < GAP_MIN_MINUTES
    too_long = gap > GAP_MAX_MINUTES
    counts["short_gap"] = int(too_short.sum())
    counts["long_gap"] = int(too_long.sum())
    velocity = velocity.mask(too_short | too_long)

    out = velocity.dropna()
    out.index = available["ts"].loc[out.index]
    out.name = "v_t"
    return out, counts


def compute_delta_velocity(
    rows: pd.DataFrame,
    *,
    smooth_n: int = SMOOTH_N,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply :func:`delta_velocity_series` across every contract group.

    ``rows`` needs columns ``ts``, ``delta`` and the GROUP_KEYS. Grouping by
    ``session_date`` is what stops an overnight gap becoming a velocity spike,
    so it is required rather than inferred.
    """
    if rows is None or rows.empty:
        return pd.DataFrame(columns=[*GROUP_KEYS, "ts", "v_t"]), dict.fromkeys(BLANK_REASONS, 0)

    missing = [k for k in (*GROUP_KEYS, "ts", "delta") if k not in rows.columns]
    if missing:
        raise ValueError(f"compute_delta_velocity missing columns: {missing}")

    totals = dict.fromkeys(BLANK_REASONS, 0)
    parts: list[pd.DataFrame] = []

    for keys, grp in rows.groupby(list(GROUP_KEYS), dropna=False):
        series, counts = delta_velocity_series(grp["ts"], grp["delta"], smooth_n=smooth_n)
        for reason, n in counts.items():
            totals[reason] += n
        if series.empty:
            continue
        part = pd.DataFrame({"ts": series.index, "v_t": series.to_numpy()})
        for name, value in zip(GROUP_KEYS, keys, strict=False):
            part[name] = value
        parts.append(part)

    if not parts:
        return pd.DataFrame(columns=[*GROUP_KEYS, "ts", "v_t"]), totals

    out = pd.concat(parts, ignore_index=True)
    return out[[*GROUP_KEYS, "ts", "v_t"]].sort_values(["ts", *GROUP_KEYS]).reset_index(drop=True), totals


def summarize_by_dte(velocity: pd.DataFrame, dte: pd.Series | None = None) -> pd.DataFrame:
    """Percentiles of v_t per days-to-expiry bucket.

    Phase 0 caveat, kept here so it travels with the function: with only one or
    two expiries per underlying, DTE and calendar date are confounded — "DTE 7"
    on a single contract *is* one specific session. Reading a DTE effect off
    this table requires several expiries covering the same calendar days.
    """
    if velocity is None or velocity.empty:
        return pd.DataFrame(columns=["dte", "n", "p50", "p95", "p99"])

    frame = velocity.copy()
    if dte is not None:
        frame["dte"] = dte.to_numpy()
    if "dte" not in frame.columns:
        expiry = pd.to_datetime(frame["expiry"])
        session = pd.to_datetime(frame["session_date"])
        frame["dte"] = (expiry - session).dt.days

    grouped = frame.groupby("dte")["v_t"]
    return pd.DataFrame(
        {
            "dte": sorted(frame["dte"].unique()),
            "n": grouped.size().to_numpy(),
            "p50": grouped.quantile(0.50).to_numpy(),
            "p95": grouped.quantile(0.95).to_numpy(),
            "p99": grouped.quantile(0.99).to_numpy(),
        }
    ).reset_index(drop=True)


def normalized_velocity(velocity: pd.DataFrame, *, by: str = "session_date") -> pd.DataFrame:
    """Rank v_t within its own session (or other key), in [0, 1].

    A global threshold on raw v_t compares a quiet Tuesday against an event day.
    Phase 0 measured the raw spread across DTE buckets at 3.0-4.1x, which is
    small enough that a global cut is defensible — but ranking within the day
    costs nothing and removes the question.
    """
    if velocity is None or velocity.empty:
        return velocity
    out = velocity.copy()
    out["v_pct"] = out.groupby(by)["v_t"].rank(pct=True)
    return out


def blank_summary(counts: dict[str, Any]) -> str:
    """One-line audit string, ordered worst-first."""
    if not counts:
        return "no blanks"
    ordered = sorted(counts.items(), key=lambda kv: -int(kv[1]))
    return ", ".join(f"{reason}={n}" for reason, n in ordered if n)
