"""Tests for the CAS intraday history store (data/cas_history.jsonl)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from options import cas_history as hist

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture(autouse=True)
def _reset_throttle() -> None:
    hist.reset_throttle()


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "cas_history.jsonl"


def _payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "underlying": "NIFTY",
        "in_cas_window": True,
        "spot": 24560.15,
        "indicative": None,
        "official_indicative": None,
        "official_raw": 1866.0,
        "official_reject_reason": "out_of_band",
        "estimate": 24611.20,
        "estimate_method": "proxy_v1",
        "estimate_components": {
            "synth_f": 24605.65,
            "fut_ltp": 24602.0,
            "ref_vwap": 24580.0,
            "ref_vwap_window": "pre_close_1515",
            "fut_poc": 24590.0,
        },
        "total_imbalance": None,
        "source": "kite_quote",
        "asof": "2026-08-10T15:29:36+05:30",
        "session_poc": {"poc": 24595.0},
        "synthetic_future": {"F": 24605.65},
    }
    base.update(over)
    return base


def _lines(p: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# --- row shape ---------------------------------------------------------------


def test_row_flattens_nested_payload() -> None:
    row = hist.row_from_payload(_payload())
    assert row["session"] == "2026-08-10"
    assert row["underlying"] == "NIFTY"
    assert row["spot"] == 24560.15
    assert row["estimate"] == 24611.20
    assert row["official_indicative"] is None
    assert row["official_raw"] == 1866.0
    assert row["official_reject_reason"] == "out_of_band"
    # Nested blocks are lifted onto the flat row.
    assert row["synth_f"] == 24605.65
    assert row["fut_poc"] == 24595.0
    assert row["ref_vwap_window"] == "pre_close_1515"
    # Phase B columns exist up front so the schema needs no migration later.
    assert row["constituent_est"] is None
    assert row["coverage"] is None


def test_row_prefers_synthetic_block_then_components() -> None:
    row = hist.row_from_payload(_payload(synthetic_future=None))
    assert row["synth_f"] == 24605.65  # falls back to estimate_components

    row2 = hist.row_from_payload(_payload(synthetic_future=None, estimate_components={}))
    assert row2["synth_f"] is None


def test_row_session_falls_back_to_now_on_bad_asof() -> None:
    row = hist.row_from_payload(_payload(asof="not-a-timestamp"))
    assert row["session"] == datetime.now(tz=IST).date().isoformat()


# --- append ------------------------------------------------------------------


def test_append_writes_one_line(path: Path) -> None:
    assert hist.append_snapshot(_payload(), path=path) is True
    rows = _lines(path)
    assert len(rows) == 1
    assert rows[0]["estimate"] == 24611.20


def test_append_throttled_per_underlying(path: Path) -> None:
    assert hist.append_snapshot(_payload(), path=path) is True
    # Second call inside MIN_APPEND_INTERVAL_SEC is dropped.
    assert hist.append_snapshot(_payload(), path=path) is False
    assert len(_lines(path)) == 1

    # A different underlying has its own throttle.
    assert hist.append_snapshot(_payload(underlying="BANKNIFTY"), path=path) is True
    assert len(_lines(path)) == 2


def test_append_skips_rows_with_nothing_plottable(path: Path) -> None:
    empty = _payload(spot=None, estimate=None, official_indicative=None)
    assert hist.append_snapshot(empty, path=path) is False
    assert not path.exists()


def test_append_skips_payload_without_underlying(path: Path) -> None:
    assert hist.append_snapshot(_payload(underlying=""), path=path) is False
    assert not path.exists()


def test_append_never_raises_on_bad_path(tmp_path: Path) -> None:
    bad = tmp_path / "no-such-dir" / "cas_history.jsonl"
    assert hist.append_snapshot(_payload(), path=bad) is False


# --- read --------------------------------------------------------------------


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_read_session_defaults_to_latest_on_disk(path: Path) -> None:
    _write(
        path,
        [
            {"underlying": "NIFTY", "session": "2026-08-07", "spot": 1.0},
            {"underlying": "NIFTY", "session": "2026-08-10", "spot": 2.0},
            {"underlying": "NIFTY", "session": "2026-08-10", "spot": 3.0},
        ],
    )
    rows = hist.read_session("NIFTY", path=path)
    assert [r["spot"] for r in rows] == [2.0, 3.0]


def test_read_session_filters_underlying_and_explicit_date(path: Path) -> None:
    _write(
        path,
        [
            {"underlying": "NIFTY", "session": "2026-08-10", "spot": 1.0},
            {"underlying": "BANKNIFTY", "session": "2026-08-10", "spot": 2.0},
            {"underlying": "NIFTY", "session": "2026-08-07", "spot": 3.0},
        ],
    )
    assert [r["spot"] for r in hist.read_session("NIFTY", "2026-08-07", path=path)] == [3.0]
    assert [r["spot"] for r in hist.read_session("BANKNIFTY", path=path)] == [2.0]
    assert hist.read_session("NIFTY", "2026-01-01", path=path) == []


def test_read_session_limit_keeps_most_recent(path: Path) -> None:
    _write(
        path,
        [{"underlying": "NIFTY", "session": "2026-08-10", "spot": float(i)} for i in range(10)],
    )
    rows = hist.read_session("NIFTY", path=path, limit=3)
    assert [r["spot"] for r in rows] == [7.0, 8.0, 9.0]


def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    assert hist.read_session("NIFTY", path=tmp_path / "absent.jsonl") == []
    assert hist.sessions(path=tmp_path / "absent.jsonl") == []


def test_read_skips_corrupt_lines(path: Path) -> None:
    path.write_text(
        '{"underlying": "NIFTY", "session": "2026-08-10", "spot": 1.0}\n'
        "{ not json\n"
        '{"underlying": "NIFTY", "session": "2026-08-10", "spot": 2.0}\n',
        encoding="utf-8",
    )
    assert [r["spot"] for r in hist.read_session("NIFTY", path=path)] == [1.0, 2.0]


# --- prune -------------------------------------------------------------------


def test_prune_keeps_most_recent_sessions(path: Path) -> None:
    _write(
        path,
        [
            {"underlying": "NIFTY", "session": f"2026-08-{d:02d}", "spot": float(d)}
            for d in range(1, 21)
        ],
    )
    dropped = hist.prune(max_sessions=5, path=path)
    assert dropped == 15
    assert hist.sessions(path=path) == [f"2026-08-{d:02d}" for d in range(16, 21)]


def test_prune_noop_under_limit(path: Path) -> None:
    _write(path, [{"underlying": "NIFTY", "session": "2026-08-10", "spot": 1.0}])
    assert hist.prune(max_sessions=14, path=path) == 0
    assert len(_lines(path)) == 1
