"""Tests for entry-bar grace (zone exit blocked on entry bar)."""

from __future__ import annotations

from execution.watchlist_exit_runner import _entry_grace_active


def test_grace_active_when_signal_bar_equals_entry_bar() -> None:
    item = {"entry_bar_time": "2026-07-10 10:03:00"}
    signals = {"bar_time": "2026-07-10 10:03:00"}
    assert _entry_grace_active(item, signals) is True


def test_grace_inactive_after_next_bar() -> None:
    item = {"entry_bar_time": "2026-07-10 10:03:00"}
    signals = {"bar_time": "2026-07-10 10:06:00"}
    assert _entry_grace_active(item, signals) is False


def test_grace_uses_entry_at_fallback() -> None:
    item = {"entry_at": "2026-07-10 10:00:00"}
    signals = {"bar_time": "2026-07-10 10:00:00"}
    assert _entry_grace_active(item, signals) is True


def test_grace_false_without_entry_timestamp() -> None:
    item = {}
    signals = {"bar_time": "2026-07-10 10:06:00"}
    assert _entry_grace_active(item, signals) is False
