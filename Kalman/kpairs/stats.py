"""Cointegration and mean-reversion statistics, plus multiple-testing control.

Everything here operates on the *cointegrating residual*, never on a naive
1:1 price difference. That distinction is the single most common error in
published pairs-trading code: ``spread = prices[a] - prices[b]`` only has a
meaningful half-life when the true hedge ratio happens to be 1.0. For a pair
like RELIANCE / ONGC the ratio is nowhere near 1, and the "half-life" of the
raw difference is measuring the drift of the larger leg, not mean reversion.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PairStat:
    x: str
    y: str
    n: int
    beta: float          # OLS hedge ratio on log prices, y on x
    alpha: float
    coint_p: float       # Engle-Granger p-value
    adf_p: float         # ADF on the residual
    half_life: float     # trading days
    hurst: float         # <0.5 mean-reverting, 0.5 random walk, >0.5 trending
    resid_sd: float      # residual sd in log units (~ fractional spread width)
    corr: float          # correlation of daily log returns


def ols_hedge(x: np.ndarray, y: np.ndarray) -> tuple[float, float, np.ndarray]:
    """Static OLS of y on x with intercept. Returns (alpha, beta, residual)."""
    X = np.column_stack([np.ones(x.size), x])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    a, b = float(coef[0]), float(coef[1])
    return a, b, y - (a + b * x)


def half_life(spread: np.ndarray) -> float:
    """Half-life of mean reversion, in bars, from an AR(1) fit on the residual.

    Regress d_spread_t on spread_{t-1}:  ds_t = c + rho * s_{t-1} + eps
    so s_t = c + (1 + rho) s_{t-1}, an AR(1) with phi = 1 + rho, and

        half_life = ln(0.5) / ln(phi) = -ln(2) / ln(1 + rho)

    The reference article gets this formula right (many implementations use the
    continuous-time shortcut ``-ln(2)/rho``, which diverges from the discrete
    answer as rho grows). What it gets wrong is the input -- it feeds in a raw
    price difference rather than a hedged residual.

    Returns ``inf`` when rho >= 0 (no reversion) or when 1 + rho <= 0
    (oscillatory / over-differenced, not a tradable reversion either).
    """
    s = np.asarray(spread, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < 30:
        return float("inf")
    lag = s[:-1]
    diff = np.diff(s)
    X = np.column_stack([np.ones(lag.size), lag])
    try:
        coef, *_ = np.linalg.lstsq(X, diff, rcond=None)
    except np.linalg.LinAlgError:
        return float("inf")
    rho = float(coef[1])
    phi = 1.0 + rho
    if rho >= 0 or phi <= 0:
        return float("inf")
    return float(-np.log(2.0) / np.log(phi))


def hurst(series: np.ndarray, max_lag: int = 60) -> float:
    """Hurst exponent via the variance-of-differences (rescaled lag) method.

    Cheap sanity check on top of the ADF: a residual can pass ADF at 5% and
    still be visually trending. H < 0.5 says the series' variance grows slower
    than linearly in the lag, i.e. it is anti-persistent.
    """
    s = np.asarray(series, dtype=float)
    s = s[np.isfinite(s)]
    if s.size < max_lag * 3:
        max_lag = max(10, s.size // 3)
    if s.size < 30:
        return float("nan")
    lags = np.arange(2, max_lag)
    tau = []
    keep = []
    for lag in lags:
        d = s[lag:] - s[:-lag]
        sd = float(np.std(d))
        if sd > 0:
            tau.append(sd)
            keep.append(lag)
    if len(tau) < 5:
        return float("nan")
    poly = np.polyfit(np.log(keep), np.log(tau), 1)
    return float(poly[0])


def pair_stats(
    x: pd.Series, y: pd.Series, x_name: str, y_name: str,
    *, maxlag: int = 6,
) -> PairStat | None:
    """Full statistical profile of one candidate pair over a formation window.

    Both series are aligned on their common dates and log-transformed. Returns
    ``None`` when there is not enough overlapping history to say anything.

    ``maxlag`` fixes the augmentation order of the Engle-Granger regression
    instead of letting statsmodels search it by AIC. This is a real modelling
    choice, not just a speed hack, and it cuts both ways:

    * Speed: AIC search on an 18,750-bar 5-minute formation window runs ~44
      nested regressions and takes 3.9s per pair -- 12 hours across this sweep.
      A fixed order takes 0.07s.
    * Correctness: on intraday data AIC routinely selects 30-40 lags, which
      shreds the test's power on exactly the short-horizon reversion the
      strategy trades. The convention used here is roughly one session of lags,
      capped -- enough to absorb intraday autocorrelation without spending the
      sample on it.

    Set it deliberately per timeframe; ``tfstudy`` derives it from bars/session.
    """
    from statsmodels.tsa.stattools import adfuller, coint

    joined = pd.concat([x, y], axis=1, keys=["x", "y"]).dropna()
    if len(joined) < 250:
        return None

    lx = np.log(joined["x"].to_numpy())
    ly = np.log(joined["y"].to_numpy())
    if not (np.all(np.isfinite(lx)) and np.all(np.isfinite(ly))):
        return None

    a, b, resid = ols_hedge(lx, ly)
    if b <= 0:
        # A negative hedge ratio means the "pair" is short one leg against the
        # other on the same side of the market -- that is a directional bet
        # dressed as a spread, and it is never what a pairs book wants.
        return None

    try:
        _, coint_p, _ = coint(ly, lx, trend="c", maxlag=int(maxlag), autolag=None)
    except Exception:  # noqa: BLE001
        return None
    try:
        adf_p = float(adfuller(resid, maxlag=1, regression="c", autolag=None)[1])
    except Exception:  # noqa: BLE001
        adf_p = 1.0

    rx = np.diff(lx)
    ry = np.diff(ly)
    corr = float(np.corrcoef(rx, ry)[0, 1]) if rx.size > 2 else float("nan")

    return PairStat(
        x=x_name, y=y_name, n=len(joined),
        beta=b, alpha=a,
        coint_p=float(coint_p), adf_p=adf_p,
        half_life=half_life(resid),
        hurst=hurst(resid),
        resid_sd=float(np.std(resid, ddof=2)),
        corr=corr,
    )


def benjamini_hochberg(pvals: np.ndarray, fdr: float = 0.10) -> np.ndarray:
    """Benjamini-Hochberg step-up. Returns a boolean 'reject the null' mask.

    Why this is not optional. Sector-constrained pairing over a NIFTY-200-ish
    list is on the order of 1,300 simultaneous cointegration tests. Accepting
    everything with ``p < 0.05``, as the reference implementation does, admits
    ~65 pairs *purely by chance* before a single real relationship is found.
    Rank them by p-value and take the top 10 and you have built a portfolio out
    of the most extreme sampling noise in the sample.

    BH controls the expected proportion of false discoveries among the accepted
    set at ``fdr``. It is the right tool rather than Bonferroni here because the
    tests are positively dependent (pairs share legs) and Bonferroni would leave
    nothing standing.
    """
    p = np.asarray(pvals, dtype=float)
    n = p.size
    if n == 0:
        return np.zeros(0, dtype=bool)
    order = np.argsort(p)
    ranked = p[order]
    thresh = fdr * (np.arange(1, n + 1) / n)
    passing = ranked <= thresh
    reject = np.zeros(n, dtype=bool)
    if passing.any():
        cutoff = np.max(np.flatnonzero(passing))
        reject[order[: cutoff + 1]] = True
    return reject
