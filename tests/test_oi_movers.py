"""Unit tests for OI Movers baseline (open / prior-day close) change logic."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import options.oi_movers as oi_movers
from options.oi_movers import (
    build_chart_series,
    build_session_change_boards,
    default_oi_movers_sample_underlyings,
    ensure_history_anchor_at_open,
    ensure_locked_side_base_oi,
    ensure_session_open_oi,
    maybe_sample_oi_movers_history_periodic,
    pick_baseline_oi,
    sum_side_oi,
)

IST = ZoneInfo("Asia/Kolkata")


def test_pick_baseline_prefers_open() -> None:
    oi, source = pick_baseline_oi(1000, 800)
    assert oi == 1000
    assert source == "open"


def test_pick_baseline_falls_back_to_prev_close() -> None:
    oi, source = pick_baseline_oi(None, 800)
    assert oi == 800
    assert source == "prev_close"


def test_pick_baseline_none() -> None:
    oi, source = pick_baseline_oi(None, None)
    assert oi is None
    assert source is None


def test_session_change_is_curr_minus_open() -> None:
    """Change = Curr − Open/PD (not interval move)."""
    calls = [
        {"key": "atm_ce", "strike": 24500, "latest_oi": 1100},
        {"key": "otm1_ce", "strike": 24600, "latest_oi": 500},
    ]
    puts = [
        {"key": "atm_pe", "strike": 24500, "latest_oi": 796},
    ]
    baselines = {
        "atm_ce": {"oi": 950, "source": "open", "open_oi": 950, "prev_close_oi": 800},
        "otm1_ce": {"oi": 520, "source": "prev_close", "open_oi": None, "prev_close_oi": 520},
        "atm_pe": {"oi": 775, "source": "open", "open_oi": 775, "prev_close_oi": 700},
    }
    boards = build_session_change_boards(
        calls,
        puts,
        expiry="2026-07-21",
        baselines=baselines,
        top_n=5,
    )
    pe = next(e for e in boards["increase_abs"] if e["option_type"] == "PE")
    assert pe["prev_oi"] == 775
    assert pe["curr_oi"] == 796
    assert pe["abs_chg"] == 21  # 796 - 775
    assert pe["pct_chg"] == round(21 / 775 * 100.0, 2)
    assert pe["prev_oi_source"] == "open"

    ce_top = boards["increase_abs"][0]
    assert ce_top["option_type"] == "CE"
    assert ce_top["abs_chg"] == 150  # 1100 - 950
    # Decrease: 500 - 520 = -20
    assert boards["decrease_abs"][0]["abs_chg"] == -20


def test_sum_side_oi_curr_and_base() -> None:
    rows = [
        {"key": "a", "latest_oi": 1100},
        {"key": "b", "latest_oi": 500},
    ]
    baselines = {
        "a": {"oi": 950, "source": "open"},
        "b": {"oi": 520, "source": "prev_close"},
    }
    curr, base, src = sum_side_oi(rows, baselines)
    assert curr == 1600
    assert base == 1470
    assert src == "open"  # any open wins dominant label


def test_ensure_session_open_stores_side_and_aggregates(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(oi_movers, "SESSION_FILE", tmp_path / "session_open.json")
    monkeypatch.setattr(oi_movers, "_today", lambda: "2026-08-04")
    monkeypatch.setattr(
        oi_movers,
        "datetime",
        type(
            "DT",
            (),
            {
                "now": staticmethod(
                    lambda tz=None: datetime(2026, 8, 4, 9, 25, tzinfo=IST)
                ),
                "fromisoformat": datetime.fromisoformat,
            },
        ),
    )
    rows = [
        {"instrument_token": 1, "latest_oi": 100, "key": "atm_ce"},
        {"instrument_token": 2, "latest_oi": 80, "key": "atm_pe"},
        {"instrument_token": 3, "latest_oi": 50, "key": "otm1_ce"},
    ]
    out = ensure_session_open_oi("NIFTY", "2026-08-04", rows, after_hhmm="09:20")
    assert out == {"1": 100, "2": 80, "3": 50}
    # Second call must not overwrite.
    ensure_session_open_oi(
        "NIFTY",
        "2026-08-04",
        [{"instrument_token": 1, "latest_oi": 999, "key": "atm_ce"}],
        after_hhmm="09:20",
    )
    data = oi_movers._load_json(oi_movers.SESSION_FILE)
    entry = data["entries"]["NIFTY|2026-08-04|2026-08-04"]
    assert entry["by_token"]["1"]["oi"] == 100
    assert entry["by_token"]["1"]["side"] == "CE"
    assert entry["ce_base_oi"] == 150
    assert entry["pe_base_oi"] == 80


def test_locked_side_base_survives_window_resume(tmp_path, monkeypatch) -> None:
    """Chart Open totals freeze; a later ATM-window sum must not move them."""
    monkeypatch.setattr(oi_movers, "SESSION_FILE", tmp_path / "session_open.json")
    monkeypatch.setattr(oi_movers, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(oi_movers, "_today", lambda: "2026-08-04")

    ce1, pe1, src1 = ensure_locked_side_base_oi(
        "NIFTY",
        "2026-08-04",
        ce_base_oi=106_334_410,
        pe_base_oi=90_640_485,
        base_source="open",
    )
    assert (ce1, pe1, src1) == (106_334_410, 90_640_485, "open")

    ce2, pe2, src2 = ensure_locked_side_base_oi(
        "NIFTY",
        "2026-08-04",
        ce_base_oi=101_809_305,  # drifted ATM window
        pe_base_oi=102_880_180,
        base_source="open",
    )
    assert (ce2, pe2, src2) == (106_334_410, 90_640_485, "open")


def test_locked_side_base_recovers_from_history_first(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(oi_movers, "SESSION_FILE", tmp_path / "session_open.json")
    monkeypatch.setattr(oi_movers, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(oi_movers, "_today", lambda: "2026-08-04")
    key = "NIFTY|2026-08-04|2026-08-04"
    oi_movers._save_json(
        oi_movers.HISTORY_FILE,
        {
            "entries": {
                key: [
                    {
                        "t": "2026-08-04T09:23:20+05:30",
                        "ce_base_oi": 106_334_410,
                        "pe_base_oi": 90_640_485,
                        "base_source": "open",
                    },
                    {
                        "t": "2026-08-04T10:59:34+05:30",
                        "ce_base_oi": 101_809_305,
                        "pe_base_oi": 102_880_180,
                        "base_source": "open",
                    },
                ]
            }
        },
    )
    # Seed empty session entry (tokens captured elsewhere, aggregates missing).
    oi_movers._save_json(
        oi_movers.SESSION_FILE,
        {"entries": {key: {"by_token": {"1": {"oi": 1}}, "session_date": "2026-08-04"}}},
    )

    def _fake_filter(underlying: str, points: list) -> list:
        return list(points)

    monkeypatch.setattr(oi_movers, "filter_history_to_session", _fake_filter)

    ce, pe, src = ensure_locked_side_base_oi(
        "NIFTY",
        "2026-08-04",
        ce_base_oi=101_809_305,
        pe_base_oi=102_880_180,
        base_source="open",
    )
    assert (ce, pe, src) == (106_334_410, 90_640_485, "open")


def test_build_chart_series_attaches_oi_near_spot() -> None:
    today = datetime.now(tz=IST).date().isoformat()
    history = [
        {
            "t": f"{today}T09:20:00+05:30",
            "spot": 23600,
            "ce_oi": 1_000_000,
            "pe_oi": 900_000,
            "ce_base_oi": 950_000,
            "pe_base_oi": 880_000,
            "pcr": 0.9,
            "base_source": "open",
        },
        {
            "t": f"{today}T10:10:00+05:30",
            "spot": 23550,
            "ce_oi": 1_100_000,
            "pe_oi": 980_000,
            "ce_base_oi": 950_000,
            "pe_base_oi": 880_000,
            "pcr": 0.89,
            "base_source": "open",
        },
    ]
    candles = [
        {"date": f"{today}T09:20:00+05:30", "close": 23600},
        {"date": f"{today}T10:10:00+05:30", "close": 23550},
        {"date": f"{today}T14:00:00+05:30", "close": 23780},
    ]
    series = build_chart_series("NIFTY", history, candles)
    assert len(series) == 3
    assert series[0]["ce_oi"] == 1_000_000
    assert series[0]["ce_base_oi"] == 950_000
    assert series[1]["pe_oi"] == 980_000
    assert series[2]["spot"] == 23780
    # Afternoon candle keeps last known OI via forward-fill (solid lines stay visible).
    assert series[2]["ce_oi"] == 1_100_000
    assert series[2]["pcr"] == 0.89


def test_ensure_history_anchor_inserts_open_tick(tmp_path, monkeypatch) -> None:
    """Late first UI poll still gets a ~09:20 CE/PE/PCR anchor from open capture."""
    today = datetime.now(tz=IST).date().isoformat()
    expiry = "2026-08-11"
    monkeypatch.setattr(oi_movers, "SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(oi_movers, "HISTORY_FILE", tmp_path / "history.json")
    monkeypatch.setattr(oi_movers, "_today", lambda: today)
    monkeypatch.setattr(
        oi_movers,
        "filter_history_to_session",
        lambda _u, points: list(points),
    )

    key = f"NIFTY|{expiry}|{today}"
    oi_movers._save_json(
        oi_movers.SESSION_FILE,
        {
            "entries": {
                key: {
                    "captured_at": f"{today}T09:21:37+05:30",
                    "ce_base_oi": 46_000_000,
                    "pe_base_oi": 48_000_000,
                    "base_source": "open",
                    "by_token": {},
                }
            }
        },
    )
    # Late live samples only (reproduces today's blank-until-~10:00 chart).
    oi_movers._save_json(
        oi_movers.HISTORY_FILE,
        {
            "entries": {
                key: [
                    {
                        "t": f"{today}T09:57:43+05:30",
                        "ce_oi": 57_000_000,
                        "pe_oi": 70_000_000,
                        "pcr": 1.23,
                        "spot": 24610,
                    }
                ]
            }
        },
    )

    out = ensure_history_anchor_at_open("NIFTY", expiry)
    assert len(out) == 2
    assert out[0]["t"].startswith(f"{today}T09:21:37")
    assert out[0]["ce_oi"] == 46_000_000
    assert out[0]["pe_oi"] == 48_000_000
    assert out[0]["pcr"] == round(48_000_000 / 46_000_000, 4)
    assert out[0]["source"] == "open_anchor"
    assert out[1]["ce_oi"] == 57_000_000

    # Idempotent — second call does not duplicate.
    out2 = ensure_history_anchor_at_open("NIFTY", expiry)
    assert len(out2) == 2


def test_default_oi_movers_sample_underlyings_prefers_cash() -> None:
    names = default_oi_movers_sample_underlyings()
    assert "NIFTY" in names
    assert names.index("NIFTY") < names.index("CRUDEOIL") if "CRUDEOIL" in names else True


def test_maybe_sample_oi_movers_history_periodic(monkeypatch) -> None:
    called: list[str] = []

    monkeypatch.setattr(
        oi_movers,
        "default_oi_movers_sample_underlyings",
        lambda: ["NIFTY", "BANKNIFTY"],
    )
    monkeypatch.setattr(
        "options.gamma_density_history.in_session",
        lambda _u, _now=None: True,
    )
    oi_movers._oi_sample_last_ok.clear()

    def _fake_snap(underlying: str, *a, **k):
        called.append(underlying)
        return {"ok": True}

    monkeypatch.setattr(oi_movers, "build_movers_snapshot", _fake_snap)
    # Weekday path — force "today" via a Monday-ish datetime if needed.
    fake_now = datetime(2026, 8, 7, 10, 0, tzinfo=IST)  # Friday
    monkeypatch.setattr(
        oi_movers,
        "datetime",
        type(
            "DT",
            (),
            {
                "now": staticmethod(lambda tz=None: fake_now),
                "fromisoformat": datetime.fromisoformat,
                "min": datetime.min,
                "combine": datetime.combine,
            },
        ),
    )

    assert maybe_sample_oi_movers_history_periodic() is True
    assert called == ["NIFTY", "BANKNIFTY"]
