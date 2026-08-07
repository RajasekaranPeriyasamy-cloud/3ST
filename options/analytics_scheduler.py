"""Background scheduler for continuous analytics-desk session-history sampling.

Runs in its own thread, independent of ``execution/scheduler.py`` (which ticks
order-placing runners — rolling straddle, survivor, wave, premium book,
watchlist exits). Analytics sampling here never places orders, never touches
``broker/``/``execution/``/``risk/``, and is intentionally kept on a separate
thread so:

* its cadence (30s, ``options.gamma_density.GEX_HISTORY_SAMPLE_INTERVAL_SEC`` /
  ``options.oi_var.OI_VAR_SAMPLE_INTERVAL_SEC``) is not coupled to whatever
  execution runner happens to have the shortest configured tick interval, and
* a slow/erroring analytics sample can never delay an order-placing tick, or
  vice versa.

Covers the two desks that had no background sampler: Gamma Density (GEX) and
OI VAR (ΔVAR). OI Movers already has its own periodic hook wired into
``execution/scheduler.py`` (``options.oi_movers.maybe_sample_oi_movers_history_periodic``)
predating this module — left as-is rather than migrated, since it already
works in production and moving it is a separate, non-urgent cleanup.

Each underlying's own hook function is already "never raises" (it catches
and logs per-underlying failures internally); the extra guard here is only
so one hook's unexpected import-time or logging failure can't take down the
other's call in the same wake.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

_thread: threading.Thread | None = None
_stop_event = threading.Event()

# Wake more often than either hook's own per-underlying sample interval (30s)
# so an underlying crossing into its session window (MCX opens 09:00, cash
# 09:15) is picked up promptly instead of waiting up to a full 30s-aligned
# cycle. The hooks themselves are cheap no-ops when nothing is due.
WAKE_INTERVAL_SEC = 15


def _safe_call(name: str, fn: Callable[[], bool]) -> None:
    try:
        fn()
    except Exception as exc:
        try:
            from utils.logging import get_logger, log_event

            log_event(
                get_logger("analytics_scheduler"),
                logging.WARNING,
                "analytics_scheduler_hook_error",
                hook=name,
                error=str(exc),
            )
        except Exception:
            pass


def _run_loop() -> None:
    while not _stop_event.is_set():
        from options.gamma_density import maybe_sample_gex_history_periodic
        from options.oi_var import maybe_sample_oi_var_history_periodic

        _safe_call("gamma_density_gex_history", maybe_sample_gex_history_periodic)
        _safe_call("oi_var_history", maybe_sample_oi_var_history_periodic)

        _stop_event.wait(WAKE_INTERVAL_SEC)


def start_analytics_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_run_loop, name="3st-analytics-scheduler", daemon=True)
    _thread.start()


def stop_analytics_scheduler() -> None:
    global _thread
    _stop_event.set()
    if _thread:
        _thread.join(timeout=2.0)
        _thread = None


def analytics_scheduler_status() -> dict[str, object]:
    alive = _thread is not None and _thread.is_alive()
    return {"analytics_scheduler_alive": alive, "wake_interval_sec": WAKE_INTERVAL_SEC}
