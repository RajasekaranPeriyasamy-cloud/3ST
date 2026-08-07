"""Unit tests for the independent analytics-desk background scheduler.

Verifies the thread lifecycle (start/stop/status) and that a hook raising
never kills the loop or start_analytics_scheduler() — the two production
hooks (Gamma Density GEX, OI VAR) are each already "never raises" on their
own, but this scheduler's _safe_call is a second line of defense.
"""

from __future__ import annotations

import time

import options.analytics_scheduler as sched


def test_safe_call_swallows_exceptions() -> None:
    def _boom() -> bool:
        raise RuntimeError("boom")

    # Must not raise.
    sched._safe_call("test_hook", _boom)


def test_start_stop_lifecycle(monkeypatch) -> None:
    calls: list[str] = []

    def _fake_gex() -> bool:
        calls.append("gex")
        return True

    def _fake_var() -> bool:
        calls.append("var")
        return True

    monkeypatch.setattr("options.gamma_density.maybe_sample_gex_history_periodic", _fake_gex)
    monkeypatch.setattr("options.oi_var.maybe_sample_oi_var_history_periodic", _fake_var)
    monkeypatch.setattr(sched, "WAKE_INTERVAL_SEC", 0.05)

    assert sched.analytics_scheduler_status()["analytics_scheduler_alive"] is False

    sched.start_analytics_scheduler()
    try:
        # Calling again while already running must be a no-op, not a second thread.
        sched.start_analytics_scheduler()
        assert sched.analytics_scheduler_status()["analytics_scheduler_alive"] is True

        deadline = time.time() + 2.0
        while time.time() < deadline and ("gex" not in calls or "var" not in calls):
            time.sleep(0.02)
        assert "gex" in calls
        assert "var" in calls
    finally:
        sched.stop_analytics_scheduler()

    assert sched.analytics_scheduler_status()["analytics_scheduler_alive"] is False
