"""Pinned tickers for the Equity Report desk.

Its own store rather than a view over ``watchlist.json``: the watchlist holds
index and commodity *option* legs (NIFTY / BANKNIFTY / CRUDEOIL), so deriving
pins from it would render an empty panel. ``import_from_watchlist`` exists for
the day stock names appear there, and reports honestly when it finds none.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from settings import data_dir

PINS_FILE = data_dir() / "equity_pins.json"

MAX_PINS = 40

_PINS: list[dict[str, Any]] = []


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _save() -> None:
    PINS_FILE.write_text(json.dumps({"pins": _PINS}, indent=2), encoding="utf-8")


def load_persisted_pins() -> None:
    global _PINS
    if not PINS_FILE.exists():
        _PINS = []
        return
    try:
        raw = json.loads(PINS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _PINS = []
        return
    pins = raw.get("pins") if isinstance(raw, dict) else None
    _PINS = [p for p in (pins or []) if isinstance(p, dict) and p.get("symbol")]


load_persisted_pins()


def list_pins() -> list[dict[str, Any]]:
    return [dict(p) for p in _PINS]


def add_pin(symbol: str, company: str = "", exchange: str = "NSE") -> dict[str, Any]:
    symbol = (symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    existing = next((p for p in _PINS if p.get("symbol") == symbol), None)
    if existing:
        # Re-pinning refreshes the display name without duplicating the row.
        if company:
            existing["company"] = company.strip()
            _save()
        return dict(existing)
    if len(_PINS) >= MAX_PINS:
        raise RuntimeError(f"Pin limit reached ({MAX_PINS}). Remove one first.")
    pin = {
        "symbol": symbol,
        "company": (company or "").strip(),
        "exchange": (exchange or "NSE").strip().upper(),
        "pinned_at": _now(),
    }
    _PINS.append(pin)
    _save()
    return dict(pin)


def remove_pin(symbol: str) -> bool:
    symbol = (symbol or "").strip().upper()
    before = len(_PINS)
    _PINS[:] = [p for p in _PINS if p.get("symbol") != symbol]
    if len(_PINS) == before:
        return False
    _save()
    return True


def import_from_watchlist() -> dict[str, Any]:
    """Pin any watchlist underlying that resolves to an NSE cash equity.

    Returns counts plus the names that were skipped, so an empty result reads as
    "your watchlist has no stocks" rather than "the button is broken".
    """
    from instruments import search_instruments
    from watchlist_store import list_items

    names: list[str] = []
    for item in list_items():
        name = str(item.get("name") or "").strip().upper()
        if name and name not in names:
            names.append(name)

    added: list[str] = []
    skipped: list[str] = []
    for name in names:
        try:
            matches = search_instruments(q=name, segment="equity", limit=10)
        except Exception:
            skipped.append(name)
            continue
        hit = next(
            (
                m
                for m in matches
                if str(m.get("tradingsymbol") or "").upper() == name
                and str(m.get("exchange") or "").upper() == "NSE"
            ),
            None,
        )
        if not hit:
            skipped.append(name)
            continue
        try:
            add_pin(name, str(hit.get("name") or ""), "NSE")
            added.append(name)
        except RuntimeError:
            skipped.append(name)

    return {
        "added": added,
        "skipped": skipped,
        "scanned": len(names),
        "pins": list_pins(),
    }
