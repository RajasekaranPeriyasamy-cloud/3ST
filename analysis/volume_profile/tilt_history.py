"""Session tilt history for the Volume Footprint desk — and how to compare it.

Tilt is ``100 * (buy - sell) / (buy + sell)`` in percentage points
(:func:`vendor.volume_footprint.metrics.balance_tilt`). The engine's own
docstring is what makes a history worth keeping: tilt is delta expressed as a
*share* of volume, so unlike raw delta it is comparable across sessions.

Three properties this module is responsible for holding
------------------------------------------------------

**Never compare a partial session against completed ones.** Tilt at 09:30 is
computed from ~15 bars; a finished session has ~385. Early-session tilt is noisy
and mean-reverts as volume accumulates, so ranking today-at-09:30 against thirty
*closing* tilts produces a confident number that means nothing. Every session is
therefore stored as a **curve** keyed by elapsed session minute, and
:func:`compare_current` only ever ranks today against prior sessions **at the
same checkpoint**.

**Tilt is a model output, not measured flow.** Only the Geometric engine is
reachable in this repo, so buy/sell are inferred from where each candle closed in
its range. A tilt history is internally consistent — same engine, same settings,
every session — but it is not thirty days of order flow, and the desk's
``estimate: True`` discipline carries over unchanged.

**Backfilled and live sessions are labelled, never blended silently.** ``source``
is ``"live"`` (sampled through the session as it happened) or ``"backfill"``
(recomputed later from historical minute bars). They are equally valid, but a
reader deciding how much to trust a percentile deserves to know which they are
looking at — especially since backfill is only possible for as long as the
contract stays listed (see ``scripts/backfill_volume_tilt.py``).
"""

from __future__ import annotations

import json
import logging
import threading
import time as time_mod
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from settings import data_dir
from utils.logging import get_logger, log_event

IST = ZoneInfo("Asia/Kolkata")
_log = get_logger("volume_tilt_history")

#: Only these two are sampled. Adding an underlying here starts a background
#: Kite pull per checkpoint for it — deliberately not driven off INDEX_OPTIONS.
TILT_HISTORY_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "SENSEX")

#: Session-elapsed minutes between stored points. 15 keeps a full session to ~25
#: points, which is enough shape to compare against without storing every minute.
CHECKPOINT_MIN = 15

#: A checkpoint below this is not worth ranking — it is the same floor the
#: profile itself refuses to shape a reading under.
MIN_CHECKPOINT = 15

#: Sessions kept per underlying. Well above the 30 the desk compares against, so
#: a month of holidays cannot quietly shrink the window below useful.
MAX_SESSIONS = 120

#: Background sampler cadence. The checkpoint gate does the real rate limiting;
#: this only stops the scheduler re-entering the profile compute every tick.
SAMPLE_INTERVAL_SEC = 60.0

_LOCK = threading.RLock()
_sample_last_run: dict[str, float] = {}


def history_file():
    """Resolved lazily, never at import.

    Stores that do ``FILE = data_dir() / "x.json"`` at module level bind the path
    before a test can redirect it, which is how a fixture once appended 1,800
    synthetic rows into a live archive. Keeping this a function means
    ``monkeypatch.setattr(tilt_history, "data_dir", lambda: tmp_path)`` actually
    works.
    """
    return data_dir() / "volume_tilt_history.json"


def checkpoint_for(bars: int) -> int | None:
    """Elapsed-minute bucket a reading of ``bars`` minutes belongs to.

    Floors to the checkpoint below, so a 188-bar reading lands on 180 and is
    ranked against other sessions' 180 — never against their close.
    """
    if bars is None or bars < MIN_CHECKPOINT:
        return None
    return (int(bars) // CHECKPOINT_MIN) * CHECKPOINT_MIN


def _session_key(underlying: str, day: str) -> str:
    return f"{underlying.strip().upper()}|{day}"


def _empty() -> dict[str, Any]:
    return {"version": 1, "sessions": {}}


def _load() -> dict[str, Any]:
    path = history_file()
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A corrupt store must not take the desk down; it rebuilds forward.
        log_event(_log, logging.WARNING, "tilt_history_unreadable", path=str(path))
        return _empty()
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return _empty()
    return data


def _save(data: dict[str, Any]) -> None:
    path = history_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _prune(data: dict[str, Any]) -> None:
    """Keep the newest ``MAX_SESSIONS`` per underlying, oldest dropped first."""
    by_u: dict[str, list[str]] = {}
    for key, row in data.get("sessions", {}).items():
        by_u.setdefault(str(row.get("underlying") or key.split("|")[0]), []).append(key)
    for keys in by_u.values():
        if len(keys) <= MAX_SESSIONS:
            continue
        keys.sort(key=lambda k: str(data["sessions"][k].get("date") or ""))
        for stale in keys[: len(keys) - MAX_SESSIONS]:
            data["sessions"].pop(stale, None)


def upsert_point(
    underlying: str,
    day: str,
    *,
    minute: int,
    tilt_pp: float | None,
    bars: int,
    contract: str | None = None,
    expiry: str | None = None,
    source: str = "live",
    overlap_pct: float | None = None,
) -> None:
    """Record one checkpoint of one session. Idempotent per (underlying, day, minute).

    Last write wins on a repeated checkpoint: re-running a backfill, or the
    sampler firing twice inside one bucket, must converge rather than duplicate.

    A ``None`` tilt is dropped rather than stored. The profile returns ``None``
    for *could not be measured*, and a null in the curve would be indistinguishable
    from a balanced session once it reached the percentile maths.
    """
    if tilt_pp is None:
        return
    cp = checkpoint_for(minute)
    if cp is None:
        return
    u = underlying.strip().upper()
    with _LOCK:
        data = _load()
        key = _session_key(u, day)
        row = data["sessions"].setdefault(
            key,
            {"underlying": u, "date": day, "curve": {}, "source": source},
        )
        row["curve"][str(cp)] = round(float(tilt_pp), 2)
        row["bars"] = max(int(bars or 0), int(row.get("bars") or 0))
        row["updated_at"] = datetime.now(tz=IST).isoformat(timespec="seconds")
        if contract:
            row["contract"] = contract
        if expiry:
            row["expiry"] = expiry
        if overlap_pct is not None:
            row["overlap_pct"] = round(float(overlap_pct), 2)
        # A session seeded by backfill and later extended live is live-anchored:
        # the live points are the ones that were observed as they happened.
        if source == "live":
            row["source"] = "live"
        elif not row.get("source"):
            row["source"] = source
        # The last checkpoint present is the closing reading.
        cps = sorted(int(k) for k in row["curve"])
        row["final_minute"] = cps[-1]
        row["final_tilt_pp"] = row["curve"][str(cps[-1])]
        _prune(data)
        _save(data)


def get_sessions(underlying: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Stored sessions for ``underlying``, oldest first."""
    u = underlying.strip().upper()
    with _LOCK:
        data = _load()
    rows = [r for r in data.get("sessions", {}).values() if str(r.get("underlying")) == u]
    rows.sort(key=lambda r: str(r.get("date") or ""))
    return rows[-limit:] if limit else rows


def _percentile(values: list[float], current: float) -> float:
    """Midrank percentile — ties split, so a flat window cannot read as 0 or 100.

    Percentile rather than a z-score on purpose: tilt is bounded [-100, +100] and
    its distribution is not normal, least of all with expiry-day outliers in the
    window.
    """
    below = sum(1 for v in values if v < current)
    equal = sum(1 for v in values if v == current)
    return round(100.0 * (below + 0.5 * equal) / len(values), 1)


def compare_current(
    underlying: str,
    *,
    tilt_pp: float | None,
    bars: int,
    window: int = 30,
) -> dict[str, Any]:
    """Rank today's tilt against prior sessions **at the same session minute**.

    Today is excluded from its own comparison — a session cannot be part of the
    distribution it is being ranked against.

    ``available: False`` with a reason whenever the ranking would be misleading:
    too early in the session to have a checkpoint, no stored history, or a window
    too thin to rank against. ``n`` is always returned, because "88th percentile
    of 30" and "88th percentile of 4" must never render alike.
    """
    u = underlying.strip().upper()
    today = datetime.now(tz=IST).date().isoformat()
    out: dict[str, Any] = {
        "underlying": u,
        "available": False,
        "reason": None,
        "checkpoint_min": None,
        "current_tilt_pp": tilt_pp,
        "n": 0,
        "window": window,
    }

    cp = checkpoint_for(bars)
    if tilt_pp is None or cp is None:
        out["reason"] = "too_early"
        return out
    out["checkpoint_min"] = cp

    prior: list[tuple[str, float, str]] = []
    for row in get_sessions(u):
        if str(row.get("date")) == today:
            continue
        val = (row.get("curve") or {}).get(str(cp))
        if val is None:
            continue
        prior.append((str(row.get("date")), float(val), str(row.get("source") or "live")))
    prior.sort(key=lambda t: t[0])
    prior = prior[-window:]

    out["n"] = len(prior)
    if not prior:
        out["reason"] = "no_history"
        return out
    if len(prior) < 5:
        # Ranking against four sessions is arithmetic, not evidence.
        out["reason"] = "window_too_thin"

    vals = [v for _, v, _ in prior]
    vals_sorted = sorted(vals)
    mid = len(vals_sorted) // 2
    median = (
        vals_sorted[mid]
        if len(vals_sorted) % 2
        else round((vals_sorted[mid - 1] + vals_sorted[mid]) / 2.0, 2)
    )

    out.update(
        {
            "available": out["reason"] is None,
            "percentile": _percentile(vals, float(tilt_pp)),
            "median": median,
            "min": min(vals),
            "max": max(vals),
            "backfilled": sum(1 for _, _, s in prior if s == "backfill"),
            "first_date": prior[0][0],
            "last_date": prior[-1][0],
            "series": [
                {"date": d, "tilt_pp": v, "source": s} for d, v, s in prior
            ],
        }
    )
    return out


def maybe_sample_tilt_history_periodic() -> bool:
    """Scheduler hook — record the current checkpoint for each tracked underlying.

    Reads the profile the desk has *already* computed when one is fresh
    (``peek_volume_profile``), so a page-open session costs nothing extra. It
    falls back to computing, because the whole point of a scheduler sampler is
    that the curve fills in on days nobody opens the page.

    Returns True if anything was written.
    """
    from analysis.volume_profile.service import get_volume_profile, peek_volume_profile

    wrote = False
    now = time_mod.time()
    for u in TILT_HISTORY_UNDERLYINGS:
        if now - _sample_last_run.get(u, 0.0) < SAMPLE_INTERVAL_SEC:
            continue
        _sample_last_run[u] = now
        try:
            snap = peek_volume_profile(u) or get_volume_profile(u)
            if not snap.get("available"):
                continue
            bars = int(snap.get("bars") or 0)
            cp = checkpoint_for(bars)
            if cp is None:
                continue
            day = datetime.now(tz=IST).date().isoformat()
            existing = (
                next(
                    (r for r in get_sessions(u) if str(r.get("date")) == day),
                    {},
                ).get("curve")
                or {}
            )
            if str(cp) in existing:
                continue  # this bucket is already recorded
            contract = (snap.get("contract") or {}).get("tradingsymbol")
            upsert_point(
                u,
                day,
                minute=bars,
                tilt_pp=snap.get("tilt_pp"),
                bars=bars,
                contract=contract,
                expiry=(snap.get("contract") or {}).get("expiry"),
                source="live",
                overlap_pct=snap.get("overlap_pct"),
            )
            wrote = True
        except Exception as exc:
            log_event(
                _log,
                logging.WARNING,
                "tilt_history_sample_failed",
                underlying=u,
                error=str(exc)[:200],
            )
    return wrote


def reset_for_tests() -> None:
    """Clear the sampler's cadence memory — the dict is process-wide."""
    _sample_last_run.clear()


def session_dates(underlying: str) -> list[str]:
    """Dates already stored, for a backfill to skip cheaply."""
    return [str(r.get("date")) for r in get_sessions(underlying)]


def purge_underlying(underlying: str) -> int:
    """Drop every stored session for ``underlying``. Returns the count removed.

    Exists because a backfill can seed *wrong* sessions — a far-month contract
    fitted as if it were the front month produces readings that look valid. When
    that happens the fix is to remove them wholesale, not to overwrite: the bad
    rows may cover dates the corrected run will not reach.
    """
    u = underlying.strip().upper()
    with _LOCK:
        data = _load()
        keys = [k for k, r in data.get("sessions", {}).items() if str(r.get("underlying")) == u]
        for k in keys:
            data["sessions"].pop(k, None)
        _save(data)
    return len(keys)


def tilt_comparison(underlying: str, *, window: int = 30) -> dict[str, Any]:
    """Today's tilt ranked against the stored window — the desk's read of this.

    Prefers a cached profile (``peek_volume_profile``) so opening the page after
    the chart has already drawn costs no second Kite pull; falls back to
    computing when nothing is fresh.
    """
    from analysis.volume_profile.service import get_volume_profile, peek_volume_profile

    u = underlying.strip().upper()
    try:
        snap = peek_volume_profile(u) or get_volume_profile(u)
    except Exception as exc:
        log_event(_log, logging.WARNING, "tilt_comparison_failed", underlying=u, error=str(exc)[:200])
        return {"underlying": u, "available": False, "reason": "profile_unavailable", "n": 0}

    if not snap.get("available"):
        return {
            "underlying": u,
            "available": False,
            "reason": snap.get("reason") or "profile_unavailable",
            "n": 0,
        }

    out = compare_current(
        u,
        tilt_pp=snap.get("tilt_pp"),
        bars=int(snap.get("bars") or 0),
        window=window,
    )
    out["current_bars"] = snap.get("bars")
    out["balance_verdict"] = snap.get("balance_verdict")
    out["contract"] = (snap.get("contract") or {}).get("tradingsymbol")
    # The desk's own dead zone: inside it the engine refuses to name a side, and
    # a percentile should not talk the reader past that refusal.
    out["dead_zone_pp"] = 5.0
    return out
