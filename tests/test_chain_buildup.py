"""Chain build-up grid tests.

The whole page is a differencing scheme, so the two properties that make it
readable are pinned here rather than left to inspection: buckets telescope to
the cumulative total, and a bucket's OI is its *last* value, never a mean.

Every test is offline. The service tests point ``delta_velocity.store`` at a
tmp_path by patching that module's own ``data_dir`` reference — patching
``settings.data_dir`` would leave it bound to the real ``data/``, which is how a
theta-decay fixture once appended 1,800 synthetic snapshots into the live
archive (see CLAUDE.md).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from analysis.chain_buildup import features, service
from analysis.delta_velocity import store as dv_store

START = datetime(2026, 8, 27, 9, 15)


def _rows(oi_by_minute, *, strike=24000.0, option_type="CE", expiry="2026-09-01", ltp_by_minute=None):
    """One leg's minute rows: ``oi_by_minute[i]`` is the OI at 09:15 + i min."""
    out = []
    for i, oi in enumerate(oi_by_minute):
        ltp = None if ltp_by_minute is None else ltp_by_minute[i]
        out.append(
            {
                "ts": (START + timedelta(minutes=i)).isoformat(),
                "expiry": expiry,
                "strike": strike,
                "option_type": option_type,
                "oi": oi,
                "ltp": ltp,
                "spot": 24010.0,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------


def test_bucket_end_is_half_open_on_the_right_edge():
    """09:20:00 closes the 09:15-09:20 bucket; 09:20:01 opens the next."""
    assert features.bucket_end(START, 5, START) == START + timedelta(minutes=5)
    assert features.bucket_end(START + timedelta(minutes=5), 5, START) == START + timedelta(minutes=5)
    assert features.bucket_end(
        START + timedelta(minutes=5, seconds=1), 5, START
    ) == START + timedelta(minutes=10)


def test_bucket_takes_last_oi_not_the_mean():
    """OI is a level. A bucket averaging 100 and 900 would report 500, which was
    never true at any instant inside it."""
    rows = _rows([100, 500, 900, 1000, 1000], ltp_by_minute=[1, 2, 3, 4, 5])
    grid = features.build_grid(rows, timeframe_min=5, atm=24000.0)
    cell = grid["rows"][0]["ce"]["cells"][0]
    assert cell["oi"] == 1000  # the 09:19 row, last inside 09:15-09:20


def test_bucket_edges_anchor_at_session_open_not_first_row():
    """A collector that starts late must not shift every column label."""
    late = _rows([10, 20], strike=24000.0)
    late = [dict(r, ts=(START + timedelta(minutes=37 + i)).isoformat()) for i, r in enumerate(late)]
    grid = features.build_grid(late, timeframe_min=15, atm=24000.0)
    assert [b["key"] for b in grid["buckets"]] == ["10:00"]


def test_out_of_order_rows_do_not_rewrite_a_bucket_backwards():
    rows = _rows([100, 900])
    rows.reverse()
    grid = features.build_grid(rows, timeframe_min=5, atm=24000.0)
    assert grid["rows"][0]["ce"]["cells"][0]["oi"] == 900


# ---------------------------------------------------------------------------
# Deltas
# ---------------------------------------------------------------------------


def test_buckets_telescope_to_the_cumulative_total():
    """Per-bucket columns and the total can never disagree — that is the point
    of differencing against the previous bucket rather than the baseline."""
    rows = _rows(list(range(100, 100 + 40)))
    grid = features.build_grid(
        rows, timeframe_min=5, baselines={(24000.0, "CE"): 50.0}, atm=24000.0
    )
    side = grid["rows"][0]["ce"]
    deltas = [c["d_oi"] for c in side["cells"] if c["d_oi"] is not None]
    assert sum(deltas) == side["total_delta"]
    assert side["total_delta"] == side["latest_oi"] - side["baseline"]


def test_first_bucket_is_measured_against_the_baseline():
    rows = _rows([100, 100, 100, 100, 100])
    grid = features.build_grid(
        rows, timeframe_min=5, baselines={(24000.0, "CE"): 60.0}, atm=24000.0
    )
    assert grid["rows"][0]["ce"]["cells"][0]["d_oi"] == 40


def test_missing_baseline_falls_back_to_the_legs_own_first_oi():
    """A strike that only enters the window mid-session must not show a jump off
    a baseline it was never measured against."""
    rows = _rows([700, 700, 700, 700, 700])
    grid = features.build_grid(rows, timeframe_min=5, atm=24000.0)
    side = grid["rows"][0]["ce"]
    assert side["baseline"] == 700
    assert side["cells"][0]["d_oi"] == 0


def test_gap_delta_spans_the_whole_absence():
    """A leg that drops out of the ATM window and returns reports the move over
    the gap, keeping the telescoping property intact."""
    # A neighbour strike present throughout establishes every bucket edge, so
    # the absent leg renders as blank columns rather than vanishing.
    rows = _rows([500] * 26, strike=23900.0)
    rows += _rows([100] * 5)
    rows += [
        {
            "ts": (START + timedelta(minutes=25)).isoformat(),
            "expiry": "2026-09-01",
            "strike": 24000.0,
            "option_type": "CE",
            "oi": 400,
            "ltp": 5.0,
            "spot": 24010.0,
        }
    ]
    grid = features.build_grid(
        rows, timeframe_min=5, baselines={(24000.0, "CE"): 100.0}, atm=24000.0
    )
    row = next(r for r in grid["rows"] if r["strike"] == 24000.0)
    cells = row["ce"]["cells"]
    assert cells[1]["d_oi"] is None and cells[1]["oi"] is None  # blank, not zero
    assert cells[-1]["d_oi"] == 300


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "d_oi,d_price,expected",
    [
        (10, 1.0, features.LONG_BUILDUP),
        (10, -1.0, features.SHORT_BUILDUP),
        (-10, 1.0, features.SHORT_COVERING),
        (-10, -1.0, features.LONG_UNWINDING),
    ],
)
def test_four_quadrant_classification(d_oi, d_price, expected):
    assert features.classify(d_oi, d_price) == expected


@pytest.mark.parametrize("d_oi,d_price", [(0, 1.0), (10, 0.0), (None, 1.0), (10, None)])
def test_flat_or_missing_axis_gets_no_class(d_oi, d_price):
    """"Unchanged" is not a quadrant — inventing one paints dead strikes with
    signal they do not carry."""
    assert features.classify(d_oi, d_price) is None


def test_classification_reaches_the_cells():
    """OI rising while premium falls is short build-up — writers, not buyers."""
    minutes = 15
    rows = _rows(
        [100 + 20 * i for i in range(minutes)],
        ltp_by_minute=[50.0 - i for i in range(minutes)],
    )
    grid = features.build_grid(
        rows, timeframe_min=5, baselines={(24000.0, "CE"): 100.0}, atm=24000.0
    )
    classes = [c["cls"] for c in grid["rows"][0]["ce"]["cells"]]
    assert features.SHORT_BUILDUP in classes
    assert features.LONG_BUILDUP not in classes


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------


def test_scale_uses_p95_so_one_outlier_cannot_wash_the_grid():
    """One expiry-day print an order of magnitude above the rest must not scale
    the whole grid to white."""
    oi = [100 + 10 * i for i in range(20)] + [10_000_000]
    rows = []
    for i, value in enumerate(oi):
        rows.append(
            {
                "ts": (START + timedelta(minutes=5 * i)).isoformat(),
                "expiry": "2026-09-01",
                "strike": 24000.0,
                "option_type": "CE",
                "oi": value,
                "ltp": 10.0,
                "spot": 24010.0,
            }
        )
    grid = features.build_grid(rows, timeframe_min=5, atm=24000.0)
    scale = grid["scale"]["ce"]["delta"]
    assert scale["max"] > scale["p95"] * 10


# ---------------------------------------------------------------------------
# Service — archive reads, offline
# ---------------------------------------------------------------------------


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """A two-session archive under tmp_path.

    Patches ``dv_store.data_dir`` — the module's own binding — not
    ``settings.data_dir``.
    """
    monkeypatch.setattr(dv_store, "data_dir", lambda: tmp_path)

    def write(day: str, base_oi: int):
        path = dv_store.underlying_dir("NIFTY") / f"{day}.jsonl"
        lines = []
        for minute in range(0, 30):
            ts = datetime.fromisoformat(f"{day}T09:15:00+05:30") + timedelta(minutes=minute)
            legs = []
            for strike in (23900.0, 24000.0, 24100.0):
                for opt in ("CE", "PE"):
                    legs.append(
                        {
                            "tradingsymbol": f"NIFTY{int(strike)}{opt}",
                            "expiry": "2026-09-01",
                            "strike": strike,
                            "option_type": opt,
                            "ltp": 100.0 + minute,
                            "oi": base_oi + minute * 10,
                            "volume": 5,
                        }
                    )
            lines.append(
                json.dumps(
                    {
                        "ts": ts.isoformat(),
                        "session_date": day,
                        "underlying": "NIFTY",
                        "spot": 24010.0,
                        "legs": legs,
                    }
                )
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    write("2026-08-26", 1000)
    write("2026-08-27", 5000)
    return tmp_path


def test_grid_defaults_to_the_latest_archived_session(archive):
    grid = service.get_grid("NIFTY", strike_range="atm5", widen=False)
    assert grid["session_date"] == "2026-08-27"
    assert grid["meta"]["source"] == "archive"
    assert grid["atm"] == 24000.0


def test_session_open_baseline_is_the_first_oi_of_the_day(archive):
    grid = service.get_grid("NIFTY", strike_range="atm5", widen=False, timeframe_min=5)
    row = next(r for r in grid["rows"] if r["strike"] == 24000.0)
    assert row["ce"]["baseline"] == 5000


def test_prev_close_baseline_reads_the_previous_session(archive):
    grid = service.get_grid(
        "NIFTY", strike_range="atm5", widen=False, baseline_mode="prev_close"
    )
    row = next(r for r in grid["rows"] if r["strike"] == 24000.0)
    assert row["ce"]["baseline"] == 1000 + 29 * 10  # last minute of 2026-08-26
    assert grid["meta"]["notes"] == []


def test_prev_close_says_so_when_there_is_no_earlier_session(archive):
    """It must not silently fall back to session-open and mislabel the column."""
    grid = service.get_grid(
        "NIFTY", session_date="2026-08-26", strike_range="atm5",
        widen=False, baseline_mode="prev_close",
    )
    assert any("previous-day close unavailable" in n.lower() for n in grid["meta"]["notes"])


def test_widen_false_never_reaches_kite(archive):
    """The offline guard would fail the call; this asserts the code path instead
    of relying on it."""
    grid = service.get_grid("NIFTY", strike_range="all", widen=False)
    assert grid["meta"]["source"] == "archive"
    assert all(r["strike"] in (23900.0, 24000.0, 24100.0) for r in grid["rows"])


def test_unknown_underlying_is_rejected(archive):
    with pytest.raises(ValueError, match="Unknown underlying"):
        service.get_grid("CRUDEOIL")


def test_unknown_timeframe_is_rejected(archive):
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        service.get_grid("NIFTY", timeframe_min=7, widen=False)


# ---------------------------------------------------------------------------
# Source mixing — archive (tz-aware) against Kite candles (tz-naive)
# ---------------------------------------------------------------------------


def test_mixed_tz_aware_and_naive_rows_bucket_together():
    """A widened grid merges +05:30-aware archive rows with naive Kite candles.
    Subtracting one from the other used to raise TypeError; both are normalised
    to naive IST at the parse boundary instead."""
    aware = [
        dict(r, ts=f"{r['ts']}+05:30") for r in _rows([100, 200, 300], strike=24000.0)
    ]
    naive = _rows([10, 20, 30], strike=24100.0)
    grid = features.build_grid(aware + naive, timeframe_min=5, atm=24000.0)
    assert len(grid["rows"]) == 2
    assert len(grid["buckets"]) == 1


def test_parse_ts_converts_a_foreign_offset_to_ist_wall_clock():
    parsed = features.parse_ts("2026-08-27T09:15:00+00:00")
    assert parsed is not None and parsed.tzinfo is None
    assert parsed.hour == 14 and parsed.minute == 45  # 09:15 UTC is 14:45 IST


def test_candles_are_stamped_at_close_not_open():
    """Kite labels a candle by its open. Passing that through would land a 09:20
    candle in the 09:20 bucket — one column early."""
    rows = service._candles_to_rows(
        [{"date": "2026-08-27T09:20:00+05:30", "close": 12.5, "oi": 900, "volume": 3}],
        strike=24000.0,
        option_type="CE",
        expiry="2026-09-01",
        timeframe_min=5,
    )
    assert features.parse_ts(rows[0]["ts"]) == datetime(2026, 8, 27, 9, 25)
