"""Tests for LTP cache TTL and batch resolution."""

from __future__ import annotations

import time

from execution.ltp_cache import LtpCache


def test_get_many_uses_fresh_cache(monkeypatch) -> None:
    cache = LtpCache()
    cache._store_rest({"MCX:CRUDEOIL26JUL6850CE": 199.4})

    positions = [{"exchange": "MCX", "tradingsymbol": "CRUDEOIL26JUL6850CE"}]
    out = cache.get_many(positions, allow_rest=False)
    assert out["MCX:CRUDEOIL26JUL6850CE"] == 199.4


def test_stale_entry_triggers_rest_fetch(monkeypatch) -> None:
    cache = LtpCache()
    cache._store_rest({"NFO:NIFTY25JUL24000CE": 100.0})

    with cache._lock:
        entry = cache._by_key["NFO:NIFTY25JUL24000CE"]
        cache._by_key["NFO:NIFTY25JUL24000CE"] = type(entry)(
            price=entry.price,
            updated_mono=time.monotonic() - 3600,
            source=entry.source,
        )

    called: list[list[str]] = []

    def fake_rest(keys: list[str]) -> dict[str, float]:
        called.append(keys)
        return {"NFO:NIFTY25JUL24000CE": 105.0}

    monkeypatch.setattr(cache, "_rest_fetch", fake_rest)
    out = cache.get_many(
        [{"exchange": "NFO", "tradingsymbol": "NIFTY25JUL24000CE"}],
        allow_rest=True,
    )
    assert called == [["NFO:NIFTY25JUL24000CE"]]
    assert out["NFO:NIFTY25JUL24000CE"] == 105.0
