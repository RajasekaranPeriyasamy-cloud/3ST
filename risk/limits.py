"""Risk limits for live / paper execution.

``max_daily_loss`` is the desk's loss cutout: once today's P&L crosses it,
``check_order`` refuses every new order. It is only as good as whatever keeps
``_daily_pnl`` current — until 2026-09-03 nothing did, and the cutout was dead
code. ``execution/pnl_tracker.py`` is the thing that feeds it now; this module
stays broker-free so it remains unit-testable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime

from settings import data_dir

LIMITS_FILE = data_dir() / "risk_limits.json"
#: Day P&L is persisted so an API restart mid-session cannot silently reopen the
#: loss cutout. Date-stamped, so yesterday's loss never blocks today.
DAILY_PNL_FILE = data_dir() / "risk_daily_pnl.json"


@dataclass
class RiskLimits:
    max_qty: float = 500.0
    max_open_positions: int = 6
    max_daily_loss: float = 10_000.0
    max_orders_per_minute: int = 10
    allowed_products: list[str] = field(default_factory=lambda: ["MIS", "NRML"])
    allowed_exchanges: list[str] = field(default_factory=lambda: ["NSE", "BSE", "NFO", "BFO", "MCX", "NCO"])


_PERSIST_KEYS = ("max_qty", "max_open_positions", "max_daily_loss", "max_orders_per_minute")

_LIMITS = RiskLimits()
_daily_pnl: float = 0.0
#: Trading day ``_daily_pnl`` belongs to. None means "never set".
_daily_pnl_date: str | None = None
#: "broker" (read from broker truth), "delta" (accumulated by record_pnl),
#: "restored" (loaded from disk after a restart) or "none".
_daily_pnl_source: str = "none"
_daily_pnl_at: str | None = None
_orders_this_minute: list[float] = []


def _persist_limits() -> None:
    payload = {k: getattr(_LIMITS, k) for k in _PERSIST_KEYS}
    LIMITS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_persisted_limits() -> None:
    """Restore user risk limits from disk (survives API restart / uvicorn reload)."""
    if not LIMITS_FILE.exists():
        return
    try:
        raw = json.loads(LIMITS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, dict):
        return
    for key in _PERSIST_KEYS:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        if key in {"max_open_positions", "max_orders_per_minute"}:
            value = int(value)
        elif key in {"max_qty", "max_daily_loss"}:
            value = float(value)
        setattr(_LIMITS, key, value)


def _load_persisted_state() -> None:
    """Import-time restore, mirroring execution/arming.py's pattern."""
    load_persisted_limits()
    load_persisted_daily_pnl()


def get_limits() -> dict:
    _roll_daily_pnl_if_stale()
    return {
        "max_qty": _LIMITS.max_qty,
        "max_open_positions": _LIMITS.max_open_positions,
        "max_daily_loss": _LIMITS.max_daily_loss,
        "max_orders_per_minute": _LIMITS.max_orders_per_minute,
        "allowed_products": list(_LIMITS.allowed_products),
        "allowed_exchanges": list(_LIMITS.allowed_exchanges),
        "daily_pnl": round(float(_daily_pnl), 2),
        "daily_pnl_date": _daily_pnl_date,
        "daily_pnl_source": _daily_pnl_source,
        "daily_pnl_at": _daily_pnl_at,
        "daily_loss_breached": _daily_pnl <= -abs(_LIMITS.max_daily_loss),
    }


def update_limits(**kwargs) -> dict:
    for k, v in kwargs.items():
        if hasattr(_LIMITS, k) and v is not None:
            setattr(_LIMITS, k, v)
    if any(k in kwargs and kwargs[k] is not None for k in _PERSIST_KEYS):
        _persist_limits()
    return get_limits()


def _today_str() -> str:
    return date.today().isoformat()


def _persist_daily_pnl() -> None:
    payload = {
        "date": _daily_pnl_date,
        "daily_pnl": round(float(_daily_pnl), 2),
        "source": _daily_pnl_source,
        "at": _daily_pnl_at,
    }
    try:
        DAILY_PNL_FILE.parent.mkdir(parents=True, exist_ok=True)
        DAILY_PNL_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        # Persistence is a restart convenience; never fail an order path over it.
        pass


def load_persisted_daily_pnl() -> None:
    """Restore today's P&L after a restart, so the cutout is not reopened.

    A stored value from an earlier day is discarded rather than carried forward
    — a stale loss must not refuse orders on a fresh session.
    """
    global _daily_pnl, _daily_pnl_date, _daily_pnl_source, _daily_pnl_at
    if not DAILY_PNL_FILE.exists():
        return
    try:
        raw = json.loads(DAILY_PNL_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, dict):
        return
    if str(raw.get("date") or "") != _today_str():
        return
    try:
        _daily_pnl = float(raw.get("daily_pnl") or 0.0)
    except (TypeError, ValueError):
        return
    _daily_pnl_date = _today_str()
    _daily_pnl_source = "restored"
    _daily_pnl_at = raw.get("at") if isinstance(raw.get("at"), str) else None


def _roll_daily_pnl_if_stale() -> None:
    """Zero the day's P&L when the trading day has turned over.

    Called from every read and from ``check_order`` — without it a loss booked
    yesterday would keep refusing orders this morning.
    """
    global _daily_pnl, _daily_pnl_date, _daily_pnl_source, _daily_pnl_at
    if _daily_pnl_date is None or _daily_pnl_date == _today_str():
        return
    _daily_pnl = 0.0
    _daily_pnl_date = None
    _daily_pnl_source = "none"
    _daily_pnl_at = None
    _persist_daily_pnl()


def set_daily_pnl(total: float, *, source: str = "broker") -> float:
    """Set today's P&L to an absolute figure — the authoritative path.

    Prefer this over ``record_pnl``: the broker already knows the day's realised
    plus unrealised P&L, so reading it is self-correcting and needs no
    per-fill attribution. See ``execution/pnl_tracker.py``.
    """
    global _daily_pnl, _daily_pnl_date, _daily_pnl_source, _daily_pnl_at
    _roll_daily_pnl_if_stale()
    _daily_pnl = float(total)
    _daily_pnl_date = _today_str()
    _daily_pnl_source = str(source)
    _daily_pnl_at = datetime.now().isoformat(timespec="seconds")
    _persist_daily_pnl()
    return _daily_pnl


def record_pnl(delta: float) -> float:
    """Accumulate a P&L delta onto today's total.

    Incremental, so it needs every contributing fill to be reported exactly
    once — which is why ``set_daily_pnl`` (broker truth) is the path the desk
    actually uses. Kept for callers that only have a delta to hand.
    """
    global _daily_pnl, _daily_pnl_date, _daily_pnl_source, _daily_pnl_at
    _roll_daily_pnl_if_stale()
    _daily_pnl += float(delta)
    _daily_pnl_date = _today_str()
    _daily_pnl_source = "delta"
    _daily_pnl_at = datetime.now().isoformat(timespec="seconds")
    _persist_daily_pnl()
    return _daily_pnl


def get_daily_pnl() -> dict:
    """Today's P&L plus enough provenance to see whether it is being fed."""
    _roll_daily_pnl_if_stale()
    return {
        "daily_pnl": round(float(_daily_pnl), 2),
        "daily_pnl_date": _daily_pnl_date,
        "daily_pnl_source": _daily_pnl_source,
        "daily_pnl_at": _daily_pnl_at,
        "max_daily_loss": _LIMITS.max_daily_loss,
        "daily_loss_breached": _daily_pnl <= -abs(_LIMITS.max_daily_loss),
    }


def check_order(
    *,
    qty: float,
    product: str,
    exchange: str,
    open_positions: int,
    is_closing: bool = False,
) -> None:
    import time

    now = time.time()
    global _orders_this_minute
    _orders_this_minute = [t for t in _orders_this_minute if now - t < 60]
    if len(_orders_this_minute) >= _LIMITS.max_orders_per_minute:
        raise RuntimeError("Risk: max orders per minute exceeded")

    if qty <= 0 or qty > _LIMITS.max_qty:
        raise RuntimeError(f"Risk: qty {qty} outside 1..{_LIMITS.max_qty}")
    if product.upper() not in {p.upper() for p in _LIMITS.allowed_products}:
        raise RuntimeError(f"Risk: product {product} not allowed")
    if exchange.upper() not in {e.upper() for e in _LIMITS.allowed_exchanges}:
        raise RuntimeError(f"Risk: exchange {exchange} not allowed")
    if not is_closing and open_positions >= _LIMITS.max_open_positions:
        raise RuntimeError(
            f"Risk: max open positions reached ({open_positions}/{_LIMITS.max_open_positions})"
        )

    # The loss cutout stops the desk *opening* risk; it must never trap it in
    # the position that caused the loss. Every algo exit — and panic-flatten,
    # which bypasses the ARM gate but not this one — comes through here as a
    # closing order. Blocking those would make the kill-switch unusable at
    # exactly the moment it matters.
    _roll_daily_pnl_if_stale()
    if not is_closing and _daily_pnl <= -abs(_LIMITS.max_daily_loss):
        raise RuntimeError(
            f"Risk: max daily loss breached "
            f"(P&L {_daily_pnl:,.2f} vs limit -{abs(_LIMITS.max_daily_loss):,.2f}) — "
            f"exits still allowed"
        )

    _orders_this_minute.append(now)


_load_persisted_state()
