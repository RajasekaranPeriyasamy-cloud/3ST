"""Background worker for the Equity Report desk.

Its own daemon thread, deliberately separate from ``execution/scheduler.py`` and
``options/analytics_scheduler.py`` for the same reason those two are separate
from each other: a report takes minutes and talks to a third-party API, and that
must never be able to delay an order-placing tick.

One job at a time, FIFO. Nothing here imports from ``broker/``, ``execution/``,
or ``risk/``.
"""

from __future__ import annotations

import logging
import threading

from . import store
from .agent import ReportError, generate_report

_thread: threading.Thread | None = None
_stop_event = threading.Event()
_wake_event = threading.Event()

# Idle poll interval. Enqueueing sets _wake_event, so this is only the ceiling on
# how long a job can sit unnoticed if that signal is ever missed.
IDLE_INTERVAL_SEC = 5.0


def _log(level: int, message: str, **fields) -> None:
    try:
        from utils.logging import get_logger, log_event

        log_event(get_logger("equity_report_runner"), level, message, **fields)
    except Exception:
        pass


def _run_job(job: dict) -> None:
    job_id = job["id"]
    ticker = job.get("ticker", "")
    store.mark_running(job_id)
    _log(logging.INFO, "equity_report_started", job_id=job_id, ticker=ticker)

    try:
        result = generate_report(
            ticker=ticker,
            company=job.get("company", ""),
            exchange=job.get("exchange", "NSE"),
            on_progress=lambda **fields: store.update_progress(job_id, **fields),
            should_cancel=lambda: store.is_cancelled(job_id) or _stop_event.is_set(),
        )
    except ReportError as exc:
        if store.is_cancelled(job_id):
            _log(logging.INFO, "equity_report_cancelled", job_id=job_id, ticker=ticker)
            return
        store.mark_failed(job_id, str(exc))
        _log(logging.WARNING, "equity_report_failed", job_id=job_id, ticker=ticker, error=str(exc))
        return
    except Exception as exc:
        store.mark_failed(job_id, f"{type(exc).__name__}: {exc}")
        _log(logging.ERROR, "equity_report_error", job_id=job_id, ticker=ticker, error=str(exc))
        return

    if store.is_cancelled(job_id):
        # Cancelled while the last request was in flight — keep the cancelled
        # status but don't throw away work already paid for.
        store.write_body(job_id, result.markdown)
        return

    saved = store.mark_done(
        job_id,
        result.markdown,
        usage=result.usage,
        citations=result.citations,
        model=result.model,
    )
    _log(
        logging.INFO,
        "equity_report_done",
        job_id=job_id,
        ticker=ticker,
        iterations=result.iterations,
        tool_calls=result.tool_calls,
        cost_usd=(saved or {}).get("cost_usd"),
    )


def _run_loop() -> None:
    while not _stop_event.is_set():
        job = store.next_queued()
        if job is None:
            _wake_event.wait(IDLE_INTERVAL_SEC)
            _wake_event.clear()
            continue
        if store.is_cancelled(job["id"]):
            continue
        try:
            _run_job(job)
        except Exception as exc:  # never let one job kill the worker
            _log(logging.ERROR, "equity_report_runner_crash", error=str(exc))


def notify_job_queued() -> None:
    """Wake the worker immediately instead of waiting out the idle interval."""
    _wake_event.set()


def start_report_runner() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _wake_event.clear()
    _thread = threading.Thread(target=_run_loop, name="3st-equity-report", daemon=True)
    _thread.start()


def stop_report_runner() -> None:
    global _thread
    _stop_event.set()
    _wake_event.set()
    if _thread:
        _thread.join(timeout=2.0)
        _thread = None


def report_runner_status() -> dict:
    alive = _thread is not None and _thread.is_alive()
    return {"equity_report_runner_alive": alive}
