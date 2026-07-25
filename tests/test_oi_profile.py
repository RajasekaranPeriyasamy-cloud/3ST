"""Tests for the OI Profile engine (futures candles + OI butterfly + daily OI)."""

from __future__ import annotations

import pandas as pd

import options.oi_profile as op
from options.oi_profile import _build_price_profile, _classify, _daily_oi_change


def _sample_df() -> pd.DataFrame:
    """3 sessions, 3 bars each, with engineered price/OI paths."""
    rows = [
        # day 1 — price up, OI up  (no prev day -> "—")
        ("2026-07-06 09:15", 95, 96, 900),
        ("2026-07-06 09:20", 96, 98, 950),
        ("2026-07-06 09:25", 98, 100, 1000),
        # day 2 — close 95 < 100, OI 1200 > 1000 -> Short buildup
        ("2026-07-07 09:15", 100, 99, 1100),
        ("2026-07-07 09:20", 99, 97, 1150),
        ("2026-07-07 09:25", 97, 95, 1200),
        # day 3 — close 98 > 95, OI 1100 < 1200 -> Short covering
        ("2026-07-08 09:15", 95, 96, 1150),
        ("2026-07-08 09:20", 96, 97, 1120),
        ("2026-07-08 09:25", 97, 98, 1100),
    ]
    recs = []
    for t, open_, close, oi in rows:
        recs.append(
            {
                "datetime": pd.Timestamp(t),
                "open": float(open_),
                "high": float(max(open_, close) + 2),
                "low": float(min(open_, close) - 2),
                "close": float(close),
                "volume": 1000.0,
                "oi": float(oi),
            }
        )
    return pd.DataFrame(recs).set_index("datetime").sort_index()


def test_classify_matches_convention() -> None:
    assert _classify(1.0, 1.0) == "Long buildup"
    assert _classify(-1.0, 1.0) == "Short buildup"
    assert _classify(1.0, -1.0) == "Short covering"
    assert _classify(-1.0, -1.0) == "Long unwinding"
    assert _classify(1.0, 0.0) == "Neutral"


def test_daily_oi_change_interpretation() -> None:
    df = _sample_df()
    daily = _daily_oi_change(df)
    assert len(daily) == 3
    assert daily[0]["interpretation"] == "—"  # no prior day
    assert daily[1]["interpretation"] == "Short buildup"
    assert daily[2]["interpretation"] == "Short covering"
    # day2: close 95 vs 100 => -5; oi 1200 vs 1000 => +200
    assert daily[1]["price_chg"] == -5.0
    assert daily[1]["oi_chg"] == 200


def test_price_profile_splits_buildup_and_unwind() -> None:
    df = _sample_df().copy()
    df["oi_change"] = df["oi"].diff().fillna(0.0)
    profile, poc = _build_price_profile(df, step=1)
    assert profile, "profile should not be empty"
    assert poc is not None
    total_buildup = sum(r["buildup"] for r in profile)
    total_unwind = sum(r["unwind"] for r in profile)
    assert total_buildup > 0
    assert total_unwind > 0  # day 3 has falling OI bars
    # rows sorted high price -> low
    mids = [r["price_mid"] for r in profile]
    assert mids == sorted(mids, reverse=True)


def test_snapshot_end_to_end(monkeypatch) -> None:
    df = _sample_df()
    monkeypatch.setattr(
        op,
        "resolve_future",
        lambda underlying, expiry=None: {
            "instrument_token": 111,
            "tradingsymbol": "NIFTY26JULFUT",
            "exchange": "NFO",
            "lot_size": 65,
            "expiry": "2026-07-30",
        },
    )
    monkeypatch.setattr(op, "list_future_expiries", lambda underlying: ["2026-07-30", "2026-08-27"])
    monkeypatch.setattr(op, "fetch_historical_by_token", lambda *a, **k: df)

    snap = op.oi_profile_snapshot("NIFTY", interval="5min", days=5)
    assert snap["ok"] is True
    assert snap["empty"] is False
    assert snap["meta"]["fut_symbol"] == "NIFTY26JULFUT"
    assert snap["meta"]["expiry"] == "2026-07-30"
    assert len(snap["candles"]) == 9
    assert len(snap["daily"]) == 3
    assert snap["stats"]["current_oi"] == 1100
    assert snap["stats"]["current_price"] == 98.0
    assert snap["poc_price"] is not None


def test_snapshot_empty_df(monkeypatch) -> None:
    monkeypatch.setattr(
        op,
        "resolve_future",
        lambda underlying, expiry=None: {
            "instrument_token": 1,
            "tradingsymbol": "NIFTY26JULFUT",
            "exchange": "NFO",
            "lot_size": 65,
            "expiry": "2026-07-30",
        },
    )
    monkeypatch.setattr(op, "list_future_expiries", lambda underlying: [])
    monkeypatch.setattr(
        op,
        "fetch_historical_by_token",
        lambda *a, **k: pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi"]),
    )
    snap = op.oi_profile_snapshot("NIFTY")
    assert snap["empty"] is True
    assert snap["candles"] == []
