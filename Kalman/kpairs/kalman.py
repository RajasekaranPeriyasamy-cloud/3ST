"""Random-walk Kalman filter for a time-varying [alpha, beta] hedge relation.

Model
-----
    state       theta_t = [alpha_t, beta_t]' = theta_{t-1} + w_t,  w_t ~ N(0, W)
    observation y_t      = H_t theta_t + v_t,  H_t = [1, x_t],  v_t ~ N(0, R)

Recursion (one bar):

    predict     theta^- = theta_{t-1}                 (transition is identity)
                P^-     = P_{t-1} + W
    innovate    e_t     = y_t - H_t theta^-           <-- THE TRADABLE SPREAD
                Q_t     = H_t P^- H_t' + R            <-- its variance, in closed form
    update      K_t     = P^- H_t' / Q_t
                theta_t = theta^- + K_t e_t
                P_t     = (I - K_t H_t) P^-

Why this is written out instead of calling pykalman
---------------------------------------------------
Two reasons, one practical and one substantive.

Practical: pykalman is unmaintained and does not import on this stack
(numpy 2.5 / pandas 3.0) -- it still reaches for numpy APIs removed in 2.0.

Substantive, and this is the important one: ``KalmanFilter.filter()`` returns
``state_means`` and ``state_covs`` and *nothing else*. The quantity a pairs
trader actually needs -- the one-step-ahead forecast error ``e_t`` and its
variance ``Q_t`` -- is computed inside the library and thrown away. Reconstruct
the spread from the returned state means, as the reference implementation does
with ``y - (alpha + beta * x)``, and you get the *posterior* residual: alpha_t
and beta_t have already been pulled toward y_t by the update step, so that
residual is shrunk toward zero by construction. It is a diagnostic, not a
signal. Trading it means trading whatever the filter failed to absorb, scaled
by a Kalman gain you never see.

``e_t`` is strictly causal: it uses theta as of the previous bar and x_t, and
compares against y_t before the filter has seen it. And because ``Q_t`` comes
out of the same recursion, ``z_t = e_t / sqrt(Q_t)`` is a standardised score
with no rolling window, no burn-in choice, and no lookahead -- which is the
whole reason the Kalman formulation is worth the trouble over rolling OLS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class KalmanResult:
    """Filter output, all arrays length T and aligned to the input index."""

    alpha: np.ndarray       # posterior intercept
    beta: np.ndarray        # posterior hedge ratio
    innovation: np.ndarray  # e_t -- one-step-ahead forecast error (the spread)
    innov_var: np.ndarray   # Q_t -- its variance
    zscore: np.ndarray      # e_t / sqrt(Q_t)
    beta_var: np.ndarray    # P_t[1,1] -- uncertainty on the hedge ratio
    loglik: float           # Gaussian log-likelihood of the innovation sequence

    @property
    def prior_alpha(self) -> np.ndarray:
        """alpha as known *before* bar t -- what a live system could have used."""
        return np.concatenate([[np.nan], self.alpha[:-1]])

    @property
    def prior_beta(self) -> np.ndarray:
        """beta as known *before* bar t. Use this for position sizing, not ``beta``."""
        return np.concatenate([[np.nan], self.beta[:-1]])


def kalman_hedge(
    x: np.ndarray,
    y: np.ndarray,
    *,
    obs_var: float = 1e-3,
    state_var: tuple[float, float] | float = (1e-6, 1e-5),
    init_state: tuple[float, float] | None = None,
    init_cov: float = 1.0,
    burn_in: int = 0,
) -> KalmanResult:
    """Run the filter over one pair.

    Parameters
    ----------
    x, y
        Independent / dependent series. Pass **log prices**: beta is then an
        elasticity, the P&L identity ``r_y - beta * r_x`` is correct to first
        order, and the same parameter values transfer across pairs whose price
        levels differ by two orders of magnitude (MRF at ~1.2 lakh vs IDEA at
        ~8 rupees). On raw prices, ``obs_var`` would have to be retuned per pair.
    obs_var
        R -- measurement noise. On daily log prices the residual of a decent
        cointegrating pair has a standard deviation around 2-5%, so R ~ 1e-3
        (sd ~3%) is the right order of magnitude.
    state_var
        W's diagonal, ``(var_alpha, var_beta)``. Two knobs, not one: alpha and
        beta are not on the same scale even in log space, and forcing them to
        share a variance (``trans_cov * np.eye(2)``, as the reference code does)
        means whichever one you tune for, the other is mis-specified. Bigger =
        faster adaptation and noisier estimates.
    init_state
        ``(alpha_0, beta_0)``. Seed this from an OLS fit on the *formation*
        window; starting from ``[0, 0]`` with a diffuse prior costs 50-100 bars
        of garbage innovations while the filter walks beta up from zero.
    burn_in
        Number of leading bars whose z-score is nulled out. Belt-and-braces on
        top of ``init_state``.

    Returns
    -------
    KalmanResult
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.shape != y.shape:
        raise ValueError(f"x and y must be the same length, got {x.shape} vs {y.shape}")
    n = x.size
    if n < 3:
        raise ValueError("need at least 3 observations")

    if np.isscalar(state_var):
        w = np.array([float(state_var), float(state_var)])
    else:
        w = np.asarray(state_var, dtype=float)
    R = float(obs_var)

    a, b = (float(init_state[0]), float(init_state[1])) if init_state is not None else (0.0, 0.0)
    p11 = p22 = float(init_cov)
    p12 = 0.0
    wa, wb = float(w[0]), float(w[1])

    alpha = np.empty(n)
    beta = np.empty(n)
    innov = np.empty(n)
    qvar = np.empty(n)
    bvar = np.empty(n)
    loglik = 0.0
    log2pi = math.log(2.0 * math.pi)
    _log = math.log            # math.log on a python float beats np.log by ~3x here

    # The 2x2 recursion written out in scalars rather than numpy arrays.
    # Per-iteration numpy overhead (~10us for a handful of 2-element ops)
    # dominates the arithmetic at this size, and this loop runs ~200k times per
    # pair per MLE evaluation at 5-minute sampling. Scalars are ~20x faster and
    # bit-for-bit equivalent -- test_kalman_scalar_matches_matrix_form pins that.
    xs = x.tolist()
    ys = y.tolist()
    for t in range(n):
        xt = xs[t]

        # --- predict: theta^- == theta (identity transition), P^- = P + W ----
        p11 += wa
        p22 += wb

        # --- innovate --------------------------------------------------------
        e = ys[t] - (a + b * xt)
        h1 = p11 + xt * p12          # (P H')[0]
        h2 = p12 + xt * p22          # (P H')[1]
        Q = h1 + xt * h2 + R         # H P H' + R
        if not (Q > 0.0) or Q != Q:  # non-positive or NaN
            Q = R

        innov[t] = e
        qvar[t] = Q
        loglik -= 0.5 * (log2pi + _log(Q) + e * e / Q)

        # --- update ----------------------------------------------------------
        k1 = h1 / Q
        k2 = h2 / Q
        a += k1 * e
        b += k2 * e
        p11 -= k1 * h1
        p12 -= k1 * h2               # == k2 * h1, so P stays symmetric exactly
        p22 -= k2 * h2

        alpha[t] = a
        beta[t] = b
        bvar[t] = p22

    z = innov / np.sqrt(qvar)
    if burn_in > 0:
        z[:burn_in] = np.nan

    return KalmanResult(
        alpha=alpha, beta=beta, innovation=innov, innov_var=qvar,
        zscore=z, beta_var=bvar, loglik=float(loglik),
    )


def fit_kalman_mle(
    x: np.ndarray,
    y: np.ndarray,
    *,
    init_state: tuple[float, float] | None = None,
    burn_in: int = 20,
    max_bars: int = 2500,
    maxiter: int = 120,
) -> dict[str, float]:
    """Estimate ``(obs_var, var_alpha, var_beta)`` by maximum likelihood.

    This replaces the reference article's ``tune_kalman_parameters``, which grid
    searches ``transition_covariance`` on *validation Sharpe*. That is
    backwards: you are selecting the filter on the same statistic you will then
    report, over a handful of validation years, with six candidate values and no
    penalty for the search. The winning value is the one that best fits the
    validation set's noise, and the Sharpe you print is not an estimate of
    anything out of sample.

    The likelihood has no such problem. It is a property of the state-space
    model, computable on the formation window alone, and it never sees a P&L
    number. It answers "how much does beta actually drift in this pair" rather
    than "which drift rate would have paid best last year".

    Optimised in log-space so the variances stay positive. Falls back to the
    starting point if the optimiser fails to converge.

    ``max_bars`` truncates to the most recent N bars of the formation window
    before optimising. Nelder-Mead needs a few hundred function evaluations and
    each one is a full filter pass, so an 18,750-bar 5-minute formation window
    costs ~9 seconds per pair per window -- two hours across the full sweep.
    2,500 observations is ample for three variance parameters, and taking them
    from the *end* of the window means the estimate reflects the regime the
    trading window is about to inherit rather than one from three years ago.

    The truncation is a real approximation and it is worth knowing which way it
    cuts: fewer observations widen the confidence region around var_beta, so the
    filter's adaptation rate is estimated more noisily. It does not bias it.
    """
    from scipy.optimize import minimize

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size > max_bars:
        x, y = x[-max_bars:], y[-max_bars:]
    start = np.log([1e-3, 1e-7, 1e-6])

    def neg_ll(logp: np.ndarray) -> float:
        r, wa, wb = np.exp(logp)
        try:
            res = kalman_hedge(x, y, obs_var=r, state_var=(wa, wb),
                               init_state=init_state, burn_in=0)
        except Exception:  # noqa: BLE001
            return 1e12
        # Drop the burn-in from the likelihood: those innovations are dominated
        # by the diffuse prior, not by the parameters being estimated.
        e, q = res.innovation[burn_in:], res.innov_var[burn_in:]
        ll = -0.5 * np.sum(np.log(2.0 * np.pi * q) + e * e / q)
        return float(-ll) if np.isfinite(ll) else 1e12

    try:
        opt = minimize(neg_ll, start, method="Nelder-Mead",
                       options={"maxiter": maxiter, "xatol": 1e-3, "fatol": 1e-2})
        params = np.exp(opt.x) if opt.success or np.isfinite(opt.fun) else np.exp(start)
    except Exception:  # noqa: BLE001
        params = np.exp(start)

    # Clamp to a sane box, but a wide one. The upper bound is the one that
    # matters: an unconstrained MLE on a pair whose relationship genuinely broke
    # will happily push var_beta to 1e-2, which is not "adaptive" -- it is a
    # filter that re-fits beta to every bar and produces no spread at all.
    # The lower bounds are deliberately far below anything plausible: a
    # 5-minute index spread has innovations of a few basis points, so an obs_var
    # floor of 1e-6 (sd = 10 bp) would silently bind on most intraday pairs and
    # make the filter look better calibrated than it is.
    r = float(np.clip(params[0], 1e-9, 1e-1))
    wa = float(np.clip(params[1], 1e-14, 1e-3))
    wb = float(np.clip(params[2], 1e-14, 1e-3))
    return {"obs_var": r, "var_alpha": wa, "var_beta": wb}


def rolling_ols_hedge(x: np.ndarray, y: np.ndarray, window: int = 60) -> dict[str, np.ndarray]:
    """Rolling-window OLS baseline -- the thing the Kalman filter has to beat.

    Kept deliberately honest and strictly causal: the hedge ratio at bar t is
    fitted on bars ``[t-window, t-1]`` and the residual at t is the *forecast*
    error using that ratio, never an in-window residual. The z-score divides by
    the residual standard deviation from the same fitting window.

    Compare against the Kalman path: identical signal machinery downstream, so
    any performance difference is attributable to the hedge estimator alone.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = x.size
    alpha = np.full(n, np.nan)
    beta = np.full(n, np.nan)
    resid = np.full(n, np.nan)
    z = np.full(n, np.nan)

    for t in range(window, n):
        xs = x[t - window:t]
        ys = y[t - window:t]
        X = np.column_stack([np.ones(window), xs])
        try:
            coef, *_ = np.linalg.lstsq(X, ys, rcond=None)
        except np.linalg.LinAlgError:
            continue
        a, b = float(coef[0]), float(coef[1])
        in_window_resid = ys - (a + b * xs)
        sd = float(np.std(in_window_resid, ddof=2))
        alpha[t] = a
        beta[t] = b
        resid[t] = y[t] - (a + b * x[t])
        if sd > 0:
            z[t] = resid[t] / sd

    return {"alpha": alpha, "beta": beta, "resid": resid, "zscore": z}
