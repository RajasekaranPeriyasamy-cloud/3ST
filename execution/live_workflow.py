"""Live desk workflow — manual entry, exchange orders, 3ST exit."""

from __future__ import annotations

from typing import Any

from execution.arming import get_arm_state
from kite_client import session_status
from watchlist_store import get_item, list_items


def _step(ok: bool, label: str, detail: str = "") -> dict[str, Any]:
    return {"ok": ok, "label": label, "detail": detail}


def validate_live_execution() -> None:
    """Raise if exchange (Kite) orders cannot be placed now."""
    if not session_status().get("authenticated"):
        raise RuntimeError("Kite login required — sign in from Settings / auth URL first")
    arm = get_arm_state()
    if str(arm.get("mode") or "paper") != "live":
        raise RuntimeError("Set trading mode to LIVE on Live Desk (step 4)")
    if not arm.get("armed"):
        raise RuntimeError("ARM the desk before exchange orders — click ARM on Live Desk (step 5)")


def get_workflow_status() -> dict[str, Any]:
    """Checklist for the 7-step manual live workflow."""
    kite_ok = bool(session_status().get("authenticated"))
    arm = get_arm_state()
    mode = str(arm.get("mode") or "paper")
    armed = bool(arm.get("armed"))

    waiting_manual = [
        i for i in list_items("waiting")
        if str(i.get("entry_mode") or "manual") == "manual"
    ]
    active = list_items("active")

    steps = [
        _step(
            bool(waiting_manual or active),
            "1. Instrument on Live Desk",
            f"{len(waiting_manual)} waiting · {len(active)} active",
        ),
        _step(
            bool(waiting_manual or active),
            "2. Manual trade mode",
            "Entry manual — exit by SL/TSL/target or 3ST zone",
        ),
        _step(
            True,
            "3. Exit parameters",
            "ST1/ST2/ST3 zone + SL/TSL/target saved on each row",
        ),
        _step(
            bool(waiting_manual or active),
            "4. Live Desk queue",
            "Open Live Desk and pick BUY or SELL",
        ),
        _step(mode == "live", "5. LIVE mode", "Paper" if mode != "live" else "Live trade enabled"),
        _step(
            armed and mode == "live",
            "6. ARMED — exchange orders allowed",
            arm.get("note") or "",
        ),
        _step(
            len(active) > 0,
            "7. Exit monitor",
            f"Watching {len(active)} active trade(s) — SL/TSL/target then 3ST zone"
            if active
            else "Starts after BUY/SELL fills",
        ),
    ]

    ready = kite_ok and mode == "live" and armed and bool(waiting_manual or active)

    return {
        "kite_authenticated": kite_ok,
        "mode": mode,
        "armed": armed,
        "ready_to_execute": ready,
        "waiting_manual": len(waiting_manual),
        "active_trades": len(active),
        "steps": steps,
    }


def workflow_summary_for_item(item_id: str) -> dict[str, Any]:
    item = get_item(item_id)
    if not item:
        raise KeyError(f"Watchlist item not found: {item_id}")
    base = get_workflow_status()
    base["item"] = {
        "id": item_id,
        "tradingsymbol": item.get("tradingsymbol"),
        "exchange": item.get("exchange"),
        "timeframe": item.get("timeframe"),
        "entry_mode": item.get("entry_mode") or "manual",
        "status": item.get("status"),
        "st1_enabled": item.get("st1_enabled"),
        "st2_enabled": item.get("st2_enabled"),
        "st3_enabled": item.get("st3_enabled"),
    }
    return base
