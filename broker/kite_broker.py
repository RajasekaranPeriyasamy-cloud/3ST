"""Kite live broker — only used when ARMED."""

from __future__ import annotations

from typing import Any

from broker.base import Broker, OrderRequest, OrderResult
from execution.arming import require_armed_for_live
from kite_auth import get_kite_client


class KiteBroker(Broker):
    def place_order(self, req: OrderRequest) -> OrderResult:
        require_armed_for_live()
        kite = get_kite_client()
        try:
            params = {
                "tradingsymbol": req.tradingsymbol,
                "exchange": req.exchange,
                "transaction_type": req.transaction_type,
                "quantity": int(req.quantity),
                "product": req.product,
                "order_type": req.order_type,
                "validity": "DAY",
                "tag": (req.tag or "3ST")[:20],
            }
            if req.order_type == "LIMIT" and req.price is not None:
                params["price"] = float(req.price)
            oid = kite.place_order(variety=kite.VARIETY_REGULAR, **params)
            return OrderResult(ok=True, order_id=str(oid), message="Order placed", raw={"order_id": oid})
        except Exception as e:
            return OrderResult(ok=False, order_id=None, message=str(e))

    def cancel_order(self, order_id: str) -> OrderResult:
        require_armed_for_live()
        kite = get_kite_client()
        try:
            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
            return OrderResult(ok=True, order_id=order_id, message="Cancelled")
        except Exception as e:
            return OrderResult(ok=False, order_id=order_id, message=str(e))

    def positions(self) -> list[dict[str, Any]]:
        kite = get_kite_client()
        data = kite.positions()
        return data.get("net", []) if isinstance(data, dict) else list(data)

    def orders(self) -> list[dict[str, Any]]:
        kite = get_kite_client()
        return list(kite.orders())

    def ltp(self, exchange: str, tradingsymbol: str) -> float:
        kite = get_kite_client()
        key = f"{exchange}:{tradingsymbol}"
        data = kite.ltp(key)
        return float(data[key]["last_price"])
