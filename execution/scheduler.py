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
    }
