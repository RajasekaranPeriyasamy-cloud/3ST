"""Daemon thread driving the minute sampler.

Its own thread, not ``execution/scheduler.py`` and not
``options/analytics_scheduler.py``, for the reason stated in that module: an
analysis sample must never be able to delay an order-placing tick. This engine
additionally solves per-strike IV, which is the slowest analysis work on the
desk, so keeping it isolated matters more here than elsewhere.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from analysis.delta_velocity import collector, store
from settings import env
from utils.logging import get_logger, log_event

IST = ZoneInfo("Asia/Kolkata")
logger = get_logger("delta_velocity.runner")

# Wake more often than the sample interval so the first minute after the open
# is caught promptly rather than up to a full interval late.
WAKE_INTERVAL_SEC = 10
SAMPLE_INTERVAL_SEC = 60

#: Refuse to prune below this many days. The retention window is operator-set
#: through the environment, and a mistyped ``DELTA_VELOCITY_RETENTION_DAYS=3``
#: on a scheduled deleter would take most of the corpus with it before anyone
#: read the log. A floor costs nothing and makes that class of typo inert.
RETENTION_MIN_DAYS = 7

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_sample_minute: str | None = None
_last_report: dict[str, Any] = {}
_last_prune_day: str | None = None


def is_alive() -> bool:
    return bool(_thread and _thread.is_alive())


def last_report() -> dict[str, Any]:
    return dict(_last_report)


def _minute_key(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M")


def _tick() -> None:
    global _last_sample_minute, _last_report

    now = datetime.now(tz=IST)
    if not collector.in_session(now):
        return

    key = _minute_key(now)
    if key == _last_sample_minute:
        return
    _last_sample_minute = key

    report = collector.sample_once(now=now)
    _last_report = report
    if not report.get("ok"):
        log_event(logger, 30, "delta_velocity_sample_failed", error=report.get("error"))
        return

    store.save_state(
        {
            "last_sample_at": report.get("ts"),
            "last_sample": report.get("underlyings"),
        }
    )


def retention_days() -> int:
    """Raw-archive retention, from ``DELTA_VELOCITY_RETENTION_DAYS``.

    Read through ``settings.env`` rather than ``os.getenv`` so it comes from
    ``.env`` regardless of which shell launched uvicorn — the trap CLAUDE.md
    documents. ``0`` (or anything unparseable) disables pruning entirely, which
    is the intended off switch for a feature that deletes data: an operator who
    wants the whole corpus kept should not have to edit code to keep it.
    """
    raw = env("DELTA_VELOCITY_RETENTION_DAYS", str(store.RAW_RETENTION_DAYS))
    try:
        days = int(float(raw))
    except (TypeError, ValueError):
        log_event(logger, 30, "delta_velocity_retention_unparseable", value=raw)
        return 0
    if days <= 0:
        return 0
    if days < RETENTION_MIN_DAYS:
        log_event(
            logger, 30, "delta_velocity_retention_below_floor",
            requested=days, floor=RETENTION_MIN_DAYS,
        )
        return RETENTION_MIN_DAYS
    return days


def _maybe_prune(now: datetime) -> None:
    """Prune the raw archive once per calendar day.

    Deliberately outside the ``in_session`` gate in ``_tick``: pruning is
    housekeeping, not sampling, and gating it on market hours would mean a desk
    left running over a weekend never reclaims anything. Once per day rather
    than per tick because it walks every session file of every underlying.
    """
    global _last_prune_day

    day = now.date().isoformat()
    if _last_prune_day == day:
        return
    _last_prune_day = day

    days = retention_days()
    if days <= 0:
        return
    for underlying in collector.UNDERLYINGS:
        try:
            removed = store.prune_raw(underlying, retention_days=days)
        except Exception as exc:  # housekeeping must never kill the sampler
            log_event(logger, 40, "delta_velocity_prune_failed",
                      underlying=underlying, error=str(exc))
            continue
        if removed:
            log_event(
                logger, 20, "delta_velocity_pruned_raw",
                underlying=underlying, retention_days=days,
                removed=len(removed), sessions=removed,
            )


def _loop() -> None:
    log_event(
        logger, 20, "delta_velocity_runner_started",
        interval_sec=SAMPLE_INTERVAL_SEC, retention_days=retention_days(),
    )
    while not _stop.is_set():
        try:
            _maybe_prune(datetime.now(tz=IST))
            _tick()
        except Exception as exc:
            log_event(logger, 40, "delta_velocity_tick_error", error=str(exc))
        _stop.wait(WAKE_INTERVAL_SEC)
    log_event(logger, 20, "delta_velocity_runner_stopped")


def start() -> bool:
    """Idempotent. Returns True if this call started the thread."""
    global _thread
    if is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="delta-velocity", daemon=True)
    _thread.start()
    return True


def stop(timeout: float = 5.0) -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=timeout)
