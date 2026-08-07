"""Tests for ATM synthetic future helper."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from options import synthetic_future as sf

IST = ZoneInfo("Asia/Kolkata")
EXPIRY = "2026-08-13"
SPOT = 24750.0
ATM = 24750.0


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    sf.clear_synthetic_future_cache()


def test_synthetic_from_prices() -> None:
    # F = 24750 + 120 - 90 = 24780
    assert sf.synthetic_from_prices(24750.0, 120.0, 90.0) == 24780.0


def test_mid_or_ltp_prefers_mid() -> None:
    quote = {
        "last_price": 100.0,
        "depth": {
            "buy": [{"price": 99.0, "quantity": 1}],
            "sell": [{"price": 101.0, "quantity": 1}],
        },
    }
    px, src = sf.mid_or_ltp(quote)
    assert px == 100.0
    assert src == "mid"


def test_mid_or_ltp_falls_back_to_ltp() -> None:
    quote = {"last_price": 88.5, "depth": {"buy": [], "sell": []}}
    px, src = sf.mid_or_ltp(quote)
    assert px == 88.5
    assert src == "ltp"


def test_mid_or_ltp_empty() -> None:
    assert sf.mid_or_ltp(None) == (None, "none")
    assert sf.mid_or_ltp({}) == (None, "none")


def _atm_chain() -> dict:
    return {
        "underlying": "NIFTY",
        "expiry": EXPIRY,
        "exchange": "NFO",
        "strike_step": 50,
        "lot_size": 65,
        "strikes": [
            {
                "strike": ATM,
                "ce": {
                    "tradingsymbol": "NIFTY24750CE",
                    "instrument_token": 1,
                    "exchange": "NFO",
                    "lot_size": 65,
                },
                "pe": {
                    "tradingsymbol": "NIFTY24750PE",
                    "instrument_token": 2,
                    "exchange": "NFO",
                    "lot_size": 65,
                },
            }
        ],
    }


def test_compute_synthetic_future_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = _atm_chain()
    quotes = {
        "NFO:NIFTY24750CE": {
            "last_price": 119.0,
            "depth": {
                "buy": [{"price": 118.0}],
                "sell": [{"price": 122.0}],
            },
        },
        "NFO:NIFTY24750PE": {
            "last_price": 89.0,
            "depth": {
                "buy": [{"price": 88.0}],
                "sell": [{"price": 92.0}],
            },
        },
    }
    # CE mid=120, PE mid=90 → F = 24750 + 120 - 90 = 24780
    when = datetime(2026, 8, 6, 15, 20, tzinfo=IST)
    out = sf.compute_synthetic_future(
        "NIFTY",
        when=when,
        spot=SPOT,
        indicative=24772.0,
        chain=chain,
        quotes=quotes,
    )
    assert out is not None
    assert out["F"] == 24780.0
    assert out["atm_strike"] == ATM
    assert out["expiry"] == EXPIRY
    assert out["ce_source"] == "mid"
    assert out["pe_source"] == "mid"
    assert out["price_source"] == "mid"
    assert out["basis_vs_spot"] == 30.0
    assert out["basis_vs_indicative"] == 8.0
    assert out["asof"].startswith("2026-08-06T15:20:00")
    assert "+05:30" in out["asof"]


def test_compute_synthetic_future_ltp_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = _atm_chain()
    quotes = {
        "NFO:NIFTY24750CE": {"last_price": 110.0, "depth": {"buy": [], "sell": []}},
        "NFO:NIFTY24750PE": {"last_price": 80.0, "depth": {"buy": [], "sell": []}},
    }
    out = sf.compute_synthetic_future(
        "NIFTY",
        spot=SPOT,
        chain=chain,
        quotes=quotes,
    )
    assert out is not None
    assert out["F"] == 24780.0  # 24750 + 110 - 80
    assert out["price_source"] == "ltp"


def test_compute_missing_leg_returns_none() -> None:
    chain = _atm_chain()
    chain["strikes"][0].pop("pe")
    out = sf.compute_synthetic_future(
        "NIFTY",
        spot=SPOT,
        chain=chain,
        quotes={},
    )
    assert out is None
