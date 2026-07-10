"""Global ARM / DISARM gate for live order placement."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class ArmState:
    armed: bool = False
    mode: str = "paper"  # paper | live
    armed_at: str | None = None
    note: str = "DISARMED by default — no live orders"


_STATE = ArmState()


def get_arm_state() -> dict:
    return {
        "armed": _STATE.armed,
        "mode": _STATE.mode,
        "armed_at": _STATE.armed_at,
        "note": _STATE.note,
    }


def set_mode(mode: str) -> dict:
    mode = (mode or "paper").lower()
    if mode not in {"paper", "live"}:
        raise ValueError("mode must be 'paper' or 'live'")
    _STATE.mode = mode
    if mode == "paper":
        _STATE.armed = False
        _STATE.armed_at = None
        _STATE.note = "Paper mode — simulated fills only"
    return get_arm_state()


def arm(confirm: bool = False) -> dict:
    if _STATE.mode != "live":
        raise RuntimeError("Switch mode to 'live' before ARM.")
    if not confirm:
        raise RuntimeError("ARM requires confirm=true")
    _STATE.armed = True
    _STATE.armed_at = datetime.now().isoformat(timespec="seconds")
    _STATE.note = "ARMED — live orders allowed"
    return get_arm_state()


def disarm() -> dict:
    _STATE.armed = False
    _STATE.armed_at = None
    _STATE.note = "DISARMED — orders blocked"
    return get_arm_state()


def require_armed_for_live() -> None:
    if _STATE.mode == "live" and not _STATE.armed:
        raise RuntimeError("Live mode is DISARMED. ARM from the Live desk first.")
