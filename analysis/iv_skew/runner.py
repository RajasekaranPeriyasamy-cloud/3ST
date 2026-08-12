"""Daemon thread sampling the skew every few minutes.

Its own thread, not ``execution/scheduler.py`` and not
``options/analytics_scheduler.py``, for the reason those modules state: an
analysis sample must never be able to delay an order-placing tick. This one
solves IV across a wide strike window for six underlyings, so it is among the
slowest analysis work on the desk and the isolation matters.

Sampling is every 5 minutes, not every minute. Skew is a slow variable — the
useful signal is the daily level and its drift, not tick noise — and a 5-minute
cadence keeps the chain cache in ``builder`` warm (10 min TTL) so each cycle
costs the warm ~2.6s per underlying rather than the cold ~16s.

Session windows are per underlying: cash hours for the indices, MCX hours for
crude and natural gas, which trade until 23:30.
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from analysis.iv_skew import store
from analysis.iv_skew.builder import build_iv_skew
from config import DEFAULT_SESSION, INDEX_OPTIONS, IV_SKEW_DEFAULTS
from utils.logging import get_logger, log_event

IST = ZoneInfo("Asia/Kolkata")
logger = get_logger("iv_skew.runner")

# Wake well inside the sample interval so the first sample after an open lands
# promptly rather than up to a full interval late.
WAKE_INTERVAL_SEC = 20
SAMPLE_INTERVAL_MIN = 5

_thread: threading.Thread | None = None
_stop = threading.Event()
_last_bucket: dict[str, str] = {}
_last_report: dict[str, Any] = {}


def underlyings() -> tuple[str, ...]:
    return tuple(IV_SKEW_DEFAULTS["underlyings"])


def is_alive() -> bool:
    return bool(_thread and _thread.is_alive())


def last_report() -> dict[str, Any]:
    return dict(_last_report)


def session_window(underlying: str) -> tuple[str, str]:
    meta = INDEX_OPTIONS.get(str(underlying).upper()) or {}
    session = meta.get("session") or DEFAULT_SESSION
    return str(session["session_start"]), str(session["session_end"])


def in_session(underlying: str, now: datetime | None = None) -> bool:
    ts = now or datetime.now(tz=IST)
    if ts.weekday() >= 5:
        return False
    start, end = session_window(underlying)
    return start <= ts.strftime("%H:%M") <= end


def _bucket(ts: datetime) -> str:
    """Sample-interval bucket key — dedupes repeated wakes inside one interval."""
    return f"{ts:%Y-%m-%dT%H:}{(ts.minute // SAMPLE_INTERVAL_MIN) * SAMPLE_INTERVAL_MIN:02d}"


def sample_once(now: datetime | None = None) -> dict[str, Any]:
    """Sample every in-session underlying. Never raises.

    A failure on one underlying must not stop the others — MCX is open long
    after the indices close, and a stale index chain should not cost the day's
    crude samples.
    """
    ts = now or datetime.now(tz=IST)
    report: dict[str, Any] = {"ts": ts.replace(microsecond=0).isoformat(), "underlyings": {}}

    for u in underlyings():
        if not in_session(u, ts):
            continue
        try:
            snapshot = build_iv_skew(u)
        except Exception as exc:
            report["underlyings"][u] = {"error": str(exc)}
            log_event(logger, 30, "iv_skew_sample_failed", underlying=u, error=str(exc))
            continue

        sample = store.compact_sample(snapshot, now=ts)
        try:
            store.append_sample(u, sample)
        except OSError as exc:
            report["underlyings"][u] = {"error": f"write failed: {exc}"}
            continue

        rows = sample["expiries"]
        report["underlyings"][u] = {
            "reference": sample["reference"],
            "expiries": len(rows),
            "clean": sum(1 for r in rows if r.get("confidence") == "clean"),
            "rr": next((r.get("rr") for r in rows if r.get("ok")), None),
        }

    report["sampled"] = len(report["underlyings"])
    return report


def _tick() -> None:
    global _last_report

    now = datetime.now(tz=IST)
    due = [u for u in underlyings() if in_session(u, now) and _last_bucket.get(u) != _bucket(now)]
    if not due:
        return
    for u in due:
        _last_bucket[u] = _bucket(now)

    _last_report = sample_once(now=now)

    # Roll up any completed session that has no daily row yet. Cheap, idempotent,
    # and means a restart across the close cannot lose a day.
    try:
        written = store.ensure_rollup(underlyings(), today=now.date())
        if written:
            log_event(logger, 20, "iv_skew_rolled_up", rows=written)
    except Exception as exc:
        log_event(logger, 30, "iv_skew_rollup_failed", error=str(exc))


def _loop() -> None:
    log_event(logger, 20, "iv_skew_runner_started", interval_min=SAMPLE_INTERVAL_MIN)
    while not _stop.is_set():
        try:
            _tick()
        except Exception as exc:
            log_event(logger, 40, "iv_skew_tick_error", error=str(exc))
        _stop.wait(WAKE_INTERVAL_SEC)
    log_event(logger, 20, "iv_skew_runner_stopped")


def start() -> bool:
    """Idempotent. Returns True if this call started the thread."""
    global _thread
    if is_alive():
        return False
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="iv-skew", daemon=True)
    _thread.start()
    return True


def stop(timeout: float = 5.0) -> None:
    _stop.set()
    if _thread and _thread.is_alive():
        _thread.join(timeout=timeout)
