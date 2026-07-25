"""Unit tests for OI Movers baseline (open / prior-day close) change logic."""

from __future__ import annotations

from options.oi_movers import build_session_change_boards, pick_baseline_oi


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
