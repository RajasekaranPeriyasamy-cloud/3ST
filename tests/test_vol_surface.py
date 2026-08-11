"""Tests for the IV surface engine (no Kite; synthetic chain + BS-priced quotes)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from vollib.black_scholes import black_scholes as bs_price

from options import vol_surface as vs

R = 0.065
SPOT = 20000.0
STEP = 50


def _weekly_expiries(count: int = 3, *, min_days: int = 3) -> list[str]:
    """``count`` upcoming weekly (Thursday) expiries, relative to today.

    These were hardcoded to July 2026 and started failing the moment real time
    passed them: ``vol_surface._select_expiries`` drops anything before
    ``date.today()``, so every test here died with "No expiries available".
    Deriving them keeps the fixtures from rotting again. ``min_days`` avoids a
    near-zero time-to-expiry, which makes the IV solve numerically touchy.
    """
    today = date.today()
    days_ahead = (3 - today.weekday()) % 7  # Thursday == 3
    while days_ahead < min_days:
        days_ahead += 7
    first = today + timedelta(days=days_ahead)
    return [(first + timedelta(days=7 * i)).isoformat() for i in range(count)]


EXPIRIES = _weekly_expiries()
# IV term structure by expiry (decimals) so we can assert it comes back through.
IV_BY_EXP = dict(zip(EXPIRIES, (0.14, 0.16, 0.18), strict=True))
TTE_BY_EXP = {e: (date.fromisoformat(e) - date.today()).days / 365 for e in EXPIRIES}


def _chain(underlying: str, expiry: str) -> dict:
    strikes = []
    for k in range(int(SPOT) - 12 * STEP, int(SPOT) + 12 * STEP + 1, STEP):
        # Pipe-delimited symbol keeps strike/expiry unambiguous for the fake quotes.
        strikes.append(
            {
                "strike": float(k),
                "ce": {"tradingsymbol": f"{underlying}|{expiry}|{k}|CE", "exchange": "NFO", "lot_size": 65},
                "pe": {"tradingsymbol": f"{underlying}|{expiry}|{k}|PE", "exchange": "NFO", "lot_size": 65},
            }
        )
    return {"underlying": underlying, "expiry": expiry, "exchange": "NFO", "strike_step": STEP, "strikes": strikes}


def _quotes_for(keys: list[str]) -> dict:
    """Price each requested OTM leg at its expiry's IV so IV solves back."""
    out = {}
    for key in keys:
        sym = key.split(":", 1)[1]  # NFO:UND|EXPIRY|STRIKE|OTYPE
        _und, exp, strike_s, otype = sym.split("|")
        strike = float(strike_s)
        flag = "c" if otype == "CE" else "p"
        price = float(bs_price(flag, SPOT, strike, TTE_BY_EXP[exp], R, IV_BY_EXP[exp]))
        out[key] = {"last_price": price}
    return out


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(vs, "list_expiries", lambda u: EXPIRIES)
    monkeypatch.setattr(vs, "require_index_spot", lambda u: SPOT)
    monkeypatch.setattr(vs, "time_to_expiry_years", lambda e: TTE_BY_EXP[e])
    monkeypatch.setattr(vs, "get_chain", _chain)
    monkeypatch.setattr(vs, "_quote_batches", _quotes_for)


def test_config():
    cfg = vs.vol_surface_config()
    assert "NIFTY" in cfg["underlyings"]
    assert cfg["strike_count"] >= 5
    assert cfg["max_expiries"] >= 1


def test_unknown_underlying_raises():
    with pytest.raises(ValueError):
        vs.build_vol_surface("FOO")


def test_surface_shape(patched):
    snap = vs.build_vol_surface("NIFTY", strike_count=11)
    assert snap["underlying"] == "NIFTY"
    assert snap["spot"] == SPOT
    assert snap["atm_strike"] == SPOT
    assert len(snap["strikes"]) == 11
    # z is expiries × strikes
    assert len(snap["z"]) == len(snap["expiries"])
    assert all(len(row) == 11 for row in snap["z"])


def test_otm_convention(patched):
    snap = vs.build_vol_surface("NIFTY", strike_count=11)
    for p in snap["points"]:
        if p["strike"] < SPOT:
            assert p["option_type"] == "PE"
        else:
            assert p["option_type"] == "CE"


def test_term_structure_recovered(patched):
    snap = vs.build_vol_surface("NIFTY", strike_count=7)
    ts = {t["expiry"]: t["atm_iv"] for t in snap["term_structure"]}
    for e, iv in IV_BY_EXP.items():
        assert ts[e] == pytest.approx(iv * 100, abs=1.5)
    # Upward-sloping term structure preserved
    ivs = [ts[e] for e in EXPIRIES]
    assert ivs[0] < ivs[1] < ivs[2]


def test_max_expiries_limit(patched):
    snap = vs.build_vol_surface("NIFTY", max_expiries=2)
    assert len(snap["expiries"]) == 2
