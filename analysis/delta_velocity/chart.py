"""Chart-ready aggregation for the delta-velocity page.

Two jobs, both deliberately server-side.

Aggregation: a session tracks ~66 contracts per underlying, which is ~25,000
(minute, contract) points. Sending that to a browser to be reduced there wastes
the payload and scatters the statistics across two languages. This collapses it
to one row per minute.

Correlation: the question the engine cannot dodge is whether v_t carries
anything beyond spot velocity. If v_t is near-perfectly correlated with the
absolute one-minute index return at lag 0, the whole pipeline is an expensive
rederivation of "the index moved fast" — computable from index bars alone, with
no options data, no IV solve and no archive. The paper's own SHAP attribution
puts Delta_Velocity at 20.5%, behind Option_Price (40.5%) and Implied_Vol
(39.0%), so this is a live possibility rather than a formality.

Sign convention, stated once because it is easy to invert:

    lag_min = L  compares  v_t[t]  against  |spot return|[t + L]

so **positive L means v_t moved first** (v_t leads), and negative L means the
spot move came first (v_t lags, and is useless as a warning). ``describe_lag``
turns the sign into words so no caller has to remember this.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from analysis.delta_velocity import store
from analysis.delta_velocity.features import compute_delta_velocity
from config import INDEX_OPTIONS

MAX_LAG_MINUTES = 10

# Correlation over a handful of minutes is noise dressed as a finding. A single
# session is 375 minutes; refusing below 60 keeps an early-morning page from
# showing a confident number built on nothing.
MIN_CORRELATION_POINTS = 60


def describe_lag(lag_min: int) -> str:
    if lag_min > 0:
        return f"v_t leads by {lag_min} min"
    if lag_min < 0:
        return f"v_t lags by {abs(lag_min)} min"
    return "coincident"


def lag_profile(
    spot_move: pd.Series,
    velocity: pd.Series,
    *,
    max_lag: int = MAX_LAG_MINUTES,
) -> list[dict[str, Any]]:
    """Pearson correlation of v_t against |spot return| at each lag."""
    out: list[dict[str, Any]] = []
    for lag in range(-max_lag, max_lag + 1):
        shifted = spot_move.shift(-lag)
        paired = pd.concat([velocity, shifted], axis=1).dropna()
        if len(paired) < MIN_CORRELATION_POINTS:
            out.append({"lag_min": lag, "corr": None, "n": int(len(paired))})
            continue
        corr = paired.iloc[:, 0].corr(paired.iloc[:, 1])
        out.append(
            {
                "lag_min": lag,
                "corr": None if pd.isna(corr) else round(float(corr), 4),
                "n": int(len(paired)),
            }
        )
    return out


def _atm_strike(underlying: str, spot: float) -> float:
    step = int((INDEX_OPTIONS.get(str(underlying).upper()) or {}).get("strike_step") or 50)
    return float(round(float(spot) / step) * step)


def session_chart(
    underlying: str,
    session_date: date | None = None,
    *,
    expiry: str | None = None,
) -> dict[str, Any]:
    """Per-minute spot and aggregated v_t for one archived session."""
    u = str(underlying).upper()
    snapshots = store.load_session(u, session_date)
    empty = {
        "underlying": u,
        "session_date": session_date.isoformat() if session_date else None,
        "atm_strike": None,
        "contracts": 0,
        "minutes": [],
        "thresholds": {},
        "correlation": {"n": 0, "lag_profile": [], "best_lag": None,
                        "contemporaneous": None, "interpretation": "no data"},
    }
    if not snapshots:
        return empty

    rows = pd.DataFrame(store.to_rows(snapshots))
    if rows.empty:
        return empty
    rows["ts"] = pd.to_datetime(rows["ts"], format="mixed", utc=True)

    if expiry:
        rows = rows[rows["expiry"].astype(str) == str(expiry)]
        if rows.empty:
            return {**empty, "session_date": snapshots[0].get("session_date")}

    spot = rows.groupby("ts")["spot"].first().sort_index()
    atm = _atm_strike(u, float(spot.median()))

    velocity, _ = compute_delta_velocity(rows)
    if velocity.empty:
        return {
            **empty,
            "session_date": snapshots[0].get("session_date"),
            "atm_strike": atm,
            "contracts": int(rows.groupby(["expiry", "strike", "option_type"]).ngroups),
            "minutes": [
                {"ts": ts.isoformat(), "clock": ts.tz_convert(store.IST).strftime("%H:%M"),
                 "spot": round(float(px), 2), "v_max": None, "v_med": None,
                 "v_atm_ce": None, "v_atm_pe": None}
                for ts, px in spot.items()
            ],
        }

    velocity["ts"] = pd.to_datetime(velocity["ts"], utc=True)
    nearest_expiry = str(sorted(velocity["expiry"].astype(str).unique())[0])
    atm_leg = velocity[
        (velocity["expiry"].astype(str) == nearest_expiry)
        & (velocity["strike"].astype(float) == atm)
    ]

    agg = velocity.groupby("ts")["v_t"].agg(v_max="max", v_med="median")
    atm_ce = atm_leg[atm_leg["option_type"] == "CE"].set_index("ts")["v_t"]
    atm_pe = atm_leg[atm_leg["option_type"] == "PE"].set_index("ts")["v_t"]

    frame = pd.DataFrame({"spot": spot}).join(agg, how="left")
    frame["v_atm_ce"] = atm_ce.reindex(frame.index)
    frame["v_atm_pe"] = atm_pe.reindex(frame.index)

    spot_move = frame["spot"].pct_change().abs()
    profile = lag_profile(spot_move, frame["v_max"])
    scored = [p for p in profile if p["corr"] is not None]
    best = max(scored, key=lambda p: abs(p["corr"])) if scored else None
    contemporaneous = next((p["corr"] for p in profile if p["lag_min"] == 0), None)
    usable = max((p["n"] for p in profile), default=0)

    return {
        "underlying": u,
        "session_date": snapshots[0].get("session_date"),
        "atm_strike": atm,
        "nearest_expiry": nearest_expiry,
        "contracts": int(velocity.groupby(["expiry", "strike", "option_type"]).ngroups),
        "minutes": [
            {
                "ts": ts.isoformat(),
                "clock": ts.tz_convert(store.IST).strftime("%H:%M"),
                "spot": round(float(row.spot), 2),
                "v_max": None if pd.isna(row.v_max) else round(float(row.v_max), 8),
                "v_med": None if pd.isna(row.v_med) else round(float(row.v_med), 8),
                "v_atm_ce": None if pd.isna(row.v_atm_ce) else round(float(row.v_atm_ce), 8),
                "v_atm_pe": None if pd.isna(row.v_atm_pe) else round(float(row.v_atm_pe), 8),
            }
            for ts, row in frame.iterrows()
        ],
        "thresholds": {
            "p95": _q(frame["v_max"], 0.95),
            "p99": _q(frame["v_max"], 0.99),
        },
        "correlation": {
            "n": int(usable),
            "lag_profile": profile,
            "best_lag": best["lag_min"] if best else None,
            "best_corr": best["corr"] if best else None,
            "contemporaneous": contemporaneous,
            "interpretation": describe_lag(best["lag_min"]) if best else
            f"insufficient data ({usable} of {MIN_CORRELATION_POINTS} minutes)",
        },
    }


def _q(series: pd.Series, q: float) -> float | None:
    clean = series.dropna()
    if clean.empty:
        return None
    return round(float(clean.quantile(q)), 8)
