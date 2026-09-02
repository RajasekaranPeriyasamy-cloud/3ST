"""Correctness tests for the filter, the state machine and the P&L identity.

Run:  python -m pytest tests -v      (from C:\\Dev\\3ST\\Kalman)

These are the tests the reference implementation does not have, and every one
of them fails against it. That is the point: the bugs in that code are not
subtle style issues, they are things a twenty-line synthetic-data test catches
immediately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kpairs.backtest import CostModel, metrics, run_pair  # noqa: E402
from kpairs.kalman import fit_kalman_mle, kalman_hedge, rolling_ols_hedge  # noqa: E402
from kpairs.signals import positions_from_z, rolling_z  # noqa: E402
from kpairs.stats import benjamini_hochberg, half_life, ols_hedge  # noqa: E402


# --------------------------------------------------------------------------
# the filter
# --------------------------------------------------------------------------
def _synthetic_pair(n=1500, beta_start=0.7, beta_end=1.3, noise=0.02, seed=0):
    """x is a random walk; y tracks it with a beta that drifts linearly."""
    rng = np.random.default_rng(seed)
    x = np.cumsum(rng.normal(0, 0.012, n)) + 5.0
    beta = np.linspace(beta_start, beta_end, n)
    y = 0.4 + beta * x + rng.normal(0, noise, n)
    return x, y, beta


def test_filter_recovers_a_constant_beta():
    rng = np.random.default_rng(1)
    x = np.cumsum(rng.normal(0, 0.01, 2000)) + 4.0
    y = 0.25 + 0.85 * x + rng.normal(0, 0.01, 2000)
    res = kalman_hedge(x, y, obs_var=1e-4, state_var=(1e-9, 1e-8), init_state=(0.0, 1.0))
    assert abs(res.beta[-1] - 0.85) < 0.03
    assert abs(res.alpha[-1] - 0.25) < 0.10


def test_filter_tracks_a_drifting_beta_better_than_static_ols():
    x, y, beta = _synthetic_pair()
    res = kalman_hedge(x, y, obs_var=4e-4, state_var=(1e-9, 1e-6), init_state=(0.4, 0.7))
    _, b_static, _ = ols_hedge(x, y)

    tail = slice(500, None)
    kal_err = np.mean(np.abs(res.beta[tail] - beta[tail]))
    ols_err = np.mean(np.abs(b_static - beta[tail]))
    assert kal_err < ols_err * 0.5, f"kalman {kal_err:.4f} vs static OLS {ols_err:.4f}"


def test_posterior_residual_is_the_innovation_times_an_unobserved_factor():
    """The exact algebra behind "do not trade ``y - (alpha_t + beta_t x_t)``".

        theta_post = theta_prior + K e,  K = P H' / Q
        y - H theta_post = e - (H P H'/Q) e = e * (R / Q)

    So the posterior residual the reference implementation computes is the
    innovation scaled by R/Q_t -- a factor that is (a) less than one, (b)
    time-varying with x_t and the state covariance, and (c) never returned by
    pykalman, so the user cannot undo it. Feed that into a rolling z-score and
    the normalisation is fighting a hidden gain term.
    """
    x, y, _ = _synthetic_pair(noise=0.03)
    R = 1e-3
    res = kalman_hedge(x, y, obs_var=R, state_var=(1e-7, 1e-5), init_state=(0.4, 0.7))
    posterior = y - (res.alpha + res.beta * x)
    np.testing.assert_allclose(posterior, res.innovation * R / res.innov_var, rtol=1e-9)

    tail = slice(300, None)
    shrink = (R / res.innov_var)[tail]
    assert shrink.max() < 1.0
    assert shrink.std() / shrink.mean() > 0.01, "the hidden factor is not constant"


def test_faster_filters_shrink_the_posterior_residual_harder():
    """The trap gets worse exactly where the article points you.

    Its pitch is "raise transition_covariance to be more adaptive". Doing that
    raises the Kalman gain, which drives R/Q down, which collapses the very
    series it tells you to trade.
    """
    x, y, _ = _synthetic_pair(noise=0.03)
    R = 1e-3
    tail = slice(300, None)
    ratios = []
    for wb in (1e-8, 1e-6, 1e-4):
        res = kalman_hedge(x, y, obs_var=R, state_var=(wb, wb), init_state=(0.4, 0.7))
        posterior = y - (res.alpha + res.beta * x)
        ratios.append(np.std(posterior[tail]) / np.std(res.innovation[tail]))
    assert ratios[0] > ratios[1] > ratios[2]
    assert ratios[2] < 0.5


def test_zscore_is_standardised_without_a_rolling_window():
    x, y, _ = _synthetic_pair(n=3000, noise=0.02)
    res = kalman_hedge(x, y, obs_var=4e-4, state_var=(1e-9, 1e-6),
                       init_state=(0.4, 0.7), burn_in=100)
    z = res.zscore[np.isfinite(res.zscore)]
    # Not exactly N(0,1) -- the model is only approximately right -- but the
    # scale must be O(1), which is what makes a fixed entry threshold portable
    # across pairs with wildly different spread widths.
    assert 0.6 < np.std(z) < 1.8
    assert abs(np.mean(z)) < 0.25


def test_mle_scales_with_actual_drift():
    x_slow, y_slow, _ = _synthetic_pair(beta_start=0.9, beta_end=0.95, noise=0.02, seed=3)
    x_fast, y_fast, _ = _synthetic_pair(beta_start=0.5, beta_end=1.6, noise=0.02, seed=3)
    p_slow = fit_kalman_mle(x_slow, y_slow, init_state=(0.4, 0.9))
    p_fast = fit_kalman_mle(x_fast, y_fast, init_state=(0.4, 0.5))
    assert p_fast["var_beta"] > p_slow["var_beta"]


def test_prior_beta_lags_posterior_by_one_bar():
    x, y, _ = _synthetic_pair(n=300)
    res = kalman_hedge(x, y, init_state=(0.4, 0.7))
    assert np.isnan(res.prior_beta[0])
    np.testing.assert_allclose(res.prior_beta[1:], res.beta[:-1])


# --------------------------------------------------------------------------
# the state machine
# --------------------------------------------------------------------------
def test_position_actually_goes_flat_on_exit():
    """The regression test for the reference article's ffill bug.

    z dives to -3 (enter long), returns to 0 (must exit), stays quiet. A book
    that cannot flatten holds +1 to the end of the array.
    """
    z = np.concatenate([np.zeros(5), np.full(5, -3.0), np.zeros(20)])
    out = positions_from_z(z, entry=2.0, exit_=0.5, exec_lag=0)
    assert out["position"][7] == 1.0, "should be long while z is at -3"
    assert out["position"][-1] == 0.0, "must be flat once z reverts to 0"
    assert out["position"][15] == 0.0


def test_hysteresis_band_prevents_churn():
    """z oscillating inside the band must not generate new trades."""
    rng = np.random.default_rng(2)
    z = np.concatenate([np.full(3, -2.5), rng.uniform(-1.4, -0.8, 60)])
    out = positions_from_z(z, entry=2.0, exit_=0.5, exec_lag=0)
    flips = np.sum(np.abs(np.diff(out["position"])) > 0)
    assert flips == 1, f"expected one entry and no churn, got {flips} changes"


def test_stop_fires_on_a_widening_spread():
    z = np.concatenate([np.full(3, -2.5), np.linspace(-2.5, -5.0, 20)])
    out = positions_from_z(z, entry=2.0, exit_=0.5, stop=4.0, exec_lag=0)
    assert out["position"][-1] == 0.0


def test_time_stop_fires():
    z = np.concatenate([np.full(2, -2.5), np.full(100, -1.5)])
    out = positions_from_z(z, entry=2.0, exit_=0.5, stop=4.0, max_hold=30, exec_lag=0)
    assert out["position"][-1] == 0.0
    assert out["position"][20] == 1.0


def test_exec_lag_shifts_the_holding():
    z = np.concatenate([np.zeros(5), np.full(10, -3.0)])
    lag0 = positions_from_z(z, exec_lag=0)["position"]
    lag1 = positions_from_z(z, exec_lag=1)["position"]
    assert lag0[5] == 1.0 and lag1[5] == 0.0 and lag1[6] == 1.0


def test_rolling_z_excludes_the_current_bar():
    s = np.arange(100, dtype=float)
    z = rolling_z(s, window=20, min_periods=20)
    # A perfectly trending series: the current point is always above a window
    # that ends one bar back, so z must be strictly positive, never zero.
    assert np.all(z[25:] > 0)


# --------------------------------------------------------------------------
# the P&L identity
# --------------------------------------------------------------------------
def test_perfect_hedge_earns_nothing_gross():
    """If y moves exactly beta-proportionally to x, a hedged pair is flat."""
    n = 300
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(4)
    rx = rng.normal(0, 0.01, n)
    beta = 1.0
    px_x = pd.Series(100 * np.exp(np.cumsum(rx)), index=idx)
    px_y = pd.Series(200 * np.exp(np.cumsum(rx * beta)), index=idx)

    pos = np.ones(n)
    bt = run_pair(px_x, px_y, pos, np.full(n, beta), np.zeros(n),
                  costs=CostModel(bps_per_turnover=0.0, apply_roll=False))
    assert abs(np.sum(bt.ret_gross)) < 1e-8


def test_spread_convergence_is_profitable_in_the_right_direction():
    """Long the spread (+1) must make money when y outperforms x."""
    n = 200
    idx = pd.bdate_range("2020-01-01", periods=n)
    px_x = pd.Series(np.full(n, 100.0), index=idx)
    px_y = pd.Series(np.linspace(100.0, 110.0, n), index=idx)
    bt_long = run_pair(px_x, px_y, np.ones(n), np.ones(n), np.zeros(n),
                       costs=CostModel(bps_per_turnover=0.0, apply_roll=False))
    bt_short = run_pair(px_x, px_y, -np.ones(n), np.ones(n), np.zeros(n),
                        costs=CostModel(bps_per_turnover=0.0, apply_roll=False))
    assert np.sum(bt_long.ret_gross) > 0.04
    assert np.sum(bt_short.ret_gross) < -0.04


def test_costs_reduce_returns_and_scale_with_turnover():
    n = 200
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(5)
    px_x = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)
    px_y = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), index=idx)
    pos = np.tile([1.0, 0.0], n // 2)  # maximal churn

    cheap = run_pair(px_x, px_y, pos, np.ones(n), np.zeros(n),
                     costs=CostModel(bps_per_turnover=1.0, apply_roll=False))
    dear = run_pair(px_x, px_y, pos, np.ones(n), np.zeros(n),
                    costs=CostModel(bps_per_turnover=20.0, apply_roll=False))
    assert dear.cost.sum() > cheap.cost.sum() * 15
    assert dear.ret_net.sum() < cheap.ret_net.sum()


def test_freeze_hedge_holds_beta_constant_within_a_trade():
    n = 100
    idx = pd.bdate_range("2020-01-01", periods=n)
    px = pd.Series(np.full(n, 100.0), index=idx)
    pos = np.concatenate([np.zeros(10), np.ones(40), np.zeros(50)])
    drifting = np.linspace(0.5, 2.0, n)
    bt = run_pair(px, px, pos, drifting, np.zeros(n), freeze_hedge=True,
                  costs=CostModel(bps_per_turnover=0.0, apply_roll=False))
    held = bt.beta[10:50]
    assert np.allclose(held, held[0])


def test_trade_extraction_counts_round_trips():
    n = 60
    idx = pd.bdate_range("2020-01-01", periods=n)
    px = pd.Series(np.full(n, 100.0), index=idx)
    pos = np.zeros(n)
    pos[5:15] = 1.0
    pos[30:40] = -1.0
    bt = run_pair(px, px, pos, np.ones(n), np.zeros(n),
                  costs=CostModel(bps_per_turnover=0.0, apply_roll=False))
    assert len(bt.trades) == 2
    assert bt.trades[0]["side"] == "long_spread"
    assert bt.trades[1]["side"] == "short_spread"
    assert bt.trades[0]["bars"] == 10


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------
def test_half_life_of_a_known_ar1():
    """AR(1) with phi=0.9 has half-life ln(0.5)/ln(0.9) = 6.58 bars."""
    rng = np.random.default_rng(6)
    n, phi = 20000, 0.9
    s = np.zeros(n)
    for t in range(1, n):
        s[t] = phi * s[t - 1] + rng.normal(0, 1)
    hl = half_life(s)
    expected = np.log(0.5) / np.log(phi)
    assert abs(hl - expected) < 0.5, f"{hl:.2f} vs {expected:.2f}"


def test_half_life_of_a_random_walk_is_infinite():
    rng = np.random.default_rng(7)
    s = np.cumsum(rng.normal(0, 1, 5000))
    assert half_life(s) > 100 or not np.isfinite(half_life(s))


def test_naive_price_difference_gives_a_misleading_half_life():
    """Why ``spread = prices[a] - prices[b]`` is not good enough.

    Two cointegrated series with a hedge ratio of 3.0. The properly hedged
    residual mean-reverts fast; the raw difference inherits the random walk of
    the un-neutralised 2x exposure and does not revert at all.
    """
    rng = np.random.default_rng(8)
    n = 4000
    x = np.cumsum(rng.normal(0, 1.0, n)) + 500
    resid = np.zeros(n)
    for t in range(1, n):
        resid[t] = 0.9 * resid[t - 1] + rng.normal(0, 1.0)
    y = 3.0 * x + resid

    _, b, hedged = ols_hedge(x, y)
    assert abs(b - 3.0) < 0.05
    hl_hedged = half_life(hedged)
    hl_naive = half_life(y - x)
    assert hl_hedged < 15
    assert hl_naive > 4 * hl_hedged or not np.isfinite(hl_naive)


def test_bh_is_stricter_than_a_raw_005_cut():
    rng = np.random.default_rng(9)
    p = rng.uniform(0, 1, 1000)          # all nulls true
    naive = int((p < 0.05).sum())
    bh = int(benjamini_hochberg(p, fdr=0.10).sum())
    assert naive > 30
    assert bh <= 2, f"BH admitted {bh} false discoveries out of pure noise"


def test_bh_keeps_genuine_signal():
    rng = np.random.default_rng(10)
    p = np.concatenate([rng.uniform(0, 1, 900), rng.uniform(0, 1e-6, 100)])
    keep = benjamini_hochberg(p, fdr=0.10)
    assert keep[900:].all()
    assert keep[:900].sum() < 20


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------
def test_max_drawdown_uses_compounded_equity():
    idx = pd.bdate_range("2020-01-01", periods=4)
    r = pd.Series([0.5, -0.5, 0.5, -0.5], index=idx)
    m = metrics(r)
    # compounded: 1.5, 0.75, 1.125, 0.5625 -> peak 1.5, trough 0.5625 -> -62.5%
    assert abs(m["max_dd"] - (0.5625 / 1.5 - 1.0)) < 1e-9
    # cumsum, as the reference computes it, would say 0.0 here.


def test_rolling_ols_is_causal():
    """The OLS baseline must not see bar t when fitting the hedge used at bar t."""
    rng = np.random.default_rng(11)
    n = 400
    x = np.cumsum(rng.normal(0, 0.01, n)) + 5
    y = 0.5 + 1.2 * x + rng.normal(0, 0.02, n)
    r = rolling_ols_hedge(x, y, window=60)
    y2 = y.copy()
    y2[300] += 10.0                      # detonate one bar
    r2 = rolling_ols_hedge(x, y2, window=60)
    np.testing.assert_allclose(r["beta"][:301], r2["beta"][:301], equal_nan=True)
    assert not np.isclose(r["beta"][301], r2["beta"][301])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


def test_stop_does_not_immediately_re_enter():
    """Regression: a stop with no re-arm rule is decorative.

    z blows out to -5, stops the trade, then hovers at -4. Without the disarm
    latch the entry rule fires again on the very next bar at a worse level.
    """
    z = np.concatenate([np.full(3, -2.5), np.linspace(-2.5, -5.0, 20),
                        np.full(30, -4.0)])
    out = positions_from_z(z, entry=2.0, exit_=0.5, stop=4.0, exec_lag=0)
    assert out["position"][-1] == 0.0
    assert np.all(out["position"][25:] == 0.0)


def test_disarmed_pair_re_arms_once_z_normalises():
    z = np.concatenate([np.full(2, -2.5), np.full(5, -5.0),
                        np.full(10, -1.0),        # back inside the band -> re-arm
                        np.full(10, -2.5)])       # new entry allowed
    out = positions_from_z(z, entry=2.0, exit_=0.5, stop=4.0, cooldown=2, exec_lag=0)
    assert out["position"][10] == 0.0
    assert out["position"][-1] == 1.0


def test_kalman_scalar_matches_matrix_form():
    """The scalar inner loop must be exactly the matrix recursion.

    kalman_hedge writes the 2x2 update out in plain floats for speed. This is
    the reference implementation it has to agree with, term for term.
    """
    rng = np.random.default_rng(12)
    n = 800
    x = np.cumsum(rng.normal(0, 0.01, n)) + 5.0
    y = 0.3 + 1.1 * x + rng.normal(0, 0.02, n)
    R, W = 1e-3, np.diag([1e-7, 1e-6])

    theta = np.array([0.3, 1.1])
    P = np.eye(2) * 1e-2
    ref_alpha, ref_beta, ref_e, ref_q = [], [], [], []
    for t in range(n):
        P = P + W
        H = np.array([1.0, x[t]])
        e = y[t] - H @ theta
        Q = float(H @ P @ H) + R
        K = (P @ H) / Q
        theta = theta + K * e
        P = P - np.outer(K, H @ P)
        ref_alpha.append(theta[0]); ref_beta.append(theta[1])
        ref_e.append(e); ref_q.append(Q)

    got = kalman_hedge(x, y, obs_var=R, state_var=(1e-7, 1e-6),
                       init_state=(0.3, 1.1), init_cov=1e-2)
    np.testing.assert_allclose(got.alpha, ref_alpha, rtol=1e-12, atol=1e-14)
    np.testing.assert_allclose(got.beta, ref_beta, rtol=1e-12, atol=1e-14)
    np.testing.assert_allclose(got.innovation, ref_e, rtol=1e-12, atol=1e-14)
    np.testing.assert_allclose(got.innov_var, ref_q, rtol=1e-12, atol=1e-14)
