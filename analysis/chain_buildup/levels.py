"""Gamma reference levels for the Chain Build-Up ladder.

Resolves the five levels the desk overlays on its strike ladder — call wall, put
wall, pin, gamma flip, futures POC — for one underlying and session.

Deliberately a **separate module behind a separate endpoint**, never folded into
``/buildup/grid``. The grid is a pure read of the delta-velocity archive: no Kite
call, no rate-limit exposure, offline in tests. ``gamma_levels`` reaches
``build_gamma_snapshot`` -> ``oi_movers`` -> Kite historical, which is the path
that once cost 80 seconds on a single page load. Keeping them apart means a
gamma outage greys one panel instead of blanking the grid, and the grid's cost
profile stays what its docstring claims.

Two kinds of level, and the distinction is not cosmetic
------------------------------------------------------
**Strike levels** — call wall, put wall, pin — *are* strikes. They land on a row.

**Price levels** — flip, futures POC — are continuous prices that generally fall
*between* two strikes. Snapping them to the nearest row would move them by up to
half a strike step and assert a precision the number does not have, so they are
returned as a price plus the two strikes they sit between, for the page to draw
as a rule rather than a row.

Live against archived
---------------------
Only the live session can produce all five. For an archived day:

* walls were not recorded before 2026-08-27 (see
  ``gamma_density_history.append_history_point``), so older sessions have none;
* pin and flip come from the ``daily_pin`` samples, which start 2026-08-21;
* POC comes from the tilt-history trail, which covers only what was sampled.

Where a level cannot be resolved for the session asked for, it is **omitted with
a reason** rather than filled in from today. Drawing today's call wall on a
two-week-old ladder is not a small inaccuracy — it invites reading a level into a
session that never had it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from utils.logging import get_logger, log_event

IST = ZoneInfo("Asia/Kolkata")
logger = get_logger("chain_buildup.levels")

#: ``key`` -> (label, kind). Order is the order the page lists them.
LEVEL_SPECS: tuple[tuple[str, str, str], ...] = (
    ("call_wall", "Call Wall", "strike"),
    ("put_wall", "Put Wall", "strike"),
    ("pin", "Pin", "strike"),
    ("flip", "Gamma Flip", "price"),
    ("fut_poc", "Fut POC", "price"),
)

#: A pin is only a *gamma* pin when the gamma desk says its source is dominant.
#: Anything else — ``wall_mid`` most often — is a derived midpoint, and labelling
#: that "PIN" on a strike ladder asserts something the data does not support.
STRONG_PIN_SOURCES = ("dominant",)


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _bracket(price: float | None, strikes: list[float]) -> tuple[float | None, float | None]:
    """The two ladder strikes a price sits between, for drawing a rule."""
    if price is None or not strikes:
        return None, None
    ordered = sorted(strikes)
    below = [s for s in ordered if s <= price]
    above = [s for s in ordered if s >= price]
    return (below[-1] if below else None, above[0] if above else None)


def _entry(
    key: str,
    label: str,
    kind: str,
    price: float | None,
    strikes: list[float],
    *,
    source: str,
    note: str | None = None,
) -> dict[str, Any]:
    lower, upper = _bracket(price, strikes)
    return {
        "key": key,
        "label": label,
        "kind": kind,
        "price": price,
        # Strike levels carry the row they land on; price levels carry the pair
        # they sit between and deliberately no single strike.
        "strike": price if kind == "strike" else None,
        "between": [lower, upper] if kind == "price" else None,
        "in_ladder": bool(price is not None and lower is not None and upper is not None),
        "source": source,
        "note": note,
    }


def _live_gamma(underlying: str) -> tuple[dict[str, Any], str | None]:
    """Live gamma levels, via the volume-profile desk's shared cache.

    Reuses ``analysis.volume_profile.service.gamma_levels`` rather than calling
    ``build_gamma_snapshot`` here, for two reasons: its 45-second cache is shared
    with the footprint desk so a second consumer is close to free, and it passes
    ``include_history=False`` so serving a page cannot append to the gamma trail
    or move a pin the sampler already recorded.
    """
    try:
        from analysis.volume_profile.service import gamma_levels

        payload = gamma_levels(underlying)
    except Exception as exc:
        log_event(logger, logging.WARNING, "chain_buildup_gamma_levels_failed",
                  underlying=underlying, error=str(exc)[:200])
        return {}, "gamma_unavailable"
    if not payload.get("available"):
        return {}, str(payload.get("reason") or "gamma_unavailable")
    return payload, None


def _historical_gamma(underlying: str, session_date: date) -> tuple[dict[str, Any], str | None]:
    """Pin and flip for a past session, from the daily_pin samples.

    Uses the session's **last** sample rather than an average: these levels are
    a state, not a statistic, and the closing state is the one a reader means by
    "where was the flip that day". Walls are absent by construction for any
    session before they started being recorded.
    """
    try:
        from options.gamma_density_history import _load  # noqa: PLC2701 — read-only

        store = _load(strict=False)
    except Exception as exc:
        log_event(logger, logging.WARNING, "chain_buildup_gamma_history_failed",
                  underlying=underlying, error=str(exc)[:200])
        return {}, "gamma_history_unavailable"

    rows = (store.get("daily_pin") or {}).get(underlying.upper()) or []
    target = session_date.isoformat()
    day_row = next((r for r in rows if str(r.get("date")) == target), None)
    if not day_row:
        return {}, "no_gamma_history_for_session"
    samples = day_row.get("samples") or []
    if not samples:
        return {}, "no_gamma_history_for_session"

    last = samples[-1]
    return {
        "pin": _num(last.get("pin")),
        "pin_source": last.get("pin_source"),
        "flip": _num(last.get("flip_level")),
        "call_wall": _num(last.get("call_wall")),
        "put_wall": _num(last.get("put_wall")),
        "gamma_regime": last.get("gamma_regime"),
        "asof": last.get("t"),
    }, None


def _fut_poc(underlying: str, session_date: date, is_live: bool) -> tuple[float | None, str | None]:
    """Closing POC of the session's trail, or None with a reason."""
    try:
        from analysis.volume_profile.service import poc_trail

        trail = poc_trail(underlying, day=None if is_live else session_date.isoformat())
    except Exception as exc:
        log_event(logger, logging.WARNING, "chain_buildup_poc_failed",
                  underlying=underlying, error=str(exc)[:200])
        return None, "poc_unavailable"
    if not trail.get("available"):
        return None, str(trail.get("reason") or "poc_unavailable")
    segments = trail.get("segments") or []
    if not segments:
        return None, "no_trail_yet"
    return _num(segments[-1].get("poc")), None


def resolve(
    underlying: str,
    session_date: date,
    *,
    strikes: list[float] | None = None,
    grid_expiry: str | None = None,
) -> dict[str, Any]:
    """The five levels for this underlying and session.

    ``strikes`` is the ladder the page is rendering, used only to bracket the
    price levels and to say whether a level is even on screen. ``grid_expiry``
    is compared against the expiry the gamma desk resolved: a front-expiry call
    wall drawn on a 30-DTE ladder is the wrong number, so a mismatch is reported
    rather than silently overlaid.
    """
    u = str(underlying).upper()
    strikes = sorted(strikes or [])
    is_live = session_date == _now_ist().date()

    levels: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    gamma_expiry: str | None = None
    asof: str | None = None
    regime: str | None = None

    if is_live:
        payload, reason = _live_gamma(u)
        source = "live"
    else:
        payload, reason = _historical_gamma(u, session_date)
        source = "history"

    if reason:
        for key, _label, _kind in LEVEL_SPECS:
            if key != "fut_poc":
                skipped[key] = reason
    else:
        gamma_expiry = payload.get("expiry")
        asof = payload.get("asof")
        regime = payload.get("gamma_regime")
        for key, label, kind in LEVEL_SPECS:
            if key == "fut_poc":
                continue
            price = _num(payload.get(key))
            if price is None:
                skipped[key] = (
                    "not_recorded_this_session" if source == "history" else "unavailable"
                )
                continue
            note = None
            if key == "pin":
                pin_source = str(payload.get("pin_source") or "")
                note = (
                    None
                    if pin_source in STRONG_PIN_SOURCES
                    else f"derived ({pin_source or 'unknown'}), not a dominant-gamma pin"
                )
            levels.append(_entry(key, label, kind, price, strikes, source=source, note=note))

    poc, poc_reason = _fut_poc(u, session_date, is_live)
    if poc is None:
        skipped["fut_poc"] = poc_reason or "unavailable"
    else:
        levels.append(
            _entry("fut_poc", "Fut POC", "price", poc, strikes,
                   source="live" if is_live else "history")
        )

    expiry_match = (
        None if (gamma_expiry is None or grid_expiry is None) else str(gamma_expiry) == str(grid_expiry)
    )
    return {
        "underlying": u,
        "session_date": session_date.isoformat(),
        "is_live": is_live,
        "source": source,
        "asof": asof,
        "gamma_regime": regime,
        "gamma_expiry": gamma_expiry,
        "grid_expiry": grid_expiry,
        # False means the levels belong to a different expiry than the ladder —
        # the page greys them rather than implying they apply.
        "expiry_match": expiry_match,
        "levels": levels,
        "skipped": skipped,
    }
