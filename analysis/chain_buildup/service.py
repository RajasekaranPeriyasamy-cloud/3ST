"""Chain build-up service — feeds :mod:`features` and owns every I/O decision.

This desk has **no collector and no store**, for the same reason
``analysis/theta_decay/`` has none: the minute archive it needs is already being
written. ``analysis/delta_velocity/collector.py`` samples every minute of every
cash session and archives per-leg ``oi`` and ``ltp`` for NIFTY / BANKNIFTY /
SENSEX. Bucketing that into 5/15/30/60-minute columns is a pure read — no Kite
call, no rate-limit exposure, no second daemon thread.

Two sources, and the split matters
----------------------------------
**Archive (default).** Free, complete from 09:15, and the only source that
survives expiry — Kite cannot serve candles for a contract whose token has been
delisted, which is exactly the point ``delta_velocity/store.py`` was written to
address. Its limit is width: the collector tracks ``STRIKE_WIDTH`` strikes each
side of ATM *at capture time*, so the strike set drifts with spot through the
day and the union across a session is wider than any single minute of it.

**Kite historical (widening only).** Requested when the strike range asks for
more than the archive holds. One ``fetch_historical_by_token`` call per leg,
which is the expensive path this repo has been bitten by before — a gamma
snapshot once walked a chain issuing ~80 sequential historical requests and took
80+ seconds. Three controls keep that from recurring:

* it is **opt-in per request** (``widen=False`` refuses rather than silently
  costing a minute),
* the leg count is **capped** at :data:`MAX_WIDEN_LEGS` and truncation is
  reported in ``meta`` rather than passed off as full coverage,
* results are **cached per (token, timeframe, session)** and the cache is only
  re-read, never re-fetched, within a session for a closed day.

A widened grid is materially the same data: Kite's ``oi`` on a candle is OI at
candle close, which is precisely what :func:`features.bucket_legs` takes from
the archive. The adapter below stamps each candle with its *close* time so the
two land in the same bucket.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from analysis.chain_buildup import features
from analysis.delta_velocity import collector as dv_collector
from analysis.delta_velocity import store as dv_store
from utils.logging import get_logger, log_event

IST = ZoneInfo("Asia/Kolkata")
logger = get_logger("chain_buildup.service")

#: Underlyings this desk serves — the archive's coverage, no more. MCX is out of
#: scope: its session runs to ~23:30 and would need its own bucket anchor.
UNDERLYINGS: tuple[str, ...] = dv_collector.UNDERLYINGS

#: Strike-window presets. ``all`` means every strike the chosen source can
#: reach, which is not the same number for the two sources — see module docs.
STRIKE_RANGES: dict[str, int | None] = {
    "atm5": 5,
    "atm10": 10,
    "atm20": 20,
    "all": None,
}

BASELINE_MODES = ("session_open", "prev_close")

#: Hard ceiling on legs fetched from Kite in one widening pass. A NIFTY weekly
#: chain is ~120 strikes; at 2 legs each that is 240 calls, well past anything
#: worth doing inside a request. Above this the window is trimmed around ATM and
#: ``meta.truncated`` says so.
MAX_WIDEN_LEGS = 160

#: Kite historical fetch concurrency. The published historical limit is 3
#: requests/second; three workers saturates it without tripping it.
WIDEN_WORKERS = 3

_CACHE_LOCK = threading.RLock()
#: ``(token, timeframe_min, session_date) -> list[candle dict]``
_WIDEN_CACHE: dict[tuple[int, int, str], list[dict[str, Any]]] = {}


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def defaults() -> dict[str, Any]:
    return {
        "underlyings": list(UNDERLYINGS),
        "timeframes_min": list(features.TIMEFRAMES_MIN),
        "timeframe_min": 5,
        "strike_ranges": list(STRIKE_RANGES),
        "strike_range": "atm10",
        "baseline_modes": list(BASELINE_MODES),
        "baseline_mode": "session_open",
        "refresh_seconds": 60,
        "max_widen_legs": MAX_WIDEN_LEGS,
        "archive_strike_width": dv_collector.STRIKE_WIDTH,
        "pct_thresholds": dict(features.PCT_THRESHOLDS),
        "cum_pct_threshold": features.CUM_PCT_THRESHOLD,
        "min_abs_oi": features.MIN_ABS_OI,
        "alert_ratio": features.ALERT_BREACH_RATIO,
    }


def _check_underlying(underlying: str) -> str:
    u = str(underlying or "").upper()
    if u not in UNDERLYINGS:
        raise ValueError(f"Unknown underlying {underlying!r}. Use {list(UNDERLYINGS)}")
    return u


def _resolve_session(underlying: str, session_date: str | None) -> date:
    """Chosen session, defaulting to the latest one actually archived.

    Defaulting to *today* renders an empty page every morning before the
    collector's first write, which reads as a broken desk — the delta-velocity
    store documents this and exposes ``latest_session`` for exactly this reason.
    """
    if session_date:
        try:
            return date.fromisoformat(session_date)
        except ValueError as exc:
            raise ValueError(f"Bad session_date {session_date!r}, expected YYYY-MM-DD") from exc
    latest = dv_store.latest_session(underlying)
    if latest is None:
        raise ValueError(f"No archived session for {underlying}. The collector has not written yet.")
    return latest


def sessions(underlying: str) -> list[str]:
    return [d.isoformat() for d in dv_store.sessions_available(_check_underlying(underlying))]


def _session_rows(underlying: str, session_date: date) -> list[dict[str, Any]]:
    return dv_store.to_rows(dv_store.load_session(underlying, session_date))


def expiries(underlying: str, session_date: str | None = None) -> list[str]:
    """Expiries present in that session's archive, nearest first."""
    u = _check_underlying(underlying)
    day = _resolve_session(u, session_date)
    seen = {str(r.get("expiry")) for r in _session_rows(u, day) if r.get("expiry")}
    return sorted(seen)


def _atm_from_rows(rows: list[dict[str, Any]], strikes: list[float]) -> tuple[float | None, float | None]:
    """Latest spot in the session and the strike nearest it.

    Uses the archived spot rather than a live quote so a historical session
    resolves its own ATM instead of today's.
    """
    spot: float | None = None
    latest: datetime | None = None
    for row in rows:
        ts = features.parse_ts(row.get("ts"))
        value = row.get("spot")
        if ts is None or value is None:
            continue
        if latest is None or ts >= latest:
            latest, spot = ts, float(value)
    if spot is None or not strikes:
        return spot, None
    return spot, min(strikes, key=lambda s: abs(s - spot))


def _window(strikes: list[float], atm: float | None, width: int | None) -> list[float]:
    """ATM +/- ``width`` strikes, on the ladder actually present."""
    if width is None or atm is None or not strikes:
        return strikes
    ordered = sorted(strikes)
    try:
        pivot = ordered.index(atm)
    except ValueError:
        pivot = min(range(len(ordered)), key=lambda i: abs(ordered[i] - atm))
    lo = max(0, pivot - width)
    hi = min(len(ordered), pivot + width + 1)
    return ordered[lo:hi]


def _prev_close_baselines(
    underlying: str, session_date: date, expiry: str | None
) -> tuple[dict[tuple[float, str], float | None], str | None]:
    """Anchor on the previous archived session's final OI per leg.

    Read from the archive rather than from a Kite previous-day candle so it
    stays consistent with the rest of the grid and keeps working after the
    contract expires. Returns ``(baselines, note)`` — ``note`` is non-None when
    there is nothing to anchor on, which the caller surfaces instead of quietly
    falling back to session-open and mislabelling the column.
    """
    available = [d for d in dv_store.sessions_available(underlying) if d < session_date]
    if not available:
        return {}, "No earlier archived session — previous-day close unavailable."

    prior = available[-1]
    rows = _session_rows(underlying, prior)
    out: dict[tuple[float, str], float | None] = {}
    seen_ts: dict[tuple[float, str], datetime] = {}
    for row in rows:
        if expiry and str(row.get("expiry")) != str(expiry):
            continue
        ts = features.parse_ts(row.get("ts"))
        strike = row.get("strike")
        opt = str(row.get("option_type") or "").upper()
        oi = row.get("oi")
        if ts is None or strike is None or oi is None or opt not in ("CE", "PE"):
            continue
        key = (float(strike), opt)
        if key not in seen_ts or ts >= seen_ts[key]:
            seen_ts[key] = ts
            out[key] = float(oi)

    if not out:
        return {}, f"Previous session {prior.isoformat()} holds no OI for this expiry."
    return out, None


def _session_open_baselines(
    rows: list[dict[str, Any]], expiry: str | None
) -> dict[tuple[float, str], float | None]:
    """First OI each leg shows in the session — its open."""
    out: dict[tuple[float, str], float | None] = {}
    seen_ts: dict[tuple[float, str], datetime] = {}
    for row in rows:
        if expiry and str(row.get("expiry")) != str(expiry):
            continue
        ts = features.parse_ts(row.get("ts"))
        strike = row.get("strike")
        opt = str(row.get("option_type") or "").upper()
        oi = row.get("oi")
        if ts is None or strike is None or oi is None or opt not in ("CE", "PE"):
            continue
        key = (float(strike), opt)
        if key not in seen_ts or ts < seen_ts[key]:
            seen_ts[key] = ts
            out[key] = float(oi)
    return out


# --------------------------------------------------------------------------
# Kite historical widening (Source B)
# --------------------------------------------------------------------------


def _timeframe_key(timeframe_min: int) -> str:
    return {5: "5min", 15: "15min", 30: "30min", 60: "60min"}[timeframe_min]


def _candles_to_rows(
    candles: list[dict[str, Any]],
    *,
    strike: float,
    option_type: str,
    expiry: str,
    timeframe_min: int,
) -> list[dict[str, Any]]:
    """Adapt Kite candles to the archive's row shape.

    Each candle is stamped with its **close** time. Kite labels a candle by its
    open, while :func:`features.bucket_end` maps a timestamp to the bucket that
    closes at or after it — so passing the open would land a 09:20 candle in the
    09:20 bucket, one column early.
    """
    rows: list[dict[str, Any]] = []
    for candle in candles:
        ts = candle.get("date")
        if isinstance(ts, str):
            ts = features.parse_ts(ts)
        if ts is None:
            continue
        rows.append(
            {
                "ts": (ts + timedelta(minutes=timeframe_min)).isoformat(),
                "expiry": expiry,
                "strike": strike,
                "option_type": option_type,
                "oi": candle.get("oi"),
                "ltp": candle.get("close"),
                "volume": candle.get("volume"),
                "spot": None,
            }
        )
    return rows


def _fetch_leg(token: int, timeframe_min: int, session_date: date) -> list[dict[str, Any]]:
    """One leg's candles for one session, memoised per (token, tf, day)."""
    key = (int(token), int(timeframe_min), session_date.isoformat())
    with _CACHE_LOCK:
        hit = _WIDEN_CACHE.get(key)
    if hit is not None:
        return hit

    from kite_client import fetch_historical_by_token

    start = datetime.combine(session_date, time(9, 0))
    end = datetime.combine(session_date, time(15, 40))
    frame = fetch_historical_by_token(
        int(token), _timeframe_key(timeframe_min), start, end, oi=True
    )
    out: list[dict[str, Any]] = []
    if frame is not None and not frame.empty:
        reset = frame.reset_index()
        stamp = reset.columns[0]
        for record in reset.to_dict("records"):
            out.append(
                {
                    "date": record.get(stamp),
                    "close": record.get("close"),
                    "volume": record.get("volume"),
                    "oi": record.get("oi"),
                }
            )
    with _CACHE_LOCK:
        _WIDEN_CACHE[key] = out
    return out


def _widen_rows(
    underlying: str,
    expiry: str,
    session_date: date,
    timeframe_min: int,
    wanted: list[float],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fetch the strikes the archive does not carry. Returns ``(rows, meta)``."""
    import concurrent.futures

    from options.chain import get_chain

    chain = get_chain(underlying, expiry)
    by_strike = {float(entry["strike"]): entry for entry in chain.get("strikes", [])}

    jobs: list[tuple[int, float, str]] = []
    missing_strikes: list[float] = []
    for strike in wanted:
        entry = by_strike.get(float(strike))
        if not entry:
            missing_strikes.append(strike)
            continue
        for opt in ("ce", "pe"):
            leg = entry.get(opt)
            if leg and leg.get("instrument_token"):
                jobs.append((int(leg["instrument_token"]), float(strike), opt.upper()))

    truncated = False
    if len(jobs) > MAX_WIDEN_LEGS:
        jobs = jobs[:MAX_WIDEN_LEGS]
        truncated = True

    rows: list[dict[str, Any]] = []
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=WIDEN_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_leg, token, timeframe_min, session_date): (strike, opt)
            for token, strike, opt in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            strike, opt = futures[future]
            try:
                candles = future.result()
            except Exception as exc:  # one dead token must not kill the grid
                failures += 1
                log_event(
                    logger,
                    logging.WARNING,
                    "chain_buildup widen leg failed",
                    underlying=underlying,
                    strike=strike,
                    option_type=opt,
                    error=str(exc),
                )
                continue
            rows.extend(
                _candles_to_rows(
                    candles,
                    strike=strike,
                    option_type=opt,
                    expiry=expiry,
                    timeframe_min=timeframe_min,
                )
            )

    return rows, {
        "legs_requested": len(jobs),
        "legs_failed": failures,
        "truncated": truncated,
        "strikes_not_listed": missing_strikes,
    }


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------


def get_grid(
    underlying: str = "NIFTY",
    *,
    expiry: str | None = None,
    session_date: str | None = None,
    timeframe_min: int = 5,
    strike_range: str = "atm10",
    baseline_mode: str = "session_open",
    widen: bool = True,
    pct_threshold: float | None = None,
    cum_pct_threshold: float = features.CUM_PCT_THRESHOLD,
    min_abs_oi: float = features.MIN_ABS_OI,
) -> dict[str, Any]:
    """The grid: one row per strike, CE and PE each carrying per-bucket delta-OI."""
    u = _check_underlying(underlying)
    if timeframe_min not in features.TIMEFRAMES_MIN:
        raise ValueError(
            f"Unsupported timeframe {timeframe_min}. Use {list(features.TIMEFRAMES_MIN)}"
        )
    if strike_range not in STRIKE_RANGES:
        raise ValueError(f"Unknown strike_range {strike_range!r}. Use {list(STRIKE_RANGES)}")
    if baseline_mode not in BASELINE_MODES:
        raise ValueError(f"Unknown baseline_mode {baseline_mode!r}. Use {list(BASELINE_MODES)}")

    day = _resolve_session(u, session_date)
    rows = _session_rows(u, day)
    if not rows:
        raise ValueError(f"Archive holds no rows for {u} on {day.isoformat()}.")

    chosen_expiry = str(expiry) if expiry else _nearest_archived_expiry(rows)
    scoped = [r for r in rows if str(r.get("expiry")) == chosen_expiry]
    if not scoped:
        raise ValueError(f"No archived legs for {u} {chosen_expiry} on {day.isoformat()}.")

    archive_strikes = sorted({float(r["strike"]) for r in scoped if r.get("strike") is not None})
    spot, atm = _atm_from_rows(scoped, archive_strikes)

    width = STRIKE_RANGES[strike_range]
    notes: list[str] = []
    widen_meta: dict[str, Any] = {}
    source = "archive"

    wanted = _resolve_wanted_strikes(u, chosen_expiry, archive_strikes, atm, width)
    to_widen = [s for s in wanted if s not in set(archive_strikes)]

    if to_widen and widen:
        extra, widen_meta = _widen_rows(u, chosen_expiry, day, timeframe_min, to_widen)
        if extra:
            scoped = scoped + extra
            source = "archive+kite_historical"
        if widen_meta.get("truncated"):
            notes.append(
                f"Strike window trimmed to {MAX_WIDEN_LEGS} fetched legs — widen the archive "
                f"(delta_velocity STRIKE_WIDTH) rather than raising this for a routine view."
            )
        if widen_meta.get("legs_failed"):
            notes.append(f"{widen_meta['legs_failed']} leg(s) failed to fetch and are blank.")
    elif to_widen:
        notes.append(
            f"{len(to_widen)} strike(s) lie outside the minute archive and were not fetched "
            f"(widen=false)."
        )
        wanted = [s for s in wanted if s in set(archive_strikes)]

    if baseline_mode == "prev_close":
        baselines, note = _prev_close_baselines(u, day, chosen_expiry)
        if note:
            notes.append(note)
    else:
        baselines = _session_open_baselines(scoped, chosen_expiry)

    grid = features.build_grid(
        scoped,
        timeframe_min=timeframe_min,
        expiry=chosen_expiry,
        baselines=baselines,
        strikes=wanted or None,
        atm=atm,
        pct_threshold=pct_threshold,
        cum_pct_threshold=cum_pct_threshold,
        min_abs_oi=min_abs_oi,
    )

    grid.update(
        {
            "underlying": u,
            "expiry": chosen_expiry,
            "session_date": day.isoformat(),
            "spot": spot,
            "atm": atm,
            "strike_range": strike_range,
            "baseline_mode": baseline_mode,
            "meta": {
                "source": source,
                "archive_strikes": len(archive_strikes),
                "rendered_strikes": len(grid["rows"]),
                "widen": widen_meta or None,
                "notes": notes,
                # The page only raises breach alerts for the live session --
                # toasting about a chain that stopped moving three days ago is
                # noise, and the grid happily renders any archived day.
                "is_live": day == _now_ist().date(),
                "generated_at": _now_ist().isoformat(timespec="seconds"),
            },
        }
    )
    return grid


def _nearest_archived_expiry(rows: list[dict[str, Any]]) -> str:
    seen = sorted({str(r.get("expiry")) for r in rows if r.get("expiry")})
    if not seen:
        raise ValueError("Archive rows carry no expiry.")
    return seen[0]


def _resolve_wanted_strikes(
    underlying: str,
    expiry: str,
    archive_strikes: list[float],
    atm: float | None,
    width: int | None,
) -> list[float]:
    """The strike ladder to render, which may reach past the archive.

    For a bounded window the ladder comes from the *listed* chain, not from the
    archive — otherwise "ATM +/- 20" silently means "the 11 the collector
    happened to hold" and the control lies about what it does.
    """
    if width is not None and width <= dv_collector.STRIKE_WIDTH:
        return _window(archive_strikes, atm, width)

    try:
        from options.chain import get_chain

        listed = sorted(float(e["strike"]) for e in get_chain(underlying, expiry).get("strikes", []))
    except Exception as exc:  # no instrument cache / no session — archive still renders
        log_event(
            logger,
            logging.WARNING,
            "chain_buildup could not read listed chain; falling back to archive strikes",
            underlying=underlying,
            expiry=expiry,
            error=str(exc),
        )
        return _window(archive_strikes, atm, width)

    if not listed:
        return _window(archive_strikes, atm, width)
    ladder = sorted(set(listed) | set(archive_strikes))
    return _window(ladder, atm, width)


def status(underlying: str = "NIFTY") -> dict[str, Any]:
    """What this desk can currently see.

    Its coverage *is* the delta-velocity archive's coverage — same files, same
    collector — so it reports that rather than pretending to own a feed.
    """
    u = _check_underlying(underlying)
    return {
        "source": "analysis/delta_velocity archive (shared; no separate collector)",
        "underlyings": list(UNDERLYINGS),
        "coverage": dv_store.coverage(u),
        "sessions": sessions(u),
        "defaults": defaults(),
    }


def reset_cache() -> None:
    with _CACHE_LOCK:
        _WIDEN_CACHE.clear()
