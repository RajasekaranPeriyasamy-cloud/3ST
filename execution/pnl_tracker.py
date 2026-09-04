"""Feed today's P&L from broker truth into the ``max_daily_loss`` cutout.

``risk/limits.py`` has enforced ``max_daily_loss`` since it was written, but
nothing ever moved ``_daily_pnl`` off 0.0 — ``record_pnl`` had no caller
anywhere in the repo, so the cutout could not fire. This module is what feeds
it.

**Why broker truth rather than per-fill attribution.** Computing the day's P&L
from our own fills means every contributing order must be reported exactly once,
across five runners plus manual trades, and any miss silently under-reports the
loss. The broker already nets it: Kite's positions payload carries ``pnl``
(realised + unrealised) per position, including rows the desk has already
closed (``quantity == 0``). Reading it is self-correcting — a missed update is
repaired by the next one — and it covers Survivor, Wave and manual Kite trades
without touching any of them. So this sets an absolute figure
(``risk.limits.set_daily_pnl``) rather than accumulating deltas.

**Paper mode measures less.** ``PaperBroker.positions()`` filters to
``quantity != 0``, so a paper position that has been closed leaves no row and
its realised P&L is not counted — paper day P&L is open MTM only. Live Kite is
the exact figure; paper is a rehearsal of the mechanism, not of the number.

**A failed positions read is not a flat day.** Mirroring
``execution/reconcile.py``: if the broker cannot be read, the last known P&L is
left alone. Treating a transient API error as "P&L is 0" would silently reopen
a cutout that had already tripped.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from broker.base import Broker
from risk import limits as risk_limits
from utils.logging import get_logger, log_event

logger = get_logger(__name__)

#: The scheduler loop wakes far more often than the day's P&L meaningfully
#: moves, and each refresh is a positions read.
REFRESH_MIN_INTERVAL_SEC = 30.0

_last_run_mono: float = 0.0
_lock = threading.Lock()


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def position_day_pnl(row: dict[str, Any]) -> float | None:
    """One position's day P&L, or None when the row carries nothing usable.

    Prefers the broker's own figure (``pnl`` is realised + unrealised on Kite;
    ``m2m`` is the same idea on some payloads) and only falls back to deriving
    it from average vs last price — that fallback cannot see realised P&L on a
    closed row, so it is a floor, not an equivalent.
    """
    for key in ("pnl", "m2m"):
        got = _num(row.get(key))
        if got is not None:
            return got

    qty = _num(row.get("quantity"))
    avg = _num(row.get("average_price"))
    if avg is None:
        avg = _num(row.get("avg_price"))
    ltp = _num(row.get("last_price"))
    if not qty or avg is None or ltp is None:
        return None
    return (ltp - avg) * qty


def _needs_last_price(row: dict[str, Any]) -> bool:
    """True when this row can only be valued once we know its last price."""
    if _num(row.get("pnl")) is not None or _num(row.get("m2m")) is not None:
        return False
    if _num(row.get("last_price")) is not None:
        return False
    return bool(_num(row.get("quantity")))


def enrich_last_price(broker: Broker, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill ``last_price`` on rows that carry no P&L figure of their own.

    PaperBroker keeps its LTPs beside the position rather than on them, so
    without this every paper row is unusable and the cutout is silently inert
    in the exact mode it is meant to be rehearsed in. Kite rows carry ``pnl``,
    so this never issues a quote for the live path.
    """
    out: list[dict[str, Any]] = []
    for row in rows or []:
        if not _needs_last_price(row):
            out.append(row)
            continue
        exch = str(row.get("exchange") or "")
        sym = str(row.get("tradingsymbol") or "")
        if not exch or not sym:
            out.append(row)
            continue
        try:
            out.append({**row, "last_price": float(broker.ltp(exch, sym))})
        except Exception:
            out.append(row)
    return out


def day_pnl_from_positions(rows: list[dict[str, Any]]) -> tuple[float, int]:
    """Sum day P&L across every position row, closed ones included.

    Returns ``(total, counted)``. Rows with nothing usable are skipped rather
    than counted as zero, so ``counted`` says how much of the book the figure
    actually covers.
    """
    total = 0.0
    counted = 0
    for row in rows or []:
        got = position_day_pnl(row)
        if got is None:
            continue
        total += got
        counted += 1
    return round(total, 2), counted


def _desk_broker() -> tuple[Broker, str]:
    from execution.positions_view import get_desk_broker

    return get_desk_broker()


def refresh_daily_pnl(broker: Broker | None = None) -> dict[str, Any]:
    """Read positions and set the day's P&L. Never raises.

    On a broker read failure the previous figure stands — see the module
    docstring on why "unreadable" must not mean "flat".
    """
    mode = "unknown"
    try:
        if broker is None:
            broker, mode = _desk_broker()
        rows = broker.positions()
    except Exception as exc:
        log_event(
            logger,
            logging.WARNING,
            "daily_pnl_refresh_failed",
            error=str(exc),
            note="kept last known P&L — a failed read is not a flat day",
        )
        return {"ok": False, "error": str(exc), **risk_limits.get_daily_pnl()}

    total, counted = day_pnl_from_positions(enrich_last_price(broker, rows))
    if counted == 0 and rows:
        # Rows exist but none carried a usable figure — do not overwrite a real
        # P&L with a zero derived from nothing.
        log_event(
            logger,
            logging.WARNING,
            "daily_pnl_rows_unusable",
            rows=len(rows),
            note="kept last known P&L",
        )
        return {"ok": False, "error": "no usable P&L fields", **risk_limits.get_daily_pnl()}

    was_breached = risk_limits.get_daily_pnl()["daily_loss_breached"]
    risk_limits.set_daily_pnl(total, source="broker")
    snapshot = risk_limits.get_daily_pnl()

    if snapshot["daily_loss_breached"] and not was_breached:
        log_event(
            logger,
            logging.WARNING,
            "daily_loss_breached",
            daily_pnl=snapshot["daily_pnl"],
            max_daily_loss=snapshot["max_daily_loss"],
            mode=mode,
            note="new entries refused; exits still allowed",
        )

    return {"ok": True, "mode": mode, "positions_counted": counted, **snapshot}


def maybe_refresh_daily_pnl_periodic(
    min_interval_sec: float = REFRESH_MIN_INTERVAL_SEC,
) -> dict[str, Any] | None:
    """Scheduler hook: refresh at most once per ``min_interval_sec``."""
    global _last_run_mono
    now = time.monotonic()
    with _lock:
        if now - _last_run_mono < max(5.0, min_interval_sec):
            return None
        _last_run_mono = now
    return refresh_daily_pnl()
