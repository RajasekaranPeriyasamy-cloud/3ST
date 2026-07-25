"""Kite live broker — only used when ARMED."""

from __future__ import annotations

from typing import Any

from broker.base import Broker, OrderRequest, OrderResult
from broker.execution_support import get_cached_positions, invalidate_position_cache, net_position_qty
from execution.arming import require_armed_for_live
from kite_auth import get_kite_client, kite_egress_status
from kite_client import fetch_ltp_batch, kite_read_client
from settings import env


def _default_market_protection() -> float:
    raw = env("KITE_MARKET_PROTECTION", "-1")
    try:
        return float(raw)
    except ValueError:
        return -1.0


def _order_client_fallback_enabled() -> bool:
    raw = env("KITE_ORDER_DIRECT_FALLBACK", "auto").lower()
    if raw in {"0", "false", "no", "off", "never"}:
        return False
    if raw in {"1", "true", "yes", "on", "always"}:
        return True
    return kite_egress_status().get("mode") == "local_bind"


def _order_transport_retryable(exc: BaseException) -> bool:
    from kite_auth import _is_proxy_error

    msg = str(exc).lower()
    if _is_proxy_error(exc):
        return True
    return any(
        token in msg
        for token in (
            "timed out",
            "timeout",
            "connecttimeouterror",
            "getaddrinfo failed",
            "failed to resolve",
            "name resolution",
        )
    )


def _place_with_order_client(place_fn):
    from kite_client import _kite_direct_client

    attempts: list[tuple[str, Any]] = [("bound", get_kite_client)]
    if _order_client_fallback_enabled():
        attempts.append(("direct", _kite_direct_client))

    last_err: Exception | None = None
    for idx, (path, factory) in enumerate(attempts):
        try:
            kite = factory()
            result = place_fn(kite)
            return result, path
        except Exception as exc:
            last_err = exc
            if idx + 1 >= len(attempts) or not _order_transport_retryable(exc):
                break
    if last_err:
        raise last_err
    raise RuntimeError("Order client unavailable")


class KiteBroker(Broker):
    def _load_positions(self) -> list[dict[str, Any]]:
        kite = kite_read_client()
        data = kite.positions()
        return data.get("net", []) if isinstance(data, dict) else list(data)

    def net_qty(self, exchange: str, tradingsymbol: str, product: str) -> int:
        rows = get_cached_positions(self, loader=self._load_positions)
        return net_position_qty(
            rows,
            tradingsymbol=tradingsymbol,
            exchange=exchange,
            product=product,
        )

    def place_order(self, req: OrderRequest) -> OrderResult:
        require_armed_for_live()
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
        if req.order_type in {"MARKET", "SL-M"}:
            params["market_protection"] = (
                float(req.market_protection)
                if req.market_protection is not None
                else _default_market_protection()
            )

        def _place(kite):
            return kite.place_order(variety=kite.VARIETY_REGULAR, **params)

        try:
            oid, path = _place_with_order_client(_place)
            invalidate_position_cache(self)
            msg = "Order placed" if path == "bound" else "Order placed (direct egress fallback)"
            return OrderResult(ok=True, order_id=str(oid), message=msg, raw={"order_id": oid, "egress": path})
        except Exception as e:
            return OrderResult(ok=False, order_id=None, message=str(e))

    def cancel_order(self, order_id: str) -> OrderResult:
        require_armed_for_live()
        try:
            _, _path = _place_with_order_client(
                lambda kite: kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
            )
            invalidate_position_cache(self)
            return OrderResult(ok=True, order_id=order_id, message="Cancelled")
        except Exception as e:
            return OrderResult(ok=False, order_id=order_id, message=str(e))

    def positions(self) -> list[dict[str, Any]]:
        return get_cached_positions(self, loader=self._load_positions)

    def orders(self) -> list[dict[str, Any]]:
        kite = kite_read_client()
        return list(kite.orders())

    def ltp(self, exchange: str, tradingsymbol: str) -> float:
        key = f"{exchange}:{tradingsymbol}"
        data = fetch_ltp_batch([key])
        return float(data[key]["last_price"])
