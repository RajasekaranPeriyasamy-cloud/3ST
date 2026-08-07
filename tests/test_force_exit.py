"""Tests for intraday force-exit window logic."""

from __future__ import annotations

from datetime import datetime

import pytest

from backtest_engine import force_exit_due


@pytest.mark.parametrize(
    ("now_hm", "force_exit", "session_end", "expected"),
    [
        ("15:25", "15:20", "15:30", True),
        ("15:15", "15:20", "15:30", False),
        ("15:35", "15:20", "15:30", False),
        ("22:50", "22:45", "23:30", True),
        ("22:40", "22:45", "23:30", False),
        ("23:35", "22:45", "23:30", False),
    ],
)
def test_force_exit_normal_window(
    now_hm: str,
    force_exit: str,
    session_end: str,
    expected: bool,
) -> None:
    h, m = map(int, now_hm.split(":"))
    when = datetime(2026, 7, 10, h, m)
    assert (
        force_exit_due(
            when,
            force_exit=force_exit,
            session_end=session_end,
            system_mode="Intraday",
        )
        is expected
    )


def test_force_exit_mcx_late_session_misconfigured_end() -> None:
    """force_exit after session_end (NSE default) — exit from force_exit until midnight."""
    when = datetime(2026, 7, 10, 22, 50)
    assert force_exit_due(
        when,
        force_exit="22:45",
        session_end="15:30",
        system_mode="Intraday",
    ) is True


def test_force_exit_positional_never() -> None:
    when = datetime(2026, 7, 10, 15, 25)
    assert not force_exit_due(
        when,
        force_exit="15:20",
        session_end="15:30",
        system_mode="Positional",
    )
