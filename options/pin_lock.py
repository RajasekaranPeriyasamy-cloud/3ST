"""Pin strength — is dealer hedging actually holding price at the pin?

A pin is a magnet with measurable strength and nameable failure conditions, not a
lock. This module reports **hard gates plus components, deliberately no blended
score**: the weights a composite would need cannot be justified until the
``daily_pin`` calibration trail (see :mod:`options.gamma_density_history`) has
accumulated enough sessions to fit them. A confident-looking number resting on
guessed weights is worse than five honest readings.

The five conditions that pin price, and where each is read from:

1. **Dealers long gamma** — hard gate. Positive GEX means dealers sell rallies and
   buy dips. Under negative gamma they chase, and there is no pin at any strike.
2. **A real gamma pin** — hard gate on ``pin_source == "dominant"``. The ``atm``
   fallback sits next to spot by construction, so it looks steady precisely when
   no pin exists.
3. **Spot contained near the pin** — measured over **minute** rows, not the sparse
   GEX ticks, because the tick trail is gappy by design and would understate time
   spent away from the pin.
4. **Flip level far away** — if the flip sits inside 1σ, one push flips the regime
   and the pin dies with it.
5. **The wall is not eroding** — ΔOI at the pin strike. Writing reinforces, unwind
   dissolves. This one *leads*; gamma concentration *lags*.

Plus the mechanism itself: how often spot **crosses** the pin. A pinned market
oscillates across the strike; a failing pin drifts away monotonically.

Pure functions only — no I/O, no store imports — so the whole thing is testable
offline against synthetic history.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

PinWindow = Literal["15m", "30m", "60m", "session"]

#: Window label → minutes. ``session`` (None) means everything recorded today.
PIN_WINDOWS: dict[str, int | None] = {"15m": 15, "30m": 30, "60m": 60, "session": None}
DEFAULT_PIN_WINDOW: PinWindow = "30m"

#: Spot counts as "at the pin" within this many strike steps.
PIN_CONTAINMENT_STEPS = 1.0
#: Share of window ticks that must show positive GEX for the long-gamma gate.
PIN_LONG_GAMMA_SHARE = 0.8
#: Flip must sit at least this many σ away for the pin to have room to work.
PIN_FLIP_ROOM_SIGMA = 1.0


def normalize_pin_window(window: str | None) -> PinWindow:
    w = str(window or DEFAULT_PIN_WINDOW).strip().lower()
    return w if w in PIN_WINDOWS else DEFAULT_PIN_WINDOW  # type: ignore[return-value]


def _parse_ts(value: Any) -> datetime | None:
    """Local copy so this module stays free of store imports (see module docstring)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out


def _rows_in_window(
    rows: list[dict[str, Any]] | None,
    minutes: int | None,
    *,
    now: datetime | None,
) -> tuple[list[dict[str, Any]], datetime | None]:
    """Rows whose timestamp falls inside the trailing window.

    ``now`` defaults to the newest timestamp present rather than wall clock, so a
    stale snapshot yields an empty window instead of silently scoring old data as
    current, and tests stay deterministic.
    """
    timed: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows or []:
        ts = _parse_ts(row.get("t"))
        if ts is None and row.get("ts_ms") is not None:
            try:
                ts = datetime.fromtimestamp(int(row["ts_ms"]) / 1000.0, tz=IST)
            except (TypeError, ValueError, OSError, OverflowError):
                ts = None
        if ts is not None:
            timed.append((ts, row))
    if not timed:
        return [], now
    timed.sort(key=lambda x: x[0])
    ref = now or timed[-1][0]
    if minutes is None:
        return [r for _, r in timed], ref
    cutoff = ref - timedelta(minutes=int(minutes))
    return [r for ts, r in timed if ts >= cutoff], ref


def _modal_pin(rows: list[dict[str, Any]], spot: float | None) -> float | None:
    """Most frequent pin in the window; ties broken by proximity to spot."""
    counts: dict[float, int] = {}
    for row in rows:
        pin = _f(row.get("pin_strike"))
        if pin is None:
            continue
        counts[pin] = counts.get(pin, 0) + 1
    if not counts:
        return None
    best = max(counts.values())
    tied = [k for k, v in counts.items() if v == best]
    if len(tied) == 1 or spot is None:
        return tied[0]
    return min(tied, key=lambda k: abs(k - float(spot)))


def _pin_doi(strikes: list[dict[str, Any]] | None, pin: float | None) -> tuple[int | None, str | None]:
    """Net ΔOI on the pin strike, and whether the wall is building or eroding."""
    if pin is None:
        return None, None
    for row in strikes or []:
        k = _f(row.get("strike"))
        if k is None or abs(k - pin) > 1e-9:
            continue
        ce, pe = row.get("ce_doi"), row.get("pe_doi")
        if ce is None and pe is None:
            return None, None
        total = int((_f(ce) or 0.0) + (_f(pe) or 0.0))
        if total > 0:
            return total, "writing"
        if total < 0:
            return total, "unwinding"
        return total, "flat"
    return None, None


def _containment_and_crossings(
    minute_rows: list[dict[str, Any]],
    pin: float | None,
    tol: float,
) -> tuple[float | None, int | None, float | None]:
    """(% of minutes within tol of pin, crossings, crossings per hour)."""
    if pin is None:
        return None, None, None
    spots: list[float] = []
    for row in minute_rows:
        s = _f(row.get("spot"))
        if s is not None:
            spots.append(s)
    if not spots:
        return None, None, None

    inside = sum(1 for s in spots if abs(s - pin) <= tol + 1e-9)
    containment = round(100.0 * inside / len(spots), 1)

    crossings = 0
    prev_side: int | None = None
    for s in spots:
        side = 1 if s > pin else (-1 if s < pin else 0)
        if side == 0:
            continue
        if prev_side is not None and side != prev_side:
            crossings += 1
        prev_side = side
    per_hour = round(crossings / (len(spots) / 60.0), 2) if len(spots) >= 2 else None
    return containment, crossings, per_hour


def compute_pin_lock(
    *,
    pin_strike: float | None,
    pin_source: str | None,
    spot: float | None,
    strike_step: float,
    history: list[dict[str, Any]] | None = None,
    chart_series: list[dict[str, Any]] | None = None,
    strikes: list[dict[str, Any]] | None = None,
    flip_level: float | None = None,
    sigma1_pts: float | None = None,
    window: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Gates + components for pin strength over a trailing window.

    Returns ``None`` for anything that cannot be measured. An unknown gate is
    ``None``, never ``False`` — "we could not tell" and "it failed" are different
    answers, and conflating them would make a quiet desk look like a broken pin.
    """
    window_key = normalize_pin_window(window)
    minutes = PIN_WINDOWS[window_key]
    step = max(float(strike_step or 0.0), 1.0)
    tol = step * PIN_CONTAINMENT_STEPS

    ticks, ref = _rows_in_window(history, minutes, now=now)
    minute_rows, _ = _rows_in_window(chart_series, minutes, now=ref)

    pin_mode = _modal_pin(ticks, spot)
    pin = pin_mode if pin_mode is not None else _f(pin_strike)

    # --- gates -----------------------------------------------------------
    is_dominant = None if not pin_source else pin_source == "dominant"

    gex_signs = [_f(t.get("total_gex")) for t in ticks]
    gex_signs = [g for g in gex_signs if g is not None]
    if gex_signs:
        long_share = round(sum(1 for g in gex_signs if g > 0) / len(gex_signs), 3)
        dealers_long = long_share >= PIN_LONG_GAMMA_SHARE
    else:
        long_share, dealers_long = None, None

    gates_known = is_dominant is not None and dealers_long is not None
    gates_passed = bool(is_dominant and dealers_long) if gates_known else None

    # --- components ------------------------------------------------------
    pin_values = [p for p in (_f(t.get("pin_strike")) for t in ticks) if p is not None]
    if pin_values and pin is not None:
        stable = sum(1 for p in pin_values if abs(p - pin) <= step + 1e-9)
        stability = round(100.0 * stable / len(pin_values), 1)
    else:
        stability = None

    containment, crossings, crossings_ph = _containment_and_crossings(minute_rows, pin, tol)

    spot_f = _f(spot)
    flip_f = _f(flip_level)
    sigma = _f(sigma1_pts)
    if spot_f is not None and flip_f is not None and sigma and sigma > 0:
        flip_room = round(abs(spot_f - flip_f) / sigma, 2)
    else:
        flip_room = None

    doi, doi_dir = _pin_doi(strikes, pin)

    # --- breaker ---------------------------------------------------------
    if flip_f is not None and spot_f is not None:
        above = flip_f > spot_f
        breaker = {
            "level": round(flip_f, 2),
            "direction": "above" if above else "below",
            "label": f"gamma flips {'above' if above else 'below'} {flip_f:,.0f}",
        }
    else:
        breaker = {"level": None, "direction": None, "label": "no gamma flip in the scanned window"}

    # --- why not, in plain language --------------------------------------
    reasons: list[str] = []
    if is_dominant is False:
        reasons.append(f"{pin_source} pin is not a gamma pin")
    if dealers_long is False:
        reasons.append("dealers are short gamma")
    if is_dominant is None or dealers_long is None:
        reasons.append("not enough session history yet")
    if doi_dir == "unwinding":
        reasons.append("pin OI is unwinding")

    return {
        "window": window_key,
        "window_minutes": minutes,
        "pin": pin,
        "pin_mode": pin_mode,
        "pin_source": pin_source,
        "gates": {
            "pin_is_dominant": is_dominant,
            "dealers_long_gamma": dealers_long,
            "long_gamma_share": long_share,
            "passed": gates_passed,
        },
        "components": {
            "stability_pct": stability,
            "containment_pct": containment,
            "containment_steps": PIN_CONTAINMENT_STEPS,
            "crossings": crossings,
            "crossings_per_hour": crossings_ph,
            "flip_room_sigma": flip_room,
            "flip_room_ok": None if flip_room is None else flip_room >= PIN_FLIP_ROOM_SIGMA,
            "pin_doi": doi,
            "pin_doi_direction": doi_dir,
        },
        "breaker": breaker,
        "samples": {"ticks": len(ticks), "minutes": len(minute_rows)},
        "reasons": reasons,
    }
