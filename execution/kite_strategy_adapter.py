"""Broker adapter: 3ST Kite/paper stack → trading-algo strategy interface."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pandas as pd

from broker.kite_broker import KiteBroker
from broker.paper_broker import get_paper_broker
from execution.arming import get_arm_state
from execution.trading_algo_path import ensure_trading_algo_path
from instruments import load_instruments, refresh_instruments


def _broker():
    if get_arm_state().get("mode") == "live":
        return KiteBroker()
    return get_paper_broker()


def _parse_symbol_key(symbol_key: str) -> tuple[str, str]:
    if ":" in symbol_key:
        exchange, sym = symbol_key.split(":", 1)
        return exchange.strip().upper(), sym.strip()
    return "NFO", symbol_key.strip()


def _product_map(product_type) -> str:
    name = getattr(product_type, "name", str(product_type)).upper()
    if name in ("MARGIN", "NRML"):
        return "NRML"
    if name in ("INTRADAY", "MIS"):
        return "MIS"
    return "NRML"


class KiteStrategyAdapter:
    """Minimal BrokerGateway surface for Survivor / Wave strategies."""

    def __init__(self) -> None:
        ensure_trading_algo_path()
        from brokers.core.enums import Exchange, OrderType, ProductType, TransactionType
        from brokers.core.schemas import OrderRequest, OrderResponse, Position, Quote

        self._Exchange = Exchange
        self._OrderType = OrderType
        self._ProductType = ProductType
        self._TransactionType = TransactionType
        self._OrderRequest = OrderRequest
        self._OrderResponse = OrderResponse
        self._Position = Position
        self._Quote = Quote
        self.on_ticks = None
        self.on_connect = None
        self.on_order_update = None

    def download_instruments(self) -> None:
        try:
            refresh_instruments(force=True)
        except Exception:
            pass

    def get_instruments(self) -> pd.DataFrame:
        df = load_instruments()
        if df.empty:
            return df
        out = df.copy()
        out["symbol"] = out["tradingsymbol"].astype(str)
        if "segment" not in out.columns:
            out["segment"] = out["exchange"].astype(str)
        if "days_to_expiry" not in out.columns and "expiry" in out.columns:
            today = date.today()

            def _dte(val) -> int | None:
                if val is None or (isinstance(val, float) and pd.isna(val)):
                    return None
                try:
                    exp = pd.to_datetime(val).date()
                    return max(0, (exp - today).days)
                except Exception:
                    return None

            out["days_to_expiry"] = out["expiry"].apply(_dte)
        return out

    def get_quote(self, symbol_key: str):
        exchange, sym = _parse_symbol_key(symbol_key)
        broker = _broker()
        try:
            if get_arm_state().get("mode") != "live":
                from kite_auth import get_kite_client

                kite = get_kite_client()
                key = f"{exchange}:{sym}"
                data = kite.quote(key)
                ltp = float(data[key]["last_price"])
                get_paper_broker().set_ltp(exchange, sym, ltp)
            else:
                ltp = float(broker.ltp(exchange, sym))
        except Exception:
            ltp = float(broker.ltp(exchange, sym) or 0)
        return self._Quote(
            symbol=sym,
            exchange=self._Exchange[exchange] if exchange in self._Exchange.__members__ else self._Exchange.NFO,
            last_price=ltp,
        )

    def get_positions(self) -> list:
        broker = _broker()
        raw = broker.positions()
        positions: list = []
        for row in raw:
            qty = int(row.get("quantity") or row.get("net_quantity") or 0)
            if qty == 0:
                continue
            exch = str(row.get("exchange") or "NFO")
            sym = str(row.get("tradingsymbol") or "")
            positions.append(
                self._Position(
                    symbol=sym,
                    exchange=self._Exchange[exch],
                    quantity_total=qty,
                    quantity_available=qty,
                    average_price=float(row.get("average_price") or 0),
                )
            )
        return positions

    def place_order(self, request):
        ensure_trading_algo_path()
        from execution.order_executor import place_leg_order

        if hasattr(request, "symbol"):
            sym = request.symbol
            exchange = request.exchange.value if hasattr(request.exchange, "value") else str(request.exchange)
            qty = int(request.quantity)
            txn = request.transaction_type.value if hasattr(request.transaction_type, "value") else str(request.transaction_type)
            order_type = request.order_type.value if hasattr(request.order_type, "value") else str(request.order_type)
            product = _product_map(request.product_type)
            price = request.price
            tag = request.tag or "3ST"
        else:
            raise ValueError("Unsupported order request type")

        leg = {
            "tradingsymbol": sym,
            "exchange": exchange,
            "quantity": qty,
        }
        result = place_leg_order(
            _broker(),
            leg,
            transaction_type=txn,
            order_type=order_type,
            price=price,
            tag=tag[:20],
            product=product,
        )
        status = "ok" if result.get("ok") else "error"
        return self._OrderResponse(
            status=status,
            order_id=str(result.get("order_id") or ""),
            message=result.get("message"),
            raw=result.get("raw"),
        )

    def cancel_order(self, order_id: str):
        broker = _broker()
        try:
            broker.cancel_order(str(order_id))
            return self._OrderResponse(status="ok", order_id=str(order_id))
        except Exception as e:
            return self._OrderResponse(status="error", order_id=str(order_id), message=str(e))

    def get_orderbook(self) -> list[dict[str, Any]]:
        try:
            from kite_auth import get_kite_client

            kite = get_kite_client()
            return list(kite.orders())
        except Exception:
            return []

    def connect_websocket(self, on_ticks=None, on_connect=None) -> None:
        self.on_ticks = on_ticks
        self.on_connect = on_connect

    def symbols_to_subscribe(self, tokens: list) -> None:
        return None

    def connect_order_websocket(self, on_order_update=None) -> None:
        self.on_order_update = on_order_update
