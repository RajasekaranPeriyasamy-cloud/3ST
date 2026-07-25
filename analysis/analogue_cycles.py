"""Expiry-cycle analogue paths for index underlyings.

Match historical expiry cycles with a similar % move at the same day-in-cycle,
then fan-chart remaining paths to expiry. Read-only — does not arm or order.

Expiry weekdays follow NSE/BSE revision (ICICI Direct / exchange circulars):
contracts expiring on/before 2025-08-31 keep the prior weekday; contracts
expiring on/after 2025-09-01 use the revised weekday. Holidays snap back to
the previous trading day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

import numpy as np
import pandas as pd

from config import ANALOGUE_DEFAULTS, INDEX_OPTIONS, INSTRUMENTS
from instruments import resolve_instrument

CycleKind = Literal["weekly", "monthly"]

# NSE/BSE derivative expiry weekday revision (effective for expiries on/after).
# Source: https://www.icicidirect.com/futures-and-options/articles/revised-expiry-days-for-nse-futures-and-options
EXPIRY_WEEKDAY_CUTOVER = date(2025, 9, 1)


def analogue_config() -> dict[str, Any]:
    d = ANALOGUE_DEFAULTS
    return {
        "underlyings": list(d.get("underlyings") or ["NIFTY", "BANKNIFTY", "SENSEX"]),
        "cycle_kinds": ["weekly", "monthly"],
        "default_cycle_kind": d.get("default_cycle_kind", "monthly"),
        "default_similarity_band_pct": d["default_similarity_band_pct"],
        "similarity_band_min": d.get("similarity_band_min", 0.5),
        "similarity_band_max": d.get("similarity_band_max", 15.0),
        "max_lookback_days": d["max_lookback_days"],
        "max_analogue_paths": d.get("max_analogue_paths", 80),
        "refresh_seconds": d.get("refresh_seconds", 300),
        "expiry_weekday_cutover": EXPIRY_WEEKDAY_CUTOVER.isoformat(),
        "expiry_weekdays": {
            "NIFTY": {
                "before": "Thursday",
                "on_or_after_cutover": "Tuesday",
            },
            "BANKNIFTY": {
                "before": "Wednesday",
                "on_or_after_cutover": "Tuesday",
                "note": "Weekly BN discontinued Nov 2024; monthly uses last weekday of month.",
            },
            "SENSEX": {
                "before": "Tuesday",
                "on_or_after_cutover": "Thursday",
            },
        },
        "note": (
            "Historical expiry-cycle analogues — similar move at same day-in-cycle. "
            "Expiry weekdays follow NSE/BSE Sep-2025 revision (NIFTY/BANKNIFTY→Tue, "
            "SENSEX→Thu). Not trade advice; does not arm or place orders."
        ),
    }


def _instrument_key(underlying: str) -> str:
    meta = INDEX_OPTIONS.get(underlying) or {}
    key = str(meta.get("index_token_key") or "")
    if key and key in INSTRUMENTS:
        return key
    aliases = {"NIFTY": "NIFTY50", "BANKNIFTY": "BANKNIFTY50", "SENSEX": "SENSEX"}
    key = aliases.get(underlying, underlying)
    if key not in INSTRUMENTS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")
    return key


def _fetch_daily_ohlc(instrument_token: int, lookback_days: int) -> pd.DataFrame:
    from kite_auth import get_kite_client

    end = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0)
    start = end - timedelta(days=int(lookback_days))
    kite = get_kite_client()
    frames: list[pd.DataFrame] = []
    cursor = start
    chunk_days = 365
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=chunk_days), end)
        raw = kite.historical_data(
            instrument_token=instrument_token,
            from_date=cursor,
            to_date=chunk_end,
            interval="day",
            continuous=False,
            oi=False,
        )
        if raw:
            rows = []
            for r in raw:
                if isinstance(r, dict):
                    rows.append(
                        {
                            "date": r["date"],
                            "open": float(r["open"]),
                            "high": float(r["high"]),
                            "low": float(r["low"]),
                            "close": float(r["close"]),
                        }
                    )
                else:
                    rows.append(
                        {
                            "date": r[0],
                            "open": float(r[1]),
                            "high": float(r[2]),
                            "low": float(r[3]),
                            "close": float(r[4]),
                        }
                    )
            part = pd.DataFrame(rows).set_index("date")
            part.index = pd.to_datetime(part.index)
            frames.append(part)
        cursor = chunk_end + timedelta(seconds=1)
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    df = pd.concat(frames).sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.astype(float)


def _as_date(ts: Any) -> date:
    if isinstance(ts, date) and not isinstance(ts, datetime):
        return ts
    return pd.Timestamp(ts).date()


def _trading_dates(closes: pd.Series) -> list[date]:
    return [_as_date(i) for i in closes.index]


def _snap_back(d: date, trading: set[date], max_back: int = 5) -> date | None:
    """If d is not a trading day, walk back up to max_back calendar days."""
    cur = d
    for _ in range(max_back + 1):
        if cur in trading:
            return cur
        cur = cur - timedelta(days=1)
    return None


def _expiry_weekday(underlying: str, expiry_date: date) -> int:
    """Python weekday Mon=0…Sun=6 for the scheduled expiry (before holiday snap).

    Regime per ICICI Direct / NSE & BSE circulars (effective expiries on/after
    2025-09-01):
      NIFTY:     Thu → Tue
      BANKNIFTY: Wed (hist. weekly/monthly) → Tue
      SENSEX:    Tue → Thu
    Contracts expiring on/before 2025-08-31 keep the prior weekday.
    """
    u = underlying.upper()
    revised = expiry_date >= EXPIRY_WEEKDAY_CUTOVER
    if u == "NIFTY":
        return 1 if revised else 3  # Tue / Thu
    if u == "BANKNIFTY":
        return 1 if revised else 2  # Tue / Wed
    if u == "SENSEX":
        return 3 if revised else 1  # Thu / Tue
    return 1 if revised else 3


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    """Calendar last occurrence of weekday in month (Mon=0)."""
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _snap_expiry(candidate: date, trading: set[date]) -> date | None:
    """Snap expiry to prior trading day; keep future candidates past known calendar."""
    if not trading:
        return candidate
    last_tr = max(trading)
    if candidate > last_tr:
        return candidate
    return _snap_back(candidate, trading)


def _generate_candidate_expiries(
    underlying: str,
    start: date,
    end: date,
    trading: set[date],
    cycle_kind: CycleKind,
) -> list[date]:
    """Build historical expiry candidates and snap to trading calendar.

    Weekly: every scheduled weekday in range (regime-aware).
    Monthly: last scheduled weekday of each calendar month (regime-aware).
    Holidays → previous trading day (exchange convention).
    """
    out: list[date] = []
    limit = end + timedelta(days=10)

    if cycle_kind == "monthly":
        y, m = start.year, start.month
        end_m = (end.year, end.month)
        while (y, m) <= end_m:
            month_end = date(y, m, 28)
            if date(y, m, 1) < EXPIRY_WEEKDAY_CUTOVER <= date(y, m, 28) + timedelta(days=3):
                wd = _expiry_weekday(underlying, EXPIRY_WEEKDAY_CUTOVER)
            else:
                wd = _expiry_weekday(underlying, month_end)
            candidate = _last_weekday_of_month(y, m, wd)
            snapped = _snap_expiry(candidate, trading)
            if snapped and start <= snapped <= limit:
                out.append(snapped)
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1
        return sorted(set(out))

    # Weekly: day-walk so Thu→Tue / Tue→Thu cutover does not skip a week
    d = start
    seen: set[date] = set()
    while d <= limit:
        wd = _expiry_weekday(underlying, d)
        if d.weekday() == wd:
            snapped = _snap_expiry(d, trading)
            if snapped and start <= snapped <= limit and snapped not in seen:
                # Accept only if snapped is the holiday-adjusted scheduled day
                if snapped == d or (
                    snapped < d
                    and _expiry_weekday(underlying, d) == d.weekday()
                    and (d - snapped).days <= 5
                ):
                    out.append(snapped)
                    seen.add(snapped)
            d += timedelta(days=1)
            continue
        d += timedelta(days=1)

    return sorted(out)

def _merge_listed_expiries(hist: list[date], listed: list[str]) -> list[date]:
    merged = set(hist)
    for s in listed:
        try:
            merged.add(pd.Timestamp(s).date())
        except Exception:
            continue
    return sorted(merged)


def _cycle_slices(
    closes: pd.Series,
    expiries: list[date],
) -> list[dict[str, Any]]:
    """
    Build completed cycles: trading days from day after prev expiry through expiry day.
    day 0 = first session after previous expiry.
    """
    tdates = _trading_dates(closes)
    tset = set(tdates)
    # map date -> iloc
    loc = {d: i for i, d in enumerate(tdates)}
    cycles: list[dict[str, Any]] = []
    for i in range(1, len(expiries)):
        prev_e, cur_e = expiries[i - 1], expiries[i]
        if prev_e not in tset:
            prev_e_s = _snap_back(prev_e, tset)
            if prev_e_s is None:
                continue
            prev_e = prev_e_s
        if cur_e not in tset:
            cur_e_s = _snap_back(cur_e, tset)
            if cur_e_s is None:
                continue
            cur_e = cur_e_s
        # first trading day strictly after prev expiry
        start_i = loc[prev_e] + 1
        end_i = loc.get(cur_e)
        if end_i is None or start_i > end_i or start_i >= len(tdates):
            continue
        path_dates = tdates[start_i : end_i + 1]
        path_closes = [float(closes.iloc[loc[d]]) for d in path_dates]
        if len(path_closes) < 2:
            continue
        start_px = path_closes[0]
        cum = [(c / start_px - 1.0) * 100.0 for c in path_closes]
        cycles.append(
            {
                "prev_expiry": prev_e.isoformat(),
                "expiry": cur_e.isoformat(),
                "start_date": path_dates[0].isoformat(),
                "start_px": start_px,
                "end_px": path_closes[-1],
                "n_days": len(path_closes),
                "cum_pct": cum,
                "closes": path_closes,
                "dates": [d.isoformat() for d in path_dates],
            }
        )
    return cycles


def _percentile_paths(paths: list[list[float]], qs: list[float]) -> dict[float, list[float]]:
    if not paths:
        return {q: [] for q in qs}
    max_len = max(len(p) for p in paths)
    out: dict[float, list[float]] = {q: [] for q in qs}
    for day in range(max_len):
        vals = [p[day] for p in paths if len(p) > day]
        if not vals:
            for q in qs:
                out[q].append(float("nan"))
            continue
        arr = np.asarray(vals, dtype=float)
        for q in qs:
            out[q].append(float(np.nanpercentile(arr, q)))
    return out


def build_analogue_snapshot(
    underlying: str,
    *,
    similarity_band_pct: float | None = None,
    override_move_pct: float | None = None,
    cycle_kind: CycleKind | str = "monthly",
    lookback_days: int | None = None,
    max_paths: int | None = None,
    ohlc: pd.DataFrame | None = None,
    listed_expiries: list[str] | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    u = underlying.upper()
    if u not in INDEX_OPTIONS and u not in ("NIFTY", "BANKNIFTY", "SENSEX"):
        raise ValueError(f"Unknown underlying '{underlying}'")

    d = ANALOGUE_DEFAULTS
    band = float(
        similarity_band_pct
        if similarity_band_pct is not None
        else d["default_similarity_band_pct"]
    )
    band = max(float(d.get("similarity_band_min", 0.5)), min(band, float(d.get("similarity_band_max", 15))))
    kind: CycleKind = "weekly" if str(cycle_kind).lower() == "weekly" else "monthly"
    max_lb = int(d["max_lookback_days"])
    lb = int(lookback_days) if lookback_days is not None else max_lb
    lb = max(120, min(lb, max_lb))
    path_cap = int(max_paths if max_paths is not None else d.get("max_analogue_paths", 80))

    if ohlc is None:
        key = _instrument_key(u)
        meta = resolve_instrument(key)
        ohlc = _fetch_daily_ohlc(int(meta["instrument_token"]), lb)
    if ohlc is None or ohlc.empty or len(ohlc) < 40:
        raise RuntimeError(f"Insufficient daily history for {u}")

    closes = ohlc["close"].astype(float)
    closes.index = pd.to_datetime(closes.index)
    trading = set(_trading_dates(closes))
    today = as_of or _as_date(closes.index[-1])

    if listed_expiries is None:
        try:
            from options.chain import list_expiries

            listed_expiries = list_expiries(u)
        except Exception:
            listed_expiries = []

    first_d = _as_date(closes.index[0])
    last_d = _as_date(closes.index[-1])
    hist = _generate_candidate_expiries(u, first_d, last_d + timedelta(days=40), trading, kind)
    expiries = _merge_listed_expiries(hist, listed_expiries or [])
    # only expiries that fall within / near trading range
    expiries = [e for e in expiries if e >= first_d]

    # Current cycle expiry: next candidate of this cycle kind on/after today.
    # Prefer a matching listed contract when available.
    kind_future = [e for e in expiries if e >= today]
    if not kind_future:
        raise RuntimeError(f"No upcoming {kind} expiry found for {u}")
    cur_exp = kind_future[0]
    if listed_expiries:
        listed_dates = []
        for x in listed_expiries:
            try:
                listed_dates.append(pd.Timestamp(x).date())
            except Exception:
                continue
        listed_future = sorted(e for e in listed_dates if e >= today)
        if kind == "weekly" and listed_future:
            cur_exp = listed_future[0]
        elif kind == "monthly" and listed_future:
            # Last listed expiry in each month, then first >= today
            by_m: dict[tuple[int, int], date] = {}
            for e in listed_dates:
                by_m[(e.year, e.month)] = e
            monthly_listed = sorted(by_m.values())
            mf = [e for e in monthly_listed if e >= today]
            if mf:
                cur_exp = mf[0]

    past = [e for e in expiries if e < cur_exp]
    if not past:
        raise RuntimeError(f"No previous expiry before {cur_exp} for {u}")
    prev_exp = past[-1]

    tdates = _trading_dates(closes)
    loc = {d: i for i, d in enumerate(tdates)}

    # On expiry day the selected contract expires today — roll to the *next*
    # expiry. The new cycle's day 0 is the next session (tomorrow).
    cur_snap = _snap_back(cur_exp, trading) or cur_exp
    if today == cur_snap or (cur_snap in loc and today == cur_snap):
        following = [e for e in expiries if e > cur_exp]
        if following:
            prev_exp = cur_exp
            cur_exp = following[0]
            cur_snap = _snap_back(cur_exp, trading) or cur_exp

    prev_snap = _snap_back(prev_exp, trading) or prev_exp
    if prev_snap not in loc:
        raise RuntimeError("Previous expiry not in trading calendar")
    start_i = loc[prev_snap] + 1

    # Build completed historical cycles (ending before current cycle's expiry)
    all_cycles = _cycle_slices(closes, expiries)
    completed = [
        c
        for c in all_cycles
        if pd.Timestamp(c["expiry"]).date() < cur_exp
    ]

    cycle_pending = start_i >= len(tdates) or prev_snap >= today

    if cycle_pending:
        # Next cycle not started yet (typical on weekly expiry day).
        spot = float(closes.iloc[-1])
        start_px = spot
        day_in_cycle = 0
        actual_move = 0.0
        move_for_match = (
            float(override_move_pct) if override_move_pct is not None else 0.0
        )
        cur_dates: list[date] = []
        cur_closes: list[float] = []
        # Estimate sessions from next day through next expiry
        if cur_snap in loc:
            days_remaining = max(0, loc[cur_snap] - (len(tdates) - 1))
        else:
            days_remaining = max(1, len(pd.bdate_range(today + timedelta(days=1), cur_exp)))
        cycle_len_est = max(days_remaining, 1)
        end_i = len(tdates) - 1
    else:
        end_i = loc.get(today)
        if end_i is None:
            end_i = len(tdates) - 1
            today = tdates[end_i]
        cur_exp_i = loc.get(cur_snap) if cur_snap in loc else loc.get(cur_exp)
        if cur_exp_i is not None:
            end_i = min(end_i, cur_exp_i)
        if end_i < start_i:
            raise RuntimeError("Invalid current cycle window")

        cur_dates = tdates[start_i : end_i + 1]
        cur_closes = [float(closes.iloc[loc[d]]) for d in cur_dates]
        start_px = cur_closes[0]
        spot = cur_closes[-1]
        day_in_cycle = len(cur_closes) - 1  # 0-based day index of "today"
        actual_move = (spot / start_px - 1.0) * 100.0
        move_for_match = (
            float(override_move_pct) if override_move_pct is not None else actual_move
        )

        if cur_snap in loc:
            days_remaining = max(0, loc[cur_snap] - end_i)
            cycle_len_est = (loc[cur_snap] - start_i) + 1
        else:
            days_remaining = max(0, (cur_exp - today).days)
            cycle_len_est = day_in_cycle + days_remaining + 1

    # Match historical cycles at same day index
    matches: list[dict[str, Any]] = []
    for c in completed:
        cum = c["cum_pct"]
        if day_in_cycle >= len(cum):
            continue
        hist_move = cum[day_in_cycle]
        if abs(hist_move - move_for_match) <= band:
            end_move = cum[-1]
            further = end_move - hist_move
            matches.append(
                {
                    **c,
                    "move_at_day": hist_move,
                    "end_move": end_move,
                    "further_pct": further,
                    "ended_up": end_move > hist_move,
                    "ended_down": end_move < hist_move,
                }
            )

    def _base_payload(
        *,
        n_match: int,
        stats: dict[str, Any] | None,
        analogue_paths: list[dict[str, Any]],
        median_path: list[dict[str, Any]],
        p25_path: list[dict[str, Any]],
        p75_path: list[dict[str, Any]],
        reasoning: list[str],
    ) -> dict[str, Any]:
        if cycle_pending:
            current_path: list[dict[str, Any]] = [
                {"day": 0, "cum_pct": 0.0, "date": None, "pending": True}
            ]
            cycle_start = None
        else:
            current_path = [
                {
                    "day": i,
                    "cum_pct": round((c / start_px - 1.0) * 100.0, 4),
                    "date": cur_dates[i].isoformat(),
                }
                for i, c in enumerate(cur_closes)
            ]
            cycle_start = cur_dates[0].isoformat()
        return {
            "underlying": u,
            "label": (INSTRUMENTS.get(_instrument_key(u)) or {}).get("label", u),
            "cycle_kind": kind,
            "cycle_start": cycle_start,
            "cycle_pending": cycle_pending,
            "prev_expiry": prev_exp.isoformat(),
            "current_expiry": cur_exp.isoformat(),
            "as_of": today.isoformat(),
            "day_in_cycle": day_in_cycle,
            "days_remaining": days_remaining,
            "cycle_length_est": cycle_len_est,
            "spot": round(spot, 4),
            "cycle_start_px": round(start_px, 4),
            "move_so_far_pct": round(actual_move, 4),
            "move_used_for_match_pct": round(move_for_match, 4),
            "override_move_pct": override_move_pct,
            "similarity_band_pct": band,
            "matched": n_match,
            "stats": stats,
            "current_path": current_path,
            "analogue_paths": analogue_paths,
            "median_path": median_path,
            "p25_path": p25_path,
            "p75_path": p75_path,
            "reasoning": reasoning,
            "disclaimer": (
                "Expiry-analogue fan from historical cycles with similar move at the same "
                "day-in-cycle. Not trade advice; does not arm or place orders."
            ),
            "engine": "analogue_cycles",
            "bars_used": int(len(closes)),
            "lookback_days_requested": lb,
            "updated_at": datetime.now().astimezone().isoformat(),
        }

    n_match = len(matches)
    if n_match == 0:
        reasoning = []
        if cycle_pending:
            reasoning.append(
                f"Today is expiry ({prev_exp.isoformat()}). Next {kind} cycle "
                f"→ {cur_exp.isoformat()} starts next session (day 0 pending)."
            )
        reasoning.append(
            f"No historical {kind} cycles matched move {move_for_match:+.2f}% at day "
            f"{day_in_cycle} within ±{band:.2f}%."
        )
        reasoning.append("Widen the similarity band or switch weekly/monthly.")
        return _base_payload(
            n_match=0,
            stats=None,
            analogue_paths=[],
            median_path=[],
            p25_path=[],
            p75_path=[],
            reasoning=reasoning,
        )

    end_moves = np.asarray([m["end_move"] for m in matches], dtype=float)
    further = np.asarray([m["further_pct"] for m in matches], dtype=float)
    # Project expiry levels onto *current* cycle start (provisional = spot when pending)
    expiry_levels = start_px * (1.0 + end_moves / 100.0)
    remaining_from_spot = (expiry_levels / spot - 1.0) * 100.0

    def q(arr: np.ndarray, p: float) -> float:
        return float(np.nanpercentile(arr, p))

    p_up = float(np.mean(further > 0))
    p_down = float(np.mean(further < 0))
    p_flat = float(np.mean(further == 0))

    stats = {
        "matched": n_match,
        "median_expiry_level": round(float(np.median(expiry_levels)), 2),
        "median_remaining_pct": round(float(np.median(remaining_from_spot)), 4),
        "p25_expiry_level": round(q(expiry_levels, 25), 2),
        "p75_expiry_level": round(q(expiry_levels, 75), 2),
        "p25_remaining_pct": round(q(remaining_from_spot, 25), 4),
        "p75_remaining_pct": round(q(remaining_from_spot, 75), 4),
        "p10_expiry_level": round(q(expiry_levels, 10), 2),
        "p90_expiry_level": round(q(expiry_levels, 90), 2),
        "p10_remaining_pct": round(q(remaining_from_spot, 10), 4),
        "p90_remaining_pct": round(q(remaining_from_spot, 90), 4),
        "p_further_up": round(p_up, 4),
        "p_further_down": round(p_down, 4),
        "p_further_flat": round(p_flat, 4),
    }

    full_paths = [m["cum_pct"] for m in matches]
    pcts = _percentile_paths(full_paths, [25, 50, 75])

    ups = [m for m in matches if m["ended_up"]]
    dns = [m for m in matches if m["ended_down"]]
    sample: list[dict[str, Any]] = []
    half = max(1, path_cap // 2)
    for m in ups[:half] + dns[:half]:
        sample.append(
            {
                "expiry": m["expiry"],
                "start_date": m["start_date"],
                "ended_up": m["ended_up"],
                "path": [
                    {"day": i, "cum_pct": round(v, 4)} for i, v in enumerate(m["cum_pct"])
                ],
            }
        )
    sample = sample[:path_cap]

    def series_from(vals: list[float]) -> list[dict[str, float | int]]:
        return [{"day": i, "cum_pct": round(v, 4)} for i, v in enumerate(vals) if not np.isnan(v)]

    reasoning = []
    if cycle_pending:
        reasoning.append(
            f"Today is expiry ({prev_exp.isoformat()}). Next {kind} cycle "
            f"→ {cur_exp.isoformat()} starts next session — showing day-0 analogues "
            f"(move {move_for_match:+.2f}%)."
        )
    else:
        reasoning.append(
            f"{u} {kind} cycle {cur_dates[0].isoformat()} → expiry {cur_exp.isoformat()}: "
            f"day {day_in_cycle}, {days_remaining} sessions left, move so far {actual_move:+.2f}%."
        )
    reasoning.append(
        f"Matched {n_match} historical cycles with move within ±{band:.2f}% of "
        f"{move_for_match:+.2f}% at day {day_in_cycle}."
    )
    reasoning.append(
        f"Median expiry level {stats['median_expiry_level']:.0f} "
        f"({stats['median_remaining_pct']:+.2f}% from spot). "
        f"P(further up)={stats['p_further_up']*100:.0f}% · "
        f"P(further down)={stats['p_further_down']*100:.0f}%."
    )

    return _base_payload(
        n_match=n_match,
        stats=stats,
        analogue_paths=sample,
        median_path=series_from(pcts[50]),
        p25_path=series_from(pcts[25]),
        p75_path=series_from(pcts[75]),
        reasoning=reasoning,
    )
