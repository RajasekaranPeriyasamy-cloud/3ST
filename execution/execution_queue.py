"""Unified execution queue — aggregates Rolling Straddle, watchlist, and broker orphans."""

from __future__ import annotations

import re
from typing import Any, Literal

from execution.arming import get_arm_state
from execution.desk_trades import adopt_position, build_active_trades_view
from execution.positions_view import _position_key
from execution.rolling_straddle import (
    adopt_leg,
    close_leg,
    dismiss_leg_signal,
    ship_leg_entry,
    status_bundle as rs_status_bundle,
    unlink_leg,
)
from execution.watchlist_activation import activate_watchlist_item
from execution.watchlist_close import close_watchlist_trade, unlink_watchlist_item
from kite_client import session_status
from watchlist_store import get_item, list_items, update_item

QueueAction = Literal["adopt", "unlink", "close", "ship", "execute", "dismiss"]


def _side_from_qty(qty: int | float | None) -> str | None:
    q = int(qty or 0)
    if q > 0:
        return "long"
    if q < 0:
        return "short"
    return None


def _option_type(tradingsymbol: str) -> str | None:
    sym = tradingsymbol.upper()
    if sym.endswith("CE"):
        return "CE"
    if sym.endswith("PE"):
        return "PE"
    return None


def _strike_hint(tradingsymbol: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)(CE|PE)$", tradingsymbol.upper())
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _rs_instance_id(cfg: dict[str, Any]) -> str:
    underlying = str(cfg.get("underlying") or "NIFTY").lower()
    expiry = str(cfg.get("expiry") or "default")
    return f"rs-{underlying}-{expiry}"


def _queue_item(
    *,
    leg_id: str,
    source: str,
    status: str,
    exchange: str,
    tradingsymbol: str,
    qty: int,
    side: str | None,
    managed: bool,
    instance_id: str | None = None,
    owner_label: str | None = None,
    entry_price: float | None = None,
    ltp: float | None = None,
    pnl: float | None = None,
    exit_triggers: dict[str, Any] | None = None,
    signal_note: str | None = None,
    actions: list[str],
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "leg_id": leg_id,
        "source": source,
        "instance_id": instance_id,
        "owner_label": owner_label,
        "instrument": tradingsymbol,
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "option_type": _option_type(tradingsymbol),
        "strike": _strike_hint(tradingsymbol),
        "status": status,
        "side": side,
        "qty": qty,
        "managed": managed,
        "entry_price": entry_price,
        "ltp": ltp,
        "pnl": pnl,
        "exit_triggers": exit_triggers,
        "signal_note": signal_note,
        "actions": actions,
        "meta": meta or {},
    }


def _exit_triggers_from_rs_leg(leg: dict[str, Any]) -> dict[str, Any] | None:
    params = leg.get("exit_params") or {}
    if not params:
        zone = leg.get("zone_exit_level")
        if zone is None:
            return None
        return {
            "zone_exit_level": zone,
            "zone_exit_label": leg.get("zone_exit_label"),
            "next_exit": params.get("next_exit"),
        }
    return params


def _rs_pending_items(cfg: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    if str(cfg.get("execution_mode") or "auto") != "confirm":
        return []
    if state.get("runner") != "running":
        return []
    instance_id = _rs_instance_id(cfg)
    underlying = str(cfg.get("underlying") or "NIFTY")
    pending: list[dict[str, Any]] = []
    for leg_key in ("ce", "pe"):
        leg = dict(state.get(leg_key) or {})
        if leg.get("status") == "open" or leg.get("blocked"):
            continue
        ready = bool(leg.get("short_ready") or leg.get("long_ready"))
        if not ready:
            continue
        side = "short" if leg.get("short_ready") else "long"
        note_parts = []
        if leg.get("short_ready"):
            note_parts.append("short ready")
        if leg.get("long_ready"):
            note_parts.append("long ready")
        pending.append(
            _queue_item(
                leg_id=f"rs:pending:{leg_key}",
                source="rolling_straddle",
                status="pending",
                exchange=str(leg.get("exchange") or "NFO"),
                tradingsymbol=str(leg.get("tradingsymbol") or f"{underlying} ATM {leg_key.upper()}"),
                qty=0,
                side=side,
                managed=False,
                instance_id=instance_id,
                owner_label=f"RS {underlying} {leg_key.upper()}",
                signal_note=", ".join(note_parts) or "signal ready",
                actions=["ship", "dismiss"],
                meta={"leg_key": leg_key, "underlying": underlying},
            )
        )
    return pending


def _rs_active_and_orphans(rs_bundle: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cfg = rs_bundle.get("config") or {}
    state = rs_bundle.get("state") or {}
    instance_id = _rs_instance_id(cfg)
    underlying = str(cfg.get("underlying") or "NIFTY")
    active: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []

    for leg_key in ("ce", "pe"):
        leg = dict(state.get(leg_key) or {})
        if leg.get("status") != "open":
            continue
        sym = str(leg.get("tradingsymbol") or "")
        exch = str(leg.get("exchange") or "NFO")
        qty = int(leg.get("broker_qty") or 0)
        if qty == 0 and leg.get("entry_side"):
            qty = -1 if str(leg.get("position_side") or "").lower() == "short" else 1
        side = leg.get("position_side") or _side_from_qty(qty)
        active.append(
            _queue_item(
                leg_id=f"rs:{leg_key}",
                source="rolling_straddle",
                status="active",
                exchange=exch,
                tradingsymbol=sym,
                qty=qty,
                side=str(side) if side else None,
                managed=True,
                instance_id=instance_id,
                owner_label=f"RS {underlying} {leg_key.upper()}",
                entry_price=float(leg["entry_price"]) if leg.get("entry_price") is not None else None,
                ltp=float(leg["ltp"]) if leg.get("ltp") is not None else None,
                exit_triggers=_exit_triggers_from_rs_leg(leg),
                actions=["close", "unlink"],
                meta={"leg_key": leg_key},
            )
        )

    for orphan in rs_bundle.get("orphans") or []:
        leg_key = orphan.get("leg_key") or "ce"
        sym = str(orphan.get("tradingsymbol") or "")
        exch = str(orphan.get("exchange") or "NFO")
        qty = int(orphan.get("quantity") or 0)
        orphans.append(
            _queue_item(
                leg_id=f"rs:orphan:{leg_key}",
                source="rolling_straddle",
                status="orphan",
                exchange=exch,
                tradingsymbol=sym,
                qty=qty,
                side=_side_from_qty(qty),
                managed=False,
                instance_id=instance_id,
                owner_label=f"RS {underlying} {leg_key.upper()}",
                entry_price=float(orphan["average_price"]) if orphan.get("average_price") is not None else None,
                signal_note="Not managed by Rolling Straddle",
                actions=["adopt", "close"],
                meta={"leg_key": leg_key, "has_3st_order": orphan.get("has_3st_order")},
            )
        )
    return active, orphans


def _watchlist_pending() -> list[dict[str, Any]]:
    pending: list[dict[str, Any]] = []
    for item in list_items("triggered"):
        item_id = str(item.get("id") or "")
        if not item_id:
            continue
        sym = str(item.get("tradingsymbol") or "")
        exch = str(item.get("exchange") or "")
        pending.append(
            _queue_item(
                leg_id=f"wl:pending:{item_id}",
                source="watchlist",
                status="pending",
                exchange=exch,
                tradingsymbol=sym,
                qty=int(item.get("entry_qty") or item.get("lot_size") or 0),
                side=str(item.get("signal") or "long"),
                managed=False,
                owner_label="Watchlist",
                signal_note=str(item.get("signal_note") or item.get("trigger_note") or "3ST signal triggered"),
                actions=["execute", "dismiss"],
                meta={"item_id": item_id},
            )
        )
    return pending


def _watchlist_active_and_orphans(desk_view: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    active: list[dict[str, Any]] = []
    orphans: list[dict[str, Any]] = []

    for trade in desk_view.get("trades") or []:
        item_id = str(trade.get("id") or "")
        if not item_id:
            continue
        qty = int(trade.get("quantity") or 0)
        side = str(trade.get("signal") or ("short" if trade.get("entry_side") == "SELL" else "long"))
        signed_qty = -abs(qty) if side == "short" else abs(qty)
        active.append(
            _queue_item(
                leg_id=f"wl:{item_id}",
                source="watchlist",
                status="active",
                exchange=str(trade.get("exchange") or ""),
                tradingsymbol=str(trade.get("tradingsymbol") or ""),
                qty=signed_qty,
                side=side,
                managed=True,
                owner_label="Watchlist",
                entry_price=float(trade["entry_price"]) if trade.get("entry_price") is not None else None,
                ltp=float(trade["last_price"]) if trade.get("last_price") is not None else None,
                pnl=float(trade["pnl"]) if trade.get("pnl") is not None else None,
                exit_triggers={
                    "exit_label": trade.get("exit_label"),
                    "exit_line": trade.get("exit_line"),
                    "st_exit_price": trade.get("st_exit_price"),
                    "st_exit_label": trade.get("st_exit_label"),
                },
                actions=["close", "unlink"],
                meta={"item_id": item_id},
            )
        )

    seen = {_position_key(str(t.get("exchange") or ""), str(t.get("tradingsymbol") or "")) for t in active}
    for orphan in desk_view.get("orphans") or []:
        exch = str(orphan.get("exchange") or "")
        sym = str(orphan.get("tradingsymbol") or "")
        key = _position_key(exch, sym)
        if key in seen:
            continue
        qty = int(orphan.get("quantity") or 0)
        orphans.append(
            _queue_item(
                leg_id=f"wl:orphan:{exch}:{sym}",
                source="watchlist",
                status="orphan",
                exchange=exch,
                tradingsymbol=sym,
                qty=qty,
                side=_side_from_qty(qty),
                managed=False,
                owner_label="Kite",
                entry_price=float(orphan["average_price"]) if orphan.get("average_price") is not None else None,
                pnl=float(orphan["pnl"]) if orphan.get("pnl") is not None else None,
                signal_note="Open on Kite — not on Live Desk",
                actions=["adopt", "close"],
                meta={},
            )
        )
    return active, orphans


def _dedupe_orphans(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = _position_key(item.get("exchange") or "", item.get("tradingsymbol") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def build_execution_queue() -> dict[str, Any]:
    rs_bundle = rs_status_bundle()
    desk_view = build_active_trades_view()

    pending = _rs_pending_items(rs_bundle.get("config") or {}, rs_bundle.get("state") or {})
    pending.extend(_watchlist_pending())

    rs_active, rs_orphans = _rs_active_and_orphans(rs_bundle)
    wl_active, wl_orphans = _watchlist_active_and_orphans(desk_view)

    active = rs_active + wl_active
    orphans = _dedupe_orphans(rs_orphans + wl_orphans)

    errors: list[dict[str, Any]] = []
    for msg in rs_bundle.get("broker_mismatches") or []:
        errors.append({"leg_id": "rs:reconcile", "message": str(msg), "source": "rolling_straddle"})

    arm = get_arm_state()
    summary = {
        "pending_count": len(pending),
        "active_count": len(active),
        "orphan_count": len(orphans),
        "error_count": len(errors),
        "armed": bool(arm.get("armed")),
        "mode": str(arm.get("mode") or "paper"),
        "kite_authenticated": bool(session_status().get("authenticated")),
        "rs_runner": rs_bundle.get("state", {}).get("runner"),
        "rs_underlying": (rs_bundle.get("config") or {}).get("underlying"),
    }

    return {
        "pending": pending,
        "active": active,
        "orphans": orphans,
        "errors": errors,
        "summary": summary,
        "arm": arm,
    }


def _parse_leg_id(leg_id: str) -> tuple[str, dict[str, str]]:
    parts = leg_id.split(":")
    if not parts:
        raise ValueError("Invalid leg_id")
    if parts[0] == "rs":
        if len(parts) == 2 and parts[1] in {"ce", "pe"}:
            return "rs_active", {"leg_key": parts[1]}
        if len(parts) == 3 and parts[1] == "orphan":
            return "rs_orphan", {"leg_key": parts[2]}
        if len(parts) == 3 and parts[1] == "pending":
            return "rs_pending", {"leg_key": parts[2]}
    if parts[0] == "wl":
        if len(parts) == 2:
            return "wl_active", {"item_id": parts[1]}
        if len(parts) == 3 and parts[1] == "pending":
            return "wl_pending", {"item_id": parts[2]}
        if len(parts) == 4 and parts[1] == "orphan":
            return "wl_orphan", {"exchange": parts[2], "tradingsymbol": parts[3]}
    raise ValueError(f"Unknown leg_id: {leg_id}")


def queue_action(leg_id: str, action: QueueAction) -> dict[str, Any]:
    kind, params = _parse_leg_id(leg_id)
    if action == "adopt":
        if kind == "rs_orphan":
            return adopt_leg(params["leg_key"])  # type: ignore[arg-type]
        if kind == "wl_orphan":
            return adopt_position(params["exchange"], params["tradingsymbol"])
        raise RuntimeError("Adopt not supported for this queue item")
    if action == "unlink":
        if kind == "rs_active":
            return unlink_leg(params["leg_key"])  # type: ignore[arg-type]
        if kind == "wl_active":
            return unlink_watchlist_item(params["item_id"])
        raise RuntimeError("Unlink not supported for this queue item")
    if action == "close":
        if kind == "rs_active":
            return close_leg(params["leg_key"])  # type: ignore[arg-type]
        if kind == "rs_orphan":
            return close_leg(params["leg_key"])  # type: ignore[arg-type]
        if kind in {"wl_active", "wl_orphan"}:
            if kind == "wl_orphan":
                from execution.desk_trades import close_broker_position

                return close_broker_position(params["exchange"], params["tradingsymbol"])
            return {"ok": True, "item": close_watchlist_trade(params["item_id"], "manual_close")}
        raise RuntimeError("Close not supported for this queue item")
    if action == "ship":
        if kind != "rs_pending":
            raise RuntimeError("Ship only applies to pending Rolling Straddle signals")
        return ship_leg_entry(params["leg_key"])  # type: ignore[arg-type]
    if action == "execute":
        if kind == "wl_pending":
            item = get_item(params["item_id"])
            if not item:
                raise KeyError("Watchlist item not found")
            updated = activate_watchlist_item(params["item_id"])
            return {"ok": True, "item": updated}
        raise RuntimeError("Execute not supported for this queue item")
    if action == "dismiss":
        if kind == "rs_pending":
            return dismiss_leg_signal(params["leg_key"])  # type: ignore[arg-type]
        if kind == "wl_pending":
            updated = update_item(
                params["item_id"],
                {"status": "waiting", "signal": None, "signal_note": None, "trigger_note": None},
            )
            return {"ok": True, "item": updated}
        raise RuntimeError("Dismiss not supported for this queue item")
    raise RuntimeError(f"Unknown action: {action}")
