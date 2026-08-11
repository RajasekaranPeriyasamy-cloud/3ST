"""Shared pytest fixtures.

**The unit suite must not talk to Kite.** Live calls made it slow, flaky and
impossible to run in CI without a broker session: one gamma-snapshot test
walked a whole option chain and issued ~80 per-strike 60min historical
requests (``gamma_density`` -> ``oi_movers.ensure_session_open_oi`` ->
``fetch_historical_by_token``), which took 80+ seconds against a throttled
Kite and produced different data every run.

Individual modules do ``from kite_client import fetch_historical_by_token``
at import time, so patching ``kite_client.fetch_historical_by_token`` would
miss them — each module holds its own reference. Instead we block the two
client accessors every market-data path resolves *at call time*:
``_kite_direct_client`` and ``get_kite_client``. Nothing can reach the
network without one of them.

Tests that genuinely need a live broker can opt out with
``@pytest.mark.live_kite`` (none do today).
"""

from __future__ import annotations

import pytest

# Every fetcher in kite_client.py obtains its client from one of these.
_CLIENT_ACCESSORS = ("_kite_direct_client", "get_kite_client", "kite_read_client")


class LiveKiteBlocked(RuntimeError):
    """Raised when a unit test tries to reach the broker."""


@pytest.fixture(autouse=True)
def block_live_kite(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("live_kite"):
        return

    import kite_client

    def _blocked(*_a, **_k):
        raise LiveKiteBlocked(
            f"{request.node.name} tried to open a Kite client. The unit suite runs "
            "offline — stub the caller (see the `patched` fixture in "
            "tests/test_gamma_density.py) or mark the test @pytest.mark.live_kite."
        )

    for name in _CLIENT_ACCESSORS:
        if hasattr(kite_client, name):
            monkeypatch.setattr(kite_client, name, _blocked)
