"""Theta-decay features — burn rate and realized-vs-theoretical decay capture.

Pure computation: no I/O, no Kite, no clock. Reads the same per-leg rows the
delta-velocity engine archives (``analysis.delta_velocity.store.to_rows``),
because every input theta needs — ``spot``, ``strike``, ``expiry``, ``ts``,
``iv``, ``option_type`` — is already written down there.

Two metrics, and they are not equally strong. Read this before trusting either.

``burn_rate`` — solid
    ``-theta / premium``: decay per day as a percent of the option's own price.
    Absolute theta is not comparable between a 5-rupee wing and a 200-rupee
    straddle; this is. Uses theta as a *level*, so it inherits none of the
    differencing noise below. Measured on the 2026-08-12 NIFTY archive it tracks
    the theoretical ``1/T`` scaling almost exactly — median 8.13%/day at 6 DTE,
    3.79% at 13, 2.48% at 20, where pure ``1/T`` predicts ratios of 2.17 and
    1.54 against the 2.14 and 1.53 observed.

``decay_attribution`` / ``capture_ratio`` — weak, and only at a long horizon
    Splits realized premium change into delta/gamma/vega/theta contributions to
    ask what fraction of quoted decay the tape actually handed over. **This is a
    session-scale statistic, not an intraday signal**, for a reason that is
    structural rather than a tuning problem — see below.

Why capture ratio needs a 60-minute horizon
-------------------------------------------
The archived IV is *inverted from the archived price*. So decomposing a price
change into delta and vega terms built from that same IV is close to circular:
measured on 2026-08-12 NIFTY, ``delta*dS + vega*dsigma`` alone reproduces the
one-minute price change with R^2 of 0.95 (6 DTE) to 0.998 (20 DTE). Theta is
left with 0.3-0.4% of the movement, so the theta term is the rounding error of
a decomposition of two much larger numbers, and the ratio is noise.

Lengthening the horizon is the fix that works, because theta accumulates
linearly while the spot and vol noise partially cancels. Measured, same session,
capture ratio by horizon:

    DTE   1min    5min   15min   30min   60min    theta share of |dP|
     6   -1.14   -0.85   -0.22    0.52    0.59      0.44% -> 2.05%
    13    0.58    0.73    0.76    0.91    0.95      0.34% -> 1.54%
    20    0.44    0.56    0.63    0.81    0.71      0.31% -> 1.37%

Hence ``DEFAULT_HORIZON_MIN = 60``. Even there the theta term is ~1.5% of the
price move, so a single session's capture ratio is indicative, not precise.

**Do not smooth IV to denoise this.** It looks like the obvious fix and it
biases instead: a rolling-mean IV lags the true vol path, the vega term
under-attributes the real vol move, and the leftover lands in ``time_pnl`` and
inflates capture. Measured at a 15-minute horizon, 30-minute IV smoothing pushed
DTE 13 from 0.76 to 1.71 — an apparent improvement that is entirely artefact.

**Never pool capture ratio across DTE.** The nearest expiry is the worst-behaved
bucket and drags a pooled figure to nonsense: pooled across all three expiries
that session gives -0.09 while the individual buckets are 0.59 / 0.95 / 0.71.

Model conventions, stated once
------------------------------
* **Dividend yield is 0, not the 0.012 in GREEKS_ENGINE_DEFAULTS.** The archived
  ``iv`` is solved by ``options.iv.implied_volatility``, which calls
  ``vollib.black_scholes`` — plain BS, no carry. Feeding a q=0 IV into a q=0.012
  greeks call describes no self-consistent model of the observed price. The q
  terms are negligible for delta (``qT`` ~ 2e-4 at 6 DTE) but not for theta: at
  NIFTY ATM the ``q*S*exp(-qT)*N(d1)`` term is ~0.40/day against a theta of
  ~-8/day, a 5% shift.
* Attribution therefore uses its own ``delta_q0`` rather than the archived
  ``delta``, which the delta-velocity desk wrote at q=0.012. Mixing them puts a
  0.04% delta error onto a term worth ~100% of the price move — comparable to
  the whole theta signal.
* **Theta is calendar-mode**, matching ``options.iv.time_to_expiry_years``,
  which is the tte the archived IV was solved against. Trading-day theta is a
  fixed 365/252 rescale and is derived, never re-solved.

Precision, for contrast with the delta feature: theta magnitudes are 5-50, so
6dp rounding is irrelevant. The noise here is entirely upstream, in the IV.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from options.iv import time_to_expiry_years

# Matches analysis.delta_velocity.collector.RISK_FREE so a theta derived here
# and a delta archived there rest on the same rate.
RISK_FREE = 0.065

# See module docstring. Deliberately not GREEKS_ENGINE_DEFAULTS["dividend_yield"].
DIVIDEND_YIELD = 0.0

GROUP_KEYS = ("session_date", "underlying", "expiry", "strike", "option_type")

GREEK_COLUMNS = ("theta", "gamma", "vega")

# Attribution's self-consistent delta, kept under its own name so the archived
# q=0.012 delta the delta-velocity desk depends on is never shadowed.
ATTRIB_DELTA = "delta_q0"

# See the horizon table in the module docstring.
DEFAULT_HORIZON_MIN = 60

# Quality gates on a capture ratio, calibrated across the nine
# (underlying, DTE) buckets in the 2026-08-12 archive. They separate every
# plausible reading from every nonsense one on that data:
#
#   underlying  DTE  theta_share  vega_share  capture
#   NIFTY         6        0.020       0.263    0.613  ok
#   NIFTY        13        0.015       0.269    0.954  ok
#   NIFTY        20        0.013       0.291    0.695  ok
#   BANKNIFTY    13        0.018       0.294    0.962  ok
#   BANKNIFTY    48        0.009       0.337    0.897  theta_too_small
#   BANKNIFTY    76        0.003       1.014   -1.255  vega_dominated
#   SENSEX        1        0.058       0.248    0.788  ok
#   SENSEX        8        0.021       0.284    0.793  ok
#   SENSEX       15        0.012       0.477   -1.382  vega_dominated
#
# One session across three underlyings is thin, so treat these as guard rails
# rather than laws — they are why a bad bucket is *labelled* rather than hidden.
# The two conditions catch different failures: theta too small to measure at
# all, versus a vol term too large to subtract cleanly.
MIN_THETA_SHARE = 0.010
MAX_VEGA_SHARE = 0.350

# Minimum number of *distinct time windows* a bucket needs before its term
# shares mean anything.
#
# Counting rows here would be wrong and was, briefly: a bucket holds one row per
# (contract, window), so a 92-minute session yields 22 rows — but all 22 are the
# same single hour seen through 22 strikes, sharing one spot path and one vol
# move, so they are near-perfectly correlated rather than 22 samples. Measured
# live, that bucket reported vega_share ~100% against 26-29% over a full
# session, purely because nothing had averaged out yet.
#
# 4 distinct windows is ~4 hours at the default horizon, so a bucket qualifies
# only late in a session. That is the honest bar.
MIN_WINDOWS_FOR_QUALITY = 4

# A 2-rupee option moves one tick (0.05) for 2.5% of its value; theta/P on it is
# noise with a decimal point. Wings below this produce burn readings in the
# hundreds of percent per day that are an artefact of the denominator.
MIN_PREMIUM = 5.0

# Longer than delta velocity's 15. ATM theta is proportional to sigma, so it
# inherits IV-solve noise linearly, where delta at ATM is nearly IV-insensitive
# (vanna ~ 0 there). At NIFTY ATM 6 DTE a routine +/-0.3 vol-point wobble moves
# theta by ~0.2/day while the deterministic drift is ~0.0018/min.
SMOOTH_N = 30

# Attribution windows are gated as a fraction of the requested horizon; the
# minute-cadence velocity path is gated in absolute minutes, matching the
# delta-velocity feature it mirrors.
GAP_MIN_FRACTION = 0.5
GAP_MAX_FRACTION = 2.0
GAP_MIN_MINUTES = 0.5
GAP_MAX_MINUTES = 2.0

BLANK_REASONS = ("no_theta", "insufficient_window", "short_gap", "long_gap")


def _norm_cdf(x: np.ndarray) -> np.ndarray:
    from scipy.special import ndtr

    return ndtr(x)


def _norm_pdf(x: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black_scholes_greeks(
    spot: np.ndarray,
    strike: np.ndarray,
    tte_years: np.ndarray,
    iv: np.ndarray,
    is_call: np.ndarray,
    *,
    risk_free_rate: float = RISK_FREE,
    dividend_yield: float = DIVIDEND_YIELD,
) -> dict[str, np.ndarray]:
    """Vectorised BS greeks, calendar theta, unrounded.

    Deliberately mirrors ``options.greeks_engine.compute_greeks`` term for term
    rather than importing it: a session is ~25,000 rows and the scalar path
    costs ~10s per underlying, which is too slow to sit behind a page load.
    ``tests/test_theta_decay.py`` asserts parity against ``compute_greeks`` so
    the duplication cannot silently drift.
    """
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    tte_years = np.asarray(tte_years, dtype=float)
    iv = np.asarray(iv, dtype=float)
    is_call = np.asarray(is_call, dtype=bool)

    r, q = float(risk_free_rate), float(dividend_yield)
    valid = (spot > 0) & (strike > 0) & (tte_years > 0) & (iv > 0)

    with np.errstate(divide="ignore", invalid="ignore"):
        sqrt_t = np.sqrt(tte_years)
        d1 = (np.log(spot / strike) + (r - q + 0.5 * iv * iv) * tte_years) / (iv * sqrt_t)
        d2 = d1 - iv * sqrt_t

        nd1 = _norm_pdf(d1)
        disc_q = np.exp(-q * tte_years)
        disc_r = np.exp(-r * tte_years)

        common = -spot * disc_q * nd1 * iv / (2.0 * sqrt_t)
        theta_call = common - r * strike * disc_r * _norm_cdf(d2) + q * spot * disc_q * _norm_cdf(d1)
        theta_put = common + r * strike * disc_r * _norm_cdf(-d2) - q * spot * disc_q * _norm_cdf(-d1)
        theta_yr = np.where(is_call, theta_call, theta_put)

        delta = np.where(is_call, disc_q * _norm_cdf(d1), disc_q * (_norm_cdf(d1) - 1.0))
        gamma = disc_q * nd1 / (spot * iv * sqrt_t)
        vega = spot * disc_q * nd1 * sqrt_t / 100.0

    def _clean(arr: np.ndarray) -> np.ndarray:
        out = np.asarray(arr, dtype=float)
        return np.where(valid & np.isfinite(out), out, np.nan)

    return {
        "theta": _clean(theta_yr / 365.0),
        "gamma": _clean(gamma),
        "vega": _clean(vega),
        ATTRIB_DELTA: _clean(delta),
    }


def _tte_years(frame: pd.DataFrame) -> np.ndarray:
    """Years to expiry per row, at that row's own timestamp.

    Vectorised against ``options.iv.time_to_expiry_years``: index options expire
    at 15:40 IST, and the per-row scalar call is the other half of the 10s that
    made the scalar path unusable.
    """
    expiry = pd.to_datetime(frame["expiry"].astype(str), format="mixed", utc=False)
    expiry_dt = (
        expiry.dt.tz_localize("Asia/Kolkata") + pd.Timedelta(hours=15, minutes=40)
    ).dt.tz_convert("UTC")
    ts = pd.to_datetime(frame["ts"], utc=True)
    seconds = (expiry_dt.to_numpy() - ts.to_numpy()) / np.timedelta64(1, "s")
    years = seconds.astype(float) / (365.0 * 24.0 * 3600.0)
    return np.where(years > 0, years, np.nan)


def ensure_greeks(rows: pd.DataFrame, *, with_delta: bool = False) -> pd.DataFrame:
    """Derive ``theta``/``gamma``/``vega`` (and optionally ``delta_q0``) from ``iv``.

    **Always derives, never trusts an existing column.** That is deliberate. The
    archive is written by ``analysis.delta_velocity.collector``, whose
    ``compute_greeks`` call takes the default q=0.012 — so any greek it stored
    would silently violate the q=0 convention this module's numbers were
    validated under, and the violation would be invisible at the call site. The
    scalar cost that would once have justified reusing a stored value is gone:
    the vectorised path derives a full session in ~0.3s.

    Deriving from the archived full-precision ``iv`` is also what lets the desk
    work over sessions collected before it existed.
    """
    if rows is None or rows.empty:
        return rows

    frame = rows.copy()
    wanted = [*GREEK_COLUMNS, ATTRIB_DELTA] if with_delta else list(GREEK_COLUMNS)

    tte = _tte_years(frame)
    frame["tte_years"] = tte
    derived = black_scholes_greeks(
        pd.to_numeric(frame["spot"], errors="coerce").to_numpy(),
        pd.to_numeric(frame["strike"], errors="coerce").to_numpy(),
        tte,
        pd.to_numeric(frame["iv"], errors="coerce").to_numpy(),
        frame["option_type"].astype(str).str.upper().eq("CE").to_numpy(),
    )
    for col in wanted:
        frame[col] = pd.Series(derived[col], index=frame.index)
    return frame


def burn_rate(rows: pd.DataFrame) -> pd.DataFrame:
    """Add ``burn_pct_day``: decay per calendar day as a percent of premium.

    Positive means premium is bleeding — the sign is flipped from theta so the
    number reads as "losing 8% of premium a day" rather than "-8".
    """
    if rows is None or rows.empty:
        return rows

    frame = ensure_greeks(rows)
    premium = pd.to_numeric(frame["ltp"], errors="coerce")
    theta = pd.to_numeric(frame["theta"], errors="coerce")

    burn = (-theta / premium) * 100.0
    frame["burn_pct_day"] = burn.where(premium >= MIN_PREMIUM)
    return frame


def decay_attribution(
    rows: pd.DataFrame,
    *,
    horizon_min: int = DEFAULT_HORIZON_MIN,
) -> pd.DataFrame:
    """P&L attribution per contract over non-overlapping ``horizon_min`` windows.

    For each consecutive pair of sampled observations within a group::

        dP_actual  = P_t - P_{t-1}
        pred_delta = delta_{t-1} * dS
        pred_gamma = 0.5 * gamma_{t-1} * dS^2
        pred_vega  = vega_{t-1} * (d_sigma * 100)   # vega is per 1% IV
        pred_theta = theta_{t-1} * dt_days          # theta is per day
        time_pnl   = dP_actual - pred_delta - pred_gamma - pred_vega
        residual   = time_pnl - pred_theta

    ``time_pnl`` is the premium change *not* explained by the underlying moving
    or the vol surface repricing — what a seller is actually being paid for.

    Windows are non-overlapping (every ``horizon_min``-th observation) rather
    than rolling, so each contributes one independent observation to the ratio
    of sums that ``capture_ratio`` forms.
    """
    if rows is None or rows.empty:
        return _empty_attribution()

    missing = [k for k in (*GROUP_KEYS, "ts", "ltp", "spot", "iv") if k not in rows.columns]
    if missing:
        raise ValueError(f"decay_attribution missing columns: {missing}")

    step = max(int(horizon_min), 1)
    frame = ensure_greeks(rows, with_delta=True)

    parts: list[pd.DataFrame] = []
    for keys, grp in frame.groupby(list(GROUP_KEYS), dropna=False):
        grp = grp.sort_values("ts").iloc[::step]
        if len(grp) < 2:
            continue

        ts = pd.to_datetime(grp["ts"], utc=True)
        gap_min = ts.diff().dt.total_seconds() / 60.0

        price = pd.to_numeric(grp["ltp"], errors="coerce")
        d_price = price.diff()
        d_spot = pd.to_numeric(grp["spot"], errors="coerce").diff()
        d_sigma = pd.to_numeric(grp["iv"], errors="coerce").diff()

        pred_delta = grp[ATTRIB_DELTA].shift(1) * d_spot
        pred_gamma = 0.5 * grp["gamma"].shift(1) * d_spot.pow(2)
        pred_vega = grp["vega"].shift(1) * (d_sigma * 100.0)
        pred_theta = grp["theta"].shift(1) * (gap_min / 1440.0)
        time_pnl = d_price - pred_delta - pred_gamma - pred_vega

        part = pd.DataFrame(
            {
                "ts": ts,
                "d_price": d_price,
                "pred_delta": pred_delta,
                "pred_gamma": pred_gamma,
                "pred_vega": pred_vega,
                "pred_theta": pred_theta,
                "time_pnl": time_pnl,
                "residual": time_pnl - pred_theta,
                "premium": price,
                "gap_min": gap_min,
            }
        )
        # A window materially shorter or longer than the horizon means the two
        # observations are not the sampling distance apart and their deltas are
        # not comparable with the rest of the session.
        ok = (gap_min >= step * GAP_MIN_FRACTION) & (gap_min <= step * GAP_MAX_FRACTION)
        part = part[ok.to_numpy()]
        for name, value in zip(GROUP_KEYS, keys, strict=False):
            part[name] = value
        parts.append(part.dropna(subset=["time_pnl", "pred_theta"]))

    usable = [p for p in parts if not p.empty]
    if not usable:
        return _empty_attribution()

    out = pd.concat(usable, ignore_index=True)
    return out.sort_values(["ts", *GROUP_KEYS]).reset_index(drop=True)


def _empty_attribution() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            *GROUP_KEYS, "ts", "d_price", "pred_delta", "pred_gamma", "pred_vega",
            "pred_theta", "time_pnl", "residual", "premium", "gap_min",
        ]
    )


def capture_ratio(attribution: pd.DataFrame) -> float | None:
    """Realized time-P&L over theoretical theta bleed.

    1.0 means the tape handed over exactly the decay the model quoted; below 1
    means premium held up better than theory, above 1 that it collapsed faster.

    A ratio of sums, not a mean of per-window ratios: an individual
    ``pred_theta`` can be near zero and its reciprocal unbounded.

    **Call this per DTE bucket, never on a pooled frame** — see the module
    docstring. ``attribution_by_dte`` does the split for you.
    """
    if attribution is None or attribution.empty:
        return None
    realized = pd.to_numeric(attribution["time_pnl"], errors="coerce").sum()
    theoretical = pd.to_numeric(attribution["pred_theta"], errors="coerce").sum()
    if not math.isfinite(float(theoretical)) or abs(float(theoretical)) < 1e-9:
        return None
    return round(float(realized) / float(theoretical), 4)


def dte_of(frame: pd.DataFrame) -> pd.Series:
    """Days to expiry, derived from the row's own session date."""
    expiry = pd.to_datetime(frame["expiry"].astype(str), format="mixed")
    session = pd.to_datetime(frame["session_date"].astype(str), format="mixed")
    return (expiry - session).dt.days


def capture_quality(
    theta_share: float | None,
    vega_share: float | None,
    time_windows: int | None = None,
) -> str:
    """Whether a capture ratio built on these term shares is worth reading.

    Returns ``too_few_windows`` (the shares are a single draw, so nothing can be
    judged yet), ``vega_dominated`` (the vol term is too large to subtract
    cleanly), ``theta_too_small`` (theta is below the noise floor of the
    decomposition), or ``ok``.

    Callers should surface the label rather than drop the row — a reader who can
    see *why* a bucket is untrustworthy learns something; a blank cell teaches
    nothing. Order matters: sample size is checked first, because a thin sample
    makes the other two verdicts meaningless rather than merely uncertain.
    """
    if theta_share is None or vega_share is None:
        return "no_data"
    if time_windows is not None and int(time_windows) < MIN_WINDOWS_FOR_QUALITY:
        return "too_few_windows"
    if float(vega_share) > MAX_VEGA_SHARE:
        return "vega_dominated"
    if float(theta_share) < MIN_THETA_SHARE:
        return "theta_too_small"
    return "ok"


def attribution_by_dte(attribution: pd.DataFrame) -> list[dict[str, Any]]:
    """Capture ratio, term shares and a quality verdict per DTE bucket.

    ``theta_share`` is what fraction of the absolute price movement the theta
    term accounts for — the honesty number behind ``quality``.
    """
    if attribution is None or attribution.empty:
        return []

    frame = attribution.copy()
    frame["dte"] = dte_of(frame)
    out: list[dict[str, Any]] = []
    for dte, grp in frame.groupby("dte"):
        gross = float(pd.to_numeric(grp["d_price"], errors="coerce").abs().sum())
        theta_share = round(float(grp["pred_theta"].abs().sum()) / gross, 4) if gross else None
        vega_share = round(float(grp["pred_vega"].abs().sum()) / gross, 4) if gross else None
        # Distinct clock windows, not rows: the rows in a bucket are one per
        # (contract, window), and the contracts within a window are not
        # independent observations of anything.
        time_windows = int(grp["ts"].nunique())
        out.append(
            {
                "dte": int(dte),
                "windows": int(len(grp)),
                "time_windows": time_windows,
                "capture": capture_ratio(grp),
                "quality": capture_quality(theta_share, vega_share, time_windows),
                "theta_share": theta_share,
                "vega_share": vega_share,
                "theoretical": round(float(grp["pred_theta"].sum()), 3),
                "realized": round(float(grp["time_pnl"].sum()), 3),
            }
        )
    return sorted(out, key=lambda r: r["dte"])


def theta_velocity_series(
    timestamps: pd.Series,
    theta: pd.Series,
    expected: pd.Series,
    *,
    smooth_n: int = SMOOTH_N,
) -> tuple[pd.Series, dict[str, int]]:
    """Clock-removed theta velocity for one contract — the delta-velocity analogue.

    ``expected`` is the theta the model predicts at ``t`` holding spot and IV at
    their ``t-1`` values, i.e. pure time passage. Subtracting it is the whole
    point: unlike delta, theta has a large deterministic component knowable a
    day in advance that carries no information. A naive ``|d_theta|/dt`` is part
    signal and part stopwatch, and near expiry the stopwatch dominates.
    """
    counts = dict.fromkeys(BLANK_REASONS, 0)

    frame = pd.DataFrame(
        {"ts": pd.to_datetime(timestamps), "theta": theta, "expected": expected}
    ).sort_values("ts")
    available = frame.dropna(subset=["theta", "expected"])
    counts["no_theta"] = int(len(frame) - len(available))

    if len(available) < smooth_n + 1:
        counts["insufficient_window"] = int(len(available))
        return pd.Series(dtype=float), counts

    surprise = (available["theta"] - available["expected"]).abs()
    smoothed = surprise.rolling(smooth_n).mean()
    counts["insufficient_window"] = int(smoothed.isna().sum())

    gap = available["ts"].diff().dt.total_seconds() / 60.0
    velocity = smoothed / gap

    too_short = gap < GAP_MIN_MINUTES
    too_long = gap > GAP_MAX_MINUTES
    counts["short_gap"] = int(too_short.sum())
    counts["long_gap"] = int(too_long.sum())
    velocity = velocity.mask(too_short | too_long)

    out = velocity.dropna()
    out.index = available["ts"].loc[out.index]
    out.name = "tau_t"
    return out, counts


def compute_theta_velocity(
    rows: pd.DataFrame,
    *,
    smooth_n: int = SMOOTH_N,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Apply :func:`theta_velocity_series` across every contract group.

    The expected-theta path is built here rather than in the collector because
    it depends on the *previous* minute's state, which a single snapshot does
    not have.
    """
    if rows is None or rows.empty:
        return pd.DataFrame(columns=[*GROUP_KEYS, "ts", "tau_t"]), dict.fromkeys(BLANK_REASONS, 0)

    frame = ensure_greeks(rows)
    totals = dict.fromkeys(BLANK_REASONS, 0)
    parts: list[pd.DataFrame] = []

    for keys, grp in frame.groupby(list(GROUP_KEYS), dropna=False):
        grp = grp.sort_values("ts")
        series, counts = theta_velocity_series(
            grp["ts"], grp["theta"], _expected_theta(grp), smooth_n=smooth_n
        )
        for reason, n in counts.items():
            totals[reason] += n
        if series.empty:
            continue
        part = pd.DataFrame({"ts": series.index, "tau_t": series.to_numpy()})
        for name, value in zip(GROUP_KEYS, keys, strict=False):
            part[name] = value
        parts.append(part)

    if not parts:
        return pd.DataFrame(columns=[*GROUP_KEYS, "ts", "tau_t"]), totals

    out = pd.concat(parts, ignore_index=True)
    return (
        out[[*GROUP_KEYS, "ts", "tau_t"]].sort_values(["ts", *GROUP_KEYS]).reset_index(drop=True),
        totals,
    )


def _expected_theta(grp: pd.DataFrame) -> pd.Series:
    """Theta at each minute if only the clock had advanced since the last one."""
    prev_spot = pd.to_numeric(grp["spot"], errors="coerce").shift(1).to_numpy()
    prev_iv = pd.to_numeric(grp["iv"], errors="coerce").shift(1).to_numpy()
    derived = black_scholes_greeks(
        prev_spot,
        pd.to_numeric(grp["strike"], errors="coerce").to_numpy(),
        _tte_years(grp),
        prev_iv,
        grp["option_type"].astype(str).str.upper().eq("CE").to_numpy(),
    )
    return pd.Series(derived["theta"], index=grp.index, dtype="float64")


def scalar_tte(expiry: str, as_of: Any) -> float | None:
    """Scalar tte, for callers that have one row rather than a frame."""
    return time_to_expiry_years(str(expiry), as_of=as_of)


def blank_summary(counts: dict[str, Any]) -> str:
    """One-line audit string, ordered worst-first."""
    if not counts:
        return "no blanks"
    ordered = sorted(counts.items(), key=lambda kv: -int(kv[1]))
    return ", ".join(f"{reason}={n}" for reason, n in ordered if n)
