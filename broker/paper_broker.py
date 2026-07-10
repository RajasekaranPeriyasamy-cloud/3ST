"""Paper broker — simulated fills, no Kite orders."""

from __future__ import annotations

import itertools
import json
import time
from typing import Any

from broker.base import Broker, OrderRequest, OrderResult
from settings import data_dir

_id = itertools.count(1)
_PAPER_FILE = data_dir() / "paper_broker.json"
_paper_singleton: "PaperBroker | None" = None


def get_paper_broker() -> "PaperBroker":
    """Single shared paper broker for API + algo runners."""
    global _paper_singleton
    if _paper_singleton is None:
        _paper_singleton = PaperBroker()
    return _paper_singleton


class PaperBroker(Broker):
    def __init__(self) -> None:
        self._orders: list[dict[str, Any]] = []
        self._positions: dict[str, dict[str, Any]] = {}
        self._ltp: dict[str, float] = {}
        self._load()

    def _load(self) -> None:
        if not _PAPER_FILE.exists():
            return
        try:
            data = json.loads(_PAPER_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if isinstance(data.get("orders"), list):
            self._orders = data["orders"]
        if isinstance(data.get("positions"), dict):
            self._positions = data["positions"]
        if isinstance(data.get("ltp"), dict):
            self._ltp = {k: float(v) for k, v in data["ltp"].items()}
        max_id = 0
        for o in self._orders:
            oid = str(o.get("order_id", ""))
            if oid.startswith("PAPER-"):
                try:
                    max_id = max(max_id, int(oid.split("-", 1)[1]))
                except ValueError:
                    pass
        global _id
        _id = itertools.count(max_id + 1)

    def _save(self) -> None:
        _PAPER_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "orders": self._orders[-200:],
            "positions": self._positions,
            "ltp": self._ltp,
        }
        _PAPER_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def set_ltp(self, exchange: str, tradingsymbol: str, price: float) -> None:
        self._ltp[f"{exchange}:{tradingsymbol}"] = float(price)
        self._save()

    def ltp(self, exchange: str, tradingsymbol: str) -> float:
        key = f"{exchange}:{tradingsymbol}"
        if key not in self._ltp:
            raise RuntimeError(f"No LTP for {key}")
        return self._ltp[key]

    def place_order(self, req: OrderRequest) -> OrderResult:
        oid = f"PAPER-{next(_id)}"
        px = req.price
        if px is None:
            try:
                px = self.ltp(req.exchange, req.tradingsymbol)
            except RuntimeError:
                px = 0.0
        row = {
            "order_id": oid,
            "tradingsymbol": req.tradingsymbol,
            "exchange": req.exchange,
            "transaction_type": req.transaction_type,
            "quantity": req.quantity,
            "product": req.product,
            "order_type": req.order_type,
            "price": px,
            "status": "COMPLETE",
            "tag": req.tag,
            "ts": time.time(),
        }
        self._orders.append(row)
        key = f"{req.exchange}:{req.tradingsymbol}:{req.product}"
        pos = self._positions.get(key, {"quantity": 0, "average_price": 0.0, **row})
        signed = req.quantity if req.transaction_type == "BUY" else -req.quantity
        new_qty = int(pos["quantity"]) + signed
        pos["quantity"] = new_qty
        pos["average_price"] = px
        pos["tradingsymbol"] = req.tradingsymbol
        pos["exchange"] = req.exchange
        pos["product"] = req.product
        self._positions[key] = pos
        self._save()
        return OrderResult(ok=True, order_id=oid, message="Paper fill", raw=row)

    def cancel_order(self, order_id: str) -> OrderResult:
        return OrderResult(ok=False, order_id=order_id, message="Paper fills are immediate; nothing to cancel")

    def reload(self) -> None:
        """Refresh in-memory state from disk (shared across API reloads / processes)."""
        self._load()

    def positions(self) -> list[dict[str, Any]]:
        self.reload()
        return [p for p in self._positions.values() if int(p.get("quantity") or 0) != 0]

    def orders(self) -> list[dict[str, Any]]:
        self.reload()
        return list(self._orders)


def sync_paper_from_rolling_straddle() -> None:
    """Ensure open CE/PE legs from algo state appear in shared paper positions."""
    broker = get_paper_broker()
    broker.reload()

    try:
        from execution.rolling_straddle_store import get_config, get_state, save_state
        from options.legs import build_atm_leg
    except ImportError:
        return

    cfg = get_config()
    state = get_state()
    product = str(cfg.get("product") or "MIS")

    for leg_key, opt in (("ce", "CE"), ("pe", "PE")):
        leg = state.get(leg_key) or {}
        if leg.get("status") != "open":
            continue
        sym = leg.get("tradingsymbol")
        exch = leg.get("exchange")
        if not sym or not exch:
            continue
        key = f"{exch}:{sym}:{product}"
        if int(broker._positions.get(key, {}).get("quantity") or 0) != 0:
            continue
        qty = 75
        try:
            if cfg.get("expiry") and leg.get("strike"):
                built = build_atm_leg(
                    cfg["underlying"],
                    cfg["expiry"],
                    opt,  # type: ignore[arg-type]
                    strike=float(leg["strike"]),
                )
                qty = int(built["quantity"])
        except Exception:
            pass
        entry_px = float(leg.get("entry_price") or 0)
        order_id = leg.get("entry_order_id") or f"PAPER-{leg_key.upper()}-sync"
        broker._positions[key] = {
            "order_id": order_id,
            "tradingsymbol": sym,
            "exchange": exch,
            "transaction_type": "BUY",
            "quantity": qty,
            "product": product,
            "average_price": entry_px,
            "price": entry_px,
            "status": "COMPLETE",
            "tag": f"3ST-{leg_key.upper()}-sync",
            "source": "rolling_straddle",
        }
        if entry_px > 0:
            broker.set_ltp(exch, sym, entry_px)
        if not any(str(o.get("order_id")) == str(order_id) for o in broker._orders):
            broker._orders.append(
                {
                    "order_id": order_id,
                    "tradingsymbol": sym,
                    "exchange": exch,
                    "transaction_type": "BUY",
                    "quantity": qty,
                    "product": product,
                    "order_type": "MARKET",
                    "price": entry_px,
                    "status": "COMPLETE",
                    "tag": f"3ST-{leg_key.upper()}-sync",
                    "ts": time.time(),
                }
            )
    # If paper still holds a leg but algo state was reset, restore the leg snapshot.
    state = get_state()
    state_patch: dict[str, Any] = {}
    for leg_key, opt in (("ce", "CE"), ("pe", "PE")):
        leg = state.get(leg_key) or {}
        if leg.get("status") == "open":
            continue
        for pos in broker._positions.values():
            if int(pos.get("quantity") or 0) == 0:
                continue
            sym = str(pos.get("tradingsymbol") or "")
            if not sym.endswith(opt):
                continue
            strike = leg.get("strike")
            digits = "".join(ch for ch in sym if ch.isdigit())
            if len(digits) >= 5:
                try:
                    strike = float(digits[-5:])
                except ValueError:
                    pass
            state_patch[leg_key] = {
                "status": "open",
                "tradingsymbol": sym,
                "exchange": pos.get("exchange"),
                "strike": strike,
                "entry_price": float(pos.get("average_price") or pos.get("price") or 0),
                "entry_order_id": pos.get("order_id"),
                "entries_today": max(1, int(leg.get("entries_today") or 0)),
                "last_action": "paper_sync",
            }
            break
    if state_patch:
        save_state(state_patch)
        state = get_state()

    for leg_key, opt in (("ce", "CE"), ("pe", "PE")):
        leg = state.get(leg_key) or {}
        if leg.get("status") != "open":
            continue
        order_id = leg.get("entry_order_id")
        sym = leg.get("tradingsymbol")
        exch = leg.get("exchange")
        if not order_id or not sym or not exch:
            continue
        if any(str(o.get("order_id")) == str(order_id) for o in broker._orders):
            continue
        key = f"{exch}:{sym}:{product}"
        pos = broker._positions.get(key) or {}
        qty = int(pos.get("quantity") or 0)
        if qty == 0:
            continue
        entry_px = float(pos.get("average_price") or pos.get("price") or leg.get("entry_price") or 0)
        broker._orders.append(
            {
                "order_id": order_id,
                "tradingsymbol": sym,
                "exchange": exch,
                "transaction_type": "BUY",
                "quantity": qty,
                "product": product,
                "order_type": "MARKET",
                "price": entry_px,
                "status": "COMPLETE",
                "tag": f"3ST-{leg_key.upper()}-sync",
                "ts": time.time(),
            }
        )
    broker._save()
