"""Tests for gamma density history session merge + reversal detection."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from options.gamma_density_history import (
    adaptive_reversal_min_move,
    build_chart_series,
    detect_spot_reversals,
    fill_session_gex_series,
    in_session,
    normalize_reversal_tf,
    reconcile_session_reversals,
    resample_chart_series,
    reversal_tf_params,
    session_history_max_points,
    session_window,
)

IST = ZoneInfo("Asia/Kolkata")


def test_session_window_cash_vs_mcx() -> None:
    s, e = session_window("NIFTY")
    assert (s.hour, s.minute) == (9, 15)
    assert (e.hour, e.minute) == (15, 40)  # equity derivatives / CAS-era close
    s2, e2 = session_window("CRUDEOIL")
    assert (s2.hour, s2.minute) == (9, 0)
    assert (e2.hour, e2.minute) == (23, 30)
    for u in ("CRUDEOIL", "NATURALGAS"):
        sg, eg = session_window(u)
        assert (sg.hour, sg.minute) == (9, 0)
        assert (eg.hour, eg.minute) == (23, 30)


def test_minutes_since_session_open_mcx_not_capped_at_600() -> None:
    """MCX evening lookback must cover from ~09:00, not stop at a 600m cash ceiling."""
    from options.gamma_density_history import minutes_since_session_open

    # At ~21:40 IST on a weekday, CRUDE should request >600 minutes of candles.
    # Function uses wall-clock now — assert the cap itself is session-sized.
    start, end = session_window("CRUDEOIL")
    session_mins = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    assert session_mins > 600
    # Indirect: cap formula allows full session (+30). Call returns ≤ session_cap.
    lookback = minutes_since_session_open("CRUDEOIL")
    assert lookback >= 60
    assert lookback <= session_mins + 30


def test_in_session_rejects_after_close() -> None:
    after = datetime(2026, 7, 24, 16, 45, tzinfo=IST)
    assert in_session("NIFTY", after) is False
    during = datetime(2026, 7, 24, 10, 10, tzinfo=IST)
    assert in_session("NIFTY", during) is True
    # Equity derivatives stay open through CAS window to 15:40
    cas = datetime(2026, 7, 24, 15, 35, tzinfo=IST)
    assert in_session("NIFTY", cas) is True
    post_deriv = datetime(2026, 7, 24, 15, 41, tzinfo=IST)
    assert in_session("NIFTY", post_deriv) is False


def test_build_chart_series_keeps_day_spot_and_sparse_gex() -> None:
    today = datetime.now(tz=IST).date().isoformat()
    candles = [
        {"date": f"{today}T09:20:00+05:30", "close": 23600, "volume": 1000},
        {"date": f"{today}T10:10:00+05:30", "close": 23550, "volume": 1500},
        {"date": f"{today}T11:00:00+05:30", "close": 23680, "volume": 900},
        {"date": f"{today}T14:00:00+05:30", "close": 23780, "volume": 2000},
    ]
    gex = [
        {
            "t": f"{today}T09:20:00+05:30",
            "spot": 23600,
            "total_gex": 1e6,
            "pos_gex": 3e6,
            "neg_gex": 2e6,
            "flip_level": 23650,
            "gamma_regime": "positive",
            "atm_iv": 12.5,
        },
        {
            "t": f"{today}T10:10:00+05:30",
            "spot": 23550,
            "total_gex": -5e5,
            "pos_gex": 1e6,
            "neg_gex": 1.5e6,
            "flip_level": 23600,
            "gamma_regime": "negative",
            "atm_iv": 13.1,
        },
        # afternoon gap — no GEX tick at 14:00
    ]
    series = build_chart_series("NIFTY", gex, candles)
    assert len(series) == 4
    assert series[0]["total_gex"] == 1e6
    assert series[0]["pos_gex"] == 3e6
    assert series[0]["neg_gex"] == 2e6
    assert series[0]["atm_iv"] == 12.5
    assert series[0]["volume"] == 1000
    assert series[1]["total_gex"] == -5e5
    assert series[1]["pos_gex"] == 1e6
    assert series[1]["atm_iv"] == 13.1
    assert series[3]["spot"] == 23780
    assert series[3]["total_gex"] is None  # no invented GEX after gap
    assert series[3]["pos_gex"] is None
    assert series[3]["atm_iv"] is None  # no invented IV after gap
    assert series[3]["volume"] == 2000


def test_gex_history_recording_meta_flags_mid_session_start() -> None:
    from options.gamma_density_history import gex_history_recording_meta

    today = datetime.now(tz=IST).date().isoformat()
    late = [
        {
            "t": f"{today}T20:52:00+05:30",
            "total_gex": -1e6,
            "pos_gex": 1e6,
            "neg_gex": 2e6,
        }
    ]
    meta = gex_history_recording_meta("CRUDEOIL", late)
    assert meta["gex_history_partial"] is True
    assert meta["gex_history_points"] == 1
    assert "20:52" in str(meta["gex_history_started_at"])

    early = [
        {
            "t": f"{today}T09:02:00+05:30",
            "total_gex": 1e6,
            "pos_gex": 2e6,
            "neg_gex": 1e6,
        }
    ]
    meta_early = gex_history_recording_meta("CRUDEOIL", early)
    assert meta_early["gex_history_partial"] is False
    assert meta_early["gex_history_points"] == 1


def test_fill_session_gex_series_reverse_and_forward_fill() -> None:
    """Display fill: reverse before first sample, forward after; sparse attach stays honest."""
    today = datetime.now(tz=IST).date().isoformat()
    candles = [
        {"date": f"{today}T09:05:00+05:30", "close": 6200, "volume": 100},
        {"date": f"{today}T10:00:00+05:30", "close": 6210, "volume": 120},
        {"date": f"{today}T14:00:00+05:30", "close": 6250, "volume": 90},
        {"date": f"{today}T18:00:00+05:30", "close": 6280, "volume": 80},
    ]
    # First GEX only at 14:00 — typical when nothing was recorded earlier.
    gex = [
        {
            "t": f"{today}T14:00:00+05:30",
            "spot": 6250,
            "total_gex": -2e6,
            "pos_gex": 1e6,
            "neg_gex": 3e6,
            "flip_level": 6240,
            "gamma_regime": "negative",
        },
        {
            "t": f"{today}T18:00:00+05:30",
            "spot": 6280,
            "total_gex": -1e6,
            "pos_gex": 1.5e6,
            "neg_gex": 2.5e6,
            "flip_level": 6260,
            "gamma_regime": "negative",
        },
    ]
    sparse = build_chart_series("CRUDEOIL", gex, candles)
    assert sparse[0]["total_gex"] is None
    assert sparse[1]["total_gex"] is None
    assert sparse[2]["total_gex"] == -2e6
    assert sparse[3]["total_gex"] == -1e6

    filled = fill_session_gex_series(sparse)
    # Pre-first-sample minutes reverse-fill from the first recorded sample.
    assert filled[0]["total_gex"] == -2e6
    assert filled[0]["pos_gex"] == 1e6
    assert filled[0]["neg_gex"] == 3e6
    assert filled[1]["total_gex"] == -2e6
    assert filled[1]["pos_gex"] == 1e6
    assert filled[1]["neg_gex"] == 3e6
    # Sample times keep their own values; forward-fill after first sample.
    assert filled[2]["total_gex"] == -2e6
    assert filled[2]["pos_gex"] == 1e6
    assert filled[3]["total_gex"] == -1e6
    assert filled[3]["pos_gex"] == 1.5e6
    # Spot / volume untouched; flip stays sparse (display fill is GEX-only).
    assert filled[0]["spot"] == 6200
    assert filled[0]["volume"] == 100
    assert filled[0].get("flip_level") is None
    assert filled[2]["flip_level"] == 6240
    # Sparse input not mutated (reversal detection still sees honest gaps).
    assert sparse[0]["total_gex"] is None
    assert sparse[1]["total_gex"] is None


def test_session_history_max_points_covers_mcx_day() -> None:
    nifty_cap = session_history_max_points("NIFTY", interval_sec=60)
    crude_cap = session_history_max_points("CRUDEOIL", interval_sec=60)
    assert nifty_cap >= 400
    # MCX 09:00–23:30 needs more retention than the old 400-tick floor.
    assert crude_cap > nifty_cap
    assert crude_cap >= 900


def test_fill_session_gex_series_forward_fills_trailing_gap() -> None:
    today = datetime.now(tz=IST).date().isoformat()
    candles = [
        {"date": f"{today}T09:20:00+05:30", "close": 23600, "volume": 1000},
        {"date": f"{today}T10:10:00+05:30", "close": 23550, "volume": 1500},
        {"date": f"{today}T14:00:00+05:30", "close": 23780, "volume": 2000},
    ]
    gex = [
        {
            "t": f"{today}T09:20:00+05:30",
            "total_gex": 1e6,
            "pos_gex": 3e6,
            "neg_gex": 2e6,
        },
        {
            "t": f"{today}T10:10:00+05:30",
            "total_gex": -5e5,
            "pos_gex": 1e6,
            "neg_gex": 1.5e6,
        },
    ]
    sparse = build_chart_series("NIFTY", gex, candles)
    assert sparse[2]["total_gex"] is None
    filled = fill_session_gex_series(sparse)
    assert filled[2]["total_gex"] == -5e5
    assert filled[2]["pos_gex"] == 1e6
    assert filled[2]["neg_gex"] == 1.5e6


def test_attach_volume_from_candles_backfills_zero_index_volume() -> None:
    from options.gamma_density_history import attach_volume_from_candles

    today = datetime.now(tz=IST).date().isoformat()
    series = [
        {
            "t": f"{today}T09:20:00+05:30",
            "ts_ms": int(datetime.fromisoformat(f"{today}T09:20:00+05:30").timestamp() * 1000),
            "spot": 23600,
            "total_gex": 1e6,
            "volume": 0,
        },
        {
            "t": f"{today}T09:21:00+05:30",
            "ts_ms": int(datetime.fromisoformat(f"{today}T09:21:00+05:30").timestamp() * 1000),
            "spot": 23610,
            "total_gex": 1e6,
            "volume": None,
        },
    ]
    fut = [
        {"date": f"{today}T09:20:00+05:30", "close": 23605, "volume": 12500},
        {"date": f"{today}T09:21:00+05:30", "close": 23615, "volume": 9800},
    ]
    out = attach_volume_from_candles(series, fut)
    assert out[0]["volume"] == 12500
    assert out[0]["volume_source"] == "future"
    assert out[1]["volume"] == 9800
    # Does not overwrite usable native volume.
    kept = attach_volume_from_candles(
        [{"t": f"{today}T09:20:00+05:30", "ts_ms": series[0]["ts_ms"], "volume": 50}],
        fut,
    )
    assert kept[0]["volume"] == 50


def _minute_series(path: list[float], base: float, ts0: datetime) -> list[dict]:
    series = []
    for i, d in enumerate(path):
        t = ts0.timestamp() + i * 60
        series.append(
            {
                "t": datetime.fromtimestamp(t, tz=IST).isoformat(),
                "ts_ms": int(t * 1000),
                "spot": base + d,
            }
        )
    return series


def test_detect_bullish_reversal_around_trough() -> None:
    # Synthetic V: decline then sharp reclaim (+80 pts)
    base = 23600.0
    ts0 = datetime(2026, 7, 24, 9, 30, tzinfo=IST)
    path = [0, -20, -40, -60, -80, -100, -90, -50, -20, 20, 40]  # trough at -100
    series = []
    for i, d in enumerate(path):
        t = ts0.timestamp() + i * 60
        series.append(
            {
                "t": datetime.fromtimestamp(t, tz=IST).isoformat(),
                "ts_ms": int(t * 1000),
                "spot": base + d,
                "total_gex": -1e5 if d < -50 else 1e5,
                "gamma_regime": "negative" if d < -50 else "positive",
            }
        )
    revs = detect_spot_reversals(
        series, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=False, tf="1m"
    )
    assert revs, "expected at least one bullish reversal"
    assert any(r["side"] == "bullish" for r in revs)


def test_plateau_trough_still_detects() -> None:
    """Rounded minute prints often repeat the low — old count==1 rule skipped them."""
    base = 77700.0
    ts0 = datetime(2026, 7, 30, 10, 0, tzinfo=IST)
    # Flat double-bottom then reclaim ~90 pts (confirm window must cover the rise)
    path = [40, 20, 0, 0, 0, 15, 40, 70, 90, 95]
    series = _minute_series(path, base, ts0)
    revs = detect_spot_reversals(
        series, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=False, tf="1m"
    )
    assert any(r["side"] == "bullish" for r in revs)


def test_dedupe_keeps_opposite_sides_within_window() -> None:
    """Bull then bear within 15m must both survive (old code kept only one)."""
    base = 77700.0
    ts0 = datetime(2026, 7, 30, 13, 0, tzinfo=IST)
    # Peak → dump, then trough → reclaim (two opposite reversals ~8 min apart)
    path = [
        0,
        40,
        80,
        120,  # peak
        90,
        40,
        0,  # trough
        50,
        100,
        140,
    ]
    series = _minute_series(path, base, ts0)
    revs = detect_spot_reversals(
        series, swing_bars=2, min_move_pts=50, confirm_bars=3, gex_gate=False, tf="1m"
    )
    sides = {r["side"] for r in revs}
    assert "bearish" in sides and "bullish" in sides


def test_adaptive_min_move_sensex_not_brutal() -> None:
    """0.15% of Sensex (~116 pts) buried morning signals; 0.08% stays usable."""
    old = max(25.0, round(77700.0 * 0.0015, 1))
    new = adaptive_reversal_min_move(77700.0)
    assert old >= 110
    assert 40 <= new <= 80
    assert adaptive_reversal_min_move(24000.0) <= 30


def test_normalize_reversal_tf_defaults() -> None:
    assert normalize_reversal_tf(None) == "5m"
    assert normalize_reversal_tf("bogus") == "5m"
    assert normalize_reversal_tf("15m") == "15m"
    assert reversal_tf_params("5m")["lock_ms"] == 55 * 60 * 1000


def test_resample_1m_to_5m_preserves_last_close() -> None:
    ts0 = datetime(2026, 7, 30, 9, 15, tzinfo=IST)
    path = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90]
    series = _minute_series(path, 23600.0, ts0)
    series[0]["total_gex"] = 1e6
    series[0]["gamma_regime"] = "positive"
    series[5]["total_gex"] = -2e5
    series[5]["gamma_regime"] = "negative"
    tf5 = resample_chart_series(series, "5m")
    assert len(tf5) < len(series)
    assert len(tf5) >= 2
    for row in tf5:
        assert row["spot"] is not None
    gex_rows = [r for r in tf5 if r.get("total_gex") is not None]
    assert len(gex_rows) >= 1


def test_gex_gate_suppresses_without_sample() -> None:
    ts0 = datetime(2026, 7, 30, 10, 0, tzinfo=IST)
    path = [0, -20, -40, -80, -100, -90, -40, 10, 40, 60]
    series = _minute_series(path, 23600.0, ts0)
    gated = detect_spot_reversals(
        series, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=True, tf="1m"
    )
    assert gated == []
    open_ = detect_spot_reversals(
        series, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=False, tf="1m"
    )
    assert any(r["side"] == "bullish" for r in open_)


def test_gex_gate_accepts_neg_to_pos_cross() -> None:
    ts0 = datetime(2026, 7, 30, 10, 0, tzinfo=IST)
    path = [0, -20, -40, -80, -100, -90, -40, 10, 40, 60]
    series = _minute_series(path, 23600.0, ts0)
    for row in series:
        d = row["spot"] - 23600.0
        row["total_gex"] = -1e5 if d < -50 else 1e5
        row["gamma_regime"] = "negative" if d < -50 else "positive"
    revs = detect_spot_reversals(
        series, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=True, tf="1m"
    )
    assert any(r["side"] == "bullish" and r["gex_confirm"] for r in revs)


def test_widen_gex_match_accepts_nearby_gex() -> None:
    """5m gate lookback finds GEX outside the 2m chart attach window."""
    from options.gamma_density_history import (
        enrich_series_nearest_gex,
        gex_gate_match_sec,
    )

    ts0 = datetime(2026, 7, 30, 10, 0, tzinfo=IST)
    path = [0, -20, -40, -80, -100, -90, -40, 10, 40, 60]
    # Chart-style series: no GEX attached (sparse poll / outside 120s window)
    series = _minute_series(path, 23600.0, ts0)
    # GEX at series start; trough ~4m later — outside chart 120s, inside 5m gate ~25m
    gex_points = [
        {
            "t": ts0.isoformat(),
            "total_gex": 1e5,
            "gamma_regime": "positive",
            "flip_level": 23600.0,
        },
        {
            "t": datetime.fromtimestamp(ts0.timestamp() + 30 * 60, tz=IST).isoformat(),
            "total_gex": 1e5,
            "gamma_regime": "positive",
        },
    ]

    narrow = enrich_series_nearest_gex(series, gex_points, match_sec=120, underlying="NIFTY")
    wide = enrich_series_nearest_gex(
        series, gex_points, match_sec=gex_gate_match_sec("5m"), underlying="NIFTY"
    )
    trough_i = 4
    assert narrow[trough_i].get("total_gex") is None
    assert wide[trough_i].get("total_gex") == 1e5

    gated_narrow = detect_spot_reversals(
        narrow, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=True, tf="1m"
    )
    gated_wide = detect_spot_reversals(
        wide, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=True, tf="1m"
    )
    assert gated_narrow == []
    assert any(r["side"] == "bullish" and r["gex_confirm"] for r in gated_wide)


def test_starved_session_live_waits_research_relaxes() -> None:
    from options.gamma_density_history import (
        REVERSAL_GEX_MIN_SAMPLES,
        resolve_gex_gate,
    )

    sparse = [{"total_gex": 1e5}]
    enough = [{"total_gex": float(i)} for i in range(REVERSAL_GEX_MIN_SAMPLES)]

    # Live + sparse → waiting (no new ungated detect; saved chips stay upstream)
    assert resolve_gex_gate(True, [], mode="live") == (False, False, True)
    assert resolve_gex_gate(True, sparse, mode="live") == (False, False, True)

    # Research + sparse → relax (ungated pivots)
    assert resolve_gex_gate(True, [], mode="research") == (False, True, False)
    assert resolve_gex_gate(True, sparse, mode="research") == (False, True, False)

    # Enough samples → hard gate in either mode
    assert resolve_gex_gate(True, enough, mode="live") == (True, False, False)
    assert resolve_gex_gate(True, enough, mode="research") == (True, False, False)

    # Require GEX OFF → mode irrelevant
    assert resolve_gex_gate(False, [], mode="live") == (False, False, False)
    assert resolve_gex_gate(False, [], mode="research") == (False, False, False)

    # Default mode is live
    assert resolve_gex_gate(True, sparse) == (False, False, True)

    # Research + sparse end-to-end: relaxed detect still finds pivot
    ts0 = datetime(2026, 7, 30, 10, 0, tzinfo=IST)
    path = [0, -20, -40, -80, -100, -90, -40, 10, 40, 60]
    series = _minute_series(path, 23600.0, ts0)
    gate, relaxed, waiting = resolve_gex_gate(True, sparse, mode="research")
    assert relaxed and not gate and not waiting
    revs = detect_spot_reversals(
        series, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=gate, tf="1m"
    )
    assert any(r["side"] == "bullish" for r in revs)

    # Live + sparse: waiting flag — caller surfaces saved reversals only
    gate_l, relaxed_l, waiting_l = resolve_gex_gate(True, sparse, mode="live")
    assert waiting_l and not relaxed_l and not gate_l


def test_enough_gex_samples_enforces_gate() -> None:
    """With ≥ min samples, gate stays on (no relax / wait)."""
    from options.gamma_density_history import REVERSAL_GEX_MIN_SAMPLES, resolve_gex_gate

    points = [{"total_gex": 1e5 * (1 if i % 2 == 0 else -1)} for i in range(REVERSAL_GEX_MIN_SAMPLES)]
    assert resolve_gex_gate(True, points, mode="live") == (True, False, False)
    assert resolve_gex_gate(True, points, mode="research") == (True, False, False)
    # One short of threshold still sparse
    assert resolve_gex_gate(True, points[:-1], mode="live") == (False, False, True)
    assert resolve_gex_gate(True, points[:-1], mode="research") == (False, True, False)


def test_partial_history_never_waits_live() -> None:
    """Mid-session GEX recording must not blank reversals (no live-wait)."""
    from options.gamma_density_history import REVERSAL_GEX_MIN_SAMPLES, resolve_gex_gate

    sparse = [{"total_gex": 1e5}]
    enough = [{"total_gex": float(i)} for i in range(REVERSAL_GEX_MIN_SAMPLES)]

    # Sparse + partial → relax (not wait), even in Live
    assert resolve_gex_gate(True, sparse, mode="live", history_partial=True) == (
        False,
        True,
        False,
    )
    # Enough + partial → gate ON + relaxed banner (hybrid upstream)
    assert resolve_gex_gate(True, enough, mode="live", history_partial=True) == (
        True,
        True,
        False,
    )


def test_partial_history_gex_policy_keeps_pre_gex_drops_regime_fail() -> None:
    """Hybrid: ungated before first GEX sample; hard gate only where GEX exists."""
    from options.gamma_density_history import apply_partial_history_gex_policy

    ts0 = datetime(2026, 8, 3, 18, 0, tzinfo=IST)
    # Morning-style pivot window with no GEX, then evening with negative GEX
    series: list[dict] = []
    for i in range(20):
        t = ts0 + timedelta(minutes=i * 5)
        row: dict = {
            "t": t.isoformat(),
            "ts_ms": int(t.timestamp() * 1000),
            "spot": 7500.0 + (10 if i < 5 else (-30 if i == 8 else (20 if i > 8 else 0))),
        }
        # GEX only from bar 12 onward (neg → bearish gate OK)
        if i >= 12:
            row["total_gex"] = -1e6
            row["gamma_regime"] = "negative"
        series.append(row)

    pre = {
        "t": series[3]["t"],
        "ts_ms": series[3]["ts_ms"],
        "side": "bullish",
        "spot": series[3]["spot"],
        "move_pts": 40.0,
        "gex_confirm": False,
        "oi_align": False,
        "tf": "5m",
        "label": "5m Bullish · (+40 pts)",
    }
    # Bearish at bar 14 with negative GEX → keep gated
    gated_ok = {
        "t": series[14]["t"],
        "ts_ms": series[14]["ts_ms"],
        "side": "bearish",
        "spot": series[14]["spot"],
        "move_pts": 55.0,
        "gex_confirm": False,
        "oi_align": False,
        "tf": "5m",
        "label": "5m Bearish · (-55 pts)",
    }
    # Bullish at bar 15 with only negative GEX → drop (regime fail)
    gated_fail = {
        "t": series[15]["t"],
        "ts_ms": series[15]["ts_ms"],
        "side": "bullish",
        "spot": series[15]["spot"],
        "move_pts": 60.0,
        "gex_confirm": False,
        "oi_align": False,
        "tf": "5m",
        "label": "5m Bullish · (+60 pts)",
    }

    out = apply_partial_history_gex_policy(
        series, [pre, gated_ok, gated_fail], confirm_bars=3
    )
    assert len(out) == 2
    by_side = {(r["side"], r["partial_ungated"]) for r in out}
    assert ("bullish", True) in by_side  # pre-GEX kept muted
    assert ("bearish", False) in by_side  # gated OK
    assert all(r["side"] != "bullish" or r.get("partial_ungated") for r in out)
    bear = next(r for r in out if r["side"] == "bearish")
    assert bear["gex_confirm"] is True
    assert "GEX" in bear["label"]


def test_same_side_lock_drops_repeat_keeps_opposite() -> None:
    """State machine: same-side within lock dropped; opposite resets."""
    from options.gamma_density_history import _apply_same_side_lock

    revs = [
        {"ts_ms": 1_000, "side": "bullish", "move_pts": 50},
        {"ts_ms": 1_000 + 5 * 60 * 1000, "side": "bullish", "move_pts": 80},
        {"ts_ms": 1_000 + 10 * 60 * 1000, "side": "bearish", "move_pts": 70},
        {"ts_ms": 1_000 + 12 * 60 * 1000, "side": "bearish", "move_pts": 40},
    ]
    out = _apply_same_side_lock(revs, 30 * 60 * 1000)
    assert [(r["side"], r["move_pts"]) for r in out] == [
        ("bullish", 50),
        ("bearish", 70),
    ]


def test_gex_gate_match_sec_by_tf() -> None:
    from options.gamma_density_history import gex_gate_match_sec

    assert gex_gate_match_sec("1m") == 3 * 60
    assert gex_gate_match_sec("5m") == 25 * 60
    assert gex_gate_match_sec("15m") == 50 * 60
    assert 15 * 60 <= gex_gate_match_sec("5m") <= 30 * 60
    assert 45 * 60 <= gex_gate_match_sec("15m") <= 60 * 60


def test_oi_align_soft_tag_near_put_wall() -> None:
    ts0 = datetime(2026, 7, 30, 10, 0, tzinfo=IST)
    path = [0, -20, -40, -80, -100, -90, -40, 10, 40, 60]
    series = _minute_series(path, 23600.0, ts0)
    revs = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=False,
        tf="1m",
        put_wall=23500.0,
        strike_step=50.0,
    )
    bulls = [r for r in revs if r["side"] == "bullish"]
    assert bulls
    assert bulls[0]["oi_align"] is True


def _bullish_v_series() -> tuple[list[dict], float]:
    """Trough at 23500 (base 23600, path -100); reclaim +60 pts."""
    ts0 = datetime(2026, 7, 30, 10, 0, tzinfo=IST)
    path = [0, -20, -40, -80, -100, -90, -40, 10, 40, 60]
    base = 23600.0
    return _minute_series(path, base, ts0), base


def test_oi_gate_pass_near_put_wall_bullish() -> None:
    series, _ = _bullish_v_series()
    revs = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=False,
        oi_gate=True,
        tf="1m",
        put_wall=23500.0,
        strike_step=50.0,
    )
    bulls = [r for r in revs if r["side"] == "bullish"]
    assert bulls
    assert bulls[0]["oi_gate_pass"] is True
    assert bulls[0]["oi_align"] is True


def test_oi_gate_blocks_wrong_side_through_call_wall() -> None:
    """Bullish trough already above call_wall with call ΔOI buildup → blocked."""
    series, _ = _bullish_v_series()
    # Trough spot = 23500; call wall below it = already broken upside
    strikes = [
        {"strike": 23450.0, "ce_doi": 8000, "pe_doi": 100},
        {"strike": 23500.0, "ce_doi": 12000, "pe_doi": 200},
        {"strike": 23550.0, "ce_doi": 9000, "pe_doi": 50},
    ]
    revs = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=False,
        oi_gate=True,
        tf="1m",
        call_wall=23400.0,
        put_wall=23000.0,  # far — not supportive proximity
        cliff_strike=23400.0,
        strike_step=50.0,
        strikes=strikes,
    )
    bulls = [r for r in revs if r["side"] == "bullish"]
    assert bulls == []


def test_oi_gate_missing_walls_allow() -> None:
    series, _ = _bullish_v_series()
    revs = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=False,
        oi_gate=True,
        tf="1m",
        strike_step=50.0,
    )
    bulls = [r for r in revs if r["side"] == "bullish"]
    assert bulls
    assert bulls[0]["oi_gate_pass"] is None  # oi_unknown — not blocked


def test_oi_gate_off_ignores_hard_filter() -> None:
    """Wrong-side structure still fires when Require OI is OFF (soft badge only)."""
    series, _ = _bullish_v_series()
    strikes = [
        {"strike": 23450.0, "ce_doi": 8000, "pe_doi": 100},
        {"strike": 23500.0, "ce_doi": 12000, "pe_doi": 200},
        {"strike": 23550.0, "ce_doi": 9000, "pe_doi": 50},
    ]
    revs = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=False,
        oi_gate=False,
        tf="1m",
        call_wall=23400.0,
        put_wall=23000.0,
        cliff_strike=23400.0,
        strike_step=50.0,
        strikes=strikes,
    )
    bulls = [r for r in revs if r["side"] == "bullish"]
    assert bulls
    assert bulls[0]["oi_gate_pass"] is False
    assert bulls[0]["oi_align"] is False


def test_15m_fewer_signals_than_1m_on_noisy_path() -> None:
    """Many micro Vs on 1m; 15m bars should yield fewer pivots."""
    base = 23600.0
    ts0 = datetime(2026, 7, 30, 9, 15, tzinfo=IST)
    path: list[float] = []
    for _ in range(18):
        path.extend([0, -30, -60, -40, -10, 20, 40, 10])
    series = _minute_series(path, base, ts0)
    for row in series:
        row["total_gex"] = 1e5
        row["gamma_regime"] = "positive"

    r1 = detect_spot_reversals(
        resample_chart_series(series, "1m"),
        min_move_pts=35,
        gex_gate=True,
        tf="1m",
    )
    r15 = detect_spot_reversals(
        resample_chart_series(series, "15m"),
        min_move_pts=35,
        gex_gate=True,
        tf="15m",
    )
    assert len(r15) < len(r1), f"expected 15m ({len(r15)}) < 1m ({len(r1)})"


def _bull_rev(
    ts_ms: int,
    move_pts: float,
    *,
    tf: str = "15m",
    side: str = "bullish",
) -> dict:
    from options.gamma_density_history import _format_reversal_label

    return {
        "t": datetime.fromtimestamp(ts_ms / 1000.0, tz=IST).isoformat(),
        "ts_ms": ts_ms,
        "spot": 23600.0,
        "side": side,
        "move_pts": move_pts,
        "gex_confirm": True,
        "oi_align": False,
        "tf": tf,
        "label": _format_reversal_label(
            tf_key=tf,
            side=side,
            move_pts=move_pts,
            gex_confirm=True,
            oi_align=False,
        ),
    }


def test_frozen_move_pts_stable_after_confirm_closed() -> None:
    """Two detect passes with changed future highs after confirm → pts unchanged."""
    from options.gamma_density_history import reconcile_session_reversals

    pivot_ms = int(datetime(2026, 7, 30, 12, 14, tzinfo=IST).timestamp() * 1000)
    # 15m × 4 confirm bars = 60m window
    after_close_ms = pivot_ms + 90 * 60 * 1000

    first = [_bull_rev(pivot_ms, 216.0)]
    merged1 = reconcile_session_reversals(
        first, [], tf="15m", confirm_bars=4, now_ms=after_close_ms
    )
    assert merged1[0]["move_pts"] == 216.0

    # Rebuild measures a different (lower) move — must not overwrite frozen pts
    second = [_bull_rev(pivot_ms, 189.0)]
    merged2 = reconcile_session_reversals(
        second, merged1, tf="15m", confirm_bars=4, now_ms=after_close_ms
    )
    assert len(merged2) == 1
    assert merged2[0]["move_pts"] == 216.0
    assert "+216" in merged2[0]["label"]


def test_new_reversal_gets_confirmed_at() -> None:
    """Without detect confirm-bar fields, first accept falls back to reconcile now."""
    from options.gamma_density_history import reconcile_session_reversals

    pivot_ms = int(datetime(2026, 7, 30, 8, 39, tzinfo=IST).timestamp() * 1000)
    accept_ms = int(datetime(2026, 7, 30, 9, 12, tzinfo=IST).timestamp() * 1000)

    merged = reconcile_session_reversals(
        [_bull_rev(pivot_ms, 88.0, tf="5m", side="bearish")],
        [],
        tf="5m",
        confirm_bars=3,
        now_ms=accept_ms,
    )
    assert len(merged) == 1
    rev = merged[0]
    assert rev["ts_ms"] == pivot_ms
    assert rev["confirmed_ts_ms"] == accept_ms
    assert rev["confirmed_at"] == datetime.fromtimestamp(accept_ms / 1000.0, tz=IST).isoformat()
    # Pivot time stays the swing bar; fallback confirm is accept wall-clock.
    assert rev["t"] != rev["confirmed_at"]


def test_reconcile_prefers_detect_confirm_bar() -> None:
    """New accept uses detect's confirm-bar stamp, not reconcile wall-clock."""
    from options.gamma_density_history import reconcile_session_reversals

    pivot_ms = int(datetime(2026, 8, 4, 9, 34, tzinfo=IST).timestamp() * 1000)
    conf_ms = int(datetime(2026, 8, 4, 9, 49, tzinfo=IST).timestamp() * 1000)
    unlock_ms = int(datetime(2026, 8, 4, 10, 20, tzinfo=IST).timestamp() * 1000)
    cand = _bull_rev(pivot_ms, 44.0, tf="5m")
    cand["confirmed_ts_ms"] = conf_ms
    cand["confirmed_at"] = datetime.fromtimestamp(conf_ms / 1000.0, tz=IST).isoformat()

    merged = reconcile_session_reversals(
        [cand], [], tf="5m", confirm_bars=7, now_ms=unlock_ms
    )
    assert merged[0]["confirmed_ts_ms"] == conf_ms
    assert merged[0]["confirmed_ts_ms"] != unlock_ms


def test_reconcile_repairs_batch_wall_clock_confirm() -> None:
    """Match syncs confirm-bar from detect over a prior Live-unlock batch stamp."""
    from options.gamma_density_history import reconcile_session_reversals

    pivot_ms = int(datetime(2026, 8, 4, 9, 34, tzinfo=IST).timestamp() * 1000)
    conf_ms = int(datetime(2026, 8, 4, 9, 49, tzinfo=IST).timestamp() * 1000)
    batch_ms = int(datetime(2026, 8, 4, 10, 20, tzinfo=IST).timestamp() * 1000)
    frozen = reconcile_session_reversals(
        [_bull_rev(pivot_ms, 44.0, tf="5m")],
        [],
        tf="5m",
        confirm_bars=7,
        now_ms=batch_ms,
    )
    assert frozen[0]["confirmed_ts_ms"] == batch_ms

    cand = _bull_rev(pivot_ms, 44.0, tf="5m")
    cand["confirmed_ts_ms"] = conf_ms
    cand["confirmed_at"] = datetime.fromtimestamp(conf_ms / 1000.0, tz=IST).isoformat()
    repaired = reconcile_session_reversals(
        [cand], frozen, tf="5m", confirm_bars=7, now_ms=batch_ms + 60_000
    )
    assert repaired[0]["confirmed_ts_ms"] == conf_ms


def test_freeze_merge_preserves_confirmed_at() -> None:
    """Without a detect confirm stamp, later merge keeps the first accept time."""
    from options.gamma_density_history import reconcile_session_reversals

    pivot_ms = int(datetime(2026, 7, 30, 8, 39, tzinfo=IST).timestamp() * 1000)
    accept_ms = int(datetime(2026, 7, 30, 9, 12, tzinfo=IST).timestamp() * 1000)
    later_ms = accept_ms + 45 * 60 * 1000

    first = reconcile_session_reversals(
        [_bull_rev(pivot_ms, 88.0, tf="5m", side="bearish")],
        [],
        tf="5m",
        confirm_bars=3,
        now_ms=accept_ms,
    )
    stamped_at = first[0]["confirmed_at"]
    stamped_ts = first[0]["confirmed_ts_ms"]

    # Confirm window still open — move_pts may refresh; confirmed_* must stay
    # when the candidate has no confirm-bar fields (legacy / helper shape).
    open_ms = pivot_ms + 10 * 60 * 1000
    refreshed = reconcile_session_reversals(
        [_bull_rev(pivot_ms, 120.0, tf="5m", side="bearish")],
        first,
        tf="5m",
        confirm_bars=3,
        now_ms=open_ms,
    )
    assert refreshed[0]["confirmed_at"] == stamped_at
    assert refreshed[0]["confirmed_ts_ms"] == stamped_ts
    assert refreshed[0]["move_pts"] == 120.0

    # After freeze — still preserved.
    frozen = reconcile_session_reversals(
        [_bull_rev(pivot_ms, 99.0, tf="5m", side="bearish")],
        refreshed,
        tf="5m",
        confirm_bars=3,
        now_ms=later_ms,
    )
    assert frozen[0]["confirmed_at"] == stamped_at
    assert frozen[0]["confirmed_ts_ms"] == stamped_ts
    assert frozen[0]["move_pts"] == 120.0


def test_detect_stamps_confirm_bar_not_pivot() -> None:
    """Detect sets confirmed_* to the first bar that clears min_move, not the trough."""
    base = 23600.0
    ts0 = datetime(2026, 7, 24, 9, 30, tzinfo=IST)
    # trough at idx 5 (-100); +50 threshold first clears at idx 7 (-50 → +50)
    path = [0, -20, -40, -60, -80, -100, -90, -50, -20, 20, 40]
    series = []
    for i, d in enumerate(path):
        t = ts0.timestamp() + i * 60
        series.append(
            {
                "t": datetime.fromtimestamp(t, tz=IST).isoformat(),
                "ts_ms": int(t * 1000),
                "spot": base + d,
                "total_gex": -1e5 if d < -50 else 1e5,
                "gamma_regime": "negative" if d < -50 else "positive",
            }
        )
    revs = detect_spot_reversals(
        series, swing_bars=2, min_move_pts=50, confirm_bars=5, gex_gate=False, tf="1m"
    )
    bulls = [r for r in revs if r["side"] == "bullish"]
    assert bulls
    rev = bulls[0]
    trough_ms = series[5]["ts_ms"]
    clear_ms = series[7]["ts_ms"]  # -100 → -50 = +50 ≥ 50
    assert rev["ts_ms"] == trough_ms
    assert rev["confirmed_ts_ms"] == clear_ms
    assert rev["confirmed_ts_ms"] != rev["ts_ms"]


def test_detect_emits_when_move_clears_before_full_confirm_pad() -> None:
    """Pivot appears once swing + min_move clear — not after a full confirm_bars pad.

    Old ``range(swing, n - confirm)`` hid a 5m-style pivot until confirm_bars of
    future bars existed even when the move already cleared on bar 1.
    """
    base = 77700.0
    ts0 = datetime(2026, 8, 6, 9, 50, tzinfo=IST)
    # Peak at idx 3; swing=2 needs idx 5; move clears by idx 4 (−160).
    # confirm_bars=7 would require n>=11 under the old right-edge rule.
    path = [0, 40, 80, 160, 0, -20, -30]
    series = _minute_series(path, base, ts0)
    revs = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=7,
        gex_gate=False,
        tf="1m",
    )
    bears = [r for r in revs if r["side"] == "bearish"]
    assert bears, "expected bearish pivot without waiting for full confirm pad"
    assert bears[0]["ts_ms"] == series[3]["ts_ms"]
    assert bears[0]["confirmed_ts_ms"] == series[4]["ts_ms"]
    assert bears[0].get("provisional") is False


def test_live_provisional_when_gex_gate_fails() -> None:
    """Live provisional_ungated emits muted pivot; Research-style hard gate drops it."""
    base = 23600.0
    ts0 = datetime(2026, 8, 6, 10, 0, tzinfo=IST)
    # Peak then dump; GEX stays positive → bearish hard gate fails.
    path = [0, 30, 60, 100, 40, 0, -20]
    series = []
    for i, d in enumerate(path):
        t = ts0.timestamp() + i * 60
        series.append(
            {
                "t": datetime.fromtimestamp(t, tz=IST).isoformat(),
                "ts_ms": int(t * 1000),
                "spot": base + d,
                "total_gex": 1e6,
                "gamma_regime": "positive",
            }
        )
    hard = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=True,
        provisional_ungated=False,
        tf="1m",
    )
    assert not any(r["side"] == "bearish" for r in hard)

    live = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=True,
        provisional_ungated=True,
        tf="1m",
    )
    bears = [r for r in live if r["side"] == "bearish"]
    assert bears
    assert bears[0]["provisional"] is True
    assert bears[0]["gex_confirm"] is False
    assert bears[0]["ts_ms"] == series[3]["ts_ms"]


def test_reconcile_promotes_provisional_when_gate_clears() -> None:
    """Matching candidate with gex_confirm clears provisional on the frozen chip."""
    from options.gamma_density_history import reconcile_session_reversals

    pivot_ms = int(datetime(2026, 8, 6, 9, 54, tzinfo=IST).timestamp() * 1000)
    conf_ms = int(datetime(2026, 8, 6, 9, 59, tzinfo=IST).timestamp() * 1000)
    now_ms = int(datetime(2026, 8, 6, 10, 30, tzinfo=IST).timestamp() * 1000)

    provisional = _bull_rev(pivot_ms, 160.0, tf="5m", side="bearish")
    provisional["gex_confirm"] = False
    provisional["provisional"] = True
    provisional["confirmed_ts_ms"] = conf_ms
    provisional["confirmed_at"] = datetime.fromtimestamp(conf_ms / 1000.0, tz=IST).isoformat()
    provisional["label"] = "5m Bearish · (-160 pts)"

    frozen = reconcile_session_reversals(
        [provisional], [], tf="5m", confirm_bars=7, now_ms=now_ms
    )
    assert frozen[0]["provisional"] is True
    assert frozen[0]["gex_confirm"] is False

    gated = dict(provisional)
    gated["gex_confirm"] = True
    gated["provisional"] = False
    gated["label"] = "5m Bearish · (-160 pts) · GEX"
    promoted = reconcile_session_reversals(
        [gated], frozen, tf="5m", confirm_bars=7, now_ms=now_ms + 60_000
    )
    assert len(promoted) == 1
    assert promoted[0]["provisional"] is False
    assert promoted[0]["gex_confirm"] is True
    assert promoted[0]["confirmed_ts_ms"] == conf_ms


def test_format_reversal_chip_times_pivot_and_confirm() -> None:
    """Chip time bits show both pivot and confirm; legacy omits conf."""
    from options.gamma_density_history import format_reversal_chip_times

    pivot_ms = int(datetime(2026, 7, 30, 8, 39, tzinfo=IST).timestamp() * 1000)
    conf_ms = int(datetime(2026, 7, 30, 9, 12, tzinfo=IST).timestamp() * 1000)
    rev = {
        "t": datetime.fromtimestamp(pivot_ms / 1000.0, tz=IST).isoformat(),
        "ts_ms": pivot_ms,
        "confirmed_at": datetime.fromtimestamp(conf_ms / 1000.0, tz=IST).isoformat(),
        "confirmed_ts_ms": conf_ms,
        "label": "5m Bearish · (-88 pts)",
    }
    assert format_reversal_chip_times(rev) == "08:39 pivot · conf 09:12"
    # Full chip shape used by GexSessionPlotly.
    chip = f"CRUDEOILM · {format_reversal_chip_times(rev)} · {rev['label']}"
    assert chip == "CRUDEOILM · 08:39 pivot · conf 09:12 · 5m Bearish · (-88 pts)"

    legacy = {"t": rev["t"], "ts_ms": pivot_ms, "label": rev["label"]}
    assert format_reversal_chip_times(legacy) == "08:39"


def test_move_pts_may_update_while_confirm_open() -> None:
    """While confirm window is open, refresh to current measured move."""
    from options.gamma_density_history import reconcile_session_reversals

    pivot_ms = int(datetime(2026, 7, 30, 12, 14, tzinfo=IST).timestamp() * 1000)
    # 15m confirm still open (30m < 60m)
    open_ms = pivot_ms + 30 * 60 * 1000

    first = [_bull_rev(pivot_ms, 120.0)]
    merged1 = reconcile_session_reversals(
        first, [], tf="15m", confirm_bars=4, now_ms=open_ms
    )
    assert merged1[0]["move_pts"] == 120.0

    second = [_bull_rev(pivot_ms, 180.0)]
    merged2 = reconcile_session_reversals(
        second, merged1, tf="15m", confirm_bars=4, now_ms=open_ms
    )
    assert merged2[0]["move_pts"] == 180.0
    assert "+180" in merged2[0]["label"]


def test_opposite_side_still_accepted_with_frozen() -> None:
    """New opposite-side signal appends even when a frozen bull exists."""
    from options.gamma_density_history import reconcile_session_reversals

    bull_ms = int(datetime(2026, 7, 30, 12, 14, tzinfo=IST).timestamp() * 1000)
    bear_ms = bull_ms + 45 * 60 * 1000
    now_ms = bear_ms + 90 * 60 * 1000

    frozen = reconcile_session_reversals(
        [_bull_rev(bull_ms, 216.0)],
        [],
        tf="15m",
        confirm_bars=4,
        now_ms=now_ms,
    )
    candidates = [
        _bull_rev(bull_ms, 189.0),  # drifted measure — ignored after freeze
        _bull_rev(bear_ms, 95.0, side="bearish"),
    ]
    merged = reconcile_session_reversals(
        candidates, frozen, tf="15m", confirm_bars=4, now_ms=now_ms
    )
    sides = [r["side"] for r in merged]
    assert sides == ["bullish", "bearish"]
    assert merged[0]["move_pts"] == 216.0
    assert merged[1]["move_pts"] == 95.0


def test_persist_session_reversals_roundtrip(tmp_path, monkeypatch) -> None:
    """Persisted reversals reload and stay frozen across detect passes."""
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    pivot_ms = int(datetime(2026, 7, 30, 12, 14, tzinfo=IST).timestamp() * 1000)
    after_close_ms = pivot_ms + 90 * 60 * 1000

    out1 = gdh.persist_session_reversals(
        "NIFTY",
        "2026-07-30",
        [_bull_rev(pivot_ms, 216.0)],
        tf="15m",
        confirm_bars=4,
        now_ms=after_close_ms,
    )
    assert out1[0]["move_pts"] == 216.0

    out2 = gdh.persist_session_reversals(
        "NIFTY",
        "2026-07-30",
        [_bull_rev(pivot_ms, 189.0)],
        tf="15m",
        confirm_bars=4,
        now_ms=after_close_ms,
    )
    assert out2[0]["move_pts"] == 216.0
    loaded = gdh.get_session_reversals("NIFTY", "2026-07-30", "15m")
    assert loaded[0]["move_pts"] == 216.0
    assert gdh.get_session_reversals is gdh.get_persisted_reversals


def test_get_history_switch_is_display_only(tmp_path, monkeypatch) -> None:
    """Reading / switching display key must not mutate other underlyings' series."""
    from datetime import date as date_cls

    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    today = date_cls.today().isoformat()
    crude_key = f"CRUDEOIL|2026-08-17|{today}"
    nifty_key = f"NIFTY|2026-08-04|{today}"
    crude_pts = [
        {
            "t": f"{today}T11:00:00+05:30",
            "spot": 6200.0,
            "total_gex": 1.0e9,
            "flip_level": 6180.0,
            "gamma_regime": "positive",
        }
    ]
    nifty_pts = [
        {
            "t": f"{today}T11:05:00+05:30",
            "spot": 24500.0,
            "total_gex": 2.0e9,
            "flip_level": 24400.0,
            "gamma_regime": "negative",
        }
    ]
    gdh._save(
        {
            "series": {crude_key: crude_pts, nifty_key: nifty_pts},
            "reversals": {},
            "daily_hhi": {},
        }
    )

    assert gdh.get_history("NIFTY", "2026-08-04") == nifty_pts
    assert gdh.get_history("CRUDEOIL", "2026-08-17") == crude_pts
    # Re-read after "switch" — disk unchanged.
    data = gdh._load(strict=True)
    assert data["series"][crude_key] == crude_pts
    assert data["series"][nifty_key] == nifty_pts


def test_nifty_snapshot_writes_preserve_crudeoil_history(tmp_path, monkeypatch) -> None:
    """Underlying switch / NIFTY refresh must not wipe CRUDEOIL series or reversals."""
    from datetime import date as date_cls

    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    today = date_cls.today().isoformat()
    during = datetime.now(tz=IST).replace(hour=11, minute=30, second=0, microsecond=0)

    crude_key = f"CRUDEOIL|2026-08-17|{today}"
    crude_rev_key = f"CRUDEOIL|2026-08-17|{today}|5m"
    crude_pts = [
        {
            "t": during.isoformat(timespec="seconds"),
            "spot": 6200.0,
            "total_gex": 1.2e9,
            "flip_level": 6180.0,
            "gamma_regime": "positive",
        }
    ]
    crude_revs = [
        {
            "t": during.isoformat(timespec="seconds"),
            "ts_ms": int(during.timestamp() * 1000),
            "side": "bullish",
            "spot": 6200.0,
            "move_pts": 49.0,
            "gex_confirm": True,
            "oi_align": True,
            "oi_gate_pass": True,
            "tf": "5m",
            "label": "5m Bullish · (+49 pts) · GEX · OI align",
        }
    ]
    gdh._save(
        {
            "series": {crude_key: crude_pts},
            "reversals": {crude_rev_key: crude_revs},
            "daily_hhi": {"CRUDEOIL": [{"date": today, "hhi": 0.31}]},
        }
    )

    # Simulate a NIFTY desk refresh (series append + daily HHI + frozen reversals).
    monkeypatch.setattr(gdh, "in_session", lambda *_a, **_k: True)
    gdh.append_history_point(
        "NIFTY",
        "2026-08-04",
        spot=24500.0,
        total_gex=5.0e9,
        flip_level=24400.0,
        gamma_regime="positive",
        hhi=0.22,
        conviction=0.5,
        pin_strike=24500.0,
        atm_iv=12.5,
    )
    gdh.upsert_daily_hhi("NIFTY", 0.22, when=during, force=True)
    gdh.persist_session_reversals(
        "NIFTY",
        "2026-08-04",
        [
            {
                "t": during.isoformat(timespec="seconds"),
                "ts_ms": int(during.timestamp() * 1000),
                "side": "bearish",
                "spot": 24500.0,
                "move_pts": 80.0,
                "gex_confirm": True,
                "oi_align": False,
                "oi_gate_pass": None,
                "tf": "5m",
                "label": "5m Bearish · (-80 pts) · GEX",
            }
        ],
        tf="5m",
        now_ms=int(during.timestamp() * 1000) + 60 * 60 * 1000,
    )

    data = gdh._load(strict=True)
    assert crude_key in data["series"]
    assert data["series"][crude_key] == crude_pts
    assert crude_rev_key in data["reversals"]
    assert data["reversals"][crude_rev_key][0]["move_pts"] == 49.0
    assert data["daily_hhi"]["CRUDEOIL"][0]["hhi"] == 0.31
    # NIFTY keys were written alongside, not instead of CRUDE.
    assert any(k.startswith("NIFTY|") for k in data["series"])
    assert any(k.startswith("NIFTY|") for k in data["reversals"])


def test_upsert_daily_hhi_persists_and_updates_same_day(tmp_path, monkeypatch) -> None:
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    during = datetime(2026, 7, 30, 11, 0, tzinfo=IST)
    later = datetime(2026, 7, 30, 15, 0, tzinfo=IST)

    s1 = gdh.upsert_daily_hhi("NIFTY", 0.22, when=during)
    assert len(s1) == 1
    assert s1[0]["date"] == "2026-07-30"
    assert s1[0]["hhi"] == 0.22

    s2 = gdh.upsert_daily_hhi("NIFTY", 0.35, when=later)
    assert len(s2) == 1
    assert s2[0]["hhi"] == 0.35  # last in-session write wins

    loaded = gdh.get_daily_hhi_series("NIFTY")
    assert len(loaded) == 1
    assert loaded[0]["date"] == "2026-07-30"
    assert loaded[0]["hhi"] == 0.35
    # Every write stamps when it happened — a day-end value that is really an
    # 11:00 value should be identifiable as such.
    assert loaded[0]["updated_at"].startswith("2026-07-30T15:00")


def test_daily_hhi_rows_carry_measurement_basis(tmp_path, monkeypatch) -> None:
    """HHI's floor is 1/N, so the window it was measured at must travel with it."""
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    during = datetime(2026, 7, 30, 11, 0, tzinfo=IST)
    gdh.upsert_daily_hhi(
        "NIFTY", 0.22, when=during, basis="gross", strike_window=20, sign_mode="naive"
    )
    row = gdh.get_daily_hhi_series("NIFTY")[0]
    assert row["basis"] == "gross"
    assert row["strike_window"] == 20
    assert row["sign_mode"] == "naive"

    # A later same-day write on a different window replaces the stamp too.
    gdh.upsert_daily_hhi(
        "NIFTY",
        0.31,
        when=datetime(2026, 7, 30, 14, 0, tzinfo=IST),
        basis="gross",
        strike_window=10,
        sign_mode="naive",
    )
    row2 = gdh.get_daily_hhi_series("NIFTY")[0]
    assert row2["hhi"] == 0.31
    assert row2["strike_window"] == 10


def test_filter_daily_hhi_basis_drops_mismatched_and_legacy_rows() -> None:
    from options.gamma_density_history import filter_daily_hhi_basis

    series = [
        {"date": "2026-07-27", "hhi": 0.11},  # legacy: untagged → net, unknown window
        {"date": "2026-07-28", "hhi": 0.12, "basis": "net", "strike_window": 20},
        {"date": "2026-07-29", "hhi": 0.13, "basis": "gross", "strike_window": 10},
        {"date": "2026-07-30", "hhi": 0.14, "basis": "gross", "strike_window": 20},
    ]
    kept = filter_daily_hhi_basis(series, basis="gross", strike_window=20)
    assert [r["date"] for r in kept] == ["2026-07-30"]

    # Legacy rows count as net basis.
    net_rows = filter_daily_hhi_basis(series, basis="net")
    assert [r["date"] for r in net_rows] == ["2026-07-27", "2026-07-28"]

    # No filters requested → everything passes through untouched.
    assert filter_daily_hhi_basis(series) == series


def test_legacy_rows_resolve_as_net_at_the_assumed_window() -> None:
    """Untagged rows are readable as net-basis even before the store is migrated."""
    from options.gamma_density_history import (
        filter_daily_hhi_basis,
        legacy_daily_hhi_window,
        normalize_legacy_daily_hhi_row,
    )

    legacy = {"date": "2026-07-27", "hhi": 0.11}
    norm = normalize_legacy_daily_hhi_row(legacy)
    assert norm["basis"] == "net"
    assert norm["hhi_net"] == 0.11
    assert norm["sign_mode"] == "naive"
    assert norm["strike_window"] == legacy_daily_hhi_window()
    assert norm["strike_window_assumed"] is True
    assert norm["legacy"] is True
    # Idempotent — a second pass must not re-stamp an already tagged row.
    assert normalize_legacy_daily_hhi_row(norm) == norm
    # The input is never mutated in place.
    assert legacy == {"date": "2026-07-27", "hhi": 0.11}

    kept = filter_daily_hhi_basis(
        [legacy], basis="net", strike_window=legacy_daily_hhi_window(), sign_mode="naive"
    )
    assert [r["date"] for r in kept] == ["2026-07-27"]
    # Legacy rows carry no gross measure, so a gross board cannot use them.
    assert filter_daily_hhi_basis([legacy], basis="gross") == []


def test_dual_basis_rows_serve_either_comparison() -> None:
    """A day recorded on gross still answers a net-basis comparison."""
    from options.gamma_density_history import filter_daily_hhi_basis

    row = {
        "date": "2026-08-11", "hhi": 0.186, "basis": "gross",
        "hhi_gross": 0.186, "hhi_net": 0.142,
        "strike_window": 20, "sign_mode": "naive",
    }
    as_gross = filter_daily_hhi_basis([row], basis="gross", strike_window=20)
    assert as_gross[0]["hhi"] == 0.186
    assert as_gross[0]["basis"] == "gross"

    as_net = filter_daily_hhi_basis([row], basis="net", strike_window=20)
    assert as_net[0]["hhi"] == 0.142
    assert as_net[0]["basis"] == "net"
    # Resolution copies — the caller's row is untouched.
    assert row["hhi"] == 0.186 and row["basis"] == "gross"


def test_upsert_migrates_legacy_rows_in_place(tmp_path, monkeypatch) -> None:
    """The first tagged write also persists the legacy interpretation."""
    import json

    import options.gamma_density_history as gdh

    hist = tmp_path / "gamma_density_history.json"
    monkeypatch.setattr(gdh, "HISTORY_FILE", hist)
    hist.write_text(
        json.dumps(
            {
                "series": {},
                "reversals": {},
                "daily_hhi": {
                    "NIFTY": [
                        {"date": "2026-07-28", "hhi": 0.073},
                        {"date": "2026-07-29", "hhi": 0.321},
                    ]
                },
            }
        ),
        encoding="utf-8",
    )

    out = gdh.upsert_daily_hhi(
        "NIFTY", 0.186,
        when=datetime(2026, 7, 30, 11, 0, tzinfo=IST),
        basis="gross", strike_window=20, sign_mode="naive",
        hhi_gross=0.186, hhi_net=0.142,
    )
    by_date = {r["date"]: r for r in out}
    assert len(by_date) == 3

    for legacy_date in ("2026-07-28", "2026-07-29"):
        row = by_date[legacy_date]
        assert row["basis"] == "net"
        assert row["hhi_net"] == row["hhi"]
        assert row["strike_window_assumed"] is True

    fresh = by_date["2026-07-30"]
    assert fresh["basis"] == "gross"
    assert fresh["hhi_gross"] == 0.186
    assert fresh["hhi_net"] == 0.142
    assert fresh["strike_window_assumed"] is False
    assert fresh["legacy"] is False

    # Migration is persisted, not just returned.
    on_disk = json.loads(hist.read_text(encoding="utf-8"))["daily_hhi"]["NIFTY"]
    assert all(r.get("basis") for r in on_disk)

    # The legacy days now serve a net-basis comparison; only today serves gross.
    net_rows = gdh.filter_daily_hhi_basis(out, basis="net", strike_window=20, sign_mode="naive")
    assert [r["date"] for r in net_rows] == ["2026-07-28", "2026-07-29", "2026-07-30"]
    assert net_rows[-1]["hhi"] == 0.142
    gross_rows = gdh.filter_daily_hhi_basis(out, basis="gross", strike_window=20, sign_mode="naive")
    assert [r["date"] for r in gross_rows] == ["2026-07-30"]
    assert gdh.count_assumed_window_rows(net_rows) == 2
    assert gdh.count_assumed_window_rows(gross_rows) == 0


def test_upsert_daily_hhi_skips_outside_session(tmp_path, monkeypatch) -> None:
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    after = datetime(2026, 7, 30, 16, 45, tzinfo=IST)
    out = gdh.upsert_daily_hhi("NIFTY", 0.40, when=after)
    assert out == []
    assert gdh.get_daily_hhi_series("NIFTY") == []


def test_upsert_daily_hhi_prunes_old_days(tmp_path, monkeypatch) -> None:
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    # Seed 45 calendar days via force so prune is exercised without session gates
    for i in range(45):
        day = datetime(2026, 6, 1, 12, 0, tzinfo=IST) + timedelta(days=i)
        gdh.upsert_daily_hhi("NIFTY", 0.10 + i * 0.001, when=day, force=True, max_days=40)

    series = gdh.get_daily_hhi_series("NIFTY")
    assert len(series) == 40
    assert series[0]["date"] == "2026-06-06"
    assert series[-1]["date"] == "2026-07-15"


def test_hhi_percentile_sessions_math() -> None:
    from options.gamma_density_history import hhi_percentile_sessions

    series = [{"date": f"2026-07-{d:02d}", "hhi": h} for d, h in zip(
        range(1, 11),
        [0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25, 0.28, 0.30, 0.35],
    )]
    # current 0.28 → days ≤ 0.28: 8 of 10 → 80th
    pct, n = hhi_percentile_sessions(series, 0.28, n=30)
    assert n == 10
    assert pct == 80.0

    # Window n=5 → last five [0.22, 0.25, 0.28, 0.30, 0.35]; ≤0.28 → 3/5 → 60th
    pct5, n5 = hhi_percentile_sessions(series, 0.28, n=5)
    assert n5 == 5
    assert pct5 == 60.0

    # Empty series → no sample yet
    pct0, n0 = hhi_percentile_sessions([], 0.40, n=30)
    assert n0 == 0
    assert pct0 is None


def _pin_kwargs(**over):
    base = dict(
        pin=24600.0,
        pin_source="dominant",
        pin_share=0.29,
        spot=24615.0,
        total_gex=1.2e12,
        gamma_regime="positive",
        hhi=0.19,
        flip_level=24807.0,
        sigma1_pts=62.0,
        strike_step=50.0,
    )
    base.update(over)
    return base


def test_record_pin_sample_throttles_and_tracks_close(tmp_path, monkeypatch) -> None:
    """Bounded samples, and close_spot refreshed on every write."""
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    t0 = datetime(2026, 7, 30, 10, 0, tzinfo=IST)

    gdh.record_pin_sample("NIFTY", when=t0, **_pin_kwargs())
    # 10 minutes later: inside the 30-min throttle → no new sample...
    gdh.record_pin_sample(
        "NIFTY", when=t0 + timedelta(minutes=10), **_pin_kwargs(spot=24630.0)
    )
    rows = gdh.get_daily_pin_series("NIFTY")
    assert len(rows) == 1
    assert len(rows[0]["samples"]) == 1
    # ...but the close is still refreshed, since the last write approximates it.
    assert rows[0]["close_spot"] == 24630.0

    # 35 minutes on → past the throttle, a second checkpoint lands.
    gdh.record_pin_sample(
        "NIFTY", when=t0 + timedelta(minutes=35), **_pin_kwargs(pin=24650.0)
    )
    samples = gdh.get_daily_pin_series("NIFTY")[0]["samples"]
    assert len(samples) == 2
    assert samples[1]["pin"] == 24650.0
    assert samples[1]["pin_source"] == "dominant"


def test_record_pin_sample_skips_outside_session(tmp_path, monkeypatch) -> None:
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    after = datetime(2026, 7, 30, 16, 45, tzinfo=IST)
    assert gdh.record_pin_sample("NIFTY", when=after, **_pin_kwargs()) == []
    assert gdh.get_daily_pin_series("NIFTY") == []


def test_record_pin_sample_leaves_other_buckets_alone(tmp_path, monkeypatch) -> None:
    """daily_pin writes must not disturb series / reversals / daily_hhi."""
    import options.gamma_density_history as gdh

    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    during = datetime(2026, 7, 30, 11, 0, tzinfo=IST)
    gdh.upsert_daily_hhi("NIFTY", 0.22, when=during, basis="gross", strike_window=20)
    gdh.record_pin_sample("NIFTY", when=during, **_pin_kwargs())

    assert gdh.get_daily_hhi_series("NIFTY")[0]["hhi"] == 0.22
    assert len(gdh.get_daily_pin_series("NIFTY")) == 1
    assert gdh.get_daily_pin_series("BANKNIFTY") == []


def test_pin_hold_outcomes_derives_held_at_read_time() -> None:
    """Verdicts are derived, so changing the tolerance re-reads history."""
    from options.gamma_density_history import pin_hold_outcomes

    series = [
        {
            "date": "2026-07-30",
            "strike_step": 50.0,
            "close_spot": 24620.0,
            "samples": [
                {"t": "2026-07-30T10:00:00+05:30", "pin": 24600.0, "pin_source": "dominant"},
                {"t": "2026-07-30T14:00:00+05:30", "pin": 24500.0, "pin_source": "atm"},
                {"t": "2026-07-30T15:00:00+05:30", "pin": None, "pin_source": None},
            ],
        },
        # No close recorded → unknown outcome, must not be scored as a failure.
        {"date": "2026-07-31", "strike_step": 50.0, "samples": [{"pin": 24700.0}]},
    ]
    out = pin_hold_outcomes(series, hold_steps=1.0)
    assert len(out) == 2  # null pin and the closeless session are both skipped
    assert out[0]["held"] is True  # |24620 - 24600| = 20 <= 50
    assert out[0]["distance_at_close"] == 20.0
    assert out[1]["held"] is False  # |24620 - 24500| = 120 > 50

    # A wider tolerance re-reads the same rows to a different verdict.
    wider = pin_hold_outcomes(series, hold_steps=3.0)
    assert [r["held"] for r in wider] == [True, True]


# --- Phase 1: lag transparency ------------------------------------------------
#
# Pivot and confirm times on a chip are both backdated. A signal whose gate
# clears late therefore advertises a confirm time nobody could have acted on --
# the 2026-09-04 report of "conf 09:49 appearing at 10:20". These pin the two
# fields that make that delay visible instead of mysterious.


def _blocked_series() -> list[dict]:
    """V-shaped trough whose GEX regime stays hostile to a bullish reversal."""
    base = 23600.0
    ts0 = datetime(2026, 7, 24, 9, 30, tzinfo=IST)
    path = [0, -20, -40, -60, -80, -100, -90, -50, -20, 20, 40]
    series = []
    for i, d in enumerate(path):
        t = ts0.timestamp() + i * 60
        series.append(
            {
                "t": datetime.fromtimestamp(t, tz=IST).isoformat(),
                "ts_ms": int(t * 1000),
                "spot": base + d,
                "total_gex": -1e5,
                "gamma_regime": "negative",  # never supportive of a bullish pivot
            }
        )
    return series


def test_blocked_by_names_the_gex_gate_when_provisional() -> None:
    revs = detect_spot_reversals(
        _blocked_series(),
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=True,
        provisional_ungated=True,
        tf="1m",
    )
    bulls = [r for r in revs if r["side"] == "bullish"]
    assert bulls, "provisional_ungated should still surface the pivot"
    assert bulls[0]["provisional"] is True
    assert bulls[0]["blocked_by"] == "gex"


def test_blocked_by_is_none_once_the_gate_clears() -> None:
    revs = detect_spot_reversals(
        _blocked_series(),
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=5,
        gex_gate=False,
        tf="1m",
    )
    bulls = [r for r in revs if r["side"] == "bullish"]
    assert bulls
    assert bulls[0]["blocked_by"] is None


def test_emit_stamp_is_set_on_first_sight() -> None:
    cand = {
        "t": "2026-07-24T09:35:00+05:30",
        "ts_ms": 1_753_330_500_000,
        "side": "bullish",
        "spot": 23500.0,
        "move_pts": 60.0,
        "confirmed_ts_ms": 1_753_330_800_000,
    }
    now_ms = 1_753_332_000_000
    out = reconcile_session_reversals([cand], [], tf="1m", now_ms=now_ms)
    assert len(out) == 1
    assert out[0]["emitted_ts_ms"] == now_ms
    # Derive rather than hardcode a year: the ISO stamp must describe the same
    # instant as the epoch stamp, in IST.
    assert datetime.fromisoformat(out[0]["emitted_at"]) == datetime.fromtimestamp(
        now_ms / 1000.0, tz=IST
    )


def test_emit_stamp_never_moves_when_the_signal_is_re_reconciled() -> None:
    """The whole point: promotion must not rewrite when it first appeared."""
    first_seen = 1_753_332_000_000
    cand = {
        "t": "2026-07-24T09:35:00+05:30",
        "ts_ms": 1_753_330_500_000,
        "side": "bullish",
        "spot": 23500.0,
        "move_pts": 60.0,
        "confirmed_ts_ms": 1_753_330_800_000,
        "provisional": True,
        "blocked_by": "gex",
    }
    frozen = reconcile_session_reversals([cand], [], tf="1m", now_ms=first_seen)

    promoted = {**cand, "provisional": False, "blocked_by": None, "gex_confirm": True}
    later = reconcile_session_reversals(
        [promoted], frozen, tf="1m", now_ms=first_seen + 45 * 60 * 1000
    )
    assert len(later) == 1
    assert later[0]["emitted_ts_ms"] == first_seen, "emit time must not follow the promotion"
    assert later[0]["blocked_by"] is None, "blocked_by should track the gate clearing"


def test_legacy_frozen_signal_is_not_backfilled_with_a_fake_emit_time() -> None:
    """A signal frozen before the field existed has no honest emit time."""
    legacy = {
        "t": "2026-07-24T09:35:00+05:30",
        "ts_ms": 1_753_330_500_000,
        "side": "bullish",
        "spot": 23500.0,
        "move_pts": 60.0,
    }
    out = reconcile_session_reversals([], [legacy], tf="1m", now_ms=1_753_340_000_000)
    assert out[0]["emitted_ts_ms"] is None


def test_new_fields_survive_canonicalisation() -> None:
    """_canonical_reversal whitelists keys; unlisted fields are silently dropped."""
    from options.gamma_density_history import FROZEN_REVERSAL_FIELDS, _canonical_reversal

    for field in ("emitted_at", "emitted_ts_ms", "blocked_by"):
        assert field in FROZEN_REVERSAL_FIELDS
    kept = _canonical_reversal(
        {"emitted_at": "x", "emitted_ts_ms": 1, "blocked_by": "gex", "side": "bullish"}
    )
    assert kept["emitted_ts_ms"] == 1
    assert kept["blocked_by"] == "gex"


# --- Phase 2: provisional-first needs a terminal state ------------------------
#
# Measured over 20 archived sessions: 18 of 18 provisional-only signals ended
# the session unpromoted. Without `gate_expired` they read "waiting GEX" for the
# rest of the day, which is worse than not showing them.


def _provisional_cand(ts_ms: int, confirmed_ts_ms: int) -> dict:
    return {
        "t": datetime.fromtimestamp(ts_ms / 1000, tz=IST).isoformat(),
        "ts_ms": ts_ms,
        "side": "bullish",
        "spot": 23500.0,
        "move_pts": 60.0,
        "confirmed_ts_ms": confirmed_ts_ms,
        "provisional": True,
        "blocked_by": "gex",
    }


def test_provisional_stays_pending_while_the_confirm_window_is_open() -> None:
    pivot = 1_753_330_500_000
    cand = _provisional_cand(pivot, pivot + 60_000)
    # 1m TF, confirm_bars=12 -> window is 12 minutes; ask at +3.
    out = reconcile_session_reversals(
        [cand], [], tf="1m", now_ms=pivot + 3 * 60 * 1000
    )
    assert out[0]["provisional"] is True
    assert not out[0].get("gate_expired"), "still inside the window; may yet promote"


def test_provisional_becomes_expired_once_the_window_closes() -> None:
    pivot = 1_753_330_500_000
    cand = _provisional_cand(pivot, pivot + 60_000)
    frozen = reconcile_session_reversals([cand], [], tf="1m", now_ms=pivot + 60_000)
    # Well past confirm_bars(12) x 1 min.
    later = reconcile_session_reversals(
        [cand], frozen, tf="1m", now_ms=pivot + 40 * 60 * 1000
    )
    assert later[0]["gate_expired"] is True
    assert later[0]["provisional"] is True


def test_promotion_clears_expiry_rather_than_stranding_the_signal() -> None:
    pivot = 1_753_330_500_000
    cand = _provisional_cand(pivot, pivot + 60_000)
    frozen = reconcile_session_reversals([cand], [], tf="1m", now_ms=pivot + 60_000)
    promoted = {**cand, "provisional": False, "gex_confirm": True, "blocked_by": None}
    out = reconcile_session_reversals(
        [promoted], frozen, tf="1m", now_ms=pivot + 40 * 60 * 1000
    )
    assert out[0]["provisional"] is False
    assert out[0]["gate_expired"] is False
    assert out[0]["blocked_by"] is None


def test_signal_first_seen_after_its_window_closed_is_terminal_immediately() -> None:
    """The retroactive-qualification path: a pivot can surface long after its own
    confirm window shut, because the adaptive threshold fell. It must not then
    advertise itself as pending."""
    pivot = 1_753_330_500_000
    cand = _provisional_cand(pivot, pivot + 60_000)
    out = reconcile_session_reversals(
        [cand], [], tf="1m", now_ms=pivot + 90 * 60 * 1000
    )
    assert out[0]["gate_expired"] is True


def test_gate_expired_survives_canonicalisation() -> None:
    from options.gamma_density_history import FROZEN_REVERSAL_FIELDS, _canonical_reversal

    assert "gate_expired" in FROZEN_REVERSAL_FIELDS
    assert _canonical_reversal({"gate_expired": True})["gate_expired"] is True


# --- Phase 3: window bounds and a threshold that cannot move backwards --------
#
# Measured over 20 archived sessions at 5m, these two together cut shape-lag max
# from 163m to 34m and conf-lag max from 62m to 17m. The third candidate
# (asymmetric swing, right_bars=1) was measured and rejected: at 1m it pushed
# shape max from 166m to 223m, because less proof means more false pivots that
# then take longer to resolve.


def _gapped_series(gap_after: int, gap_minutes: int) -> list[dict]:
    """V-shaped reclaim with a sampling hole inserted mid-window."""
    base = 23600.0
    ts = datetime(2026, 7, 24, 9, 30, tzinfo=IST).timestamp()
    path = [0, -20, -40, -60, -80, -100, -90, -50, -20, 20, 40]
    series = []
    for i, dpx in enumerate(path):
        if i == gap_after:
            ts += gap_minutes * 60
        series.append(
            {
                "t": datetime.fromtimestamp(ts, tz=IST).isoformat(),
                "ts_ms": int(ts * 1000),
                "spot": base + dpx,
                "total_gex": 1e5,
                "gamma_regime": "positive",
            }
        )
        ts += 60
    return series


def test_pivot_proved_across_a_sampling_hole_is_rejected() -> None:
    series = _gapped_series(gap_after=6, gap_minutes=135)
    kw = {"swing_bars": 2, "min_move_pts": 50, "confirm_bars": 5, "gex_gate": False, "tf": "1m"}
    assert detect_spot_reversals(series, **kw), "sanity: detected without the bound"
    bounded = detect_spot_reversals(series, **kw, max_bar_gap_ratio=3.0)
    assert bounded == [], "a pivot whose window straddles a 135-minute hole is not proven"


def test_gap_outside_the_pivot_window_does_not_reject_it() -> None:
    """Only the bars this pivot depends on matter; a hole elsewhere is irrelevant."""
    series = _gapped_series(gap_after=10, gap_minutes=135)  # after the whole window
    revs = detect_spot_reversals(
        series,
        swing_bars=2,
        min_move_pts=50,
        confirm_bars=3,
        gex_gate=False,
        tf="1m",
        max_bar_gap_ratio=3.0,
    )
    assert revs, "a gap past the confirm window must not veto the pivot"


def test_frozen_threshold_is_stable_as_later_bars_arrive() -> None:
    """The retroactive-admission bug: the same pivot must qualify identically
    whether judged now or judged with another hour of quiet tape appended."""
    base = 23600.0
    ts0 = datetime(2026, 7, 24, 9, 30, tzinfo=IST).timestamp()
    path = [0, -20, -40, -60, -80, -100, -90, -50, -20, 20, 40]
    series = []
    for i, dpx in enumerate(path):
        t = ts0 + i * 60
        series.append(
            {
                "t": datetime.fromtimestamp(t, tz=IST).isoformat(),
                "ts_ms": int(t * 1000),
                "spot": base + dpx,
                "total_gex": 1e5,
                "gamma_regime": "positive",
            }
        )
    kw = {
        "swing_bars": 2,
        "confirm_bars": 5,
        "gex_gate": False,
        "tf": "1m",
        "freeze_threshold": True,
    }
    early = detect_spot_reversals(series, min_move_pts=50, **kw)

    # Append an hour of near-flat tape: the *global* adaptive threshold falls,
    # which is what used to admit old pivots after the fact.
    quiet = list(series)
    for i in range(60):
        t = ts0 + (len(path) + i) * 60
        quiet.append(
            {
                "t": datetime.fromtimestamp(t, tz=IST).isoformat(),
                "ts_ms": int(t * 1000),
                "spot": base + 40 + (i % 2),
                "total_gex": 1e5,
                "gamma_regime": "positive",
            }
        )
    late = detect_spot_reversals(quiet, min_move_pts=50, **kw)

    early_ids = {(r["ts_ms"], r["side"]) for r in early}
    late_ids = {(r["ts_ms"], r["side"]) for r in late}
    assert early_ids <= late_ids, "a pivot must not vanish as tape arrives"
    new_in_window = {
        k for k in late_ids - early_ids if k[0] <= series[-1]["ts_ms"]
    }
    assert not new_in_window, (
        "no pivot inside the original window may appear only after quiet tape "
        f"lowered the threshold: {sorted(new_in_window)}"
    )


def test_defaults_leave_detection_behaviour_unchanged() -> None:
    """Every Phase 3 knob is opt-in; omitting them must match the old detector."""
    series = _gapped_series(gap_after=6, gap_minutes=135)
    kw = {"swing_bars": 2, "min_move_pts": 50, "confirm_bars": 5, "gex_gate": False, "tf": "1m"}
    assert detect_spot_reversals(series, **kw) == detect_spot_reversals(
        series, **kw, right_bars=None, freeze_threshold=False, max_bar_gap_ratio=None
    )


def test_matching_candidate_does_not_backfill_a_legacy_emit_time() -> None:
    """The real shape of the legacy case, which an empty candidate list missed.

    Detect re-produces the same candidates on every poll, so a frozen signal is
    matched on every pass. Stamping on match backfilled every pre-existing
    signal with the current time — after an API restart, this morning's 09:39
    pivot claimed it was first seen at 12:10.
    """
    legacy = {
        "t": "2026-07-24T09:35:00+05:30",
        "ts_ms": 1_753_330_500_000,
        "side": "bullish",
        "spot": 23500.0,
        "move_pts": 60.0,
        "confirmed_ts_ms": 1_753_330_800_000,
    }
    # Same signal, re-detected — this is what actually happens every poll.
    out = reconcile_session_reversals(
        [dict(legacy)], [legacy], tf="1m", now_ms=1_753_340_000_000
    )
    assert len(out) == 1, "must match the frozen signal, not append a duplicate"
    assert out[0]["emitted_ts_ms"] is None, "a legacy signal must not gain a fake emit time"
