"""Chain build-up features — bucket per-leg OI into a strike x time grid.

Pure computation: no I/O, no Kite, no clock. Feed it the per-leg rows that
``analysis.delta_velocity.store.to_rows`` already produces (``ts``, ``strike``,
``option_type``, ``oi``, ``ltp``, ``spot``) and it returns the grid the desk
renders.

Two conventions worth stating once, because they are the whole reading of the
page:

**A bucket's OI is the last OI seen inside it, not an average.** Open interest
is a level, not a flow — averaging two levels produces a number that was never
true. This mirrors what Kite's own ``oi`` field on a candle means (OI at candle
close), so a grid built from the minute archive and one built from 5-minute
historical candles agree cell for cell.

**Delta-OI is against the previous bucket; the first bucket is against the
baseline.** That makes the row telescope exactly: the buckets sum to
``latest_oi - baseline``, so the per-bucket columns and the cumulative column
can never disagree. The alternative — every bucket measured against the
baseline — is offered as ``cum`` on the same cells rather than as a second
differencing scheme.

Classification is the standard four-quadrant read of delta-OI against
delta-price, applied per option (not per underlying), which is how an option
chain is normally read:

    OI up   + price up     -> long build-up
    OI up   + price down   -> short build-up
    OI down + price up     -> short covering
    OI down + price down   -> long unwinding
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

#: Bucket sizes the desk offers. Each is also a native Kite candle interval
#: (``config.KITE_INTERVALS``), so the historical widening path needs no
#: client-side rollup to agree with the archive path.
TIMEFRAMES_MIN: tuple[int, ...] = (5, 15, 30, 60)

IST = ZoneInfo("Asia/Kolkata")

#: NSE/BSE cash session open. Bucket edges anchor here, not at the first
#: snapshot — otherwise a collector that started late shifts every column label
#: and two sessions stop being comparable.
SESSION_START = "09:15"

LONG_BUILDUP = "long_buildup"
SHORT_BUILDUP = "short_buildup"
SHORT_COVERING = "short_covering"
LONG_UNWINDING = "long_unwinding"

#: Per-bucket |delta-OI %| above which a cell counts as breached, by timeframe.
#: Seeded from ``config.OI_TRACKER_DEFAULTS["pct_thresholds"]`` so the two desks
#: speak the same language at 5/15/30m; 60m is this desk's own extrapolation.
PCT_THRESHOLDS: dict[int, float] = {5: 8.0, 15: 15.0, 30: 25.0, 60: 35.0}

#: Cumulative |delta-OI %| against the baseline above which a *strike* counts as
#: breached. Deliberately not the same number as the per-bucket threshold: a 25%
#: move inside one bucket is extreme, while 25% accumulated by 15:00 is ordinary.
CUM_PCT_THRESHOLD = 40.0

#: Absolute floor, in contracts, that a move must clear before any percentage is
#: allowed to call it a breach. Without it the far wings dominate: a strike
#: holding 400 OI swings +/-50% on noise, and at "All strikes" the whole edge of
#: the grid lights up while the strikes that matter look calm.
MIN_ABS_OI = 25_000

#: Share of a column's live cells that must breach before the desk raises an
#: alert. Matches OI Tracker's ``alert_breach_ratio``.
ALERT_BREACH_RATIO = 0.5

#: Short codes the grid stamps in the cell corner.
CLASS_CODES = {
    LONG_BUILDUP: "LB",
    SHORT_BUILDUP: "SB",
    SHORT_COVERING: "SC",
    LONG_UNWINDING: "LU",
}


def classify(d_oi: float | None, d_price: float | None) -> str | None:
    """Four-quadrant tag for one cell, or None when either axis is flat/missing.

    A zero on either axis is deliberately *not* forced into a quadrant: "OI
    unchanged" and "price unchanged" are genuinely uninformative, and inventing
    a class for them would paint every dead strike with signal it does not
    carry.
    """
    if d_oi is None or d_price is None or d_oi == 0 or d_price == 0:
        return None
    if d_oi > 0:
        return LONG_BUILDUP if d_price > 0 else SHORT_BUILDUP
    return SHORT_COVERING if d_price > 0 else LONG_UNWINDING


def is_breach(
    pct: float | None,
    absolute: float | None,
    *,
    pct_threshold: float,
    min_abs_oi: float = MIN_ABS_OI,
) -> bool:
    """True when a move clears both the percentage *and* the absolute floor.

    Both, not either. A percentage alone flags noise on thin strikes; an
    absolute alone flags every ATM tick on a liquid one. The pair is what makes
    the mark mean "this is a real move, and it is large for this contract".
    """
    if pct is None or absolute is None:
        return False
    return abs(pct) > pct_threshold and abs(absolute) >= min_abs_oi


def parse_ts(value: Any) -> datetime | None:
    """Parse a timestamp to **naive IST wall-clock**.

    The two sources disagree on tzinfo: the minute archive writes
    ``+05:30``-aware stamps, while a Kite candle can arrive naive depending on
    how pandas carried its index. Bucketing subtracts one timestamp from
    another, and mixing the two raises ``TypeError`` the moment a widened grid
    merges them — so both are normalised here, at the single parse boundary,
    rather than at each call site. Aware stamps are converted to IST before the
    tz is dropped; naive ones are already IST, which is the only wall clock
    either source speaks.
    """
    if isinstance(value, datetime):
        out = value
    elif not value:
        return None
    else:
        try:
            out = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if out.tzinfo is not None:
        out = out.astimezone(IST).replace(tzinfo=None)
    return out


def _session_start_dt(ref: datetime, session_start: str = SESSION_START) -> datetime:
    hh, _, mm = str(session_start).partition(":")
    return ref.replace(hour=int(hh), minute=int(mm or 0), second=0, microsecond=0)


def bucket_end(ts: datetime, timeframe_min: int, start: datetime) -> datetime:
    """Right edge of the bucket ``ts`` falls in, anchored at the session open.

    A tick exactly on an edge belongs to the bucket that edge closes — 09:20:00
    is the last tick of the 09:15-09:20 bucket, not the first of the next one —
    the same half-open convention a candle uses.
    """
    delta_min = (ts - start).total_seconds() / 60.0
    if delta_min <= 0:
        return start + timedelta(minutes=timeframe_min)
    index = int((delta_min - 1e-9) // timeframe_min) + 1
    return start + timedelta(minutes=index * timeframe_min)


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out  # drop NaN


def _pct(delta: float | None, base: float | None) -> float | None:
    if delta is None or base is None or base == 0:
        return None
    return round(delta / abs(base) * 100.0, 2)


def bucket_legs(
    rows: list[dict[str, Any]],
    *,
    timeframe_min: int,
    expiry: str | None = None,
    session_start: str = SESSION_START,
) -> tuple[
    dict[tuple[float, str], dict[datetime, dict[str, Any]]],
    list[datetime],
    dict[datetime, float | None],
]:
    """Collapse per-minute rows into last-value-per-bucket, per (strike, type).

    Returns ``(series, bucket_ends, spot_by_bucket)``. Rows arrive in file order
    (the archive is append-only per minute), but this does not rely on that —
    each bucket keeps the row with the greatest ``ts``, so an out-of-order or
    replayed line cannot rewrite a bucket backwards.
    """
    series: dict[tuple[float, str], dict[datetime, dict[str, Any]]] = defaultdict(dict)
    spot_seen: dict[datetime, tuple[datetime, float | None]] = {}
    start: datetime | None = None
    ends: set[datetime] = set()

    for row in rows:
        if expiry and str(row.get("expiry")) != str(expiry):
            continue
        ts = parse_ts(row.get("ts"))
        strike = _num(row.get("strike"))
        opt = str(row.get("option_type") or "").upper()
        if ts is None or strike is None or opt not in ("CE", "PE"):
            continue
        if start is None:
            start = _session_start_dt(ts, session_start)
        end = bucket_end(ts, timeframe_min, start)
        ends.add(end)

        prior = series[(strike, opt)].get(end)
        if prior is None or ts >= prior["ts"]:
            series[(strike, opt)][end] = {
                "ts": ts,
                "oi": _num(row.get("oi")),
                "ltp": _num(row.get("ltp")),
                "volume": _num(row.get("volume")),
            }

        spot_prior = spot_seen.get(end)
        if spot_prior is None or ts >= spot_prior[0]:
            spot_seen[end] = (ts, _num(row.get("spot")))

    return series, sorted(ends), {end: val for end, (_, val) in spot_seen.items()}


def _side_row(
    buckets: list[datetime],
    per_bucket: dict[datetime, dict[str, Any]],
    baseline: float | None,
    *,
    pct_threshold: float,
    cum_threshold: float,
    min_abs_oi: float,
) -> dict[str, Any]:
    """One side (CE or PE) of one strike: baseline, per-bucket cells, totals.

    ``prev_oi`` walks forward across gaps rather than resetting: when a strike
    drops out of the collector's ATM window for a stretch and returns, the
    delta-OI on the returning bucket covers the whole absence. That is honest —
    the OI did move over that span — and it keeps the telescoping property.
    """
    cells: list[dict[str, Any]] = []
    prev_oi = baseline
    prev_ltp: float | None = None
    latest_oi: float | None = None
    latest_ltp: float | None = None

    for end in buckets:
        point = per_bucket.get(end)
        if point is None:
            cells.append(
                {
                    "oi": None,
                    "d_oi": None,
                    "d_oi_pct": None,
                    "cum": None,
                    "cum_pct": None,
                    "ltp": None,
                    "d_price": None,
                    "volume": None,
                    "cls": None,
                    "breach": False,
                }
            )
            continue

        oi = point["oi"]
        ltp = point["ltp"]
        d_oi = None if (oi is None or prev_oi is None) else oi - prev_oi
        d_price = None if (ltp is None or prev_ltp is None) else round(ltp - prev_ltp, 4)
        cum = None if (oi is None or baseline is None) else oi - baseline

        cells.append(
            {
                "oi": oi,
                "d_oi": d_oi,
                "d_oi_pct": _pct(d_oi, prev_oi),
                "cum": cum,
                "cum_pct": _pct(cum, baseline),
                "ltp": ltp,
                "d_price": d_price,
                "volume": point.get("volume"),
                "cls": classify(d_oi, d_price),
                "breach": is_breach(
                    _pct(d_oi, prev_oi),
                    d_oi,
                    pct_threshold=pct_threshold,
                    min_abs_oi=min_abs_oi,
                ),
            }
        )

        if oi is not None:
            prev_oi = oi
            latest_oi = oi
        if ltp is not None:
            prev_ltp = ltp
            latest_ltp = ltp

    total = None if (latest_oi is None or baseline is None) else latest_oi - baseline
    total_pct = _pct(total, baseline)
    return {
        "baseline": baseline,
        "latest_oi": latest_oi,
        "latest_ltp": latest_ltp,
        "total_delta": total,
        "total_delta_pct": total_pct,
        # Strike-level: has this contract been written or unwound hard *today*,
        # against its own opening book? Independent of any single bucket.
        "breach": is_breach(
            total_pct, total, pct_threshold=cum_threshold, min_abs_oi=min_abs_oi
        ),
        "cells": cells,
    }


def _percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile. Small, dependency-free, and enough for a scale."""
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((pct / 100.0) * (len(ordered) - 1)))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def _scale(cells: list[dict[str, Any]], key: str) -> dict[str, float | None]:
    """Shading scale for one side.

    p95 rather than max: a single expiry-day print can be an order of magnitude
    above everything else, and scaling to it washes the whole grid to white.
    The UI clamps above p95 so the outlier still reads as full intensity.
    """
    mags = [abs(c[key]) for c in cells if c.get(key) is not None and c[key] != 0]
    return {
        "p95": _percentile(mags, 95.0),
        "p50": _percentile(mags, 50.0),
        "max": max(mags) if mags else None,
    }


def _implied_baseline(
    bucket_ends: list[datetime], per_bucket: dict[datetime, dict[str, Any]]
) -> float | None:
    """First OI this leg shows, used when no explicit anchor exists.

    Its own first bucket then has ``d_oi == 0`` rather than a spurious jump off
    a baseline the leg was never measured against.
    """
    for end in bucket_ends:
        point = per_bucket.get(end)
        if point and point.get("oi") is not None:
            return point["oi"]
    return None


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Chain-level sums. PCR is on OI, the convention the other desks here use."""
    ce_oi = sum(r["ce"]["latest_oi"] or 0 for r in rows)
    pe_oi = sum(r["pe"]["latest_oi"] or 0 for r in rows)
    ce_delta = sum(r["ce"]["total_delta"] or 0 for r in rows)
    pe_delta = sum(r["pe"]["total_delta"] or 0 for r in rows)
    return {
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "ce_delta": ce_delta,
        "pe_delta": pe_delta,
        "pcr_oi": round(pe_oi / ce_oi, 4) if ce_oi else None,
        "pcr_delta": round(pe_delta / ce_delta, 4) if ce_delta else None,
        "strikes": len(rows),
    }


def latest_bucket_alert(
    rows: list[dict[str, Any]],
    bucket_count: int,
    *,
    ratio_threshold: float = ALERT_BREACH_RATIO,
) -> dict[str, Any]:
    """Breach concentration in the **most recent bucket only**, per side.

    OI Tracker ratios over its whole board because that board is four intervals
    wide. This grid is a whole session wide, so the same ratio taken over every
    cell converges to a session average and stops reacting to anything. Taken
    over the newest column it answers the question an alert should answer: is
    the chain being written *right now*.

    The denominator counts only cells that carry a delta -- a strike blank in
    this bucket (outside the collector's window at capture time) is absent, not
    calm, and letting it dilute the ratio would mute the alert exactly when the
    grid is widest.
    """
    out: dict[str, Any] = {"bucket_index": bucket_count - 1 if bucket_count else None}
    for side in ("ce", "pe"):
        breached = live = 0
        if bucket_count:
            for row in rows:
                cell = row[side]["cells"][bucket_count - 1]
                if cell.get("d_oi") is None:
                    continue
                live += 1
                if cell.get("breach"):
                    breached += 1
        ratio = (breached / live) if live else 0.0
        out[side] = {
            "breached": breached,
            "cells": live,
            "ratio": round(ratio, 4),
            "alert": bool(live) and ratio > ratio_threshold,
        }
    out["ratio_threshold"] = ratio_threshold
    return out


def build_grid(
    rows: list[dict[str, Any]],
    *,
    timeframe_min: int,
    expiry: str | None = None,
    baselines: dict[tuple[float, str], float | None] | None = None,
    strikes: list[float] | None = None,
    atm: float | None = None,
    session_start: str = SESSION_START,
    pct_threshold: float | None = None,
    cum_pct_threshold: float = CUM_PCT_THRESHOLD,
    min_abs_oi: float = MIN_ABS_OI,
) -> dict[str, Any]:
    """Strike x time-bucket delta-OI grid, CE and PE side by side.

    ``baselines`` maps ``(strike, "CE"|"PE")`` to the anchor OI — session-open
    or previous-day close, chosen by the caller. Where it has no entry the first
    bucket carrying OI for that leg becomes the anchor, so a strike that only
    entered the window mid-session still renders instead of dropping out.
    """
    if timeframe_min not in TIMEFRAMES_MIN:
        raise ValueError(f"Unsupported timeframe {timeframe_min}. Use {list(TIMEFRAMES_MIN)}")

    if pct_threshold is None:
        pct_threshold = PCT_THRESHOLDS[timeframe_min]

    series, bucket_ends, spot_by_bucket = bucket_legs(
        rows, timeframe_min=timeframe_min, expiry=expiry, session_start=session_start
    )
    baselines = baselines or {}

    all_strikes = sorted({strike for strike, _ in series})
    if strikes is not None:
        wanted = {float(s) for s in strikes}
        all_strikes = [s for s in all_strikes if s in wanted]

    out_rows: list[dict[str, Any]] = []
    ce_cells: list[dict[str, Any]] = []
    pe_cells: list[dict[str, Any]] = []

    for strike in all_strikes:
        row: dict[str, Any] = {"strike": strike, "atm": atm is not None and strike == atm}
        for opt, sink in (("CE", ce_cells), ("PE", pe_cells)):
            per_bucket = series.get((strike, opt), {})
            base = baselines.get((strike, opt))
            if base is None:
                base = _implied_baseline(bucket_ends, per_bucket)
            side = _side_row(
                bucket_ends,
                per_bucket,
                base,
                pct_threshold=pct_threshold,
                cum_threshold=cum_pct_threshold,
                min_abs_oi=min_abs_oi,
            )
            row[opt.lower()] = side
            sink.extend(side["cells"])
        out_rows.append(row)

    return {
        "timeframe_min": timeframe_min,
        "buckets": [
            {
                "key": end.strftime("%H:%M"),
                "end": end.isoformat(),
                "spot": spot_by_bucket.get(end),
            }
            for end in bucket_ends
        ],
        "rows": out_rows,
        "scale": {
            "ce": {"delta": _scale(ce_cells, "d_oi"), "cum": _scale(ce_cells, "cum")},
            "pe": {"delta": _scale(pe_cells, "d_oi"), "cum": _scale(pe_cells, "cum")},
        },
        "totals": _totals(out_rows),
        "class_codes": CLASS_CODES,
        "thresholds": {
            "pct": pct_threshold,
            "cum_pct": cum_pct_threshold,
            "min_abs_oi": min_abs_oi,
            "alert_ratio": ALERT_BREACH_RATIO,
        },
        "alert": latest_bucket_alert(out_rows, len(bucket_ends)),
    }
