"""Chart-ready aggregation for the theta-decay page.

Server-side for the same reason the delta-velocity chart is: a session is ~25,000
(minute, contract) points, and keeping the statistics in one place means they are
testable instead of reimplemented in TypeScript.

Reuses ``analysis.delta_velocity.chart`` for the lag profile and the spot /
straddle / PCR context rather than growing a second copy of either.

Caching: computing all three metrics for one session costs ~5s, which is too
slow to sit under a page load unconditionally. Archived sessions are immutable
once the day is over, and today's file only ever grows, so a cache keyed on the
session file's ``(size, mtime)`` is both correct and self-invalidating.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

from analysis.delta_velocity import store
from analysis.delta_velocity.chart import (
    MIN_CORRELATION_POINTS,
    atm_strike,
    describe_lag,
    lag_profile,
    session_context,
    strike_step,
)
from analysis.theta_decay import features as F

_CACHE: dict[tuple, dict[str, Any]] = {}
_CACHE_MAX = 12


def _file_stamp(underlying: str, session_date: date | None) -> tuple | None:
    """``(size, mtime)`` of the session file — what makes the cache self-invalidating.

    A finished session's file never changes again; today's only grows. Either
    way a changed stamp means the payload must be rebuilt.
    """
    if not session_date:
        return None
    path = store.session_file(underlying, session_date)
    if not path.exists():
        return None
    stat = path.stat()
    return (stat.st_size, int(stat.st_mtime))


def _empty(underlying: str, session_date: date | None, expiry: str | None) -> dict[str, Any]:
    u = str(underlying).upper()
    return {
        "underlying": u,
        "session_date": session_date.isoformat() if session_date else None,
        "atm_strike": None,
        "contracts": 0,
        "minutes": [],
        "burn_by_dte": [],
        "burn_by_strike": [],
        "capture": {
            "horizon_min": F.DEFAULT_HORIZON_MIN,
            "by_dte": [],
            "note": "no data",
        },
        "expiries": [],
        "selected_expiry": expiry,
        "step": strike_step(u),
        "context": session_context(None, u),
    }


def _q(series: pd.Series, q: float) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return round(float(clean.quantile(q)), 4)


def burn_by_dte(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Median and tail burn rate per days-to-expiry bucket.

    The 1/T scaling shows up here directly: measured on the 2026-08-12 NIFTY
    archive, 8.13%/day at 6 DTE against 3.79% at 13 and 2.48% at 20, where pure
    1/T predicts ratios of 2.17 and 1.53 versus 2.14 and 1.53 observed.
    """
    if frame is None or frame.empty or "burn_pct_day" not in frame.columns:
        return []
    work = frame.copy()
    work["dte"] = F.dte_of(work)
    out: list[dict[str, Any]] = []
    for dte, grp in work.groupby("dte"):
        clean = pd.to_numeric(grp["burn_pct_day"], errors="coerce").dropna()
        if clean.empty:
            continue
        out.append(
            {
                "dte": int(dte),
                "n": int(len(clean)),
                "p50": round(float(clean.quantile(0.50)), 3),
                "p95": round(float(clean.quantile(0.95)), 3),
                "expiry": str(grp["expiry"].iloc[0]),
            }
        )
    return sorted(out, key=lambda r: r["dte"])


def burn_by_strike(frame: pd.DataFrame, *, expiry: str | None = None) -> list[dict[str, Any]]:
    """Burn rate across the strike ladder at the session's last observation.

    A snapshot rather than a session statistic — this is the shape of the decay
    curve right now, which is what a seller picking a strike is looking at.
    """
    if frame is None or frame.empty:
        return []
    work = frame if not expiry else frame[frame["expiry"].astype(str) == str(expiry)]
    work = work.dropna(subset=["ts"])
    if work.empty:
        return []
    last_ts = work["ts"].max()
    last = work[work["ts"] == last_ts]
    out: list[dict[str, Any]] = []
    for row in last.sort_values(["strike", "option_type"]).itertuples():
        burn = getattr(row, "burn_pct_day", None)
        out.append(
            {
                "strike": float(row.strike),
                "option_type": str(row.option_type),
                "premium": None if pd.isna(row.ltp) else round(float(row.ltp), 2),
                "theta": None if pd.isna(row.theta) else round(float(row.theta), 4),
                "burn_pct_day": None if burn is None or pd.isna(burn) else round(float(burn), 3),
            }
        )
    return out


def straddle_burn(frame: pd.DataFrame, underlying: str) -> pd.Series:
    """Per-minute burn rate of the ATM straddle: -(theta_CE+theta_PE)/(P_CE+P_PE).

    The straddle is what the Rolling Straddle and Premium Book runners actually
    hold, and pairing the legs cancels most of the directional exposure, so this
    is a cleaner read than either leg alone.

    ATM is re-resolved against **each minute's own spot**, so the series can
    change contract intraday. That is the opposite of the delta desk's ladder,
    which pins to the open ATM — and deliberately so. Pinning exists there
    because the ladder plots a *premium change*, and re-striking mid-series
    would show a move nobody traded. Burn rate is a ratio computed inside a
    single minute, never differenced across them, so switching contracts costs
    nothing and pinning would instead answer the wrong question: a strike pinned
    to the open is not the ATM straddle by lunchtime.
    """
    if frame is None or frame.empty:
        return pd.Series(dtype=float)

    work = frame.dropna(subset=["ts"]).copy()
    if work.empty:
        return pd.Series(dtype=float)

    spot_at = work.groupby("ts")["spot"].first()
    step = strike_step(underlying)
    atm_at = (spot_at / step).round() * step
    work["atm_now"] = work["ts"].map(atm_at)

    leg = work[pd.to_numeric(work["strike"], errors="coerce") == work["atm_now"]]
    if leg.empty:
        return pd.Series(dtype=float)

    grouped = leg.groupby("ts").agg(
        theta=("theta", "sum"),
        premium=("ltp", "sum"),
        legs=("option_type", "nunique"),
    )
    # Both legs or nothing — a one-sided minute would halve the numerator and
    # show a phantom drop in burn rate.
    grouped = grouped[grouped["legs"] == 2]
    if grouped.empty:
        return pd.Series(dtype=float)
    burn = -grouped["theta"] / grouped["premium"] * 100.0
    return burn.where(grouped["premium"] >= F.MIN_PREMIUM * 2)


def session_chart(
    underlying: str,
    session_date: date | None = None,
    *,
    expiry: str | None = None,
    horizon_min: int = F.DEFAULT_HORIZON_MIN,
) -> dict[str, Any]:
    """Burn rate and decay capture for one archived session.

    Theta velocity is **not** here: it costs ~3.5s of the ~5s total, and it is
    the weakest of the three metrics (measured 2026-08-12, it correlates only
    0.12-0.16 with spot moves and *lags* them by 6-9 minutes). Paying for it on
    every page load to render a tertiary panel is the wrong trade, so it lives
    behind :func:`velocity_chart`.

    With no ``session_date`` this resolves to the latest archived session, not
    to today — the delta desk learned that the hard way (before the open there
    is no file for today, and defaulting to it renders an empty page while a
    full session sits on disk one day back).
    """
    u = str(underlying).upper()
    session_date = session_date or store.latest_session(u)
    key = ("chart", u, str(session_date), expiry, horizon_min, _file_stamp(u, session_date))
    return _cached(key, lambda: _build(u, session_date, expiry, horizon_min))


def velocity_chart(
    underlying: str,
    session_date: date | None = None,
    *,
    expiry: str | None = None,
) -> dict[str, Any]:
    """Clock-removed theta velocity and its lag profile against spot moves.

    Separate from :func:`session_chart` on cost, and separate on meaning: this
    is the panel that says whether theta velocity carries anything, and the
    answer so far is "not much". Read ``correlation.interpretation`` before
    reading the series.
    """
    u = str(underlying).upper()
    session_date = session_date or store.latest_session(u)
    key = ("velocity", u, str(session_date), expiry, _file_stamp(u, session_date))
    return _cached(key, lambda: _build_velocity(u, session_date, expiry))


def _cached(key: tuple, build):
    if key in _CACHE:
        return _CACHE[key]
    payload = build()
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = payload
    return payload


def _build(u: str, session_date: date | None, expiry: str | None, horizon_min: int) -> dict[str, Any]:
    snapshots = store.load_session(u, session_date) if session_date else []
    if not snapshots:
        return _empty(u, session_date, expiry)

    rows = pd.DataFrame(store.to_rows(snapshots))
    if rows.empty:
        return _empty(u, session_date, expiry)
    rows["ts"] = pd.to_datetime(rows["ts"], format="mixed", utc=True)

    session_expiries = sorted(rows["expiry"].astype(str).unique())
    base = {**_empty(u, session_date, expiry), "expiries": session_expiries}
    base["session_date"] = snapshots[0].get("session_date")

    if expiry:
        rows = rows[rows["expiry"].astype(str) == str(expiry)]
        if rows.empty:
            return base

    priced = F.burn_rate(rows)
    spot = priced.groupby("ts")["spot"].first().sort_index()
    atm = atm_strike(u, float(spot.iloc[-1]))

    # Context and the strike ladder are per-expiry: without this the same strike
    # appears once per tracked expiry.
    ladder_expiry = expiry or session_expiries[0]
    context = session_context(priced, u, expiry=ladder_expiry)
    per_expiry = priced[priced["expiry"].astype(str) == str(ladder_expiry)]

    straddle = straddle_burn(per_expiry, u)
    # Fixed at the session's closing ATM, unlike the straddle series above.
    # These two lines answer different questions: "what is the ATM straddle
    # burning" (re-struck each minute) versus "what did today's final ATM strike
    # do all session" (fixed), and the second is only meaningful pinned.
    atm_legs = per_expiry[pd.to_numeric(per_expiry["strike"], errors="coerce") == atm]
    atm_ce = atm_legs[atm_legs["option_type"] == "CE"].set_index("ts")["burn_pct_day"]
    atm_pe = atm_legs[atm_legs["option_type"] == "PE"].set_index("ts")["burn_pct_day"]
    med = priced.groupby("ts")["burn_pct_day"].median()

    frame = pd.DataFrame({"spot": spot})
    frame["burn_straddle"] = straddle.reindex(frame.index)
    frame["burn_atm_ce"] = atm_ce.reindex(frame.index)
    frame["burn_atm_pe"] = atm_pe.reindex(frame.index)
    frame["burn_med"] = med.reindex(frame.index)

    attribution = F.decay_attribution(rows, horizon_min=horizon_min)
    by_dte = F.attribution_by_dte(attribution)

    return {
        **base,
        "atm_strike": atm,
        "contracts": int(rows.groupby(["expiry", "strike", "option_type"]).ngroups),
        "selected_expiry": expiry,
        "context": context,
        "minutes": [
            {
                "ts": ts.isoformat(),
                "clock": ts.tz_convert(store.IST).strftime("%H:%M"),
                "spot": round(float(row.spot), 2),
                "burn_straddle": _num(row.burn_straddle, 3),
                "burn_atm_ce": _num(row.burn_atm_ce, 3),
                "burn_atm_pe": _num(row.burn_atm_pe, 3),
                "burn_med": _num(row.burn_med, 3),
            }
            for ts, row in frame.iterrows()
        ],
        "burn_by_dte": burn_by_dte(priced),
        "burn_by_strike": burn_by_strike(priced, expiry=ladder_expiry),
        "capture": {
            "horizon_min": horizon_min,
            "by_dte": by_dte,
            "note": _capture_note(by_dte, len(snapshots), horizon_min),
        },
    }


def _build_velocity(u: str, session_date: date | None, expiry: str | None) -> dict[str, Any]:
    empty = {
        "underlying": u,
        "session_date": session_date.isoformat() if session_date else None,
        "selected_expiry": expiry,
        "minutes": [],
        "thresholds": {},
        "blanks": "no data",
        "correlation": {
            "n": 0,
            "lag_profile": [],
            "best_lag": None,
            "best_corr": None,
            "contemporaneous": None,
            "interpretation": "no data",
        },
    }
    snapshots = store.load_session(u, session_date) if session_date else []
    if not snapshots:
        return empty

    rows = pd.DataFrame(store.to_rows(snapshots))
    if rows.empty:
        return empty
    rows["ts"] = pd.to_datetime(rows["ts"], format="mixed", utc=True)
    if expiry:
        rows = rows[rows["expiry"].astype(str) == str(expiry)]
        if rows.empty:
            return empty

    empty["session_date"] = snapshots[0].get("session_date")
    spot = rows.groupby("ts")["spot"].first().sort_index()
    velocity, blanks = F.compute_theta_velocity(rows)
    if velocity.empty:
        return {**empty, "blanks": F.blank_summary(blanks)}

    velocity["ts"] = pd.to_datetime(velocity["ts"], utc=True)
    agg = velocity.groupby("ts")["tau_t"].agg(tau_med="median", tau_max="max")
    frame = pd.DataFrame({"spot": spot}).join(agg, how="left")

    profile = lag_profile(frame["spot"].pct_change().abs(), frame["tau_max"])
    scored = [p for p in profile if p["corr"] is not None]
    best = max(scored, key=lambda p: abs(p["corr"])) if scored else None
    usable = max((p["n"] for p in profile), default=0)

    return {
        **empty,
        "minutes": [
            {
                "ts": ts.isoformat(),
                "clock": ts.tz_convert(store.IST).strftime("%H:%M"),
                "spot": round(float(row.spot), 2),
                "tau_med": _num(row.tau_med, 6),
                "tau_max": _num(row.tau_max, 6),
            }
            for ts, row in frame.iterrows()
        ],
        "blanks": F.blank_summary(blanks),
        "thresholds": {"p95": _q(frame["tau_max"], 0.95), "p99": _q(frame["tau_max"], 0.99)},
        "correlation": {
            "n": int(usable),
            "lag_profile": profile,
            "best_lag": best["lag_min"] if best else None,
            "best_corr": best["corr"] if best else None,
            "contemporaneous": next((p["corr"] for p in profile if p["lag_min"] == 0), None),
            "interpretation": describe_lag(best["lag_min"]) if best
            else f"insufficient data ({usable} of {MIN_CORRELATION_POINTS} minutes)",
        },
    }


def _capture_note(by_dte: list[dict[str, Any]], minutes: int, horizon_min: int) -> str:
    """Say why the capture table is empty or partly greyed out, never just blank."""
    if not by_dte:
        # Observations at minute 0 and minute H are the first valid pair, so the
        # threshold is H+1 samples, not 2H.
        needed = horizon_min + 1
        if minutes < needed:
            return (
                f"needs {needed} minutes for one {horizon_min}-minute window; "
                f"{minutes} collected so far"
            )
        return "no usable windows"
    ok = [r for r in by_dte if r["quality"] == "ok"]
    windows = min(r["time_windows"] for r in by_dte)

    # Distinguish "not enough of the session has elapsed" from "structurally
    # unreadable". Until several distinct windows exist the term shares are one
    # draw seen through many strikes, and the gate fires almost regardless of
    # the data — reporting that as a vol problem sends the reader after
    # something that is really just the clock.
    if windows < F.MIN_WINDOWS_FOR_QUALITY:
        return (
            f"{windows} distinct {horizon_min}-minute window(s) so far — too few to judge; "
            "the strikes inside one window are not independent samples"
        )
    if not ok:
        return "no bucket passed the quality gate — theta too small or vol term dominant"
    thin = " (thin — one session is indicative, not precise)" if windows < 6 else ""
    return f"{len(ok)} of {len(by_dte)} buckets usable at a {horizon_min}-minute horizon{thin}"


def _num(value: Any, digits: int) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits)
