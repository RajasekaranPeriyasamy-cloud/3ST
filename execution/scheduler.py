"""Background scheduler for automated strategy ticks."""

from __future__ import annotations

import threading
from typing import Any, Callable

from execution.rolling_straddle_store import get_config as rs_get_config
from execution.rolling_straddle_store import get_state as rs_get_state
from execution.rolling_straddle_store import save_state as rs_save_state
from execution.survivor_store import get_config as survivor_get_config
from execution.survivor_store import get_state as survivor_get_state
from execution.wave_store import get_config as wave_get_config
from execution.wave_store import get_state as wave_get_state
from execution.premium_book_store import get_config as pb_get_config
from execution.premium_book_store import get_state as pb_get_state

_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _safe_tick(name: str, fn: Callable[[], Any], log_fn: Callable[[str, str], None]) -> None:
    try:
        fn()
    except Exception as e:
        log_fn("scheduler_error", f"{name}: {e}")


def _run_sync_loop() -> None:
    while not _stop_event.is_set():
        intervals: list[int] = []

        rs_cfg = rs_get_config()
        rs_state = rs_get_state()
        rs_interval = max(15, int(rs_cfg.get("tick_interval_sec") or 60))
        intervals.append(rs_interval)
        if rs_state.get("runner") == "running":
            from execution.rolling_straddle import tick as rs_tick
            from execution.rolling_straddle_store import append_log as rs_log

            _safe_tick("rolling_straddle", rs_tick, rs_log)

        sv_cfg = survivor_get_config()
        sv_state = survivor_get_state()
        sv_interval = max(10, int(sv_cfg.get("tick_interval_sec") or 15))
        intervals.append(sv_interval)
        if sv_state.get("runner") == "running":
            from execution.survivor_runner import tick as survivor_tick
            from execution.survivor_store import append_log as survivor_log

            _safe_tick("survivor", survivor_tick, survivor_log)

        wave_cfg = wave_get_config()
        wave_state = wave_get_state()
        wave_interval = max(30, int(wave_cfg.get("check_interval_sec") or 60))
        intervals.append(wave_interval)
        if wave_state.get("runner") == "running":
            from execution.wave_runner import tick as wave_tick
            from execution.wave_store import append_log as wave_log

            _safe_tick("wave", wave_tick, wave_log)

        pb_cfg = pb_get_config()
        pb_state = pb_get_state()
        pb_interval = max(15, int(pb_cfg.get("tick_interval_sec") or 60))
        intervals.append(pb_interval)
        if pb_state.get("runner") == "running":
            from execution.premium_book_runner import tick as pb_tick
            from execution.premium_book_store import append_log as pb_log

            _safe_tick("premium_book", pb_tick, pb_log)

        from watchlist_store import list_items as wl_list

        if wl_list("active"):
            from execution.watchlist_exit_runner import scan_watchlist_exits

            def _exit_tick() -> None:
                scan_watchlist_exits(auto_close=True)

            _safe_tick("watchlist_exit", _exit_tick, lambda _k, _m: None)

        from execution.reconcile import maybe_reconcile_periodic

        _safe_tick("reconcile", maybe_reconcile_periodic, lambda _k, _m: None)

        # Day P&L -> risk.limits, so max_daily_loss can actually fire. Runs
        # unconditionally: the cutout is account-wide rather than per-runner, and
        # it has to keep tracking while every runner is stopped — manual Kite
        # trades and adopted orphans still move the day's P&L.
        from execution.pnl_tracker import maybe_refresh_daily_pnl_periodic

        _safe_tick("daily_pnl", maybe_refresh_daily_pnl_periodic, lambda _k, _m: None)

        # OI Movers chart history — CE/PE/PCR lines need samples from ~09:20 even
        # when the desk page is not open (spot candles alone leave those blank).
        try:
            from options.oi_movers import maybe_sample_oi_movers_history_periodic

            _safe_tick(
                "oi_movers_history",
                maybe_sample_oi_movers_history_periodic,
                lambda _k, _m: None,
            )
        except Exception:
            pass

        # Session tilt curve — the comparison window only accrues if the point is
        # captured while the session is running, and nobody keeps the page open
        # all day. Cheap: reuses the profile the desk already cached when fresh.
        try:
            from analysis.volume_profile.tilt_history import (
                maybe_sample_tilt_history_periodic,
            )

            _safe_tick(
                "volume_tilt_history",
                maybe_sample_tilt_history_periodic,
                lambda _k, _m: None,
            )
        except Exception:
            pass

        sleep_sec = min(intervals) if intervals else 30
        _stop_event.wait(sleep_sec)


def start_scheduler() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_run_sync_loop, name="3st-strategy-scheduler", daemon=True)
    _thread.start()
    rs_save_state({"scheduler_running": True})


def stop_scheduler() -> None:
    global _thread
    _stop_event.set()
    if _thread:
        _thread.join(timeout=2.0)
        _thread = None
    rs_save_state({"scheduler_running": False})


def scheduler_status() -> dict[str, Any]:
    alive = _thread is not None and _thread.is_alive()
    return {
        "scheduler_alive": alive,
        "rolling_straddle": rs_get_state(),
        "survivor": survivor_get_state(),
        "wave": wave_get_state(),
        "premium_book": pb_get_state(),
    }
