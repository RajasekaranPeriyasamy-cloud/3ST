"""Pluggable market-data providers for the Gamma Density desk.

Broker-configurable: the options chain / spot / OI quote source is abstracted
behind :class:`GammaDensityDataProvider`. The default provider uses Kite
Connect, but any broker (Firstock, Dhan, a REST feed, offline snapshots, test
stubs, …) can supply data by implementing the provider methods and calling
:func:`set_gamma_density_provider` (or passing ``provider=`` to
:func:`options.gamma_density.build_gamma_snapshot`).

Minimal contract
----------------
Each provider must return:

* **Chain** — same shape as :func:`options.chain.get_chain` (``strikes`` with
  ``ce`` / ``pe`` legs containing ``tradingsymbol``, ``exchange``, ``lot_size``).
* **Quotes** — ``dict[exchange:symbol]`` with at least ``oi`` (or
  ``open_interest``) and ``last_price``.
* **Spot** — underlying LTP used for IV / gamma / ATM selection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

QUOTE_BATCH = 500

ChainFn = Callable[[str, str], dict[str, Any]]
SpotFn = Callable[[str], float | None]
ExpiriesFn = Callable[[str], list[str]]
QuotesFn = Callable[[list[str]], dict[str, dict[str, Any]]]


class GammaDensityDataProvider(ABC):
    """Pluggable options market-data source for Gamma Density."""

    #: Short stable id (``kite``, ``firstock``, ``static``, …).
    name: str = "base"

    def requires_session(self) -> bool:
        """When ``True``, API routes should require a live broker session."""
        return True

    @abstractmethod
    def list_expiries(self, underlying: str) -> list[str]:
        """Sorted ISO dates (``YYYY-MM-DD``) for option expiries."""

    def nearest_expiry(self, underlying: str) -> str | None:
        from options.chain import nearest_expiry as _nearest

        return _nearest(underlying)

    @abstractmethod
    def get_chain(self, underlying: str, expiry: str) -> dict[str, Any]:
        """Option chain for one expiry (Kite-shaped dict)."""

    @abstractmethod
    def get_spot(self, underlying: str) -> float | None:
        """Underlying spot LTP."""

    @abstractmethod
    def fetch_quotes(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """Batch quotes keyed ``EXCHANGE:tradingsymbol`` → ``{oi, last_price, …}``."""


class KiteGammaDensityDataProvider(GammaDensityDataProvider):
    """Default provider — Kite instruments dump + ``kite.quote`` / ``kite.ltp``."""

    name = "kite"

    def list_expiries(self, underlying: str) -> list[str]:
        from options.chain import list_expiries

        return list_expiries(underlying)

    def nearest_expiry(self, underlying: str) -> str | None:
        from options.chain import nearest_expiry

        return nearest_expiry(underlying)

    def get_chain(self, underlying: str, expiry: str) -> dict[str, Any]:
        from options.chain import get_chain

        return get_chain(underlying, expiry)

    def get_spot(self, underlying: str) -> float | None:
        from options.chain import require_index_spot

        return require_index_spot(underlying)

    def fetch_quotes(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        from kite_client import fetch_quote_batch

        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(keys), QUOTE_BATCH):
            chunk = keys[i : i + QUOTE_BATCH]
            try:
                out.update(fetch_quote_batch(chunk))
            except Exception:
                continue
        return out


class CallableGammaDensityDataProvider(GammaDensityDataProvider):
    """Wrap callables — quickest way to plug a custom broker without subclassing."""

    def __init__(
        self,
        *,
        name: str,
        get_chain: ChainFn,
        get_spot: SpotFn,
        fetch_quotes: QuotesFn,
        list_expiries: ExpiriesFn | None = None,
        nearest_expiry: Callable[[str], str | None] | None = None,
        requires_session: bool = True,
    ) -> None:
        self.name = name
        self._get_chain = get_chain
        self._get_spot = get_spot
        self._fetch_quotes = fetch_quotes
        self._list_expiries = list_expiries
        self._nearest_expiry = nearest_expiry
        self._requires_session = requires_session

    def requires_session(self) -> bool:
        return self._requires_session

    def list_expiries(self, underlying: str) -> list[str]:
        if self._list_expiries is not None:
            return self._list_expiries(underlying)
        from options.chain import list_expiries

        return list_expiries(underlying)

    def nearest_expiry(self, underlying: str) -> str | None:
        if self._nearest_expiry is not None:
            return self._nearest_expiry(underlying)
        return super().nearest_expiry(underlying)

    def get_chain(self, underlying: str, expiry: str) -> dict[str, Any]:
        return self._get_chain(underlying, expiry)

    def get_spot(self, underlying: str) -> float | None:
        return self._get_spot(underlying)

    def fetch_quotes(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        return self._fetch_quotes(keys)


class StaticGammaDensityDataProvider(GammaDensityDataProvider):
    """Offline / test provider with fixed chain, spot, and quote map."""

    name = "static"

    def __init__(
        self,
        *,
        chain: dict[str, Any] | Callable[[str, str], dict[str, Any]],
        spot: float | Callable[[str], float | None],
        quotes: dict[str, dict[str, Any]],
        expiries: list[str] | None = None,
        name: str = "static",
    ) -> None:
        self.name = name
        self._chain = chain
        self._spot = spot
        self._quotes = quotes
        self._expiries = expiries or []

    def requires_session(self) -> bool:
        return False

    def list_expiries(self, underlying: str) -> list[str]:
        return list(self._expiries)

    def nearest_expiry(self, underlying: str) -> str | None:
        return self._expiries[0] if self._expiries else None

    def get_chain(self, underlying: str, expiry: str) -> dict[str, Any]:
        if callable(self._chain):
            return self._chain(underlying, expiry)
        return self._chain

    def get_spot(self, underlying: str) -> float | None:
        if callable(self._spot):
            return self._spot(underlying)
        return float(self._spot)

    def fetch_quotes(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        return {k: self._quotes[k] for k in keys if k in self._quotes}


# Active provider (swappable). Defaults to Kite.
_provider: GammaDensityDataProvider = KiteGammaDensityDataProvider()


def set_gamma_density_provider(provider: GammaDensityDataProvider) -> None:
    """Install a broker/data-source provider for Gamma Density."""
    global _provider
    _provider = provider


def get_gamma_density_provider() -> GammaDensityDataProvider:
    return _provider


def list_gamma_density_providers() -> list[str]:
    """Known built-in provider ids (extend by registering your own instance)."""
    return ["kite", "static", "callable"]
