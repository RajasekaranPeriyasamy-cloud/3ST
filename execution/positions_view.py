"""Normalize broker positions into Kite-style desk rows with LTP and P&L."""

from __future__ import annotations

import re
from typing import Any

from broker.base import Broker
from broker.paper_broker import PaperBroker, get_paper_broker
from broker.kite_broker import KiteBroker
from execution.arming import get_arm_state
from broker.paper_broker import sync_paper_from_rolling_straddle


def _position_key(exchange: str, tradingsymbol: str) -> str:
    return f"{exchange}:{tradingsymbol}"


def _underlying_root(tradingsymbol: str) -> str:
    m = re.match(r"^([A-Z&]+)", tradingsymbol.upper())
    return m.group(1) if m else tradingsymbol


def _display_underlying(root: str) -> str:
    if root == "NIFTY":
        return "NIFTY 50"
    if root == "BANKNIFTY":
        return "BANK NIFTY"
    if root == "FINNIFTY":
        return "FIN NIFTY"
    return root


def _expiry_hint(tradingsymbol: str) -> str:
    """Best-effort expiry label from NFO symbol (e.g. NIFTY25JUL23000PE)."""
    m = re.search(r"(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)", tradingsymbol.upper())
    if not m:
        return ""
    day = m.group(1).lstrip("0") or "0"
    return f"{day} {m.group(2).title()}"


def _group_key(tradingsymbol: str, exchange: str) -> str:
    root = _underlying_root(tradingsymbol)
    expiry = _expiry_hint(tradingsymbol)
    if expiry:
        return f"{root}|{expiry}|{exchange}"
    return f"{root}|{exchange}"


def _group_label(key: str, count: int) -> str:
    parts = key.split("|")
    root = _display_underlying(parts[0])
    if len(parts) >= 2 and parts[1]:
        return f"{root} - {parts[1]} ({count})"
    exch = parts[-1] if len(parts) > 1 else ""
    suffix = f" · {exch}" if exch and exch not in {root, parts[0]} else ""
    return f"{root}{suffix} ({count})"


def _fetch_ltp_map(positions: list[dict[str, Any]]) -> dict[str, float]:
    if not positions:
        return {}
    try:
        from execution.ltp_cache import fetch_ltps_for_positions

        return fetch_ltps_for_positions(positions)
    except Exception:
        pass
    keys: list[str] = []
    for p in positions:
        exch = str(p.get("exchange") or "")
        sym = str(p.get("tradingsymbol") or "")
        if exch and sym:
            keys.append(_position_key(exch, sym))
    if not keys:
        return {}
    out: dict[str, float] = {}
    try:
        from kite_client import fetch_quote_batch, session_status

        if not session_status().get("authenticated"):
            return out
        quotes = fetch_quote_batch(keys)
        for key, q in quotes.items():
            px = q.get("last_price")
            if px is not None:
                out[key] = float(px)
    except Exception:
        pass
    if len(out) < len(keys):
        try:
            from kite_client import fetch_ltp_batch

            missing = [k for k in keys if k not in out]
            for i in range(0, len(missing), 400):
                chunk = missing[i : i + 400]
                data = fetch_ltp_batch(chunk)
                for key, row in data.items():
                    out[key] = float(row["last_price"])
        except Exception:
            pass
    return out


def _seed_paper_ltps(broker: PaperBroker, ltp_map: dict[str, float]) -> None:
    for key, px in ltp_map.items():
        if ":" not in key:
            continue
        exchange, tradingsymbol = key.split(":", 1)
        broker.set_ltp(exchange, tradingsymbol, px)


def _normalize_row(raw: dict[str, Any], ltp_map: dict[str, float]) -> dict[str, Any]:
    exchange = str(raw.get("exchange") or "")
    tradingsymbol = str(raw.get("tradingsymbol") or "")
    product = str(raw.get("product") or "NRML").upper()
    qty = int(raw.get("quantity") or 0)
    avg = float(raw.get("average_price") or raw.get("avg_price") or 0)
    key = _position_key(exchange, tradingsymbol)

    ltp = raw.get("last_price")
    if ltp is None:
        ltp = ltp_map.get(key)
    if ltp is None:
        ltp = avg
    ltp = float(ltp)

    pnl = raw.get("pnl")
    if pnl is None:
        pnl = raw.get("m2m")
    if pnl is None:
        pnl = (ltp - avg) * qty if avg else 0.0
    pnl = float(pnl)

    if avg and avg > 0:
        if qty >= 0:
            change_pct = ((ltp - avg) / avg) * 100.0
        else:
            change_pct = ((avg - ltp) / avg) * 100.0
    else:
        change_pct = 0.0

    instrument_label = tradingsymbol
    if exchange and exchange not in instrument_label:
        instrument_label = f"{tradingsymbol} {exchange}"

    return {
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "instrument": instrument_label,
        "product": product,
        "quantity": qty,
        "average_price": round(avg, 2),
        "last_price": round(ltp, 2),
        "pnl": round(pnl, 2),
        "change_pct": round(change_pct, 2),
        "group_key": _group_key(tradingsymbol, exchange),
    }


def _build_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(row["group_key"], []).append(row)
    groups: list[dict[str, Any]] = []
    for key, items in sorted(buckets.items()):
        total_pnl = sum(float(i["pnl"]) for i in items)
        groups.append(
            {
                "key": key,
                "label": _group_label(key, len(items)),
                "count": len(items),
                "total_pnl": round(total_pnl, 2),
                "positions": items,
            }
        )
    return groups


def get_desk_broker() -> tuple[Broker, str]:
    state = get_arm_state()
    mode = str(state.get("mode") or "paper")
    if mode != "live":
        sync_paper_from_rolling_straddle()
        return get_paper_broker(), mode
    return KiteBroker(), mode


def build_positions_view() -> dict[str, Any]:
    broker, mode = get_desk_broker()
    raw = broker.positions()
    ltp_map = _fetch_ltp_map(raw)
    if isinstance(broker, PaperBroker):
        _seed_paper_ltps(broker, ltp_map)
    rows = [_normalize_row(p, ltp_map) for p in raw if int(p.get("quantity") or 0) != 0]
    groups = _build_groups(rows)
    total_pnl = round(sum(float(r["pnl"]) for r in rows), 2)
    return {
        "mode": mode,
        "positions": rows,
        "groups": groups,
        "total_pnl": total_pnl,
        "count": len(rows),
    }
