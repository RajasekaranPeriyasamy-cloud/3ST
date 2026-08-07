"""Tests for LTP-cache health metrics and the trade-management safety gate."""

from __future__ import annotations

import time

import execution.ltp_cache as lc
from execution.ltp_cache import LtpCache


def test_health_disconnected_when_no_feed() -> None:
    cache = LtpCache()
    h = cache.health()
    assert h["feed_connected"] is False
    assert h["last_tick_age_sec"] is None
    assert h["total_updates"] == 0
    assert h["reconnects"] == 0
    # ws_enabled default on -> disconnected (no feed); if disabled -> rest_only
    assert h["status"] in {"disconnected", "rest_only"}


def test_ingest_updates_health_and_snapshot() -> None:
    cache = LtpCache()
    cache.register("NFO", "NIFTY25JUL24000CE", 111)
    cache.ingest_ws_ticks([{"instrument_token": 111, "last_price": 123.5}])

    h = cache.health()
    assert h["total_updates"] == 1
    assert h["last_tick_age_sec"] is not None
    assert h["last_tick_age_sec"] < 5

    snap = cache.snapshot()
    assert "NFO:NIFTY25JUL24000CE" in snap
    row = snap["NFO:NIFTY25JUL24000CE"]
    assert row["price"] == 123.5
    assert row["source"] == "ws"
    assert row["fresh"] is True


def test_bad_ticks_do_not_bump_updates() -> None:
    cache = LtpCache()
    cache.register("NFO", "NIFTY25JUL24000CE", 111)
    cache.ingest_ws_ticks([{"instrument_token": 111, "last_price": 0}])
    cache.ingest_ws_ticks([{"instrument_token": 111, "last_price": None}])
    assert cache.health()["total_updates"] == 0


def test_gate_safe_on_fresh_ws(monkeypatch) -> None:
    cache = LtpCache()
    cache.register("NFO", "NIFTY25JUL24000CE", 111)
    cache.ingest_ws_ticks([{"instrument_token": 111, "last_price": 123.5}])

    class _Feed:
        connected = True

    cache._feed = _Feed()
    monkeypatch.setattr(lc, "_cache", cache)

    safe, reason = lc.is_trade_management_safe()
    assert safe is True
    assert "healthy" in reason.lower()


def test_gate_rest_reconfirm_when_ws_down(monkeypatch) -> None:
    cache = LtpCache()  # no feed, no ticks
    monkeypatch.setattr(lc, "_cache", cache)
    monkeypatch.setattr(lc, "rest_fallback_enabled", lambda: True)
    monkeypatch.setattr(
        "kite_client.session_status", lambda: {"authenticated": True}
    )
    safe, reason = lc.is_trade_management_safe()
    assert safe is True
    assert "reconfirm" in reason.lower()


def test_gate_unsafe_when_no_rest_and_no_session(monkeypatch) -> None:
    cache = LtpCache()
    monkeypatch.setattr(lc, "_cache", cache)
    monkeypatch.setattr(lc, "rest_fallback_enabled", lambda: False)
    safe, reason = lc.is_trade_management_safe()
    assert safe is False


def test_reconnect_counter_via_health() -> None:
    cache = LtpCache()
    with cache._lock:
        cache._reconnects = 3
    assert cache.health()["reconnects"] == 3


def test_rest_prices_forces_refetch(monkeypatch) -> None:
    cache = LtpCache()
    calls: list[list[str]] = []

    def fake_rest(keys: list[str]) -> dict[str, float]:
        calls.append(keys)
        return {"NFO:NIFTY25JUL24000CE": 150.0}

    monkeypatch.setattr(cache, "_rest_fetch", fake_rest)
    out = cache.rest_prices([{"exchange": "NFO", "tradingsymbol": "NIFTY25JUL24000CE"}])
    assert out["NFO:NIFTY25JUL24000CE"] == 150.0
    assert calls == [["NFO:NIFTY25JUL24000CE"]]
