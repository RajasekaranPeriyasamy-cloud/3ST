"""Position Ledger — single source of truth for every leg any runner has open.

Part of the Phase 3 execution-architecture plan (see
docs/CONVERSATION_SUMMARY.md, "Execution architecture — phase reminders").

Today (pre-Phase-3), Rolling Straddle, Watchlist/Live Desk, Survivor, Wave, and
Premium Book each keep their own state file and each reconcile against Kite
independently (``execution/reconcile.py`` only covers the watchlist store;
Rolling Straddle has its own broker-sync in ``rolling_straddle.py``). That's
exactly why orphan positions have shown up before (see "Broker sync — CE open
locally but flat at Kite", 2026-07-13) — every extra store is another place
local/broker drift can happen.

This module doesn't replace those stores yet (that's the runner-by-runner
migration in the Phase 3 plan). It gives every owner one shared place to
record "I opened/closed this leg", plus one shared reconcile-against-broker
routine, so new callers (starting with ``execution/order_router.py``) don't
need to invent their own bookkeeping.

Storage: ``data/position_ledger.json`` — same flat-JSON-file convention as
``execution/arming.py`` (arm_state.json) and ``risk/limits.py``
(risk_limits.json). Fine at this scale; revisit only if row count or
concurrent-writer count grows a lot.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from broker.base import Broker
from settings import data_dir
from utils.logging import get_logger, log_event

logger = get_logger("position_ledger")

LEDGER_FILE = data_dir() / "position_ledger.json"

Status = str  # "pending" | "open" | "closing" | "closed" | "error"

_lock = threading.Lock()


@dataclass
class LedgerEntry:
    leg_id: str
    owner: str
    instance_id: str
    leg_key: str
    exchange: str
    tradingsymbol: str
    product: str = "MIS"
    status: Status = "pending"
    side: str | None = None  # "long" | "short"
    quantity: int = 0  # signed, broker-truth once open
    entry_price: float | None = None
    exit_price: float | None = None
    entry_order_id: str | None = None
    exit_order_id: str | None = None
    entry_tag: str | None = None
    exit_tag: str | None = None
    reason: str = ""
    error: str | None = None
    created_at: str = field(default_factory=lambda: _now())
    updated_at: str = field(default_factory=lambda: _now())
    opened_at: str | None = None
    closed_at: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _instrument_key(exchange: str, tradingsymbol: str, product: str | None = None) -> str:
    key = f"{exchange.upper()}:{tradingsymbol.upper()}"
    return f"{key}:{product.upper()}" if product else key


_LEDGER: dict[str, LedgerEntry] = {}


def _load() -> None:
    global _LEDGER
    if not LEDGER_FILE.exists():
        return
    try:
        raw = json.loads(LEDGER_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, dict):
        return
    loaded: dict[str, LedgerEntry] = {}
    for leg_id, row in raw.items():
        try:
            loaded[leg_id] = LedgerEntry(**row)
        except TypeError:
            # Unknown/renamed field from an older schema — skip rather than crash.
            continue
    _LEDGER = loaded


def _save() -> None:
    payload = {leg_id: asdict(row) for leg_id, row in _LEDGER.items()}
    LEDGER_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


_load()


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------


def get(leg_id: str) -> dict[str, Any] | None:
    row = _LEDGER.get(leg_id)
    return asdict(row) if row else None


def list_all(*, owner: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    rows = list(_LEDGER.values())
    if owner is not None:
        rows = [r for r in rows if r.owner == owner]
    if status is not None:
        rows = [r for r in rows if r.status == status]
    return [asdict(r) for r in rows]


def list_open() -> list[dict[str, Any]]:
    return list_all(status="open") + list_all(status="closing")


def find_by_instrument(
    exchange: str, tradingsymbol: str, product: str | None = None
) -> list[dict[str, Any]]:
    key = _instrument_key(exchange, tradingsymbol, product)
    out = []
    for row in _LEDGER.values():
        row_key = _instrument_key(row.exchange, row.tradingsymbol, row.product if product else None)
        if row_key == key:
            out.append(asdict(row))
    return out


def has_open_tag(entry_tag: str) -> bool:
    """True if some pending/open leg already used this entry tag today.

    Backs the order router's duplicate-entry guard (Phase 3 acceptance
    criterion: "Duplicate entry blocked by tag within same bar").
    """
    return any(
        row.entry_tag == entry_tag and row.status in {"pending", "open"} for row in _LEDGER.values()
    )


def upsert_pending(intent: Any) -> dict[str, Any]:
    """Record a signal intent as ``pending`` before an order is sent.

    Accepts an ``execution.signal_bus.SignalIntent`` (duck-typed here to avoid
    a hard import cycle — signal_bus has no ledger import, this module doesn't
    need to import signal_bus either).
    """
    with _lock:
        leg_id = intent.leg_id
        existing = _LEDGER.get(leg_id)
        row = existing or LedgerEntry(
            leg_id=leg_id,
            owner=intent.owner,
            instance_id=intent.instance_id,
            leg_key=intent.leg_key,
            exchange=intent.exchange,
            tradingsymbol=intent.tradingsymbol,
        )
        row.exchange = intent.exchange
        row.tradingsymbol = intent.tradingsymbol
        row.product = intent.product
        row.status = "pending"
        row.reason = intent.reason
        row.entry_tag = intent.tag() if intent.kind == "enter" else row.entry_tag
        row.exit_tag = intent.tag() if intent.kind == "exit" else row.exit_tag
        row.error = None
        row.updated_at = _now()
        row.meta = {**row.meta, **(intent.meta or {})}
        _LEDGER[leg_id] = row
        _save()
        return asdict(row)


def mark_open(
    leg_id: str,
    *,
    quantity: int,
    entry_price: float | None,
    order_id: str | None,
    tag: str | None = None,
) -> dict[str, Any]:
    with _lock:
        row = _require(leg_id)
        row.status = "open"
        row.quantity = int(quantity)
        row.side = "long" if quantity > 0 else "short" if quantity < 0 else row.side
        row.entry_price = entry_price
        row.entry_order_id = order_id
        if tag:
            row.entry_tag = tag
        row.opened_at = row.opened_at or _now()
        row.updated_at = _now()
        row.error = None
        _LEDGER[leg_id] = row
        _save()
        log_event(logger, logging.INFO, "ledger_opened", leg_id=leg_id, quantity=quantity)
        return asdict(row)


def mark_closed(
    leg_id: str,
    *,
    exit_price: float | None = None,
    order_id: str | None = None,
    tag: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    with _lock:
        row = _require(leg_id)
        row.status = "closed"
        row.quantity = 0
        row.exit_price = exit_price
        row.exit_order_id = order_id
        if tag:
            row.exit_tag = tag
        if reason:
            row.reason = reason
        row.closed_at = _now()
        row.updated_at = _now()
        row.error = None
        _LEDGER[leg_id] = row
        _save()
        log_event(logger, logging.INFO, "ledger_closed", leg_id=leg_id, reason=row.reason)
        return asdict(row)


def mark_error(leg_id: str, message: str) -> dict[str, Any]:
    with _lock:
        row = _require(leg_id)
        row.status = "error"
        row.error = message
        row.updated_at = _now()
        _LEDGER[leg_id] = row
        _save()
        log_event(logger, logging.WARNING, "ledger_error", leg_id=leg_id, error=message)
        return asdict(row)


def remove(leg_id: str) -> None:
    """Forget a local record — mirrors existing "unlink" semantics elsewhere:
    never places an order, just stops tracking. Use for manual cleanup only.
    """
    with _lock:
        _LEDGER.pop(leg_id, None)
        _save()


def _require(leg_id: str) -> LedgerEntry:
    row = _LEDGER.get(leg_id)
    if row is None:
        raise KeyError(f"No ledger entry for {leg_id} — call upsert_pending() first")
    return row


# --------------------------------------------------------------------------
# Reconcile against broker truth
# --------------------------------------------------------------------------


def reconcile_with_broker(broker: Broker, *, apply_changes: bool = True) -> dict[str, Any]:
    """Diff the ledger against the broker's actual open positions.

    Same safety rule as ``execution/reconcile.py``: a transient broker-read
    failure must never be interpreted as "broker is flat" (which would close
    every open leg). If positions can't be read, abort with no mutations.
    """
    report: dict[str, Any] = {
        "ok": True,
        "checked_at": _now(),
        "apply_changes": apply_changes,
        "updated": [],
        "closed_stale": [],
        "orphans": [],
        "errors": [],
    }

    try:
        positions = broker.positions()
    except Exception as exc:
        report["ok"] = False
        report["aborted"] = "could not read broker positions — no changes made"
        report["errors"].append({"stage": "positions", "error": str(exc)})
        return report

    pos_by_key: dict[str, dict[str, Any]] = {}
    for p in positions:
        if int(p.get("quantity") or 0) == 0:
            continue
        key = _instrument_key(str(p.get("exchange") or ""), str(p.get("tradingsymbol") or ""))
        pos_by_key[key] = p

    covered_keys: set[str] = set()
    open_rows = [r for r in _LEDGER.values() if r.status in {"open", "closing"}]

    for row in open_rows:
        key = _instrument_key(row.exchange, row.tradingsymbol)
        covered_keys.add(key)
        pos = pos_by_key.get(key)

        if pos is not None:
            broker_qty = int(pos.get("quantity") or 0)
            broker_avg = pos.get("average_price")
            drift: dict[str, Any] = {}
            if broker_qty and broker_qty != row.quantity:
                drift["quantity"] = broker_qty
            if broker_avg is not None and (
                row.entry_price is None or abs(float(broker_avg) - float(row.entry_price)) > 0.01
            ):
                drift["entry_price"] = round(float(broker_avg), 2)
            if drift and apply_changes:
                with _lock:
                    if "quantity" in drift:
                        row.quantity = drift["quantity"]
                    if "entry_price" in drift:
                        row.entry_price = drift["entry_price"]
                    row.updated_at = _now()
                    _LEDGER[row.leg_id] = row
                    _save()
                report["updated"].append({"leg_id": row.leg_id, **drift})
            continue

        # Broker flat, ledger says open -> local record is stale.
        entry = {"leg_id": row.leg_id, "owner": row.owner, "was_quantity": row.quantity}
        if apply_changes:
            mark_closed(row.leg_id, reason="reconcile_broker_flat")
        report["closed_stale"].append(entry)

    for key, pos in pos_by_key.items():
        if key in covered_keys:
            continue
        report["orphans"].append(
            {
                "key": key,
                "exchange": pos.get("exchange"),
                "tradingsymbol": pos.get("tradingsymbol"),
                "quantity": pos.get("quantity"),
                "average_price": pos.get("average_price"),
            }
        )

    return report
