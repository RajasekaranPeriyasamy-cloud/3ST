"""Tests for the theta-decay desk.

The load-bearing ones, in rough order of what would hurt most if it broke:

* ``black_scholes_greeks`` matches ``options.greeks_engine.compute_greeks``.
  The vectorised copy exists only for speed; if it drifts, every number on the
  page is quietly wrong and nothing else would catch it.
* Greeks are derived at q=0, never read from the archive's q=0.012 columns.
* ``capture_ratio``'s quality gate labels the buckets that are not worth reading.
* Burn rate reproduces the analytic ``theta/premium``, and blanks penny wings.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from analysis.theta_decay import chart
from analysis.theta_decay import features as F
from options.greeks_engine import compute_greeks

IST = ZoneInfo("Asia/Kolkata")


# --------------------------------------------------------------------------
# parity with the scalar engine
# --------------------------------------------------------------------------

def test_vectorised_greeks_match_compute_greeks():
    """The whole justification for the numpy copy is that it agrees."""
    rng = np.random.default_rng(11)
    for _ in range(300):
        spot = float(rng.uniform(15_000, 80_000))
        strike = spot * float(rng.uniform(0.85, 1.15))
        tte = float(rng.uniform(1 / (365 * 24), 0.30))
        iv = float(rng.uniform(0.05, 0.60))
        call = bool(rng.integers(0, 2))

        scalar = compute_greeks(
            spot=spot, strike=strike, tte_years=tte, iv=iv,
            option_type="CE" if call else "PE",
            risk_free_rate=F.RISK_FREE, dividend_yield=F.DIVIDEND_YIELD,
            theta_mode="calendar",
        )
        vec = F.black_scholes_greeks(
            np.array([spot]), np.array([strike]), np.array([tte]),
            np.array([iv]), np.array([call]),
        )
        # compute_greeks rounds: theta/vega/delta to 6dp, gamma to 8dp.
        assert vec["theta"][0] == pytest.approx(scalar["theta"], abs=1e-6)
        assert vec["vega"][0] == pytest.approx(scalar["vega"], abs=1e-6)
        assert vec[F.ATTRIB_DELTA][0] == pytest.approx(scalar["delta"], abs=1e-6)
        assert vec["gamma"][0] == pytest.approx(scalar["gamma"], abs=1e-8)


def test_invalid_inputs_produce_nan_not_exceptions():
    out = F.black_scholes_greeks(
        np.array([100.0, 0.0, 100.0, 100.0]),
        np.array([100.0, 100.0, 0.0, 100.0]),
        np.array([0.1, 0.1, 0.1, -1.0]),
        np.array([0.2, 0.2, 0.2, 0.2]),
        np.array([True, True, True, True]),
    )
    assert np.isfinite(out["theta"][0])
    assert np.isnan(out["theta"][1:]).all()


def test_call_and_put_theta_are_both_negative_without_carry():
    """At q=0 and a positive rate, long options bleed. A sign flip here would
    invert every burn rate on the page."""
    out = F.black_scholes_greeks(
        np.array([24_000.0, 24_000.0]), np.array([24_000.0, 24_000.0]),
        np.array([0.02, 0.02]), np.array([0.12, 0.12]), np.array([True, False]),
    )
    assert (out["theta"] < 0).all()


# --------------------------------------------------------------------------
# the q=0 convention
# --------------------------------------------------------------------------

def _rows(n=8, *, premium=100.0, iv=0.12, spot=24_000.0, strike=24_000.0, start=None):
    start = start or datetime(2026, 8, 12, 9, 15, tzinfo=IST)
    return pd.DataFrame(
        {
            "ts": [start + timedelta(minutes=i) for i in range(n)],
            "session_date": ["2026-08-12"] * n,
            "underlying": ["NIFTY"] * n,
            "expiry": ["2026-08-18"] * n,
            "strike": [strike] * n,
            "option_type": ["CE"] * n,
            "ltp": [premium] * n,
            "iv": [iv] * n,
            "spot": [spot] * n,
        }
    )


def test_ensure_greeks_ignores_an_archived_theta():
    """Archived greeks carry the collector's q=0.012; this desk is q=0.

    Trusting a stored column would silently mix conventions, so ``ensure_greeks``
    always re-derives. A sentinel value must not survive.
    """
    rows = _rows()
    rows["theta"] = -999.0
    out = F.ensure_greeks(rows)
    assert (out["theta"] != -999.0).all()
    assert (out["theta"] < 0).all()


def test_dividend_yield_is_zero_not_the_greeks_engine_default():
    from config import GREEKS_ENGINE_DEFAULTS

    assert F.DIVIDEND_YIELD == 0.0
    assert GREEKS_ENGINE_DEFAULTS["dividend_yield"] != F.DIVIDEND_YIELD, (
        "this test exists to pin the deliberate divergence; if the engine default "
        "became 0 the comment in features.py needs revisiting, not this assert"
    )


def test_carry_convention_materially_changes_theta():
    """Guards the claim in the module docstring that q matters for theta."""
    args = {
        "spot": 24_000.0, "strike": 24_000.0, "tte_years": 6 / 365, "iv": 0.12,
        "option_type": "CE", "risk_free_rate": F.RISK_FREE, "theta_mode": "calendar",
    }
    at_zero = compute_greeks(**args, dividend_yield=0.0)["theta"]
    at_default = compute_greeks(**args, dividend_yield=0.012)["theta"]
    assert abs(at_default - at_zero) / abs(at_zero) > 0.02


# --------------------------------------------------------------------------
# burn rate
# --------------------------------------------------------------------------

def test_burn_rate_is_theta_over_premium_flipped_positive():
    rows = _rows(premium=100.0)
    out = F.burn_rate(rows)
    expected = -out["theta"] / 100.0 * 100.0
    assert out["burn_pct_day"].to_numpy() == pytest.approx(expected.to_numpy())
    assert (out["burn_pct_day"] > 0).all()


def test_penny_wings_are_blanked_not_plotted():
    """A 2-rupee option's theta/premium is noise with a decimal point."""
    cheap = F.burn_rate(_rows(premium=F.MIN_PREMIUM - 0.01))
    rich = F.burn_rate(_rows(premium=F.MIN_PREMIUM + 1.0))
    assert cheap["burn_pct_day"].isna().all()
    assert rich["burn_pct_day"].notna().all()


def test_burn_rate_scales_roughly_as_one_over_time():
    """ATM theta ~ sigma/sqrt(T) and ATM premium ~ sigma*sqrt(T), so theta/P ~ 1/T.

    This is the relationship measured on the live archive — 8.13%/day at 6 DTE
    against 3.79% at 13, a ratio of 2.14 where 1/T predicts 2.17 — and it is
    what makes the metric trustworthy. Both legs must be priced from the model,
    not held at a fixed premium, or the denominator cancels the effect.
    """
    ts = datetime(2026, 8, 12, 9, 15, tzinfo=IST)
    out = {}
    for label, expiry in (("near", "2026-08-18"), ("far", "2026-09-15")):
        rows = _rows(2, strike=24_000.0, spot=24_000.0)
        rows["expiry"] = expiry
        tte = F.scalar_tte(expiry, ts)
        rows["ltp"] = _bs_call(24_000.0, 24_000.0, tte, 0.12)
        out[label] = (F.burn_rate(rows)["burn_pct_day"].iloc[0], tte)

    observed = out["near"][0] / out["far"][0]
    predicted = out["far"][1] / out["near"][1]  # 1/T scaling
    assert observed == pytest.approx(predicted, rel=0.15)


# --------------------------------------------------------------------------
# decay attribution + capture
# --------------------------------------------------------------------------

def test_attribution_needs_two_observations_a_horizon_apart():
    """The first valid pair is minute 0 against minute H, so H+1 samples."""
    assert F.decay_attribution(_rows(30), horizon_min=60).empty
    assert len(F.decay_attribution(_rows(61), horizon_min=60)) == 1
    assert len(F.decay_attribution(_rows(121), horizon_min=60)) == 2


def test_pure_time_passage_gives_capture_of_one():
    """With spot and IV frozen, the only thing moving premium is the clock.

    Price the option from the model at each minute so realized decay is exactly
    theoretical decay; capture must then be 1.0. This is the sharpest single
    test of the attribution's sign and unit conventions — a per-day vs per-year
    theta mix-up, or a 1440-vs-375 minute error, shows up here immediately.
    """
    n, start = 121, datetime(2026, 8, 12, 9, 15, tzinfo=IST)
    ts = [start + timedelta(minutes=i) for i in range(n)]
    spot = strike = 24_000.0
    iv = 0.12
    prices = []
    for t in ts:
        tte = F.scalar_tte("2026-08-18", t)
        prices.append(_bs_call(spot, strike, tte, iv))

    rows = _rows(n)
    rows["ts"] = ts
    rows["ltp"] = prices
    attribution = F.decay_attribution(rows, horizon_min=60)
    assert not attribution.empty
    assert F.capture_ratio(attribution) == pytest.approx(1.0, abs=0.02)


def _bs_call(spot: float, strike: float, tte: float, iv: float) -> float:
    """Black-Scholes call price under the module's own conventions (q=0)."""
    import math

    r = F.RISK_FREE
    d1 = (math.log(spot / strike) + (r + 0.5 * iv * iv) * tte) / (iv * math.sqrt(tte))
    d2 = d1 - iv * math.sqrt(tte)
    nd = lambda x: 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))  # noqa: E731
    return spot * nd(d1) - strike * math.exp(-r * tte) * nd(d2)


def test_a_spot_move_is_attributed_to_delta_not_to_time():
    """If a jump in spot leaked into time_pnl, capture would blow up."""
    n, start = 121, datetime(2026, 8, 12, 9, 15, tzinfo=IST)
    ts = [start + timedelta(minutes=i) for i in range(n)]
    spots, prices = [], []
    for i, t in enumerate(ts):
        spot = 24_000.0 + (150.0 if i >= 60 else 0.0)
        tte = F.scalar_tte("2026-08-18", t)
        spots.append(spot)
        prices.append(_bs_call(spot, 24_000.0, tte, 0.12))

    rows = _rows(n)
    rows["ts"], rows["spot"], rows["ltp"] = ts, spots, prices
    attribution = F.decay_attribution(rows, horizon_min=60)
    # A 150-point move dwarfs an hour of decay; without correct attribution the
    # ratio would be orders of magnitude off rather than merely imprecise.
    assert abs(F.capture_ratio(attribution)) < 5.0


def test_capture_quality_flags_the_unreadable_buckets():
    plenty = F.MIN_WINDOWS_FOR_QUALITY
    assert F.capture_quality(0.02, 0.26, plenty) == "ok"
    assert F.capture_quality(0.003, 1.01, plenty) == "vega_dominated"
    assert F.capture_quality(0.009, 0.30, plenty) == "theta_too_small"
    assert F.capture_quality(None, None, plenty) == "no_data"


def test_vega_domination_is_checked_before_theta_size():
    """A bucket failing both should report the more fundamental problem."""
    assert F.capture_quality(0.001, 0.9, F.MIN_WINDOWS_FOR_QUALITY) == "vega_dominated"


def test_a_thin_sample_is_reported_as_such_not_as_a_vol_problem():
    """Observed live: a 92-minute session put vega_share near 100% in every
    bucket off one window, against 26-29% over a full session. Calling that a
    vol problem sends the reader after something that is really just the hour.
    """
    assert F.capture_quality(0.037, 0.98, 1) == "too_few_windows"
    assert F.capture_quality(0.02, 0.26, 1) == "too_few_windows"


def test_quality_counts_distinct_windows_not_rows():
    """A bucket holds one row per (contract, window). The 22 strikes inside a
    single hour share one spot path and one vol move, so they are one sample
    seen 22 ways — gating on row count would wave that through."""
    ts = pd.to_datetime(["2026-08-12T10:15"], utc=True)
    frame = pd.DataFrame(
        {
            "ts": ts.repeat(22),
            "session_date": ["2026-08-12"] * 22,
            "underlying": ["NIFTY"] * 22,
            "expiry": ["2026-08-18"] * 22,
            "strike": [24_000.0 + 50 * i for i in range(22)],
            "option_type": ["CE"] * 22,
            "d_price": [1.0] * 22,
            "pred_theta": [-0.02] * 22,
            "pred_vega": [0.3] * 22,
            "time_pnl": [-0.02] * 22,
        }
    )
    row = F.attribution_by_dte(frame)[0]
    assert row["windows"] == 22
    assert row["time_windows"] == 1
    assert row["quality"] == "too_few_windows"


def test_capture_ratio_is_a_ratio_of_sums_not_a_mean_of_ratios():
    """One window with near-zero theoretical theta must not dominate."""
    attribution = pd.DataFrame(
        {"time_pnl": [1.0, 1.0, 1.0], "pred_theta": [1.0, 1.0, 1e-12]}
    )
    assert F.capture_ratio(attribution) == pytest.approx(1.5, abs=0.01)


def test_capture_ratio_of_empty_or_zero_theta_is_none():
    assert F.capture_ratio(pd.DataFrame()) is None
    assert F.capture_ratio(pd.DataFrame({"time_pnl": [1.0], "pred_theta": [0.0]})) is None


def test_attribution_rejects_missing_columns():
    with pytest.raises(ValueError, match="missing columns"):
        F.decay_attribution(pd.DataFrame({"ts": [1], "ltp": [2]}))


def test_overnight_gap_cannot_produce_a_window():
    """Two sessions' observations are never a valid 60-minute pair."""
    day1 = _rows(2, start=datetime(2026, 8, 12, 15, 0, tzinfo=IST))
    day2 = _rows(2, start=datetime(2026, 8, 13, 9, 15, tzinfo=IST))
    day2["session_date"] = "2026-08-13"
    out = F.decay_attribution(pd.concat([day1, day2], ignore_index=True), horizon_min=1)
    # Grouping includes session_date, so no row can straddle the two days.
    assert out.empty or (out["gap_min"] < 60).all()


# --------------------------------------------------------------------------
# theta velocity (the delta-velocity analogue)
# --------------------------------------------------------------------------

def test_frozen_spot_and_iv_give_near_zero_clock_removed_velocity():
    """The whole point of subtracting expected theta.

    With spot and IV constant, every change in theta is pure time passage, so
    the clock-removed velocity must collapse to ~0. A naive |d_theta|/dt would
    report the deterministic ramp instead.
    """
    rows = _rows(60)
    velocity, _ = F.compute_theta_velocity(rows, smooth_n=5)
    assert not velocity.empty
    assert velocity["tau_t"].abs().max() < 1e-6


def test_an_iv_jump_registers_as_theta_velocity():
    rows = _rows(60)
    rows.loc[30:, "iv"] = 0.18
    velocity, _ = F.compute_theta_velocity(rows, smooth_n=5)
    assert velocity["tau_t"].max() > 1e-3


def test_velocity_needs_a_full_smoothing_window():
    velocity, counts = F.compute_theta_velocity(_rows(10), smooth_n=30)
    assert velocity.empty
    assert counts["insufficient_window"] > 0


def test_blank_summary_orders_worst_first():
    text = F.blank_summary({"short_gap": 1, "no_theta": 9, "long_gap": 0})
    assert text.startswith("no_theta=9")
    assert "long_gap" not in text


# --------------------------------------------------------------------------
# chart layer
# --------------------------------------------------------------------------

@pytest.fixture()
def archive(tmp_path, monkeypatch):
    """A synthetic two-expiry session on disk, in the shared archive's format."""
    from analysis.delta_velocity import store

    # store.py does `from settings import data_dir` at import time and holds its
    # own reference, so patching settings.data_dir leaves it pointed at the real
    # archive — the same binding trap conftest.py documents for kite_client.
    # Getting this wrong writes synthetic snapshots into live data.
    monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
    chart._CACHE.clear()

    day = date(2026, 8, 12)
    start = datetime(2026, 8, 12, 9, 15, tzinfo=IST)
    for i in range(200):
        ts = start + timedelta(minutes=i)
        spot = 24_000.0 + i * 0.5
        legs = []
        for expiry in ("2026-08-18", "2026-08-25"):
            for strike in (23_950.0, 24_000.0, 24_050.0):
                for opt in ("CE", "PE"):
                    tte = F.scalar_tte(expiry, ts)
                    legs.append(
                        {
                            "tradingsymbol": f"NIFTY{int(strike)}{opt}",
                            "expiry": expiry,
                            "strike": strike,
                            "option_type": opt,
                            "ltp": max(_bs_call(spot, strike, tte, 0.12), 6.0),
                            "oi": 1000,
                            "volume": 10,
                            "iv": 0.12,
                            "delta": 0.5,
                        }
                    )
        store.append_snapshot(
            "NIFTY",
            {
                "ts": ts.isoformat(),
                "session_date": day.isoformat(),
                "underlying": "NIFTY",
                "spot": spot,
                "legs": legs,
                "legs_valid": len(legs),
            },
        )
    yield day
    chart._CACHE.clear()


def test_chart_reports_one_row_per_minute(archive):
    payload = chart.session_chart("NIFTY", archive)
    assert len(payload["minutes"]) == 200
    assert payload["contracts"] == 12
    assert payload["expiries"] == ["2026-08-18", "2026-08-25"]


def test_chart_is_json_serialisable(archive):
    """NaN and numpy scalars must not reach FastAPI."""
    text = json.dumps(chart.session_chart("NIFTY", archive))
    assert "NaN" not in text
    assert "Infinity" not in text


def test_chart_explains_an_empty_capture_table_rather_than_blanking_it(archive):
    """200 archived minutes cannot fill a 250-minute window."""
    payload = chart.session_chart("NIFTY", archive, horizon_min=250)
    assert payload["capture"]["by_dte"] == []
    assert "needs 251 minutes" in payload["capture"]["note"]
    assert "200 collected" in payload["capture"]["note"]


def test_chart_expiry_filter_narrows_contracts(archive):
    both = chart.session_chart("NIFTY", archive)
    one = chart.session_chart("NIFTY", archive, expiry="2026-08-18")
    assert one["contracts"] < both["contracts"]
    assert one["selected_expiry"] == "2026-08-18"


def test_chart_with_no_archive_at_all_is_safe(archive):
    payload = chart.session_chart("NIFTY", date(2020, 1, 1))
    assert payload["minutes"] == []
    assert payload["capture"]["note"] == "no data"


def test_cache_is_invalidated_when_the_session_file_grows(archive, monkeypatch):
    """Today's file grows all session; a stale payload would freeze the page."""
    from analysis.delta_velocity import store

    first = chart.session_chart("NIFTY", archive)
    ts = datetime(2026, 8, 12, 14, 0, tzinfo=IST)
    store.append_snapshot(
        "NIFTY",
        {
            "ts": ts.isoformat(),
            "session_date": archive.isoformat(),
            "underlying": "NIFTY",
            "spot": 24_100.0,
            "legs": [
                {
                    "tradingsymbol": "NIFTY2681824100CE",
                    "expiry": "2026-08-18",
                    "strike": 24_100.0,
                    "option_type": "CE",
                    "ltp": 120.0,
                    "oi": 1000,
                    "volume": 10,
                    "iv": 0.12,
                    "delta": 0.5,
                }
            ],
            "legs_valid": 1,
        },
    )
    assert len(chart.session_chart("NIFTY", archive)["minutes"]) == len(first["minutes"]) + 1


def test_straddle_burn_restrikes_with_spot_unlike_the_delta_ladder(archive):
    """Burn rate is a ratio inside one minute, never differenced across them, so
    following ATM costs nothing — and pinning would answer the wrong question."""
    rows = pd.DataFrame(
        {
            "ts": pd.to_datetime(["2026-08-12T09:15", "2026-08-12T09:16"], utc=True).repeat(2),
            "spot": [23_950.0, 23_950.0, 24_050.0, 24_050.0],
            "strike": [23_950.0, 23_950.0, 24_050.0, 24_050.0],
            "option_type": ["CE", "PE", "CE", "PE"],
            "theta": [-8.0, -8.0, -8.0, -8.0],
            "ltp": [100.0, 100.0, 100.0, 100.0],
        }
    )
    out = chart.straddle_burn(rows, "NIFTY")
    assert len(out) == 2, "both minutes resolve, each at its own ATM strike"


def test_burn_by_dte_is_sorted_and_carries_its_expiry(archive):
    payload = chart.session_chart("NIFTY", archive)
    buckets = payload["burn_by_dte"]
    assert [b["dte"] for b in buckets] == sorted(b["dte"] for b in buckets)
    assert all(b["expiry"] for b in buckets)


def test_velocity_chart_is_separate_and_reports_its_blanks(archive):
    payload = chart.velocity_chart("NIFTY", archive)
    assert "correlation" in payload
    assert isinstance(payload["blanks"], str)


# --------------------------------------------------------------------------
# isolation
# --------------------------------------------------------------------------

def test_desk_never_imports_broker_execution_or_risk():
    """Analysis-only, like analysis/equity_report. A slow model of decay must
    never be able to reach an order path."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "analysis" / "theta_decay"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for banned in ("import broker", "from broker", "import execution",
                       "from execution", "import risk", "from risk"):
            assert banned not in text, f"{path.name} imports {banned}"
