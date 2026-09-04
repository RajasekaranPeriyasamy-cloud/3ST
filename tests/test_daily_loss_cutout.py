"""The max_daily_loss cutout — feeding it, tripping it, and rolling it over.

Until 2026-09-03 ``risk.limits.record_pnl`` had no caller anywhere in the repo,
so ``_daily_pnl`` never left 0.0 and the cutout could not fire.
``execution/pnl_tracker.py`` feeds it from broker truth; these tests pin the
behaviour that makes it safe to have live.
"""

from __future__ import annotations

import json

import pytest

from broker.base import Broker


@pytest.fixture
def rl(tmp_path, monkeypatch):
    """risk.limits with isolated files and a clean P&L slate."""
    from risk import limits as mod

    monkeypatch.setattr(mod, "LIMITS_FILE", tmp_path / "risk_limits.json")
    monkeypatch.setattr(mod, "DAILY_PNL_FILE", tmp_path / "risk_daily_pnl.json")
    monkeypatch.setattr(mod, "_LIMITS", mod.RiskLimits())
    monkeypatch.setattr(mod, "_daily_pnl", 0.0)
    monkeypatch.setattr(mod, "_daily_pnl_date", None)
    monkeypatch.setattr(mod, "_daily_pnl_source", "none")
    monkeypatch.setattr(mod, "_daily_pnl_at", None)
    monkeypatch.setattr(mod, "_orders_this_minute", [])
    return mod


def _check(rl, *, is_closing=False):
    rl.check_order(
        qty=75, product="MIS", exchange="NFO", open_positions=0, is_closing=is_closing
    )


# --------------------------------------------------------------------------- #
# The cutout fires
# --------------------------------------------------------------------------- #


def test_order_allowed_below_the_loss_limit(rl):
    rl.set_daily_pnl(-9_999.0)
    _check(rl)  # does not raise


def test_new_entry_refused_once_the_loss_limit_is_breached(rl):
    rl.set_daily_pnl(-10_000.0)
    with pytest.raises(RuntimeError, match="max daily loss breached"):
        _check(rl)


def test_breach_message_names_the_numbers(rl):
    rl.set_daily_pnl(-12_500.0)
    with pytest.raises(RuntimeError) as exc:
        _check(rl)
    assert "12,500.00" in str(exc.value)
    assert "10,000.00" in str(exc.value)


# --------------------------------------------------------------------------- #
# ...but never traps the desk in the position that caused the loss.
# run_panic -> close_watchlist_trade -> place_leg_order -> check_order, and
# panic bypasses the ARM gate but not this one.
# --------------------------------------------------------------------------- #


def test_closing_order_still_allowed_after_a_breach(rl):
    rl.set_daily_pnl(-50_000.0)
    _check(rl, is_closing=True)  # must not raise — this is the panic path


def test_breach_does_not_relax_the_other_checks(rl):
    """is_closing is not a skeleton key: size/product/exchange still apply."""
    rl.set_daily_pnl(-50_000.0)
    with pytest.raises(RuntimeError, match="qty"):
        rl.check_order(
            qty=99_999, product="MIS", exchange="NFO", open_positions=0, is_closing=True
        )
    with pytest.raises(RuntimeError, match="exchange"):
        rl.check_order(
            qty=75, product="MIS", exchange="LSE", open_positions=0, is_closing=True
        )


# --------------------------------------------------------------------------- #
# Day rollover and restart
# --------------------------------------------------------------------------- #


def test_yesterdays_loss_does_not_block_today(rl):
    rl.set_daily_pnl(-50_000.0)
    rl._daily_pnl_date = "2020-01-01"  # pretend the day turned over
    _check(rl)  # must not raise
    assert rl.get_daily_pnl()["daily_pnl"] == 0.0


def test_daily_pnl_survives_a_restart(rl, monkeypatch):
    rl.set_daily_pnl(-7_500.0)
    assert rl.DAILY_PNL_FILE.exists()

    # Simulate a fresh import: module globals back to defaults, then restore.
    monkeypatch.setattr(rl, "_daily_pnl", 0.0)
    monkeypatch.setattr(rl, "_daily_pnl_date", None)
    rl.load_persisted_daily_pnl()

    snap = rl.get_daily_pnl()
    assert snap["daily_pnl"] == -7_500.0
    assert snap["daily_pnl_source"] == "restored"


def test_restart_discards_a_stale_days_pnl(rl, monkeypatch):
    rl.DAILY_PNL_FILE.write_text(
        json.dumps({"date": "2020-01-01", "daily_pnl": -50_000.0, "source": "broker"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(rl, "_daily_pnl", 0.0)
    rl.load_persisted_daily_pnl()
    assert rl.get_daily_pnl()["daily_pnl"] == 0.0
    _check(rl)  # must not raise


def test_record_pnl_accumulates(rl):
    rl.record_pnl(-3_000.0)
    rl.record_pnl(-2_000.0)
    assert rl.get_daily_pnl()["daily_pnl"] == -5_000.0
    assert rl.get_daily_pnl()["daily_pnl_source"] == "delta"


# --------------------------------------------------------------------------- #
# Reading the day's P&L out of broker positions
# --------------------------------------------------------------------------- #


def test_day_pnl_prefers_the_brokers_own_figure():
    from execution import pnl_tracker as pt

    rows = [
        # Kite: pnl is realised + unrealised, and survives on a closed row.
        {"tradingsymbol": "A", "quantity": 0, "pnl": -4_200.0, "average_price": 100.0},
        {"tradingsymbol": "B", "quantity": 75, "pnl": 1_200.0},
    ]
    total, counted = pt.day_pnl_from_positions(rows)
    assert total == -3_000.0
    assert counted == 2


def test_day_pnl_counts_closed_positions():
    """A leg opened and closed today has quantity 0 but real realised P&L."""
    from execution import pnl_tracker as pt

    closed = {"tradingsymbol": "A", "quantity": 0, "pnl": -8_000.0}
    total, counted = pt.day_pnl_from_positions([closed])
    assert total == -8_000.0
    assert counted == 1


def test_day_pnl_falls_back_to_avg_vs_ltp():
    from execution import pnl_tracker as pt

    rows = [{"tradingsymbol": "A", "quantity": -75, "average_price": 100.0, "last_price": 120.0}]
    total, counted = pt.day_pnl_from_positions(rows)
    assert total == -1_500.0
    assert counted == 1


def test_day_pnl_skips_unusable_rows_rather_than_counting_zero():
    from execution import pnl_tracker as pt

    rows = [{"tradingsymbol": "A"}, {"tradingsymbol": "B", "pnl": -500.0}]
    total, counted = pt.day_pnl_from_positions(rows)
    assert total == -500.0
    assert counted == 1


# --------------------------------------------------------------------------- #
# A failed broker read is not a flat day (the reconcile invariant).
# --------------------------------------------------------------------------- #


class _Positions(Broker):
    def __init__(self, rows=None, raises=False):
        self._rows = rows or []
        self._raises = raises

    def place_order(self, req):
        raise NotImplementedError

    def cancel_order(self, order_id):
        raise NotImplementedError

    def positions(self):
        if self._raises:
            raise RuntimeError("Kite read failed")
        return self._rows

    def orders(self):
        return []

    def ltp(self, exchange, tradingsymbol):
        return 100.0


def test_paper_rows_are_valued_from_the_brokers_ltp(rl):
    """PaperBroker keeps LTPs beside the position, not on it.

    Without the enrichment step every paper row is unusable and the cutout is
    silently inert in the exact mode it is meant to be rehearsed in — which is
    how this was caught.
    """
    from execution import pnl_tracker as pt

    row = {"exchange": "NFO", "tradingsymbol": "A", "quantity": -75, "average_price": 100.0}
    assert pt.position_day_pnl(row) is None, "no last_price, no figure"

    broker = _Positions([row])
    broker.ltp = lambda exchange, tradingsymbol: 260.0
    out = pt.refresh_daily_pnl(broker)
    assert out["ok"] is True
    assert rl.get_daily_pnl()["daily_pnl"] == -12_000.0


def test_enrichment_leaves_rows_that_already_have_pnl_alone():
    """Kite rows carry pnl, so the live path never issues an extra quote."""
    from execution import pnl_tracker as pt

    calls: list[str] = []
    broker = _Positions([])
    broker.ltp = lambda exchange, tradingsymbol: calls.append(tradingsymbol) or 1.0

    rows = [{"exchange": "NFO", "tradingsymbol": "A", "quantity": 75, "pnl": -500.0}]
    assert pt.enrich_last_price(broker, rows) == rows
    assert calls == []


def test_refresh_sets_pnl_from_broker(rl):
    from execution import pnl_tracker as pt

    out = pt.refresh_daily_pnl(_Positions([{"tradingsymbol": "A", "pnl": -2_500.0}]))
    assert out["ok"] is True
    assert rl.get_daily_pnl()["daily_pnl"] == -2_500.0
    assert rl.get_daily_pnl()["daily_pnl_source"] == "broker"


def test_failed_positions_read_keeps_the_last_known_pnl(rl):
    """Never reopen a tripped cutout because the broker was briefly unreadable."""
    from execution import pnl_tracker as pt

    rl.set_daily_pnl(-11_000.0)
    out = pt.refresh_daily_pnl(_Positions(raises=True))
    assert out["ok"] is False
    assert rl.get_daily_pnl()["daily_pnl"] == -11_000.0
    with pytest.raises(RuntimeError, match="max daily loss breached"):
        _check(rl)


def test_rows_without_usable_pnl_keep_the_last_known_figure(rl):
    from execution import pnl_tracker as pt

    rl.set_daily_pnl(-11_000.0)
    out = pt.refresh_daily_pnl(_Positions([{"tradingsymbol": "A"}]))
    assert out["ok"] is False
    assert rl.get_daily_pnl()["daily_pnl"] == -11_000.0


def test_empty_book_is_a_real_zero(rl):
    """No positions at all is genuinely flat — that must still update."""
    from execution import pnl_tracker as pt

    rl.set_daily_pnl(-11_000.0)
    out = pt.refresh_daily_pnl(_Positions([]))
    assert out["ok"] is True
    assert rl.get_daily_pnl()["daily_pnl"] == 0.0


def test_periodic_refresh_is_throttled(rl, monkeypatch):
    from execution import pnl_tracker as pt

    monkeypatch.setattr(pt, "_last_run_mono", 0.0)
    calls: list[int] = []
    monkeypatch.setattr(pt, "refresh_daily_pnl", lambda broker=None: calls.append(1) or {})

    pt.maybe_refresh_daily_pnl_periodic()
    assert len(calls) == 1, "first call should run"

    assert pt.maybe_refresh_daily_pnl_periodic() is None, "second call should be throttled"
    assert len(calls) == 1
