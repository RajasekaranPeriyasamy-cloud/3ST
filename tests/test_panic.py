"""Tests for panic ARM bypass."""

from __future__ import annotations

from execution.arming import arm, get_arm_state, require_armed_for_live, set_mode
from execution.panic import is_panic_active, panic_mode, run_panic


def test_panic_mode_bypasses_arm_check() -> None:
    set_mode("live")
    assert not get_arm_state()["armed"]
    with panic_mode():
        assert is_panic_active()
        require_armed_for_live()  # should not raise
    assert not is_panic_active()


def test_run_panic_disarms(monkeypatch) -> None:
    set_mode("live")
    arm(confirm=True)
    assert get_arm_state()["armed"]

    monkeypatch.setattr("watchlist_store.list_items", lambda _s: [])
    monkeypatch.setattr("execution.panic._cancel_open_3st_orders", lambda: [])

    result = run_panic(cancel_orders=False, close_positions=False)
    assert result["ok"] is True
    assert not get_arm_state()["armed"]
