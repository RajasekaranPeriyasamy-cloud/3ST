"""Global ARM / DISARM gate for live order placement."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from settings import data_dir

ARM_STATE_FILE = data_dir() / "arm_state.json"


@dataclass
class ArmState:
    armed: bool = False
    mode: str = "paper"  # paper | live
    armed_at: str | None = None
    note: str = "DISARMED by default — no live orders"


_STATE = ArmState()


def _persist_state() -> None:
    payload = {
        "armed": _STATE.armed,
        "mode": _STATE.mode,
        "armed_at": _STATE.armed_at,
        "note": _STATE.note,
    }
    ARM_STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_persisted_state() -> None:
    """Restore ARM/mode from disk (survives API restart)."""
    if not ARM_STATE_FILE.exists():
        return
    try:
        raw = json.loads(ARM_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    mode = str(raw.get("mode") or "paper").lower()
    if mode not in {"paper", "live"}:
        mode = "paper"
    _STATE.mode = mode
    _STATE.armed = bool(raw.get("armed")) and mode == "live"
    _STATE.armed_at = raw.get("armed_at") if _STATE.armed else None
    if _STATE.armed:
        _STATE.note = str(raw.get("note") or "ARMED — live orders allowed")
    elif mode == "live":
        _STATE.note = str(raw.get("note") or "DISARMED — orders blocked")
    else:
        _STATE.note = "Paper mode — simulated fills only"
        _STATE.armed = False
        _STATE.armed_at = None


load_persisted_state()


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
    elif not _STATE.armed:
        _STATE.note = "DISARMED — orders blocked"
    _persist_state()
    return get_arm_state()


def arm(confirm: bool = False) -> dict:
    if _STATE.mode != "live":
        raise RuntimeError("Switch mode to 'live' before ARM.")
    if not confirm:
        raise RuntimeError("ARM requires confirm=true")
    _STATE.armed = True
    _STATE.armed_at = datetime.now().isoformat(timespec="seconds")
    _STATE.note = "ARMED — live orders allowed"
    _persist_state()
    return get_arm_state()


def disarm() -> dict:
    _STATE.armed = False
    _STATE.armed_at = None
    _STATE.note = "DISARMED — orders blocked"
    _persist_state()
    return get_arm_state()


def require_armed_for_live() -> None:
    try:
        from execution.panic import is_panic_active

        if is_panic_active():
            return
    except ImportError:
        pass
    if _STATE.mode == "live" and not _STATE.armed:
        raise RuntimeError("Live mode is DISARMED. ARM from the Live desk first.")
