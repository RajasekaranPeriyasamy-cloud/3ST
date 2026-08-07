"""Unit tests for Straddle Watch analytics helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

import options.straddle_watch as sw

IST = ZoneInfo("Asia/Kolkata")


def test_max_pain_prefers_heavy_oi_cluster() -> None:
    # Pain minimized near 100 when PE OI piles above and CE below
    rows = [
        {"strike": 90.0, "ce": {"oi": 100}, "pe": {"oi": 10}},
        {"strike": 100.0, "ce": {"oi": 50}, "pe": {"oi": 50}},
        {"strike": 110.0, "ce": {"oi": 10}, "pe": {"oi": 100}},
    ]
    mp = sw.max_pain_strike(rows)
    assert mp == 100.0


def test_max_pain_empty() -> None:
    assert sw.max_pain_strike([]) is None


def test_iv_rank_and_percentile() -> None:
    hist = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
    ivr, ivp = sw.iv_rank_and_percentile(15.0, hist)
    assert ivr == 50.0
    assert ivp == 50.0
    assert sw.iv_rank_and_percentile(None, hist) == (None, None)
    assert sw.iv_rank_and_percentile(15.0, [1.0, 2.0]) == (None, None)


def test_straddle_vwap_series() -> None:
    prices = [100.0, 110.0, 120.0]
    vols = [10.0, 10.0, 20.0]
    vwap = sw.straddle_vwap_series(prices, vols)
    assert vwap[0] == 100.0
    assert vwap[1] == 105.0
    # (100*10 + 110*10 + 120*20) / 40 = 4500/40 = 112.5
    assert vwap[2] == 112.5


def test_align_leg_frames_sums_straddle() -> None:
    idx = pd.to_datetime(["2026-08-05 09:15", "2026-08-05 09:16", "2026-08-05 09:17"])
    ce = pd.DataFrame(
        {"close": [100.0, 102.0, 101.0], "oi": [1000, 1100, 1200], "volume": [10, 20, 30]},
        index=idx,
    )
    pe = pd.DataFrame(
        {"close": [90.0, 88.0, 89.0], "oi": [800, 850, 900], "volume": [5, 5, 5]},
        index=idx,
    )
    joined = sw.align_leg_frames(ce, pe)
    assert list(joined["straddle_price"]) == [190.0, 190.0, 190.0]
    assert list(joined["straddle_vol"]) == [15.0, 25.0, 35.0]


def test_parse_range_rejects_bad() -> None:
    try:
        sw._parse_range("7D")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    assert sw._parse_range("5d") == "5D"


def test_range_bounds_1d_during_session() -> None:
    now = datetime(2026, 8, 5, 12, 0, tzinfo=IST)
    start, end = sw._range_bounds("NIFTY", "1D", now=now)
    assert start.hour == 9 and start.minute == 15
    assert end == now


def test_build_snapshot_end_to_end(monkeypatch) -> None:
    idx = pd.to_datetime(
        [
            "2026-08-05 09:15",
            "2026-08-05 09:16",
            "2026-08-05 10:00",
            "2026-08-05 15:20",
        ]
    )
    ce_df = pd.DataFrame(
        {
            "open": [100, 101, 102, 103],
            "high": [101, 102, 103, 104],
            "low": [99, 100, 101, 102],
            "close": [100.0, 101.0, 102.0, 103.0],
            "volume": [10, 10, 10, 10],
            "oi": [1000, 1100, 1200, 1300],
        },
        index=idx,
    )
    pe_df = pd.DataFrame(
        {
            "open": [90, 91, 92, 93],
            "high": [91, 92, 93, 94],
            "low": [89, 90, 91, 92],
            "close": [90.0, 89.0, 88.0, 87.0],
            "volume": [5, 5, 5, 5],
            "oi": [800, 820, 840, 860],
        },
        index=idx,
    )

    monkeypatch.setattr(sw, "list_expiries", lambda u: ["2026-08-11"])
    monkeypatch.setattr(
        sw,
        "find_option_leg",
        lambda u, e, k, otype: {
            "tradingsymbol": f"NIFTY{otype}",
            "instrument_token": 1 if otype == "CE" else 2,
            "exchange": "NFO",
            "lot_size": 65,
            "strike": k,
            "option_type": otype,
        },
    )
    monkeypatch.setattr(
        sw,
        "_fetch_leg_history",
        lambda token, start, end: ce_df if token == 1 else pe_df,
    )
    monkeypatch.setattr(
        sw,
        "get_chain",
        lambda u, e: {
            "exchange": "NFO",
            "strikes": [
                {
                    "strike": 24600.0,
                    "ce": {"tradingsymbol": "NIFTYCE", "exchange": "NFO", "instrument_token": 1},
                    "pe": {"tradingsymbol": "NIFTYPE", "exchange": "NFO", "instrument_token": 2},
                }
            ],
        },
    )
    def fake_quote_batches(keys):
        out = {}
        for k in keys:
            if "CE" in k:
                out[k] = {"last_price": 103.0, "oi": 1300, "ohlc": {"close": 100.0}}
            elif "PE" in k:
                out[k] = {"last_price": 87.0, "oi": 860, "ohlc": {"close": 90.0}}
            else:
                out[k] = {"last_price": 24637.0, "oi": 0, "ohlc": {"close": 24555.0}}
        return out

    import options.oi_var as oi_var

    monkeypatch.setattr(oi_var, "_quote_batches", fake_quote_batches)
    monkeypatch.setattr(sw, "require_index_spot", lambda u: 24600.0)
    monkeypatch.setattr(
        sw,
        "resolve_future",
        lambda underlying, expiry=None: {
            "instrument_token": 99,
            "tradingsymbol": "NIFTY25AUGFUT",
            "exchange": "NFO",
            "lot_size": 65,
            "expiry": "2026-08-25",
        },
    )
    monkeypatch.setattr(sw, "get_index_spot_detail", lambda u: (24591.0, None))
    monkeypatch.setattr(
        sw,
        "_now_ist",
        lambda: datetime(2026, 8, 5, 15, 40, tzinfo=IST),
    )

    # Patch instruments.resolve_instrument used in fair summary
    import instruments as instruments_mod

    monkeypatch.setattr(
        instruments_mod,
        "resolve_instrument",
        lambda key, force_refresh=False: {
            "exchange": "NSE",
            "tradingsymbol": "NIFTY 50",
            "instrument_token": 256265,
        },
    )

    snap = sw.build_straddle_watch_snapshot(
        "NIFTY",
        "2026-08-11",
        24600,
        24600,
        range_key="1D",
    )
    assert snap["ok"] is True
    assert snap["range"] == "1D"
    assert snap["summary"]["lot_size"] == 65
    assert snap["summary"]["fut_symbol"] == "NIFTY-25AUG26"
    assert snap["summary"]["straddle_ltp"] == 190.0
    assert len(snap["series"]["t"]) == 4
    assert snap["series"]["straddle_price"][0] == 190.0
    assert snap["summary"]["max_pain"] == 24600.0
    assert snap["summary"]["pcr"] is not None
    # Timestamps must carry an explicit IST offset for the chart axis.
    assert "+05:30" in snap["series"]["t"][0]


def test_invalid_underlying() -> None:
    try:
        sw.build_straddle_watch_snapshot("..", "2026-08-11", 100, 100)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
