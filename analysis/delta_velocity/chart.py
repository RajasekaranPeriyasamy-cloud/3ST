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

# Strikes each side of ATM carried on the premium ladder. Matches the collector's
# STRIKE_WIDTH so the ladder never asks for a strike that was not archived.
LADDER_OFFSETS = 5

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


def _step(underlying: str) -> int:
    return int((INDEX_OPTIONS.get(str(underlying).upper()) or {}).get("strike_step") or 50)


def ladder_series_spec(atm: float, step: int, *, offsets: int = LADDER_OFFSETS) -> list[dict[str, Any]]:
    """The 12 series: ATM on both sides, then OTM calls up and OTM puts down.

    Only the out-of-the-money side of each type is carried. Plotting all 22
    tracked strikes doubles the ink for the ITM legs, whose premium change is
    dominated by intrinsic value and therefore just restates spot.
    """
    spec: list[dict[str, Any]] = [
        {"label": "ATM CE", "strike": atm, "option_type": "CE", "offset": 0},
        {"label": "ATM PE", "strike": atm, "option_type": "PE", "offset": 0},
    ]
    for i in range(1, offsets + 1):
        spec.append({"label": f"OTM CE {i}", "strike": atm + i * step, "option_type": "CE", "offset": i})
        spec.append({"label": f"OTM PE {i}", "strike": atm - i * step, "option_type": "PE", "offset": -i})
    return spec


def session_ladder(
    rows: pd.DataFrame,
    underlying: str,
    *,
    expiry: str | None = None,
    offsets: int = LADDER_OFFSETS,
) -> dict[str, Any]:
    """Premium change since the session's first snapshot, pinned to the open ATM.

    Pinned deliberately. ``collector.tracked_legs`` recomputes ATM every minute,
    so the tracked strike set slides as spot moves — measured 2026-08-10, a
    71-point NIFTY move produced 13 distinct strikes of which only 9 spanned the
    full session. Re-striking the ladder mid-session would make a series jump
    between contracts and show a premium change that nobody traded. Pinning
    instead leaves edge strikes with partial coverage, which is returned as a
    short series rather than dropped, and each series carries its own baseline
    timestamp so a late-arriving strike is not silently rebased to 09:15.
    """
    empty = {"baseline_ts": None, "atm_at_open": None, "step": _step(underlying), "series": []}
    if rows is None or rows.empty:
        return empty

    frame = rows.dropna(subset=["ts"]).sort_values("ts")
    if expiry:
        frame = frame[frame["expiry"].astype(str) == str(expiry)]
    if frame.empty:
        return empty

    first_ts = frame["ts"].iloc[0]
    open_spot = frame[frame["ts"] == first_ts]["spot"].dropna()
    if open_spot.empty:
        return empty

    step = _step(underlying)
    atm = _atm_strike(underlying, float(open_spot.iloc[0]))

    out: list[dict[str, Any]] = []
    for spec in ladder_series_spec(atm, step, offsets=offsets):
        leg = frame[
            (frame["strike"].astype(float) == float(spec["strike"]))
            & (frame["option_type"].astype(str).str.upper() == spec["option_type"])
        ].dropna(subset=["ltp"])
        if leg.empty:
            continue
        baseline = float(leg["ltp"].iloc[0])
        out.append(
            {
                **spec,
                "baseline_ts": leg["ts"].iloc[0].isoformat(),
                "baseline_ltp": round(baseline, 4),
                # Whether this series is rebased to the session open or to a
                # later first appearance. Distinct from coverage: a strike can
                # be present at the open and still leave the tracked window
                # minutes later, giving a short series with a valid baseline.
                "baseline_at_open": bool(leg["ts"].iloc[0] == first_ts),
                "coverage": int(len(leg)),
                "points": [
                    {
                        "clock": ts.tz_convert(store.IST).strftime("%H:%M"),
                        "change": round(float(px) - baseline, 4),
                    }
                    for ts, px in zip(leg["ts"], leg["ltp"], strict=True)
                ],
            }
        )

    return {
        "baseline_ts": first_ts.isoformat(),
        "atm_at_open": atm,
        "step": step,
        "series": out,
    }


def session_context(rows: pd.DataFrame, underlying: str, *, expiry: str | None = None) -> dict[str, Any]:
    """Spot, ATM straddle, PCR and OI over the tracked strikes.

    PCR and the OI totals are computed over the collected window only — ATM+/-5
    on the nearest expiry — not the full chain. That is a materially different
    number from the OI desks and is labelled ``scope`` so it cannot be read as
    a chain-wide PCR.
    """
    empty = {"spot": None, "spot_change": None, "spot_change_pct": None, "atm": None,
             "straddle": None, "straddle_pct": None, "pcr": None, "ce_oi": None,
             "pe_oi": None, "scope": None}
    if rows is None or rows.empty:
        return empty

    frame = rows.dropna(subset=["ts"]).sort_values("ts")
    if expiry:
        frame = frame[frame["expiry"].astype(str) == str(expiry)]
    if frame.empty:
        return empty

    first_ts, last_ts = frame["ts"].iloc[0], frame["ts"].iloc[-1]
    open_spot = frame[frame["ts"] == first_ts]["spot"].dropna()
    last = frame[frame["ts"] == last_ts]
    last_spot = last["spot"].dropna()
    if open_spot.empty or last_spot.empty:
        return empty

    spot0, spot1 = float(open_spot.iloc[0]), float(last_spot.iloc[0])
    atm = _atm_strike(underlying, spot1)

    def _leg_ltp(opt: str) -> float | None:
        hit = last[
            (last["strike"].astype(float) == atm)
            & (last["option_type"].astype(str).str.upper() == opt)
        ]["ltp"].dropna()
        return float(hit.iloc[0]) if not hit.empty else None

    ce, pe = _leg_ltp("CE"), _leg_ltp("PE")
    straddle = round(ce + pe, 2) if ce is not None and pe is not None else None

    oi = last.dropna(subset=["oi"]) if "oi" in last.columns else last.iloc[0:0]
    ce_oi = float(oi[oi["option_type"].astype(str).str.upper() == "CE"]["oi"].sum()) if not oi.empty else None
    pe_oi = float(oi[oi["option_type"].astype(str).str.upper() == "PE"]["oi"].sum()) if not oi.empty else None
    pcr = round(pe_oi / ce_oi, 4) if ce_oi else None

    return {
        "spot": round(spot1, 2),
        "spot_change": round(spot1 - spot0, 2),
        "spot_change_pct": round((spot1 - spot0) / spot0 * 100.0, 3) if spot0 else None,
        "atm": atm,
        "straddle": straddle,
        "straddle_pct": round(straddle / spot1 * 100.0, 3) if straddle and spot1 else None,
        "pcr": pcr,
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "scope": f"ATM+/-{LADDER_OFFSETS} strikes"
        + (f", expiry {expiry}" if expiry else ", all tracked expiries"),
    }


def session_chart(
    underlying: str,
    session_date: date | None = None,
    *,
    expiry: str | None = None,
) -> dict[str, Any]:
    """Per-minute spot and aggregated v_t for one archived session.

    With no ``session_date`` this resolves to the latest archived session, not
    to today. Before the open there is no file for today, and defaulting to it
    returned an empty payload while coverage simultaneously reported a full
    session on disk — the page then showed "161 of 16 minutes collected" beside
    zero contracts.
    """
    u = str(underlying).upper()
    session_date = session_date or store.latest_session(u)
    snapshots = store.load_session(u, session_date) if session_date else []
    empty = {
        "underlying": u,
        "session_date": session_date.isoformat() if session_date else None,
        "atm_strike": None,
        "contracts": 0,
        "minutes": [],
        "thresholds": {},
        "correlation": {"n": 0, "lag_profile": [], "best_lag": None,
                        "contemporaneous": None, "interpretation": "no data"},
        "expiries": [],
        "selected_expiry": expiry,
        "ladder": {"baseline_ts": None, "atm_at_open": None, "step": _step(u), "series": []},
        "context": session_context(None, u),
    }
    if not snapshots:
        return empty

    rows = pd.DataFrame(store.to_rows(snapshots))
    if rows.empty:
        return empty
    rows["ts"] = pd.to_datetime(rows["ts"], format="mixed", utc=True)

    # Every expiry archived this session, before any filter, so the selector can
    # offer them all. Worth isolating: measured NIFTY 2026-08-11, DTE 0 ran 4.9x
    # the DTE 14 p95 and supplied every one of the session's high readings, so a
    # figure pooled across expiries is dominated by whichever is nearest expiry.
    session_expiries = sorted(rows["expiry"].astype(str).unique())
    empty = {**empty, "expiries": session_expiries}

    if expiry:
        rows = rows[rows["expiry"].astype(str) == str(expiry)]
        if rows.empty:
            return {**empty, "session_date": snapshots[0].get("session_date")}

    # Ladder and context are per-expiry: without this the same strike appears
    # once per tracked expiry and the 12 series become 36.
    ladder_expiry = expiry or str(sorted(rows["expiry"].astype(str).unique())[0])
    ladder = session_ladder(rows, u, expiry=ladder_expiry)
    context = session_context(rows, u, expiry=ladder_expiry)

    spot = rows.groupby("ts")["spot"].first().sort_index()
    atm = _atm_strike(u, float(spot.median()))

    velocity, _ = compute_delta_velocity(rows)
    if velocity.empty:
        return {
            **empty,
            "session_date": snapshots[0].get("session_date"),
            "atm_strike": atm,
            "expiries": session_expiries,
            "selected_expiry": expiry,
            "ladder": ladder,
            "context": context,
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
        "expiries": session_expiries,
        "selected_expiry": expiry,
        "ladder": ladder,
        "context": context,
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
