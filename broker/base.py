"""Broker interfaces."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

Side = Literal["BUY", "SELL"]
Product = Literal["MIS", "NRML", "CNC"]


@dataclass
class OrderRequest:
    tradingsymbol: str
    exchange: str
    transaction_type: Side
    quantity: int
    product: Product = "MIS"
    order_type: str = "MARKET"
    price: float | None = None
    tag: str = "3ST"


@dataclass
class OrderResult:
    ok: bool
    order_id: str | None
    message: str
    raw: dict[str, Any] | None = None


class Broker(ABC):
    @abstractmethod
    def place_order(self, req: OrderRequest) -> OrderResult: ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> OrderResult: ...

    @abstractmethod
    def positions(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def orders(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def ltp(self, exchange: str, tradingsymbol: str) -> float: ...
