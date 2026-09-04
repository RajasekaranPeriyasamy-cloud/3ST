"""The Rolling Straddle decision ladder, without a broker.

``tick()`` used to be ~310 lines of fetch-and-act with no test importing it —
every unit test targeted a helper, so the *orchestration* (ladder order, the
exit-then-entry interaction, ARM handling, confirm mode) was untested. It is now
``decide()`` (pure) + ``apply_decisions()`` (the only side-effecting half), and
this file covers the part that decides.

The load-bearing property is the last section: an exit and a re-entry must never
both happen on one bar. That is the 2026-07-14 whipsaw incident.
"""

from __future__ import annotations

import pytest

from execution import rolling_straddle as rs

BAR = "2026-09-03 12:50:00"
PREV_BAR = "2026-09-03 12:45:00"

ARMED_LIVE = {"mode": "live", "armed": True}
DISARMED_LIVE = {"mode": "live", "armed": False}
PAPER = {"mode": "paper", "armed": False}


def _signals(*, ts: str = BAR, **over) -> dict:
    sig = {
        "ts": ts,
        "close": 49.0,
        "st1": 61.0,
        "exit_line": 61.0,
        "atr1": 10.0,
        "long_zone_exit": False,
        "short_zone_exit": False,
        "long_entry": False,
        "long_ready": False,
        "short_entry": False,
        "short_ready": False,
    }
    sig.update(over)
    return sig


def _cfg(**over) -> dict:
    cfg = {
        "underlying": "NIFTY",
        "trade_mode": "Both",
        "allow_dual_open": True,
        "execution_mode": "auto",
        "exit_on_bar_close_only": True,
        "entry_exit_enabled": False,
        "sl_mode": "Off",
        "tgt_mode": "Off",
        "tsl_mode": "Off",
        "max_reentries_ce": 1,
        "max_reentries_pe": 1,
        "reentry_style": "zone_active",
    }
    cfg.update(over)
    return cfg


def _flat_leg(**over) -> dict:
    leg = {"status": "flat", "entries_today": 0, "signal_bar_ts": BAR}
    leg.update(over)
    return leg


def _open_short(**over) -> dict:
    leg = {
        "status": "open",
        "managed_by": "algo",
        "entry_side": "SELL",
        "position_side": "short",
        "entry_price": 55.0,
        "tradingsymbol": "NIFTY2690924500PE",
        "exchange": "NFO",
        "signal_bar_ts": BAR,
        "last_action_bar_ts": PREV_BAR,
        "entries_today": 1,
    }
    leg.update(over)
    return leg


def _state(ce: dict | None = None, pe: dict | None = None) -> dict:
    return {"ce": ce or _flat_leg(), "pe": pe or _flat_leg()}


def _run(cfg, state, ce_sig=None, pe_sig=None, *, force=False, arm=PAPER, gate=None):
    return rs.decide(
        cfg,
        state,
        {"ce": ce_sig or _signals(), "pe": pe_sig or _signals()},
        force=force,
        atm=24500.0,
        arm=arm,
        gate=gate,
    )


def _kinds(decisions, leg=None):
    return [d.kind for d in decisions if leg is None or d.leg_key == leg]


def _for(decisions, leg, kind=None):
    return [d for d in decisions if d.leg_key == leg and (kind is None or d.kind == kind)]


# --------------------------------------------------------------------------- #
# It really is pure
# --------------------------------------------------------------------------- #


def test_quiet_tick_decides_nothing():
    assert _run(_cfg(), _state()) == []


def test_decide_touches_neither_broker_nor_state(monkeypatch):
    """No broker call, no state write — that is what makes it testable."""

    def _boom(*a, **k):
        raise AssertionError("decide() must not perform I/O")

    monkeypatch.setattr(rs, "_broker", _boom)
    monkeypatch.setattr(rs, "save_state", _boom)
    monkeypatch.setattr(rs, "get_state", _boom)
    monkeypatch.setattr(rs, "append_log", _boom)

    _run(
        _cfg(),
        _state(ce=_open_short(), pe=_flat_leg()),
        ce_sig=_signals(short_zone_exit=True),
        pe_sig=_signals(long_entry=True, long_ready=True),
    )


# --------------------------------------------------------------------------- #
# Purge — a leg holding another underlying's symbol
# --------------------------------------------------------------------------- #


def test_foreign_symbol_is_purged():
    state = _state(ce=_open_short(tradingsymbol="CRUDEOILM26SEP7050CE", exchange="MCX"))
    out = _run(_cfg(), state)
    purge = _for(out, "ce", "purge")
    assert len(purge) == 1
    assert purge[0].patch["status"] == "flat"
    assert purge[0].patch["last_action"] == "purged_foreign_symbol"


def test_purged_leg_is_not_also_exited():
    """Purge runs first and flattens the leg, so no exit is decided for it."""
    state = _state(ce=_open_short(tradingsymbol="CRUDEOILM26SEP7050CE", exchange="MCX"))
    out = _run(_cfg(), state, ce_sig=_signals(short_zone_exit=True))
    assert _kinds(out, "ce") == ["purge"]


def test_matching_symbol_is_left_alone():
    out = _run(_cfg(), _state(ce=_open_short(tradingsymbol="NIFTY2690924500CE")))
    assert _for(out, "ce", "purge") == []


# --------------------------------------------------------------------------- #
# Exits
# --------------------------------------------------------------------------- #


def test_zone_break_decides_an_exit():
    out = _run(
        _cfg(),
        _state(pe=_open_short()),
        pe_sig=_signals(short_zone_exit=True),
    )
    exits = _for(out, "pe", "exit")
    assert len(exits) == 1
    assert exits[0].reason == "short_zone_exit"


def test_force_exit_wins_over_everything():
    out = _run(_cfg(), _state(pe=_open_short()), force=True)
    assert [d.reason for d in _for(out, "pe", "exit")] == ["force_exit"]


def test_disarmed_live_records_the_exit_instead_of_placing_it():
    """The order is refused later anyway; deciding it silently would hide that."""
    out = _run(
        _cfg(),
        _state(pe=_open_short()),
        pe_sig=_signals(short_zone_exit=True),
        arm=DISARMED_LIVE,
    )
    assert _kinds(out, "pe") == ["note"]
    assert _for(out, "pe")[0].log_event == "pe_exit_blocked_disarm"


def test_armed_live_places_the_exit():
    out = _run(
        _cfg(),
        _state(pe=_open_short()),
        pe_sig=_signals(short_zone_exit=True),
        arm=ARMED_LIVE,
    )
    assert len(_for(out, "pe", "exit")) == 1


def test_deferred_ltp_is_a_note_not_an_exit(monkeypatch):
    monkeypatch.setattr(rs, "_leg_ltp", lambda _leg: 62.0)
    out = _run(_cfg(exit_on_bar_close_only=True), _state(pe=_open_short()))
    assert _kinds(out, "pe") == ["note"]
    assert _for(out, "pe")[0].log_event == "pe_exit_deferred_ltp"


def test_exit_predicate_failure_becomes_an_error_not_a_crash(monkeypatch):
    """One leg blowing up must not stop the other being decided."""
    monkeypatch.setattr(
        rs, "_should_exit_ce", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = _run(
        _cfg(),
        _state(ce=_open_short(tradingsymbol="NIFTY2690924500CE"), pe=_open_short()),
        pe_sig=_signals(short_zone_exit=True),
    )
    errs = _for(out, "ce", "error")
    assert len(errs) == 1
    assert errs[0].reason == "ce exit: boom"
    assert len(_for(out, "pe", "exit")) == 1


# --------------------------------------------------------------------------- #
# Entries
# --------------------------------------------------------------------------- #


def test_long_signal_decides_an_entry():
    out = _run(
        _cfg(),
        _state(ce=_flat_leg(last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
    )
    entries = _for(out, "ce", "enter")
    assert len(entries) == 1
    assert entries[0].log_event == "ce_entry_signal"
    assert entries[0].log_extra["atm"] == 24500.0


def test_confirm_mode_logs_the_signal_but_does_not_enter():
    out = _run(
        _cfg(execution_mode="confirm"),
        _state(ce=_flat_leg(last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
    )
    assert _kinds(out, "ce") == ["note"]
    assert _for(out, "ce")[0].log_event == "ce_entry_signal"


def test_no_entries_during_the_force_exit_window():
    out = _run(
        _cfg(),
        _state(ce=_flat_leg(last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
        force=True,
    )
    assert _for(out, "ce", "enter") == []


def test_max_entries_reached_decides_nothing():
    out = _run(
        _cfg(max_reentries_ce=1),
        _state(ce=_flat_leg(entries_today=2, last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
    )
    assert _for(out, "ce", "enter") == []


def test_max_reentries_zero_pins_the_or_1_fallback():
    """``max_reentries_ce=0`` does not mean "no re-entries" — it means one.

    ``_can_enter_ce`` reads ``int(cfg.get("max_reentries_ce") or 1)``, and
    ``0 or 1`` is ``1``, so a configured zero silently allows a second entry.
    Surfaced by this refactor; pinned rather than changed, because fixing it
    alters live re-entry behaviour. A deliberate fix should fail here.
    """
    out = _run(
        _cfg(max_reentries_ce=0),
        _state(ce=_flat_leg(entries_today=1, last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
    )
    assert _kinds(out, "ce") == ["enter"], "0 currently behaves as 1 re-entry"


# --------------------------------------------------------------------------- #
# The whipsaw guard — the reason decide() has to model the exit locally
# --------------------------------------------------------------------------- #


def test_exit_and_reentry_on_the_same_bar_cannot_both_happen():
    """2026-07-14: intrabar zone exits re-triggering same-bar re-entries."""
    state = _state(pe=_open_short())
    out = _run(
        _cfg(),
        state,
        pe_sig=_signals(short_zone_exit=True, long_entry=True, long_ready=True),
    )
    assert "exit" in _kinds(out, "pe")
    assert "enter" not in _kinds(out, "pe"), "same-bar re-entry after an exit"


def test_the_blocked_reentry_is_recorded():
    state = _state(pe=_open_short())
    out = _run(
        _cfg(),
        state,
        pe_sig=_signals(short_zone_exit=True, long_entry=True, long_ready=True),
    )
    notes = [d.log_event for d in _for(out, "pe", "note")]
    assert "pe_skipped_same_bar" in notes


def test_decide_does_not_mutate_the_state_it_was_given():
    """The local exit model must not leak back into the caller's dict."""
    leg = _open_short()
    state = _state(pe=leg)
    _run(_cfg(), state, pe_sig=_signals(short_zone_exit=True))
    assert state["pe"]["status"] == "open"
    assert leg["status"] == "open"


def test_reentry_cooldown_is_recorded():
    out = _run(
        _cfg(),
        _state(ce=_flat_leg(last_exit_bar_ts=BAR, last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
    )
    notes = [d.log_event for d in _for(out, "ce", "note")]
    assert "ce_reentry_cooldown" in notes


# --------------------------------------------------------------------------- #
# The gate seam — inert by default, can only ever suppress
# --------------------------------------------------------------------------- #


def test_no_gate_means_unchanged_behaviour():
    args = (_cfg(), _state(ce=_flat_leg(last_action_bar_ts=PREV_BAR)))
    kw = {"ce_sig": _signals(long_entry=True, long_ready=True)}
    assert _kinds(_run(*args, **kw)) == _kinds(_run(*args, **kw, gate=None))


def test_a_gate_can_suppress_an_entry():
    out = _run(
        _cfg(),
        _state(ce=_flat_leg(last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
        gate=lambda leg, reason, sig: (False, "into the call wall"),
    )
    assert _kinds(out, "ce") == ["note"]
    note = _for(out, "ce")[0]
    assert note.log_event == "ce_entry_gated"
    assert "call wall" in note.log_detail


def test_a_permissive_gate_changes_nothing():
    out = _run(
        _cfg(),
        _state(ce=_flat_leg(last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
        gate=lambda leg, reason, sig: (True, "short gamma"),
    )
    assert _kinds(out, "ce") == ["enter"]


def test_a_gate_is_never_consulted_for_exits():
    """A gate bug must not be able to leave a position unmanaged."""
    calls: list[str] = []

    def gate(leg, reason, sig):
        calls.append(leg)
        return False, "veto everything"

    out = _run(
        _cfg(),
        _state(pe=_open_short()),
        pe_sig=_signals(short_zone_exit=True),
        gate=gate,
    )
    assert len(_for(out, "pe", "exit")) == 1
    assert _for(out, "pe", "enter") == []
    assert calls == [], "gate must not be consulted for exits"


def test_a_broken_gate_fails_open_to_the_3st_signal():
    out = _run(
        _cfg(),
        _state(ce=_flat_leg(last_action_bar_ts=PREV_BAR)),
        ce_sig=_signals(long_entry=True, long_ready=True),
        gate=lambda leg, reason, sig: (_ for _ in ()).throw(RuntimeError("gate down")),
    )
    assert _kinds(out, "ce") == ["enter"]


# --------------------------------------------------------------------------- #
# Pinned, not endorsed
# --------------------------------------------------------------------------- #


def test_dual_open_pins_current_stale_read_behaviour():
    """``allow_dual_open=False`` does not actually prevent a same-tick dual open.

    The loop this replaced read the leg snapshot once before iterating, so the
    CE entry was not visible to the PE check. ``decide()`` reproduces that
    deliberately — surfaced by the refactor, pinned here so a deliberate fix
    shows up as a failure in this test rather than as a silent behaviour change.
    """
    out = _run(
        _cfg(allow_dual_open=False),
        _state(
            ce=_flat_leg(last_action_bar_ts=PREV_BAR),
            pe=_flat_leg(last_action_bar_ts=PREV_BAR),
        ),
        ce_sig=_signals(long_entry=True, long_ready=True),
        pe_sig=_signals(long_entry=True, long_ready=True),
    )
    assert _kinds(out, "ce") == ["enter"]
    assert _kinds(out, "pe") == ["enter"]


def test_dual_open_does_block_against_an_already_open_leg():
    out = _run(
        _cfg(allow_dual_open=False),
        _state(
            ce=_open_short(tradingsymbol="NIFTY2690924500CE"),
            pe=_flat_leg(last_action_bar_ts=PREV_BAR),
        ),
        pe_sig=_signals(long_entry=True, long_ready=True),
    )
    assert _for(out, "pe", "enter") == []


# --------------------------------------------------------------------------- #
# apply_decisions — the side-effecting half
# --------------------------------------------------------------------------- #


def test_a_failed_exit_blocks_the_entry_behind_it(monkeypatch):
    """decide() assumed the exit worked; if it did not, the leg is still open."""
    logged: list[str] = []
    monkeypatch.setattr(rs, "append_log", lambda e, d="", x=None: logged.append(e))
    monkeypatch.setattr(
        rs, "_exit_leg", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("not algo-managed"))
    )
    entered: list[str] = []
    monkeypatch.setattr(rs, "_enter_leg", lambda leg, *a, **k: entered.append(leg))

    decisions = [
        rs.Decision("ce", "exit", "long_zone_exit"),
        rs.Decision("ce", "enter", "long_entry"),
    ]
    errors = rs.apply_decisions(decisions, _cfg(), atm=24500.0, spot=24512.0)

    assert entered == []
    assert errors == ["ce exit: not algo-managed"]
    assert "ce_entry_skipped_after_failed_exit" in logged


def test_error_decisions_surface_as_tick_errors():
    errors = rs.apply_decisions(
        [rs.Decision("pe", "error", "pe entry: boom")], _cfg(), atm=1.0, spot=1.0
    )
    assert errors == ["pe entry: boom"]


def test_notes_only_log(monkeypatch):
    logged: list[str] = []
    monkeypatch.setattr(rs, "append_log", lambda e, d="", x=None: logged.append(e))
    monkeypatch.setattr(rs, "_enter_leg", lambda *a, **k: pytest.fail("must not enter"))
    monkeypatch.setattr(rs, "_exit_leg", lambda *a, **k: pytest.fail("must not exit"))

    errors = rs.apply_decisions(
        [rs.Decision("ce", "note", "x", log_event="ce_entry_signal", log_detail="d")],
        _cfg(),
        atm=1.0,
        spot=1.0,
    )
    assert errors == []
    assert logged == ["ce_entry_signal"]


def test_purge_does_not_log(monkeypatch):
    """Preserved from the loop this replaced — the purge was silent."""
    logged: list[str] = []
    monkeypatch.setattr(rs, "append_log", lambda e, d="", x=None: logged.append(e))
    monkeypatch.setattr(rs, "get_state", lambda: {"ce": {}, "pe": {}})
    monkeypatch.setattr(rs, "save_state", lambda patch: None)

    rs.apply_decisions(
        [rs.Decision("ce", "purge", "purged_foreign_symbol", patch={"status": "flat"})],
        _cfg(),
        atm=1.0,
        spot=1.0,
    )
    assert logged == []
