"""Distinguish 3ST algo positions from manual Kite / paper trades."""

from __future__ import annotations

from typing import Any

# Legs adopted from broker/paper without an algo entry must never be exited or re-sized.
_EXTERNAL_ORIGINS = frozenset({"paper_sync", "reconcile_adopted", "reconcile"})


def leg_is_algo_managed(leg: dict[str, Any] | None) -> bool:
    """True only for legs opened by Rolling Straddle (or other 3ST algos), not manual Kite orders."""
    if not leg or leg.get("status") != "open":
        return False
    managed_by = str(leg.get("managed_by") or "").strip().lower()
    if managed_by == "algo":
        return True
    if managed_by in {"manual", "external", "kite_manual"}:
        return False
    last = str(leg.get("last_action") or "")
    if last in _EXTERNAL_ORIGINS or last.startswith("reconcile"):
        return False
    oid = str(leg.get("entry_order_id") or "")
    if oid.endswith("-sync"):
        return False
    if leg.get("entry_at") and last and last not in _EXTERNAL_ORIGINS:
        return True
    if oid and not oid.endswith("-sync"):
        return True
    return False


def order_tag_is_3st(tag: str | None) -> bool:
    return str(tag or "").strip().upper().startswith("3ST")


def paper_position_is_algo_owned(pos: dict[str, Any]) -> bool:
    """Paper lots created by algos — not manual watchlist / hand-entered paper trades."""
    tag = str(pos.get("tag") or "")
    if not order_tag_is_3st(tag):
        return False
    if "-sync" in tag.lower():
        return False
    if str(pos.get("source") or "") == "rolling_straddle" and tag.endswith("-sync"):
        return False
    return True
