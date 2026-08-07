"""Tests for IV smile (no Kite)."""

from __future__ import annotations

import pytest
from vollib.black_scholes import black_scholes as bs_price

from options import iv_smile as ivs

R = 0.065
SPOT = 20000.0
STEP = 50
EXPIRY = "2026-07-16"
TTE = 4 / 365


def _chain(underlying: str, expiry: str) -> dict:
    strikes = []
    for k in range(int(SPOT) - 12 * STEP, int(SPOT) + 12 * STEP + 1, STEP):
        strikes.append(
            {
                "strike": float(k),
                "ce": {"tradingsymbol": f"{underlying}|{expiry}|{k}|CE", "exchange": "NFO", "lot_size": 65},
                "pe": {"tradingsymbol": f"{underlying}|{expiry}|{k}|PE", "exchange": "NFO", "lot_size": 65},
            }
        )
    return {"underlying": underlying, "expiry": expiry, "exchange": "NFO", "strike_step": STEP, "strikes": strikes}


def _quotes(keys: list[str]) -> dict:
    iv = 0.15
    out = {}
    for key in keys:
        sym = key.split(":", 1)[1]
        _und, _exp, strike_s, otype = sym.split("|")
        strike = float(strike_s)
        flag = "c" if otype == "CE" else "p"
        out[key] = {"last_price": float(bs_price(flag, SPOT, strike, TTE, R, iv))}
    return out


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(ivs, "list_expiries", lambda u: [EXPIRY])
    monkeypatch.setattr(ivs, "require_index_spot", lambda u: SPOT)
    monkeypatch.setattr(ivs, "time_to_expiry_years", lambda e: TTE)
    monkeypatch.setattr(ivs, "get_chain", _chain)
    monkeypatch.setattr(ivs, "_quote_batches", _quotes)


def test_config():
    cfg = ivs.iv_smile_config()
    assert "NIFTY" in cfg["underlyings"]


def test_smile_shape(patched):
    snap = ivs.build_iv_smile("NIFTY", EXPIRY, strike_count=11)
    assert snap["underlying"] == "NIFTY"
    assert snap["atm_strike"] == SPOT
    assert len(snap["chain"]) == 11
    assert snap["atm_iv"] == pytest.approx(15.0, abs=0.5)


def test_skew_present(patched):
    snap = ivs.build_iv_smile("NIFTY", EXPIRY, strike_count=25)
    assert snap["skew"] is not None
