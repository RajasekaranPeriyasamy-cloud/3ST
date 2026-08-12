"""Delta-velocity feature and archive tests.

The feature is equation 1 of arXiv 2608.05373. Every downstream number depends
on it, so the blanking rules and the day-grouping are pinned here rather than
left to inspection.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from analysis.delta_velocity import store
from analysis.delta_velocity.features import (
    GROUP_KEYS,
    SMOOTH_N,
    compute_delta_velocity,
    delta_velocity_series,
    normalized_velocity,
    summarize_by_dte,
)


def _series(n: int, *, start="2026-08-10 09:15", step_min=1.0, delta_step=0.01):
    ts = [pd.Timestamp(start) + timedelta(minutes=step_min * i) for i in range(n)]
    delta = [0.50 + delta_step * i for i in range(n)]
    return pd.Series(ts), pd.Series(delta)


def test_velocity_is_zero_for_constant_delta():
    ts, _ = _series(40)
    flat = pd.Series([0.5] * 40)
    v, _ = delta_velocity_series(ts, flat)
    assert not v.empty
    assert v.abs().max() == pytest.approx(0.0, abs=1e-12)


def test_velocity_matches_hand_computation():
    """A constant per-minute Delta step gives a constant smoothed velocity."""
    ts, delta = _series(40, delta_step=0.01)
    v, _ = delta_velocity_series(ts, delta)
    assert v.abs().sub(0.01).abs().max() < 1e-9


def test_absolute_value_direction_does_not_matter():
    ts, up = _series(40, delta_step=0.01)
    _, down = _series(40, delta_step=-0.01)
    v_up, _ = delta_velocity_series(ts, up)
    v_down, _ = delta_velocity_series(ts, down)
    assert v_up.to_numpy() == pytest.approx(v_down.to_numpy())


def test_needs_full_smoothing_window():
    """Fewer than SMOOTH_N+1 observations yields nothing, not a short-window mean."""
    ts, delta = _series(SMOOTH_N)
    v, counts = delta_velocity_series(ts, delta)
    assert v.empty
    assert counts["insufficient_window"] == SMOOTH_N


def test_gap_longer_than_two_minutes_is_blanked():
    ts, delta = _series(40)
    shifted = ts.copy()
    shifted[30:] = shifted[30:] + timedelta(minutes=5)
    v, counts = delta_velocity_series(shifted, delta)
    assert counts["long_gap"] == 1
    assert pd.Timestamp(shifted[30]) not in set(v.index)


def test_gap_shorter_than_half_a_minute_is_blanked():
    ts, delta = _series(40, step_min=0.25)
    v, counts = delta_velocity_series(ts, delta)
    assert counts["short_gap"] > 0
    assert v.empty


def test_missing_delta_counted_and_excluded():
    ts, delta = _series(40)
    delta[5] = None
    delta[6] = None
    _, counts = delta_velocity_series(ts, delta)
    assert counts["no_delta"] == 2


def test_overnight_gap_cannot_produce_velocity():
    """The whole point of grouping by session date.

    Two sessions of the same contract, 17 hours apart. Without day-grouping the
    first bar of day two differences against the last bar of day one and
    produces a large spurious velocity.
    """
    rows = []
    for day, base in (("2026-08-10", "2026-08-10 09:15"), ("2026-08-11", "2026-08-11 09:15")):
        ts, delta = _series(30, start=base)
        for t, d in zip(ts, delta, strict=True):
            rows.append(
                {
                    "session_date": day,
                    "underlying": "NIFTY",
                    "expiry": "2026-08-18",
                    "strike": 24500.0,
                    "option_type": "CE",
                    "ts": t,
                    "delta": d,
                }
            )
    out, _ = compute_delta_velocity(pd.DataFrame(rows))
    assert not out.empty
    # A leaked overnight difference would be orders of magnitude above the
    # steady 0.01/min; anything near that ceiling means grouping broke.
    assert out["v_t"].max() < 0.02
    assert set(out["session_date"]) == {"2026-08-10", "2026-08-11"}


def test_groups_are_kept_separate():
    rows = []
    for strike, step in ((24500.0, 0.01), (24600.0, 0.02)):
        ts, delta = _series(30, delta_step=step)
        for t, d in zip(ts, delta, strict=True):
            rows.append(
                {
                    "session_date": "2026-08-10",
                    "underlying": "NIFTY",
                    "expiry": "2026-08-18",
                    "strike": strike,
                    "option_type": "CE",
                    "ts": t,
                    "delta": d,
                }
            )
    out, _ = compute_delta_velocity(pd.DataFrame(rows))
    by_strike = out.groupby("strike")["v_t"].mean()
    assert by_strike[24600.0] == pytest.approx(2 * by_strike[24500.0], rel=1e-6)


def test_compute_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing columns"):
        compute_delta_velocity(pd.DataFrame({"ts": [1], "delta": [0.5]}))


def test_empty_input_returns_empty_frame():
    out, counts = compute_delta_velocity(pd.DataFrame())
    assert out.empty
    assert set(out.columns) >= set(GROUP_KEYS)
    assert all(v == 0 for v in counts.values())


def test_normalized_velocity_ranks_within_session():
    out = pd.DataFrame(
        {
            "session_date": ["d1", "d1", "d2", "d2"],
            "v_t": [1.0, 2.0, 100.0, 200.0],
        }
    )
    ranked = normalized_velocity(out)
    assert ranked["v_pct"].tolist() == [0.5, 1.0, 0.5, 1.0]


def test_summarize_by_dte_derives_dte():
    out = pd.DataFrame(
        {
            "session_date": ["2026-08-10"] * 4,
            "expiry": ["2026-08-11"] * 2 + ["2026-08-18"] * 2,
            "v_t": [0.001, 0.002, 0.003, 0.004],
        }
    )
    table = summarize_by_dte(out)
    assert sorted(table["dte"].tolist()) == [1, 8]


def test_store_roundtrip_and_flatten(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    snap = {
        "ts": "2026-08-10T09:20:00+05:30",
        "session_date": "2026-08-10",
        "underlying": "NIFTY",
        "spot": 24500.0,
        "legs": [
            {"expiry": "2026-08-11", "strike": 24500.0, "option_type": "CE", "delta": 0.51, "iv": 0.12, "ltp": 80.0},
        ],
    }
    store.append_snapshot("NIFTY", snap)
    loaded = store.load_session("NIFTY", date(2026, 8, 10))
    assert len(loaded) == 1
    rows = store.to_rows(loaded)
    assert rows[0]["strike"] == 24500.0
    assert rows[0]["spot"] == 24500.0
    assert rows[0]["underlying"] == "NIFTY"


def test_store_skips_truncated_line(tmp_path, monkeypatch):
    """A crash mid-write costs one minute, not the whole session."""
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    snap = {"ts": "2026-08-10T09:20:00", "session_date": "2026-08-10", "underlying": "NIFTY", "legs": []}
    store.append_snapshot("NIFTY", snap)
    path = store.session_file("NIFTY", date(2026, 8, 10))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write('{"ts": "2026-08-10T09:21:00", "leg')
    assert len(store.load_session("NIFTY", date(2026, 8, 10))) == 1


def test_prune_raw_respects_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    today = datetime.now(tz=store.IST).date()
    old = today - timedelta(days=90)
    for d in (old, today):
        snap = {"ts": f"{d}T09:20:00", "session_date": d.isoformat(), "underlying": "NIFTY", "legs": []}
        store.append_snapshot("NIFTY", snap)
    removed = store.prune_raw("NIFTY", retention_days=30)
    assert removed == [old.isoformat()]
    assert store.sessions_available("NIFTY") == [today]


def test_collector_never_reaches_broker_or_execution():
    """Analysis desks must not import order-placing code (CLAUDE.md invariant)."""
    import analysis.delta_velocity.collector as mod

    source = open(mod.__file__, encoding="utf-8").read()
    for forbidden in ("from broker", "import broker", "from execution", "import execution", "from risk", "import risk"):
        assert forbidden not in source, f"collector must not reference {forbidden}"


def test_session_gate_rejects_weekends():
    from analysis.delta_velocity import collector

    saturday = datetime(2026, 8, 8, 11, 0, tzinfo=store.IST)
    monday = datetime(2026, 8, 10, 11, 0, tzinfo=store.IST)
    assert collector.in_session(saturday) is False
    assert collector.in_session(monday) is True


def test_session_gate_rejects_pre_open_and_post_close():
    from analysis.delta_velocity import collector

    assert collector.in_session(datetime(2026, 8, 10, 8, 0, tzinfo=store.IST)) is False
    assert collector.in_session(datetime(2026, 8, 10, 16, 30, tzinfo=store.IST)) is False


def test_mid_preferred_over_last_price():
    from analysis.delta_velocity.collector import _mid_or_last

    quote = {"last_price": 100.0, "depth": {"buy": [{"price": 90.0}], "sell": [{"price": 94.0}]}}
    assert _mid_or_last(quote) == pytest.approx(92.0)


def test_falls_back_to_last_price_on_one_sided_depth():
    from analysis.delta_velocity.collector import _mid_or_last

    quote = {"last_price": 100.0, "depth": {"buy": [{"price": 90.0}], "sell": [{"price": 0}]}}
    assert _mid_or_last(quote) == pytest.approx(100.0)


def test_no_price_returns_none():
    from analysis.delta_velocity.collector import _mid_or_last

    assert _mid_or_last({"last_price": 0, "depth": {}}) is None


def test_build_snapshot_uses_per_minute_spot():
    """Two spots, same option price, must give different Delta.

    This is the straddle_watch.py:412 defect the engine exists partly to avoid:
    holding spot constant across a session removes the spot term entirely.
    """
    from analysis.delta_velocity.collector import build_snapshot

    legs = [{"key": "NFO:X", "tradingsymbol": "X", "exchange": "NFO",
             "expiry": (date.today() + timedelta(days=7)).isoformat(),
             "strike": 24500.0, "option_type": "CE"}]
    quotes = {"NFO:X": {"last_price": 200.0, "depth": {}}}
    now = datetime.now(tz=store.IST)

    low = build_snapshot("NIFTY", 24450.0, legs, quotes, now=now)["legs"][0]["delta"]
    high = build_snapshot("NIFTY", 24550.0, legs, quotes, now=now)["legs"][0]["delta"]
    assert low is not None and high is not None
    assert high > low


def test_sub_intrinsic_price_yields_no_delta():
    """A call quoted below intrinsic has no implied volatility, so no Delta.

    Blanking it is correct — the alternative is a fabricated Delta feeding the
    velocity series. features.delta_velocity_series counts these as 'no_delta'.
    """
    from analysis.delta_velocity.collector import build_snapshot

    legs = [{"key": "NFO:X", "tradingsymbol": "X", "exchange": "NFO",
             "expiry": (date.today() + timedelta(days=7)).isoformat(),
             "strike": 24500.0, "option_type": "CE"}]
    snap = build_snapshot("NIFTY", 24700.0, legs, {"NFO:X": {"last_price": 120.0, "depth": {}}})
    assert snap["legs"][0]["delta"] is None
    assert snap["legs_valid"] == 0


def test_snapshot_is_json_serializable():
    from analysis.delta_velocity.collector import build_snapshot

    legs = [{"key": "NFO:X", "tradingsymbol": "X", "exchange": "NFO",
             "expiry": (date.today() + timedelta(days=7)).isoformat(),
             "strike": 24500.0, "option_type": "CE"}]
    snap = build_snapshot("NIFTY", 24500.0, legs, {"NFO:X": {"last_price": 120.0, "depth": {}}})
    json.dumps(snap, default=str)


# --------------------------------------------------------------------------
# chart aggregation + lag correlation
# --------------------------------------------------------------------------


def _write_session(tmp_path, monkeypatch, *, minutes=90, spot_fn=None, delta_fn=None):
    """Archive a synthetic session: 2 strikes x CE/PE, one snapshot per minute."""
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    base = datetime(2026, 8, 10, 9, 15, tzinfo=store.IST)
    for i in range(minutes):
        ts = base + timedelta(minutes=i)
        spot = spot_fn(i) if spot_fn else 24500.0 + i
        legs = []
        for strike in (24500.0, 24550.0):
            for opt in ("CE", "PE"):
                d = delta_fn(i, strike, opt) if delta_fn else 0.5 + 0.001 * i
                legs.append({"expiry": "2026-08-18", "strike": strike,
                             "option_type": opt, "delta": d, "iv": 0.12, "ltp": 100.0})
        store.append_snapshot("NIFTY", {
            "ts": ts.isoformat(), "session_date": "2026-08-10",
            "underlying": "NIFTY", "spot": spot, "legs": legs,
        })


def test_chart_returns_one_row_per_minute(tmp_path, monkeypatch):
    from analysis.delta_velocity import chart

    _write_session(tmp_path, monkeypatch, minutes=90)
    out = chart.session_chart("NIFTY", date(2026, 8, 10))
    assert len(out["minutes"]) == 90
    assert out["contracts"] == 4
    assert out["atm_strike"] in (24500.0, 24550.0)
    assert all("spot" in m and "v_max" in m for m in out["minutes"])


def test_chart_empty_session_is_not_an_error(tmp_path, monkeypatch):
    from analysis.delta_velocity import chart

    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    out = chart.session_chart("NIFTY", date(2026, 8, 10))
    assert out["minutes"] == []
    assert out["correlation"]["interpretation"] == "no data"


def test_chart_v_max_is_at_least_v_med(tmp_path, monkeypatch):
    from analysis.delta_velocity import chart

    def delta(i, strike, opt):
        return 0.5 + (0.002 if strike == 24500.0 else 0.0005) * i

    _write_session(tmp_path, monkeypatch, minutes=90, delta_fn=delta)
    out = chart.session_chart("NIFTY", date(2026, 8, 10))
    pairs = [(m["v_max"], m["v_med"]) for m in out["minutes"] if m["v_max"] is not None]
    assert pairs
    assert all(mx >= md for mx, md in pairs)


def test_lag_sign_convention_positive_means_velocity_leads():
    """Pin the sign. v_t built to move 3 minutes BEFORE spot must report +3."""
    from analysis.delta_velocity import chart

    n = 400
    pulse = [0.0] * n
    for centre in (100, 200, 300):
        pulse[centre] = 1.0
    move = pd.Series(pulse)
    velocity = pd.Series(pulse).shift(-3).fillna(0.0)

    profile = chart.lag_profile(move, velocity, max_lag=6)
    scored = [p for p in profile if p["corr"] is not None]
    best = max(scored, key=lambda p: abs(p["corr"]))
    assert best["lag_min"] == 3
    assert chart.describe_lag(3) == "v_t leads by 3 min"
    assert chart.describe_lag(-2) == "v_t lags by 2 min"
    assert chart.describe_lag(0) == "coincident"


def test_lag_profile_refuses_short_samples():
    from analysis.delta_velocity import chart

    short = pd.Series(range(10), dtype=float)
    profile = chart.lag_profile(short, short, max_lag=2)
    assert all(p["corr"] is None for p in profile)


def test_correlation_reports_insufficient_data_early_in_session(tmp_path, monkeypatch):
    from analysis.delta_velocity import chart

    _write_session(tmp_path, monkeypatch, minutes=40)
    out = chart.session_chart("NIFTY", date(2026, 8, 10))
    assert out["correlation"]["best_lag"] is None
    assert "insufficient data" in out["correlation"]["interpretation"]


def test_chart_expiry_filter_narrows_contracts(tmp_path, monkeypatch):
    from analysis.delta_velocity import chart

    _write_session(tmp_path, monkeypatch, minutes=90)
    out = chart.session_chart("NIFTY", date(2026, 8, 10), expiry="2026-08-18")
    assert out["contracts"] == 4
    missing = chart.session_chart("NIFTY", date(2026, 8, 10), expiry="2099-01-01")
    assert missing["minutes"] == []


def test_chart_thresholds_present_once_velocity_exists(tmp_path, monkeypatch):
    from analysis.delta_velocity import chart

    _write_session(tmp_path, monkeypatch, minutes=90)
    out = chart.session_chart("NIFTY", date(2026, 8, 10))
    assert out["thresholds"]["p95"] is not None
    assert out["thresholds"]["p99"] >= out["thresholds"]["p95"]


# --------------------------------------------------------------------------
# premium ladder + context tiles
# --------------------------------------------------------------------------


def test_to_rows_still_returns_every_prior_field(tmp_path, monkeypatch):
    """Regression on the oi/volume extension — nothing may be dropped."""
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    store.append_snapshot("NIFTY", {
        "ts": "2026-08-10T09:20:00+05:30", "session_date": "2026-08-10",
        "underlying": "NIFTY", "spot": 24500.0,
        "legs": [{"expiry": "2026-08-11", "strike": 24500.0, "option_type": "CE",
                  "delta": 0.51, "iv": 0.12, "ltp": 80.0, "oi": 1234.0, "volume": 99.0}],
    })
    row = store.to_rows(store.load_session("NIFTY", date(2026, 8, 10)))[0]
    for field in ("ts", "session_date", "underlying", "expiry", "strike",
                  "option_type", "delta", "iv", "ltp", "spot"):
        assert field in row, f"to_rows dropped {field}"
    assert row["oi"] == 1234.0
    assert row["volume"] == 99.0


def _ladder_rows(*, minutes=30, spot_fn=None, drop_after=None):
    """ATM+/-5 CE/PE on one expiry, one row per minute per leg."""
    base = datetime(2026, 8, 10, 9, 15, tzinfo=store.IST)
    out = []
    for i in range(minutes):
        ts = base + timedelta(minutes=i)
        spot = spot_fn(i) if spot_fn else 24500.0
        for k in range(-5, 6):
            strike = 24500.0 + k * 50
            for opt in ("CE", "PE"):
                if drop_after is not None and strike == 24750.0 and i > drop_after:
                    continue
                out.append({
                    "ts": ts, "session_date": "2026-08-10", "underlying": "NIFTY",
                    "expiry": "2026-08-11", "strike": strike, "option_type": opt,
                    "delta": 0.5, "iv": 0.12, "ltp": 100.0 + i, "oi": 1000.0 if opt == "CE" else 800.0,
                    "volume": 10.0, "spot": spot,
                })
    return pd.DataFrame(out)


def test_ladder_has_twelve_series_atm_and_otm_only():
    from analysis.delta_velocity import chart

    out = chart.session_ladder(_ladder_rows(), "NIFTY")
    labels = [s["label"] for s in out["series"]]
    assert len(labels) == 12
    assert "ATM CE" in labels and "ATM PE" in labels
    assert "OTM CE 5" in labels and "OTM PE 5" in labels
    ce = [s for s in out["series"] if s["option_type"] == "CE" and s["offset"] > 0]
    pe = [s for s in out["series"] if s["option_type"] == "PE" and s["offset"] < 0]
    assert all(s["strike"] > out["atm_at_open"] for s in ce), "OTM calls must sit above ATM"
    assert all(s["strike"] < out["atm_at_open"] for s in pe), "OTM puts must sit below ATM"


def test_ladder_change_is_zero_at_baseline():
    from analysis.delta_velocity import chart

    out = chart.session_ladder(_ladder_rows(), "NIFTY")
    for s in out["series"]:
        assert s["points"][0]["change"] == 0.0
    assert out["series"][0]["points"][-1]["change"] == pytest.approx(29.0)


def test_ladder_pins_to_open_atm_when_spot_drifts():
    """Spot moving two strikes must not re-strike the ladder mid-session.

    Re-striking would make a series jump contracts and display a premium change
    nobody traded.
    """
    from analysis.delta_velocity import chart

    drifting = _ladder_rows(minutes=30, spot_fn=lambda i: 24500.0 + i * 5)
    out = chart.session_ladder(drifting, "NIFTY")
    assert out["atm_at_open"] == 24500.0
    assert next(s for s in out["series"] if s["label"] == "ATM CE")["strike"] == 24500.0


def test_ladder_returns_partial_series_rather_than_dropping():
    """A strike that leaves the tracked window keeps the points it had."""
    from analysis.delta_velocity import chart

    out = chart.session_ladder(_ladder_rows(minutes=30, drop_after=10), "NIFTY")
    partial = next(s for s in out["series"] if s["strike"] == 24750.0)
    assert 0 < len(partial["points"]) < 30


def test_ladder_marks_series_that_missed_the_baseline():
    from analysis.delta_velocity import chart

    rows = _ladder_rows(minutes=30)
    late = rows[~((rows["strike"] == 24750.0) & (rows["ts"] == rows["ts"].min()))]
    out = chart.session_ladder(late, "NIFTY")
    series = next(s for s in out["series"] if s["strike"] == 24750.0)
    assert series["baseline_at_open"] is False


def test_baseline_at_open_is_not_coverage():
    """A strike can be baselined at the open and still leave the window.

    Observed live 2026-08-11: OTM CE 5 was present at 09:15 and held only 2 of
    161 minutes as spot fell away from it. Conflating the two would report that
    series as fully covered.
    """
    from analysis.delta_velocity import chart

    out = chart.session_ladder(_ladder_rows(minutes=30, drop_after=10), "NIFTY")
    series = next(s for s in out["series"] if s["strike"] == 24750.0)
    assert series["baseline_at_open"] is True
    assert series["coverage"] < 30


def test_ladder_empty_input_is_not_an_error():
    from analysis.delta_velocity import chart

    out = chart.session_ladder(pd.DataFrame(), "NIFTY")
    assert out["series"] == []
    assert out["step"] == 50


def test_context_reports_spot_move_straddle_and_pcr():
    from analysis.delta_velocity import chart

    rows = _ladder_rows(minutes=30, spot_fn=lambda i: 24500.0 + i)
    ctx = chart.session_context(rows, "NIFTY")
    assert ctx["spot"] == pytest.approx(24529.0)
    assert ctx["spot_change"] == pytest.approx(29.0)
    assert ctx["straddle"] == pytest.approx(258.0)
    assert ctx["pcr"] == pytest.approx(0.8)


def test_context_labels_its_scope():
    """PCR here is over the tracked window, not the chain — it must say so."""
    from analysis.delta_velocity import chart

    ctx = chart.session_context(_ladder_rows(), "NIFTY", expiry="2026-08-11")
    assert "ATM+/-5" in ctx["scope"]
    assert "2026-08-11" in ctx["scope"]


def test_chart_payload_carries_ladder_and_context(tmp_path, monkeypatch):
    from analysis.delta_velocity import chart

    _write_session(tmp_path, monkeypatch, minutes=90)
    out = chart.session_chart("NIFTY", date(2026, 8, 10))
    assert "ladder" in out and "context" in out
    assert out["context"]["scope"] is not None


def test_chart_ladder_uses_one_expiry_only(tmp_path, monkeypatch):
    """Three tracked expiries must not turn 12 series into 36."""
    from analysis.delta_velocity import chart

    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    base = datetime(2026, 8, 10, 9, 15, tzinfo=store.IST)
    for i in range(40):
        legs = []
        for exp in ("2026-08-11", "2026-08-18", "2026-08-25"):
            for k in range(-5, 6):
                for opt in ("CE", "PE"):
                    legs.append({"expiry": exp, "strike": 24500.0 + k * 50, "option_type": opt,
                                 "delta": 0.5 + 0.001 * i, "iv": 0.12, "ltp": 100.0 + i,
                                 "oi": 1000.0, "volume": 1.0})
        store.append_snapshot("NIFTY", {
            "ts": (base + timedelta(minutes=i)).isoformat(), "session_date": "2026-08-10",
            "underlying": "NIFTY", "spot": 24500.0, "legs": legs,
        })
    out = chart.session_chart("NIFTY", date(2026, 8, 10))
    assert len(out["ladder"]["series"]) == 12


def test_chart_lists_session_expiries(tmp_path, monkeypatch):
    """The selector needs every archived expiry, not just the filtered one."""
    from analysis.delta_velocity import chart

    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    base = datetime(2026, 8, 10, 9, 15, tzinfo=store.IST)
    for i in range(40):
        legs = [
            {"expiry": exp, "strike": 24500.0, "option_type": opt,
             "delta": 0.5 + 0.001 * i, "iv": 0.12, "ltp": 100.0 + i, "oi": 1000.0, "volume": 1.0}
            for exp in ("2026-08-11", "2026-08-18") for opt in ("CE", "PE")
        ]
        store.append_snapshot("NIFTY", {
            "ts": (base + timedelta(minutes=i)).isoformat(), "session_date": "2026-08-10",
            "underlying": "NIFTY", "spot": 24500.0, "legs": legs,
        })

    pooled = chart.session_chart("NIFTY", date(2026, 8, 10))
    assert pooled["expiries"] == ["2026-08-11", "2026-08-18"]
    assert pooled["selected_expiry"] is None

    filtered = chart.session_chart("NIFTY", date(2026, 8, 10), expiry="2026-08-18")
    assert filtered["selected_expiry"] == "2026-08-18"
    # Still lists both, or the selector loses its other options after one click.
    assert filtered["expiries"] == ["2026-08-11", "2026-08-18"]
    assert filtered["contracts"] < pooled["contracts"]


def test_latest_session_prefers_newest_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    assert store.latest_session("NIFTY") is None
    for d in ("2026-08-10", "2026-08-11"):
        store.append_snapshot("NIFTY", {"ts": f"{d}T09:20:00", "session_date": d,
                                        "underlying": "NIFTY", "spot": 1.0, "legs": []})
    assert store.latest_session("NIFTY") == date(2026, 8, 11)


def test_chart_defaults_to_latest_session_not_today(tmp_path, monkeypatch):
    """Pre-open, today has no file — defaulting to it renders an empty desk.

    Observed 2026-08-12 08:54: coverage reported 161 archived minutes while the
    chart returned zero contracts, because the two resolved different days.
    """
    from analysis.delta_velocity import chart

    _write_session(tmp_path, monkeypatch, minutes=90)  # writes 2026-08-10 only
    out = chart.session_chart("NIFTY")
    assert out["session_date"] == "2026-08-10"
    assert out["minutes"], "default must resolve to the archived session"
    assert out["contracts"] > 0


def test_chart_with_no_archive_at_all_is_still_safe(tmp_path, monkeypatch):
    from analysis.delta_velocity import chart

    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    out = chart.session_chart("NIFTY")
    assert out["minutes"] == []
    assert out["contracts"] == 0
