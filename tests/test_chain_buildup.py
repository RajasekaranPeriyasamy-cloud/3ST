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
from datetime import date, datetime, timedelta

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
    """A two-session archive under tmp_path. Returns ``(today, yesterday)``.

    Dates derive from ``date.today()`` rather than being hardcoded: a fixture
    pinned to a date that is *currently* valid silently stops testing anything
    the moment it ages out, which is the rot CLAUDE.md calls out.

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

    today = date.today()
    yesterday = today - timedelta(days=1)
    write(yesterday.isoformat(), 1000)
    write(today.isoformat(), 5000)
    return today.isoformat(), yesterday.isoformat()


def test_grid_defaults_to_the_latest_archived_session(archive):
    today, _ = archive
    grid = service.get_grid("NIFTY", strike_range="atm5", widen=False)
    assert grid["session_date"] == today
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
    assert row["ce"]["baseline"] == 1000 + 29 * 10  # last minute of the prior session
    assert grid["meta"]["notes"] == []


def test_prev_close_says_so_when_there_is_no_earlier_session(archive):
    """It must not silently fall back to session-open and mislabel the column."""
    _, yesterday = archive
    grid = service.get_grid(
        "NIFTY", session_date=yesterday, strike_range="atm5",
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


def test_wide_range_falls_back_when_the_listed_chain_is_unreadable(archive, monkeypatch):
    """No instrument cache (CI, or before the first Kite sync) must degrade to the
    archive's own strikes, not raise.

    The fallback logs a warning, and `log_event` takes a numeric level — passing
    the string "warning" blew up inside logging with
    `'>=' not supported between instances of 'str' and 'int'`, but only on this
    path, which is why a machine with a populated data/ never saw it.
    """
    import options.chain as chain_mod

    def _boom(*_a, **_k):
        raise RuntimeError("Instrument cache empty")

    monkeypatch.setattr(chain_mod, "get_chain", _boom)
    grid = service.get_grid("NIFTY", strike_range="all", widen=False)
    assert [r["strike"] for r in grid["rows"]] == [23900.0, 24000.0, 24100.0]
    assert grid["meta"]["source"] == "archive"


# ---------------------------------------------------------------------------
# Breach thresholds
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pct,absolute,expected",
    [
        (20.0, 100_000, True),   # clears both
        (20.0, 1_000, False),    # big percent, trivial size
        (2.0, 100_000, False),   # big size, small percent
        (-20.0, -100_000, True), # unwinding breaches the same way
        (None, 100_000, False),
        (20.0, None, False),
    ],
)
def test_breach_needs_percent_and_size_together(pct, absolute, expected):
    """Either alone is a false positive: percent alone flags noise on thin
    strikes, size alone flags every ATM tick on a liquid one."""
    assert features.is_breach(pct, absolute, pct_threshold=8.0, min_abs_oi=25_000) is expected


def test_the_absolute_floor_silences_thin_strikes():
    """A 400-OI wing swinging 50% must not outshine the strikes that matter."""
    thin = _rows([400, 600], ltp_by_minute=[1.0, 1.2])
    grid = features.build_grid(
        thin, timeframe_min=5, baselines={(24000.0, "CE"): 400.0}, atm=24000.0
    )
    cell = grid["rows"][0]["ce"]["cells"][0]
    assert cell["d_oi_pct"] == 50.0  # the percentage is real
    assert cell["breach"] is False  # and still not a breach


def test_cell_breach_uses_the_timeframes_own_threshold():
    """5m is 8%, 60m is 35% -- the same move is a breach at one and not the other."""
    rows = _rows([1_000_000, 1_200_000], ltp_by_minute=[10.0, 11.0])  # +20%
    for tf, expected in ((5, True), (60, False)):
        grid = features.build_grid(
            rows, timeframe_min=tf, baselines={(24000.0, "CE"): 1_000_000.0}, atm=24000.0
        )
        assert grid["rows"][0]["ce"]["cells"][0]["breach"] is expected
        assert grid["thresholds"]["pct"] == features.PCT_THRESHOLDS[tf]


def test_strike_level_breach_is_cumulative_not_per_bucket():
    """Many small buckets that add up to a big day breach the row without any one
    cell breaching."""
    rows = _rows([1_000_000 + 30_000 * i for i in range(25)], ltp_by_minute=[10.0] * 25)
    grid = features.build_grid(
        rows, timeframe_min=5, baselines={(24000.0, "CE"): 1_000_000.0}, atm=24000.0
    )
    side = grid["rows"][0]["ce"]
    assert side["total_delta_pct"] > features.CUM_PCT_THRESHOLD
    assert side["breach"] is True


def test_strike_breach_respects_the_floor_too():
    rows = _rows([1_000, 2_000], ltp_by_minute=[1.0, 2.0])  # +100%, 1k contracts
    grid = features.build_grid(
        rows, timeframe_min=5, baselines={(24000.0, "CE"): 1_000.0}, atm=24000.0
    )
    assert grid["rows"][0]["ce"]["breach"] is False


# ---------------------------------------------------------------------------
# Latest-bucket alert
# ---------------------------------------------------------------------------


def _grid_with_breaches(n_breaching: int, n_quiet: int):
    """One bucket, `n_breaching` strikes moving hard and `n_quiet` barely moving."""
    rows: list = []
    baselines = {}
    for i in range(n_breaching + n_quiet):
        strike = 24000.0 + 50 * i
        base = 1_000_000.0
        end = base + (300_000 if i < n_breaching else 1_000)
        rows += _rows([base, end], strike=strike, ltp_by_minute=[10.0, 11.0])
        baselines[(strike, "CE")] = base
    return features.build_grid(rows, timeframe_min=5, baselines=baselines, atm=24000.0)


def test_alert_fires_when_most_of_the_latest_bucket_breaches():
    grid = _grid_with_breaches(8, 2)
    assert grid["alert"]["ce"]["ratio"] == 0.8
    assert grid["alert"]["ce"]["alert"] is True


def test_alert_stays_quiet_below_the_ratio():
    grid = _grid_with_breaches(3, 7)
    assert grid["alert"]["ce"]["ratio"] == 0.3
    assert grid["alert"]["ce"]["alert"] is False


def test_alert_denominator_skips_blank_cells():
    """A strike outside the collector's window in this bucket is absent, not calm.
    Counting it would mute the alert exactly when the grid is widest."""
    rows = _rows([1_000_000, 1_300_000], ltp_by_minute=[10.0, 11.0])
    # A second strike present only in the *first* bucket, blank in the last.
    rows += _rows([500_000] * 5, strike=24100.0, ltp_by_minute=[5.0] * 5)
    rows += [
        dict(r, ts=(START + timedelta(minutes=6)).isoformat())
        for r in _rows([1_000_000], ltp_by_minute=[10.0])
    ]
    grid = features.build_grid(
        rows,
        timeframe_min=5,
        baselines={(24000.0, "CE"): 1_000_000.0, (24100.0, "CE"): 500_000.0},
        atm=24000.0,
    )
    last = grid["alert"]["ce"]
    assert last["cells"] == 1  # only the strike that carried a delta in that bucket


def test_alert_indexes_the_newest_bucket():
    grid = _grid_with_breaches(1, 1)
    assert grid["alert"]["bucket_index"] == len(grid["buckets"]) - 1


def test_grid_marks_whether_the_session_is_live(archive):
    """The page only toasts for today -- an archived day cannot be alerted on.

    Both sessions are derived from ``date.today()`` by the fixture rather than
    hardcoded, so this keeps testing something after today stops being today.
    """
    today, yesterday = archive

    live = service.get_grid("NIFTY", session_date=today, strike_range="atm5", widen=False)
    assert live["meta"]["is_live"] is True

    old = service.get_grid("NIFTY", session_date=yesterday, strike_range="atm5", widen=False)
    assert old["meta"]["is_live"] is False


# ---------------------------------------------------------------------------
# Adaptive thresholds
# ---------------------------------------------------------------------------


def test_fixed_mode_repeats_one_constant_across_the_session():
    keys = ["09:20", "12:30", "15:15"]
    values, used = features.resolve_thresholds(5, keys, mode="fixed")
    assert used == "fixed"
    assert values == [features.PCT_THRESHOLDS[5]] * 3


def test_adaptive_bar_rises_at_the_open_and_falls_at_midday():
    """The measured spread the fitted table exists to absorb: OI churns hardest
    in the first minutes and least around 12:30."""
    values, used = features.resolve_thresholds(
        5, ["09:30", "12:30"], mode="adaptive", dte_days=5
    )
    assert used == "adaptive"
    assert values[0] > values[1] * 3


def test_adaptive_bar_rises_with_nearness_to_expiry():
    """Expiry-day OI churns several times harder than a far month; a flat rule
    fired on 13.7% of expiry-day cells and 0.2% of far-dated ones."""
    expiry_day, _ = features.resolve_thresholds(5, ["11:00"], mode="adaptive", dte_days=0)
    far_month, _ = features.resolve_thresholds(5, ["11:00"], mode="adaptive", dte_days=45)
    assert expiry_day[0] > far_month[0] * 2


def test_adaptive_never_goes_below_the_floor_percentage():
    """The midday trough fits under 2% at 5m; a threshold that low would mark
    ordinary book-keeping."""
    values, _ = features.resolve_thresholds(
        5, list(features.calibration.TOD_FACTOR.get(5, {})), mode="adaptive", dte_days=45
    )
    assert values and min(values) >= features.MIN_ADAPTIVE_PCT


@pytest.mark.parametrize(
    "days,expected",
    [(0, "0-1"), (1, "0-1"), (2, "2-7"), (7, "2-7"), (8, "8-21"), (21, "8-21"), (22, "22+")],
)
def test_dte_bucketing(days, expected):
    assert features.dte_bucket(days) == expected


def test_unfitted_timeframe_falls_back_and_says_so(monkeypatch):
    """Serving a different rule than the one requested, silently, is how a
    threshold stops meaning anything."""
    monkeypatch.setattr(features.calibration, "BASE_P95", {})
    values, used = features.resolve_thresholds(5, ["11:00"], mode="adaptive", dte_days=5)
    assert used == "fixed"
    assert values == [features.PCT_THRESHOLDS[5]]


def test_adaptive_judges_each_column_against_its_own_bar():
    """Under `adaptive` the bar moves with time of day, so a cell must be judged
    against its own bucket rather than a grid-wide constant."""
    rows = _rows(
        [1_000_000 + 200_000 * i for i in range(60)],
        ltp_by_minute=[10.0] * 60,
    )
    grid = features.build_grid(
        rows,
        timeframe_min=5,
        baselines={(24000.0, "CE"): 1_000_000.0},
        atm=24000.0,
        threshold_mode="adaptive",
        dte_days=5,
    )
    per_bucket = grid["thresholds"]["pct_by_bucket"]
    assert len(per_bucket) == len(grid["buckets"])
    assert len(set(per_bucket)) > 1  # not a repeated constant
    assert grid["thresholds"]["mode"] == "adaptive"


def test_grid_reports_the_mode_it_actually_applied(archive):
    today, _ = archive
    grid = service.get_grid(
        "NIFTY", session_date=today, strike_range="atm5", widen=False, threshold_mode="adaptive"
    )
    assert grid["thresholds"]["requested_mode"] == "adaptive"
    assert grid["thresholds"]["mode"] in features.THRESHOLD_MODES
    assert grid["thresholds"]["pct_min"] <= grid["thresholds"]["pct_max"]


def test_unknown_threshold_mode_is_rejected(archive):
    with pytest.raises(ValueError, match="Unknown threshold_mode"):
        service.get_grid("NIFTY", strike_range="atm5", widen=False, threshold_mode="magic")


# ---------------------------------------------------------------------------
# Gamma levels overlay
# ---------------------------------------------------------------------------


LADDER = [23900.0, 24000.0, 24100.0, 24200.0, 24300.0]


def _stub_live(monkeypatch, payload):
    from analysis.chain_buildup import levels as cb_levels

    monkeypatch.setattr(cb_levels, "_live_gamma", lambda u: (payload, None))
    monkeypatch.setattr(cb_levels, "_fut_poc", lambda u, d, live: (None, "not_sampled"))
    return cb_levels


def test_price_levels_are_bracketed_never_snapped(monkeypatch):
    """Snapping flip to the nearest row would move it up to half a strike step
    and assert a precision the number does not have."""
    cb = _stub_live(monkeypatch, {"available": True, "flip": 24067.22, "expiry": "2026-09-01"})
    out = cb.resolve("NIFTY", date.today(), strikes=LADDER, grid_expiry="2026-09-01")
    flip = next(x for x in out["levels"] if x["key"] == "flip")
    assert flip["kind"] == "price"
    assert flip["strike"] is None          # never pinned to a row
    assert flip["between"] == [24000.0, 24100.0]
    assert flip["in_ladder"] is True


def test_strike_levels_land_on_a_row(monkeypatch):
    cb = _stub_live(monkeypatch, {"available": True, "call_wall": 24200.0, "expiry": "E"})
    out = cb.resolve("NIFTY", date.today(), strikes=LADDER, grid_expiry="E")
    cw = next(x for x in out["levels"] if x["key"] == "call_wall")
    assert cw["kind"] == "strike" and cw["strike"] == 24200.0 and cw["between"] is None


def test_a_level_outside_the_rendered_ladder_is_flagged(monkeypatch):
    """Off-screen is not the same as absent — the page needs to say which."""
    cb = _stub_live(monkeypatch, {"available": True, "flip": 25500.0, "expiry": "E"})
    out = cb.resolve("NIFTY", date.today(), strikes=LADDER, grid_expiry="E")
    flip = next(x for x in out["levels"] if x["key"] == "flip")
    assert flip["price"] == 25500.0 and flip["in_ladder"] is False


@pytest.mark.parametrize(
    "pin_source,expect_note",
    [("dominant", False), ("wall_mid", True), ("", True), (None, True)],
)
def test_a_derived_pin_says_so(monkeypatch, pin_source, expect_note):
    """`wall_mid` is the midpoint of two walls, not a gamma pin. Labelling it
    PIN on a strike ladder asserts what the data does not support."""
    cb = _stub_live(
        monkeypatch,
        {"available": True, "pin": 24100.0, "pin_source": pin_source, "expiry": "E"},
    )
    out = cb.resolve("NIFTY", date.today(), strikes=LADDER, grid_expiry="E")
    pin = next(x for x in out["levels"] if x["key"] == "pin")
    assert (pin["note"] is not None) is expect_note


def test_archived_session_never_borrows_todays_levels(monkeypatch):
    """The grid renders any archived day. Drawing today's call wall on a
    two-week-old ladder invites reading a level into a session that never had
    it, so an unresolvable level is omitted with a reason instead."""
    from analysis.chain_buildup import levels as cb_levels

    called: list[str] = []
    monkeypatch.setattr(
        cb_levels, "_live_gamma",
        lambda u: (called.append("live"), ({"available": True, "call_wall": 99999.0}, None))[1],
    )
    monkeypatch.setattr(cb_levels, "_historical_gamma", lambda u, d: ({}, "no_gamma_history_for_session"))
    monkeypatch.setattr(cb_levels, "_fut_poc", lambda u, d, live: (None, "no_trail_yet"))

    out = cb_levels.resolve("NIFTY", date.today() - timedelta(days=14), strikes=LADDER)
    assert called == []                      # the live snapshot is never consulted
    assert out["levels"] == []
    assert out["source"] == "history"
    assert set(out["skipped"]) == {"call_wall", "put_wall", "pin", "flip", "fut_poc"}


def test_walls_absent_from_an_old_session_are_reported_not_invented(monkeypatch):
    """Walls were only recorded from 2026-08-27; before that they cannot exist."""
    from analysis.chain_buildup import levels as cb_levels

    monkeypatch.setattr(
        cb_levels, "_historical_gamma",
        lambda u, d: ({"pin": 24100.0, "flip": 24080.0, "call_wall": None, "put_wall": None}, None),
    )
    monkeypatch.setattr(cb_levels, "_fut_poc", lambda u, d, live: (None, "no_trail_yet"))
    out = cb_levels.resolve("NIFTY", date.today() - timedelta(days=5), strikes=LADDER)
    assert {x["key"] for x in out["levels"]} == {"pin", "flip"}
    assert out["skipped"]["call_wall"] == "not_recorded_this_session"


def test_expiry_mismatch_is_reported(monkeypatch):
    """A front-expiry call wall on a 30-DTE ladder is the wrong number."""
    cb = _stub_live(monkeypatch, {"available": True, "call_wall": 24200.0, "expiry": "2026-09-01"})
    same = cb.resolve("NIFTY", date.today(), strikes=LADDER, grid_expiry="2026-09-01")
    diff = cb.resolve("NIFTY", date.today(), strikes=LADDER, grid_expiry="2026-09-29")
    assert same["expiry_match"] is True
    assert diff["expiry_match"] is False


def test_gamma_outage_degrades_to_reasons_not_an_exception(monkeypatch):
    """A gamma failure must grey one panel, never blank the grid."""
    from analysis.chain_buildup import levels as cb_levels

    monkeypatch.setattr(cb_levels, "_live_gamma", lambda u: ({}, "gamma_unavailable"))
    monkeypatch.setattr(cb_levels, "_fut_poc", lambda u, d, live: (None, "poc_unavailable"))
    out = cb_levels.resolve("NIFTY", date.today(), strikes=LADDER)
    assert out["levels"] == []
    assert out["skipped"]["call_wall"] == "gamma_unavailable"


# ---------------------------------------------------------------------------
# Phase 3 — levels as of each bucket
# ---------------------------------------------------------------------------


def _trail(rows):
    """(t, fields) points as the gamma trail stores them."""
    return [dict(r, t=r["t"]) for r in rows]


def _stub_trail(monkeypatch, rows, segments=()):
    from analysis.chain_buildup import levels as cb

    monkeypatch.setattr(cb, "_series_for", lambda u, d, e: _trail(rows))
    monkeypatch.setattr(cb, "_poc_by_minute", lambda u, d, live: list(segments))
    return cb


def _ends(*hhmm):
    return [datetime(2026, 8, 25, int(h[:2]), int(h[3:])) for h in hhmm]


def test_track_reads_the_trails_field_names_not_the_snapshots():
    """The trail says pin_strike/flip_level where the snapshot says pin/flip.
    Mapping one and assuming the other returned an empty flip track on a session
    whose trail carried all 300 of them."""
    from analysis.chain_buildup import levels as cb

    assert cb.TRACK_FIELDS["pin"] == "pin_strike"
    assert cb.TRACK_FIELDS["flip"] == "flip_level"


def test_track_resolves_each_bucket_to_the_last_sample_at_or_before_it(monkeypatch):
    cb = _stub_trail(monkeypatch, [
        {"t": "2026-08-25T09:40:00", "pin_strike": 24200.0, "flip_level": 24180.0},
        {"t": "2026-08-25T10:10:00", "pin_strike": 24150.0, "flip_level": 24130.0},
    ])
    out = cb.track("NIFTY", date(2026, 8, 25), _ends("09:45", "10:15"))
    assert [p["pin"] for p in out["points"]] == [24200.0, 24150.0]
    assert [p["flip"] for p in out["points"]] == [24180.0, 24130.0]
    assert out["coverage"]["pin"] == 2


def test_track_holds_a_level_forward_but_not_indefinitely(monkeypatch):
    """A level is a state, so it holds between samples — but a gap wider than the
    carry limit is a gap in the RECORDING, not a level that sat still."""
    cb = _stub_trail(monkeypatch, [{"t": "2026-08-25T09:40:00", "pin_strike": 24200.0}])
    out = cb.track("NIFTY", date(2026, 8, 25), _ends("09:45", "11:00"))
    assert out["points"][0]["pin"] == 24200.0          # 5 min later: held
    assert out["points"][1]["pin"] is None             # 80 min later: unknown


def test_track_never_interpolates_between_samples(monkeypatch):
    """Averaging two flips produces a price the desk never published — the same
    reason a bucket's OI is its last value, not its mean."""
    cb = _stub_trail(monkeypatch, [
        {"t": "2026-08-25T09:40:00", "flip_level": 24100.0},
        {"t": "2026-08-25T09:50:00", "flip_level": 24200.0},
    ])
    out = cb.track("NIFTY", date(2026, 8, 25), _ends("09:45"))
    assert out["points"][0]["flip"] == 24100.0          # not 24150


def test_track_reports_coverage_per_level(monkeypatch):
    """"The wall did not move" and "the wall was never recorded" must not look
    alike, so each level reports how many buckets it actually filled."""
    cb = _stub_trail(monkeypatch, [
        {"t": "2026-08-25T09:40:00", "pin_strike": 24200.0, "call_wall": None},
    ])
    out = cb.track("NIFTY", date(2026, 8, 25), _ends("09:45", "09:50"))
    assert out["coverage"]["pin"] == 2
    assert out["coverage"]["call_wall"] == 0
    assert out["available"] is True


def test_track_says_unavailable_when_nothing_resolves(monkeypatch):
    cb = _stub_trail(monkeypatch, [])
    out = cb.track("NIFTY", date(2026, 8, 25), _ends("09:45"))
    assert out["available"] is False and out["trail_points"] == 0


def test_track_maps_poc_segments_onto_buckets(monkeypatch):
    """POC arrives as minute ranges since the open, not as samples."""
    cb = _stub_trail(monkeypatch, [], segments=[(0, 60, 24100.0), (61, 200, 24250.0)])
    out = cb.track("NIFTY", date(2026, 8, 25), _ends("09:45", "12:00"))
    assert out["points"][0]["fut_poc"] == 24100.0      # 30 min in
    assert out["points"][1]["fut_poc"] == 24250.0      # 165 min in


def test_track_tolerates_tz_aware_trail_stamps(monkeypatch):
    """The trail writes +05:30; bucket ends are naive IST wall-clock."""
    cb = _stub_trail(monkeypatch, [{"t": "2026-08-25T09:40:00+05:30", "pin_strike": 24200.0}])
    out = cb.track("NIFTY", date(2026, 8, 25), _ends("09:45"))
    assert out["points"][0]["pin"] == 24200.0


# ---------------------------------------------------------------------------
# Traded volume per bucket
# ---------------------------------------------------------------------------


def _vol_rows(volumes, *, strike=24000.0):
    rows = _rows([1_000_000] * len(volumes), strike=strike, ltp_by_minute=[10.0] * len(volumes))
    for r, v in zip(rows, volumes, strict=True):
        r["volume"] = v
    return rows


def test_bucket_volume_is_a_difference_not_the_cumulative_figure():
    """The archive stores cumulative day volume per leg. Showing it raw per
    bucket would read as "this bucket traded 6 crore"."""
    rows = _vol_rows([100, 500, 900, 1500, 2000])
    grid = features.build_grid(rows, timeframe_min=5, atm=24000.0)
    cell = grid["rows"][0]["ce"]["cells"][0]
    assert cell["volume"] == 2000        # cumulative, as archived
    assert cell["cum_volume"] == 0.0     # first bucket is the anchor


def test_bucket_volume_telescopes_to_the_cumulative():
    rows = []
    for i in range(25):
        r = _rows([1_000_000], ltp_by_minute=[10.0])[0]
        r["ts"] = (START + timedelta(minutes=i)).isoformat()
        r["volume"] = 1000 * (i + 1)
        rows.append(r)
    grid = features.build_grid(rows, timeframe_min=5, atm=24000.0)
    cells = grid["rows"][0]["ce"]["cells"]
    deltas = [c["d_volume"] for c in cells if c["d_volume"] is not None]
    last_cum = [c["cum_volume"] for c in cells if c["cum_volume"] is not None][-1]
    assert sum(deltas) == last_cum


def test_cumulative_volume_anchors_on_the_legs_first_bucket():
    """A strike that entered the window at noon has not traded its whole day's
    volume since noon."""
    rows = _vol_rows([5_000_000, 5_400_000, 5_900_000, 6_000_000, 6_100_000])
    grid = features.build_grid(rows, timeframe_min=5, atm=24000.0)
    assert grid["rows"][0]["ce"]["cells"][0]["cum_volume"] == 0.0


def test_bucket_volume_never_goes_negative():
    """Cumulative volume should only rise; a decrease means a bad tick or an
    instrument roll, and a negative "traded volume" is not a thing."""
    rows = _vol_rows([9_000, 8_000, 8_500, 9_000, 9_500])
    rows += [dict(r, ts=(START + timedelta(minutes=6 + i)).isoformat(), volume=v)
             for i, (r, v) in enumerate(zip(_vol_rows([1, 2]), [7_000, 7_500], strict=True))]
    grid = features.build_grid(rows, timeframe_min=5, atm=24000.0)
    for c in grid["rows"][0]["ce"]["cells"]:
        assert c["d_volume"] is None or c["d_volume"] >= 0


# ---------------------------------------------------------------------------
# Underlying flow strip
# ---------------------------------------------------------------------------


def _flow_ends(*hhmm):
    return [datetime(2026, 8, 27, int(h[:2]), int(h[3:])) for h in hhmm]


def test_flow_aligns_bars_by_close_not_open(monkeypatch):
    """Kite stamps a candle by its OPEN; the ladder buckets by close. Passing the
    open through would put every bar one column early."""
    from analysis.chain_buildup import flow

    monkeypatch.setattr(flow, "_fetch_bars", lambda u, tf, d: [
        {"date": "2026-08-27T09:15:00", "volume": 100.0, "close": 24000.0},
        {"date": "2026-08-27T09:20:00", "volume": 250.0, "close": 24010.0},
    ])
    out = flow.underlying_flow("NIFTY", date(2026, 8, 27), _flow_ends("09:20", "09:25"), timeframe_min=5)
    assert [p["volume"] for p in out["points"]] == [100.0, 250.0]


def test_flow_cumulative_runs_across_the_rendered_buckets(monkeypatch):
    from analysis.chain_buildup import flow

    monkeypatch.setattr(flow, "_fetch_bars", lambda u, tf, d: [
        {"date": "2026-08-27T09:15:00", "volume": 100.0, "close": 1.0},
        {"date": "2026-08-27T09:20:00", "volume": 250.0, "close": 1.0},
    ])
    out = flow.underlying_flow("NIFTY", date(2026, 8, 27), _flow_ends("09:20", "09:25"), timeframe_min=5)
    assert [p["cum_volume"] for p in out["points"]] == [100.0, 350.0]
    assert out["total_volume"] == 350.0 and out["coverage"] == 2


def test_flow_failure_keeps_the_same_shape_as_success(monkeypatch):
    """A failure path that drops keys turns a degraded feed into an
    AttributeError two layers up — which is what an expired Kite token did the
    first time this ran."""
    from analysis.chain_buildup import flow

    ends = _flow_ends("09:20", "09:25")
    monkeypatch.setattr(flow, "_fetch_bars", lambda u, tf, d: [
        {"date": "2026-08-27T09:15:00", "volume": 100.0, "close": 1.0},
    ])
    good = flow.underlying_flow("NIFTY", date(2026, 8, 27), ends, timeframe_min=5)

    def boom(*_a, **_k):
        raise RuntimeError("Incorrect `api_key` or `access_token`.")

    monkeypatch.setattr(flow, "_fetch_bars", boom)
    bad = flow.underlying_flow("NIFTY", date(2026, 8, 27), ends, timeframe_min=5)

    assert set(good) == set(bad)
    assert len(bad["points"]) == len(ends)
    assert set(good["points"][0]) == set(bad["points"][0])
    assert bad["available"] is False and bad["reason"] == "futures_bars_unavailable"


def test_flow_rejects_an_unsupported_timeframe():
    from analysis.chain_buildup import flow

    with pytest.raises(ValueError, match="Unsupported timeframe"):
        flow.underlying_flow("NIFTY", date(2026, 8, 27), _flow_ends("09:20"), timeframe_min=7)
