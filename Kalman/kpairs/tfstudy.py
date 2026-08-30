"""Timeframe sweep: which sampling interval fits an index pair best?

"Best fit" is two questions, and they usually have different answers
--------------------------------------------------------------------
1. **Statistical fit.** Does the state-space model describe the data at this
   frequency? A correctly specified Kalman filter leaves innovations that are
   white, unit-variance and unautocorrelated. Where that breaks, the model is
   wrong -- and it breaks in opposite directions at the two ends of the sweep:

   - Too fine (5m): the spread is dominated by microstructure. Innovations are
     leptokurtic and typically *negatively* autocorrelated at lag 1 (bid-ask
     bounce, and index levels computed from last-traded prices that are not
     synchronous across constituents). z looks tradable and is largely quote
     noise.
   - Too coarse (4h): two bars a session, one of which is a 2h15m stub. The
     random-walk-beta model is fine but there is barely any data, the MLE is
     unstable, and the innovation variance is dominated by the overnight gap.

   Diagnostics: innovation std (should be ~1), excess kurtosis, Ljung-Box p on
   the first 10 lags, lag-1 autocorrelation, and the |z|>4 tail frequency.

2. **Economic fit.** Does it survive costs? Gross edge per trade shrinks roughly
   with the square root of the bar interval while cost per trade is flat, so
   there is a frequency floor below which a genuine signal still loses money.
   Reported as gross bps per trade against cost bps per trade.

Both are computed strictly out of sample on a rolling formation/trading split
measured in **sessions**, not bars, so a 250-session formation window is the
same calendar span at 5m as at 4h. Splitting on a fixed bar count would give 5m
a 13-day window and 4h a 500-day one and prove nothing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

import numpy as np
import pandas as pd

from . import bars as B
from . import kalman as K
from . import signals as S
from . import stats as ST
from .backtest import CostModel, metrics, run_pair
from .indices import pair_is_tradable


@dataclass
class TFConfig:
    # --- windows, in SESSIONS so they mean the same thing at every timeframe --
    formation_sessions: int = 250
    trading_sessions: int = 60

    # --- selection ---
    max_pairs: int = 8
    coint_p_max: float = 0.05
    use_fdr: bool = True
    fdr: float = 0.10
    hl_min_sessions: float = 0.05     # ~20 minutes
    hl_max_sessions: float = 10.0     # two trading weeks
    max_hurst: float = 0.55
    min_corr: float = 0.40
    tradable_only: bool = False       # both legs must have listed futures

    # --- signal ---
    method: str = "kalman"            # "kalman" | "ols"
    ols_window_sessions: float = 3.0
    entry_z: float = 2.0
    exit_z: float = 0.5
    stop_z: float = 4.0
    max_hold_sessions: float = 5.0
    exec_lag: int = 1
    intraday_only: bool = True        # square off before each session close
    block_open_entries: bool = False  # refuse entries triggered by the first bar
    reverse: bool = False             # flip the sign: trade the spread as momentum

    # --- filter ---
    use_mle: bool = True
    obs_var: float = 1e-5
    var_alpha: float = 1e-10
    var_beta: float = 1e-9

    # --- execution ---
    freeze_hedge: bool = True
    costs: CostModel = field(default_factory=CostModel.index_futures)


@dataclass
class TFResult:
    timeframe: str
    config: TFConfig
    bars_per_session: float
    bars_per_year: float
    daily: pd.DataFrame
    summary: dict
    diagnostics: dict
    selections: pd.DataFrame
    trades: pd.DataFrame


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def _sessions_to_bars(sessions: float, bps: float, minimum: int = 1) -> int:
    return max(minimum, int(round(sessions * bps)))


def _eg_maxlag(bps: float) -> int:
    """Engle-Granger augmentation order: about one session of lags, capped at 20.

    Scaling with the timeframe rather than fixing a constant keeps the test
    comparable across the sweep -- 6 lags means 30 minutes at 5m and two days
    at 4h, which are not the same hypothesis.
    """
    return int(np.clip(round(bps), 2, 20))


def _select(px: pd.DataFrame, cfg: TFConfig, bps: float) -> pd.DataFrame:
    """Cointegration screen over one formation window.

    Every bar-count threshold is derived from the session-based config, so the
    same economics are being asked for at every timeframe.
    """
    cols = [c for c in px.columns if px[c].notna().sum() >= 0.8 * len(px)]
    if len(cols) < 2:
        return pd.DataFrame()

    cands = list(combinations(sorted(cols), 2))
    if cfg.tradable_only:
        cands = [(a, b) for a, b in cands if pair_is_tradable(a, b)]

    maxlag = _eg_maxlag(bps)
    rows = []
    for a, b in cands:
        st = ST.pair_stats(px[a], px[b], a, b, maxlag=maxlag)
        if st is not None:
            rows.append(st.__dict__)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)

    df["half_life_sessions"] = df["half_life"] / bps
    df = df[(df["half_life"] >= cfg.hl_min_sessions * bps)
            & (df["half_life"] <= cfg.hl_max_sessions * bps)]
    df = df[df["hurst"] <= cfg.max_hurst]
    df = df[df["corr"] >= cfg.min_corr]
    if df.empty:
        return df

    df = df.reset_index(drop=True)
    if cfg.use_fdr and len(df) >= 20:
        df = df[ST.benjamini_hochberg(df["coint_p"].to_numpy(), fdr=cfg.fdr)]
    else:
        df = df[df["coint_p"] <= cfg.coint_p_max]
    if df.empty:
        return df

    return df.sort_values("coint_p").head(cfg.max_pairs).reset_index(drop=True)


# --------------------------------------------------------------------------
# per pair-window: hedge estimation, shared by every variant
# --------------------------------------------------------------------------
def _hedge_paths(px_form, px_trade, x, y, cfg: TFConfig, bps: float):
    """Both hedge estimators for one pair-window, computed once.

    The Kalman MLE is the expensive step and the cointegration screen ahead of
    it is more expensive still. Every variant in the sweep -- costed, zero-cost,
    overnight, OLS -- shares the same formation window and the same pair, so
    fitting per variant would repeat that work four times for identical answers.
    Worse than slow: it would let the variants drift onto different pairs if any
    selection input were ever made variant-specific, and then the comparison
    would stop being like for like.

    Returns ``(trade_frame, {"kalman": (z, beta), "ols": (z, beta)})``.
    """
    form = pd.concat([px_form[x], px_form[y]], axis=1, keys=["x", "y"]).dropna()
    trade = pd.concat([px_trade[x], px_trade[y]], axis=1, keys=["x", "y"]).dropna()
    if len(form) < 200 or len(trade) < 20:
        return None, None

    lfx, lfy = np.log(form["x"].to_numpy()), np.log(form["y"].to_numpy())
    a0, b0, _ = ST.ols_hedge(lfx, lfy)

    full = pd.concat([form, trade])
    full = full[~full.index.duplicated(keep="first")].sort_index()
    lx, ly = np.log(full["x"].to_numpy()), np.log(full["y"].to_numpy())
    n_form = len(form)

    paths: dict[str, tuple[np.ndarray, np.ndarray]] = {}

    if cfg.use_mle:
        p = K.fit_kalman_mle(lfx, lfy, init_state=(a0, b0))
        obs_var, sv = p["obs_var"], (p["var_alpha"], p["var_beta"])
    else:
        obs_var, sv = cfg.obs_var, (cfg.var_alpha, cfg.var_beta)
    res = K.kalman_hedge(lx, ly, obs_var=obs_var, state_var=sv,
                         init_state=(a0, b0), init_cov=1e-3,
                         burn_in=min(50, n_form // 4))
    paths["kalman"] = (res.zscore[n_form:], res.prior_beta[n_form:])

    w = _sessions_to_bars(cfg.ols_window_sessions, bps, minimum=20)
    r = K.rolling_ols_hedge(lx, ly, window=w)
    paths["ols"] = (
        S.rolling_z(r["resid"], window=w, min_periods=max(10, w // 2))[n_form:],
        np.concatenate([[np.nan], r["beta"][:-1]])[n_form:],
    )

    m = min(len(trade), min(len(v[0]) for v in paths.values()))
    trade = trade.iloc[-m:]
    paths = {k: (v[0][-m:], v[1][-m:]) for k, v in paths.items()}
    return trade, paths


def _evaluate(trade, z, beta, cfg: TFConfig, bps: float, x: str, y: str):
    """Position state machine plus P&L for one variant on a precomputed path."""
    idx = pd.DatetimeIndex(trade.index)
    flat = B.session_boundaries(idx) if cfg.intraday_only else None
    no_entry = B.session_opens(idx) if cfg.block_open_entries else None
    pos = S.positions_from_z(
        z, entry=cfg.entry_z, exit_=cfg.exit_z, stop=cfg.stop_z,
        max_hold=_sessions_to_bars(cfg.max_hold_sessions, bps, minimum=2),
        exec_lag=cfg.exec_lag, force_flat=flat, no_entry=no_entry,
    )["position"]
    if cfg.reverse:
        # Trade the extreme as continuation rather than reversion. A
        # diagnostic, not a strategy proposal: a mean-reversion rule with a 30%
        # hit rate and a negative average trade might be an edge pointing the
        # other way, and flipping the sign is the way to find out.
        #
        # Read it knowing that **gross** P&L negates exactly, so gross Sharpe is
        # exactly the mirror and tells you nothing new -- its only use is
        # confirming the P&L identity has no sign asymmetry. The informative
        # comparison is *net*: costs stay positive under the flip, so
        # net = -gross - cost rather than -(gross - cost), and a spread whose
        # reversed net still loses has no edge in either direction.
        pos = -pos
    costs = CostModel(
        bps_per_turnover=cfg.costs.bps_per_turnover,
        roll_bps_per_month=cfg.costs.roll_bps_per_month,
        apply_roll=cfg.costs.apply_roll and not cfg.intraday_only,
        bars_per_session=bps,
    )
    return run_pair(trade["x"], trade["y"], pos, beta, z,
                    x_name=x, y_name=y, costs=costs, freeze_hedge=cfg.freeze_hedge)


# --------------------------------------------------------------------------
# walk-forward
# --------------------------------------------------------------------------
class _Acc:
    """Per-variant accumulator across windows."""

    def __init__(self) -> None:
        self.net: list[pd.Series] = []
        self.gross: list[pd.Series] = []
        self.trades: list[dict] = []
        self.z: list[np.ndarray] = []
        self.z_open: list[np.ndarray] = []   # mask: bar is the session's first
        self.bars: list[pd.DatetimeIndex] = []


def run_grid(
    px5: pd.DataFrame,
    timeframe: str,
    variants: dict[str, TFConfig],
    *,
    verbose: bool = False,
) -> dict[str, TFResult]:
    """Walk-forward at one timeframe, evaluating every variant on shared pairs.

    Selection and hedge estimation run once per pair-window; only the position
    rules and the cost model differ between variants. That makes the comparison
    exact: any difference in the output is attributable to the thing the variant
    changed and to nothing else.
    """
    if not variants:
        raise ValueError("no variants")
    ref = next(iter(variants.values()))

    px = B.resample(px5, timeframe)
    idx = pd.DatetimeIndex(px.index)
    bps = len(px) / idx.normalize().nunique()
    ppy = B.bars_per_year(idx)

    form_bars = _sessions_to_bars(ref.formation_sessions, bps, minimum=250)
    trade_bars = _sessions_to_bars(ref.trading_sessions, bps, minimum=40)

    accs = {name: _Acc() for name in variants}
    sel_all: list[pd.DataFrame] = []
    n_windows = 0

    start = 0
    while start + form_bars + trade_bars <= len(px):
        f0, f1 = start, start + form_bars
        t0, t1 = f1, min(f1 + trade_bars, len(px))
        px_form, px_trade = px.iloc[f0:f1], px.iloc[t0:t1]
        label = f"{idx[t0]:%Y-%m-%d}..{idx[t1 - 1]:%Y-%m-%d}"

        sel = _select(px_form, ref, bps)
        if verbose:
            print(f"    [{timeframe}] {label}  pairs={len(sel)}", flush=True)

        if not sel.empty:
            sel = sel.copy()
            sel["window"] = label
            sel["timeframe"] = timeframe
            sel_all.append(sel)

            for _, prow in sel.iterrows():
                x, y = str(prow["x"]), str(prow["y"])
                trade, paths = _hedge_paths(px_form, px_trade, x, y, ref, bps)
                if trade is None:
                    continue
                for name, cfg in variants.items():
                    z, beta = paths[cfg.method]
                    bt = _evaluate(trade, z, beta, cfg, bps, x, y)
                    acc = accs[name]
                    nm = f"{y}/{x}|{label}"
                    acc.net.append(pd.Series(bt.ret_net, index=bt.index, name=nm))
                    acc.gross.append(pd.Series(bt.ret_gross, index=bt.index, name=nm))
                    acc.z.append(z)
                    acc.z_open.append(B.session_opens(pd.DatetimeIndex(bt.index)))
                    acc.bars.append(pd.DatetimeIndex(bt.index))
                    for tr in bt.trades:
                        acc.trades.append({**tr, "pair": f"{y}/{x}",
                                           "window": label, "timeframe": timeframe})

        n_windows += 1
        start += trade_bars

    sel_df = pd.concat(sel_all, ignore_index=True) if sel_all else pd.DataFrame()
    return {name: _assemble(timeframe, cfg, accs[name], sel_df, bps, ppy,
                            n_windows, idx)
            for name, cfg in variants.items()}


def _assemble(timeframe, cfg, acc: _Acc, sel_df, bps, ppy, n_windows, idx) -> TFResult:
    if not acc.net:
        return TFResult(timeframe, cfg, bps, ppy, pd.DataFrame(),
                        {"sharpe": np.nan, "n_trades": 0, "timeframe": timeframe},
                        {"timeframe": timeframe}, sel_df, pd.DataFrame())

    # Sum the pair-window return streams onto one portfolio series.
    #
    # Concatenating along axis=1 and summing across columns would be the
    # obvious way and is a memory trap: at 5-minute sampling with 8 pairs over
    # 44 windows that is a 212k x 352 dense frame, ~600 MB of mostly-NaN. Stack
    # them long instead and group by timestamp -- same answer, O(total bars).
    long_net = pd.concat(acc.net)
    long_gross = pd.concat(acc.gross)
    net = long_net.groupby(level=0).sum() / cfg.max_pairs
    gross = long_gross.groupby(level=0).sum() / cfg.max_pairs

    # Reindex onto every bar the walk-forward was live for, including bars where
    # the screen selected nothing. Those are real flat days: dropping them would
    # shorten the sample and inflate CAGR and Sharpe by pretending the capital
    # was never idle.
    live = pd.DatetimeIndex(sorted(set().union(*[set(b) for b in acc.bars])))
    net = net.reindex(live, fill_value=0.0)
    gross = gross.reindex(live, fill_value=0.0)

    daily = pd.DataFrame({"ret_net": net, "ret_gross": gross})
    daily["equity"] = (1 + daily["ret_net"]).cumprod()

    summary = metrics(daily["ret_net"], acc.trades, periods_per_year=ppy)
    gsum = metrics(daily["ret_gross"], None, periods_per_year=ppy)
    summary["sharpe_gross"] = gsum["sharpe"]
    summary["cagr_gross"] = gsum["cagr"]
    summary["timeframe"] = timeframe
    summary["bars_per_session"] = round(bps, 2)
    summary["bars_per_year"] = round(ppy)
    summary["n_windows"] = n_windows
    summary["n_bars"] = len(daily)

    if acc.trades:
        tdf = pd.DataFrame(acc.trades)
        n_tr = len(tdf)
        cost_rt_bps = 2.0 * cfg.costs.bps_per_turnover
        summary["gross_bps_per_trade"] = 1e4 * float(gross.sum()) * cfg.max_pairs / n_tr
        summary["net_bps_per_trade"] = 1e4 * float(net.sum()) * cfg.max_pairs / n_tr
        summary["cost_bps_per_trade"] = cost_rt_bps
        summary["edge_over_cost"] = (summary["gross_bps_per_trade"] / cost_rt_bps
                                     if cost_rt_bps else np.inf)
        summary["trades_per_session"] = n_tr / max(1, idx.normalize().nunique())
        summary["median_hold_bars"] = float(tdf["bars"].median())
        summary["median_hold_sessions"] = float(tdf["bars"].median() / bps)

    diagnostics = innovation_diagnostics(acc.z)
    diagnostics.update(_gap_diagnostics(acc.z, acc.z_open))
    if sel_df is not None and not sel_df.empty:
        diagnostics["median_half_life_sessions"] = float(sel_df["half_life_sessions"].median())
        diagnostics["median_coint_p"] = float(sel_df["coint_p"].median())
        diagnostics["median_hurst"] = float(sel_df["hurst"].median())
        diagnostics["median_resid_sd_bps"] = float(sel_df["resid_sd"].median() * 1e4)
        diagnostics["pairs_per_window"] = float(len(sel_df) / max(1, n_windows))
    diagnostics["timeframe"] = timeframe

    return TFResult(timeframe=timeframe, config=cfg, bars_per_session=bps,
                    bars_per_year=ppy, daily=daily, summary=summary,
                    diagnostics=diagnostics, selections=sel_df,
                    trades=pd.DataFrame(acc.trades))


def run_timeframe(px5, timeframe, cfg: TFConfig, *, verbose: bool = False) -> TFResult:
    """Single-config convenience wrapper around ``run_grid``."""
    return run_grid(px5, timeframe, {"only": cfg}, verbose=verbose)["only"]


def sweep(px5: pd.DataFrame, cfg: TFConfig, timeframes=None, *,
          verbose: bool = True) -> dict[str, TFResult]:
    """One config across every timeframe."""
    out: dict[str, TFResult] = {}
    for tf in (timeframes or list(B.TIMEFRAMES)):
        if verbose:
            print(f"  -> {tf}", flush=True)
        out[tf] = run_timeframe(px5, tf, cfg)
    return out


def sweep_grid(px5: pd.DataFrame, variants: dict[str, TFConfig], timeframes=None,
               *, verbose: bool = True) -> dict[str, dict[str, TFResult]]:
    """Every variant across every timeframe, sharing selection and hedge fits.

    Returns ``{variant: {timeframe: TFResult}}``.
    """
    per_tf: dict[str, dict[str, TFResult]] = {}
    for tf in (timeframes or list(B.TIMEFRAMES)):
        if verbose:
            print(f"  -> {tf}", flush=True)
        per_tf[tf] = run_grid(px5, tf, variants, verbose=verbose)

    out: dict[str, dict[str, TFResult]] = {name: {} for name in variants}
    for tf, res in per_tf.items():
        for name, r in res.items():
            out[name][tf] = r
    return out


def sweep_table(results: dict[str, TFResult]) -> pd.DataFrame:
    rows = []
    for tf, r in results.items():
        row = {"timeframe": tf}
        row.update({k: v for k, v in r.summary.items() if k != "timeframe"})
        row.update({f"fit_{k}": v for k, v in r.diagnostics.items() if k != "timeframe"})
        rows.append(row)
    df = pd.DataFrame(rows).set_index("timeframe")
    return df.loc[[tf for tf in B.TIMEFRAMES if tf in df.index]]


# --------------------------------------------------------------------------
# fit diagnostics
# --------------------------------------------------------------------------
def innovation_diagnostics(z_samples: list[np.ndarray]) -> dict:
    """Whiteness and calibration of the out-of-sample innovations.

    This is the "does the model fit" half of the study, and it is the half P&L
    cannot answer -- a strategy can be profitable on a badly specified filter
    (by accident, or because the mis-specification happens to lean the right
    way) and unprofitable on a well specified one (because costs).

    - ``z_std``: 1.0 means the filter's own forecast variance Q_t is right. Far
      above 1 means Q is too small: the model underestimates how wrong it is,
      so a z of 2 is not really a 2-sigma event and entries fire far more often
      than the threshold implies.
    - ``z_kurt``: excess kurtosis. Fat innovations at fine sampling are the
      microstructure signature.
    - ``lb_p``: Ljung-Box p on 10 lags. Below 0.05 the innovations are
      autocorrelated -- structure the state-space model is not capturing.
      **Do not compare this across timeframes.** A 5-minute run has ~600k
      innovations and a 4-hour run has ~16k, and with 600k observations the
      test rejects on an autocorrelation of 0.005, which is real and utterly
      irrelevant. Read the effect size (``ac1``) across the sweep and keep
      ``lb_p`` for comparing pairs within one timeframe.
    - ``ac1``: lag-1 autocorrelation, signed, so the direction is visible.
      Negative points at bid-ask bounce; positive at genuine momentum the
      mean-reversion rule will fight.
    """
    z = np.concatenate([s[np.isfinite(s)] for s in z_samples]) if z_samples else np.array([])
    if z.size < 100:
        return {"z_std": np.nan, "z_kurt": np.nan, "lb_p": np.nan,
                "ac1": np.nan, "tail_4sig": np.nan, "n": int(z.size)}

    s = pd.Series(z)
    ac1 = float(s.autocorr(lag=1))
    try:
        from statsmodels.stats.diagnostic import acorr_ljungbox

        lb_p = float(acorr_ljungbox(z, lags=[10], return_df=True)["lb_pvalue"].iloc[0])
    except Exception:  # noqa: BLE001
        lb_p = np.nan

    return {
        "z_std": float(s.std(ddof=1)),
        "z_kurt": float(s.kurt()),
        "lb_p": lb_p,
        "ac1": ac1,
        "tail_4sig": float((s.abs() > 4).mean()),
        "n": int(z.size),
    }


def _gap_diagnostics(z_samples: list[np.ndarray],
                     open_masks: list[np.ndarray]) -> dict:
    """Split the innovation diagnostics by session-open vs mid-session bars.

    The filter has no notion of a trading session: it prices the forecast error
    on the 09:20 bar with the same variance it uses at 11:40, even though that
    error contains the entire overnight move. The result is a z-score that is
    systematically inflated on the first bar of every day -- and since a
    mean-reversion rule fires on large |z|, the strategy is disproportionately
    entering on gaps it has mispriced.

    If ``z_std_open`` is much larger than ``z_std_mid``, that is the finding,
    and the fix is a session-aware observation variance (or simply not trading
    the first bar), not a different timeframe.
    """
    if not z_samples or not open_masks:
        return {}
    zo, zm = [], []
    for z, m in zip(z_samples, open_masks):
        if z.size != m.size:
            continue
        f = np.isfinite(z)
        zo.append(z[f & m])
        zm.append(z[f & ~m])
    if not zo:
        return {}
    a = np.concatenate(zo)
    b = np.concatenate(zm)
    if a.size < 50 or b.size < 50:
        return {}
    return {
        "z_std_open": float(np.std(a, ddof=1)),
        "z_std_mid": float(np.std(b, ddof=1)),
        "gap_inflation": float(np.std(a, ddof=1) / np.std(b, ddof=1)),
        "kurt_open": float(pd.Series(a).kurt()),
        "kurt_mid": float(pd.Series(b).kurt()),
    }


def fit_scan(
    px5: pd.DataFrame,
    timeframes=None,
    *,
    pairs: list[tuple[str, str]] | None = None,
    formation_sessions: int = 250,
    trading_sessions: int = 60,
    tradable_only: bool = True,
    max_pairs_scanned: int | None = None,
    seed: int = 11,
) -> pd.DataFrame:
    """Model-fit diagnostics only: no trading rules, no costs, no selection.

    This answers "which timeframe fits best" in the narrow statistical sense,
    decoupled from every discretionary choice in the strategy. For each pair and
    each rolling window it fits the filter on the formation slice, runs it
    causally over the trading slice, and scores the out-of-sample innovations.

    Worth having separately because a P&L-based answer confounds model fit with
    entry thresholds, holding limits, session handling and the cost model. Move
    the entry z from 2.0 to 2.5 and the "best" timeframe can move with it. The
    likelihood and the whiteness of the innovations do not care about any of it.

    Columns
    -------
    z_std, z_kurt, ac1, lb_p, tail_4sig
        innovation calibration -- see ``innovation_diagnostics``
    ll_per_bar
        out-of-sample Gaussian log-likelihood per bar. **Not** comparable across
        timeframes on its own (different data, differently scaled densities),
        but very comparable across pairs within one timeframe.
    half_life_sessions
        reversion speed in wall-clock terms, which *is* comparable across the
        sweep and is the number to look at when picking a holding horizon.

    ``max_pairs_scanned`` takes a deterministic random subset of the candidate
    pairs. A 15-index sectoral universe is 105 pairs, and at 44 windows x 6
    timeframes that is 27,720 MLE fits -- around 15 hours. The diagnostics being
    measured are distributional (median z_std, median kurtosis, fraction white),
    so a fixed random 20-pair sample estimates them perfectly adequately. The
    sample is drawn once and reused across every timeframe, so the timeframes
    are compared on the *same* pairs.
    """
    # Draw the pair sample once, outside the timeframe loop, so every timeframe
    # is scored on an identical set of pairs.
    _all_cols = [c for c in px5.columns if px5[c].notna().sum() >= 0.8 * len(px5)]
    _cand = pairs or list(combinations(sorted(_all_cols), 2))
    if tradable_only and pairs is None:
        _cand = [(a, b) for a, b in _cand if pair_is_tradable(a, b)]
    if max_pairs_scanned is not None and len(_cand) > max_pairs_scanned:
        rng = np.random.default_rng(seed)
        pick = rng.choice(len(_cand), size=max_pairs_scanned, replace=False)
        _cand = [_cand[i] for i in sorted(pick)]
        print(f"[fit_scan] sampled {len(_cand)} of the candidate pairs (seed={seed})")

    rows = []
    for tf in (timeframes or list(B.TIMEFRAMES)):
        px = B.resample(px5, tf)
        idx = pd.DatetimeIndex(px.index)
        bps = len(px) / idx.normalize().nunique()
        form_bars = _sessions_to_bars(formation_sessions, bps, minimum=250)
        trade_bars = _sessions_to_bars(trading_sessions, bps, minimum=40)

        for x, y in _cand:
            if x not in px.columns or y not in px.columns:
                continue
            zs, lls, hls = [], [], []
            n_win = 0
            start = 0
            while start + form_bars + trade_bars <= len(px):
                f0, f1 = start, start + form_bars
                t0, t1 = f1, min(f1 + trade_bars, len(px))
                form = pd.concat([px[x].iloc[f0:f1], px[y].iloc[f0:f1]],
                                 axis=1, keys=["x", "y"]).dropna()
                trade = pd.concat([px[x].iloc[t0:t1], px[y].iloc[t0:t1]],
                                  axis=1, keys=["x", "y"]).dropna()
                start += trade_bars
                if len(form) < 200 or len(trade) < 20:
                    continue

                lfx, lfy = np.log(form["x"].to_numpy()), np.log(form["y"].to_numpy())
                a0, b0, resid = ST.ols_hedge(lfx, lfy)
                hl = ST.half_life(resid)
                if np.isfinite(hl):
                    hls.append(hl / bps)

                p = K.fit_kalman_mle(lfx, lfy, init_state=(a0, b0))
                full = pd.concat([form, trade])
                full = full[~full.index.duplicated(keep="first")].sort_index()
                lx, ly = np.log(full["x"].to_numpy()), np.log(full["y"].to_numpy())
                res = K.kalman_hedge(lx, ly, obs_var=p["obs_var"],
                                     state_var=(p["var_alpha"], p["var_beta"]),
                                     init_state=(a0, b0), init_cov=1e-3)
                n_form = len(form)
                e, q = res.innovation[n_form:], res.innov_var[n_form:]
                if e.size < 20:
                    continue
                zs.append(e / np.sqrt(q))
                lls.append(float(np.mean(-0.5 * (np.log(2 * np.pi * q) + e * e / q))))
                n_win += 1

            if not zs:
                continue
            rows.append({
                "timeframe": tf, "x": x, "y": y, "windows": n_win,
                "bars_per_session": round(bps, 2),
                "ll_per_bar": float(np.mean(lls)) if lls else np.nan,
                "half_life_sessions": float(np.median(hls)) if hls else np.nan,
                **innovation_diagnostics(zs),
            })
    return pd.DataFrame(rows)
