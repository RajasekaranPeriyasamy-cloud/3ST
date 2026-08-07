"""Unit tests for OI Tracker change boards and prev_oi math."""

from __future__ import annotations

from options.oi_tracker import (
    _contract_label,
    _format_expiry_short,
    _prev_oi,
    build_oi_change_boards,
)


def _row(
    *,
    strike: float,
    latest_oi: int | None,
    abs_map: dict[str, int | None],
    pct_map: dict[str, float | None],
    key: str = "atm_ce",
) -> dict:
    prev = {k: _prev_oi(latest_oi, v) for k, v in abs_map.items()}
    return {
        "key": key,
        "strike": strike,
        "symbol": "TEST",
        "position": 0,
        "latest_oi": latest_oi,
        "pct": pct_map,
        "abs": abs_map,
        "prev_oi": prev,
        "breach": {},
    }


def test_format_expiry_short() -> None:
    assert _format_expiry_short("2026-07-21") == "21-Jul-26"
    assert _format_expiry_short("2026-07-21T00:00:00+05:30") == "21-Jul-26"


def test_contract_label() -> None:
    assert _contract_label(24200, "PE", "2026-07-21") == "24,200 PE 21-Jul-26"
    assert _contract_label(24500.0, "CE", "2026-07-21") == "24,500 CE 21-Jul-26"


def test_prev_oi_math() -> None:
    assert _prev_oi(1000, 150) == 850
    assert _prev_oi(1000, -200) == 1200
    assert _prev_oi(None, 10) is None
    assert _prev_oi(1000, None) is None


def test_boards_rank_ce_and_pe_separately() -> None:
    calls = [
        _row(
            strike=24500,
            latest_oi=1100,
            abs_map={"15": 100},
            pct_map={"15": 10.0},
            key="atm_ce",
        ),
        _row(
            strike=24600,
            latest_oi=2000,
            abs_map={"15": 500},
            pct_map={"15": 33.3},
            key="otm1_ce",
        ),
        _row(
            strike=24400,
            latest_oi=900,
            abs_map={"15": -300},
            pct_map={"15": -25.0},
            key="itm1_ce",
        ),
    ]
    puts = [
        _row(
            strike=24200,
            latest_oi=1645,
            abs_map={"15": 1363},
            pct_map={"15": 483.0},
            key="otm2_pe",
        ),
        _row(
            strike=24300,
            latest_oi=800,
            abs_map={"15": -50},
            pct_map={"15": -5.9},
            key="otm1_pe",
        ),
        _row(
            strike=24100,
            latest_oi=None,
            abs_map={"15": 999},
            pct_map={"15": 99.0},
            key="otm3_pe",
        ),
        _row(
            strike=24000,
            latest_oi=500,
            abs_map={"15": None},
            pct_map={"15": None},
            key="otm4_pe",
        ),
    ]

    boards = build_oi_change_boards(
        calls,
        puts,
        expiry="2026-07-21",
        intervals_min=(15,),
        top_n=2,
    )
    assert set(boards.keys()) == {"15"}
    b = boards["15"]

    # Increase abs: CE block first (top_n=2), then PE block
    assert [e["abs_chg"] for e in b["increase_abs"]] == [500, 100, 1363]
    assert [e["option_type"] for e in b["increase_abs"]] == ["CE", "CE", "PE"]
    assert b["increase_abs"][0]["contract"] == "24,600 CE 21-Jul-26"
    assert b["increase_abs"][0]["bar_pct"] == 100.0
    assert b["increase_abs"][1]["bar_pct"] == round(100 / 500 * 100.0, 1)
    pe = b["increase_abs"][2]
    assert pe["contract"] == "24,200 PE 21-Jul-26"
    assert pe["prev_oi"] == 282  # 1645 - 1363
    assert pe["curr_oi"] == 1645
    assert pe["bar_pct"] == 100.0  # peak within PE side

    # Increase pct: CE then PE
    assert [e["pct_chg"] for e in b["increase_pct"]] == [33.3, 10.0, 483.0]
    assert b["increase_pct"][0]["option_type"] == "CE"
    assert b["increase_pct"][-1]["option_type"] == "PE"

    # Decrease abs: CE then PE
    assert [e["abs_chg"] for e in b["decrease_abs"]] == [-300, -50]
    assert [e["option_type"] for e in b["decrease_abs"]] == ["CE", "PE"]
    assert b["decrease_abs"][0]["bar_pct"] == 100.0

    # Decrease pct
    assert [e["pct_chg"] for e in b["decrease_pct"]] == [-25.0, -5.9]

    # Nulls / missing latest_oi skipped
    contracts = {e["contract"] for bucket in b.values() for e in bucket}
    assert not any("24,100" in c for c in contracts)
    assert not any("24,000" in c for c in contracts)


def test_boards_keyed_by_all_intervals() -> None:
    calls = [
        _row(
            strike=24500,
            latest_oi=1100,
            abs_map={"5": 10, "15": 100},
            pct_map={"5": 1.0, "15": 10.0},
        )
    ]
    boards = build_oi_change_boards(
        calls,
        [],
        expiry="2026-07-21",
        intervals_min=(5, 15),
        top_n=5,
    )
    assert set(boards.keys()) == {"5", "15"}
    assert boards["5"]["increase_abs"][0]["abs_chg"] == 10
    assert boards["15"]["increase_abs"][0]["abs_chg"] == 100
    assert boards["5"]["decrease_abs"] == []
