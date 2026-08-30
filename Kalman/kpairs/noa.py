"""Faithful replica of Noa_zVWAP_Nifty_Backtest.pine, for cost sensitivity.

Why replicate it in Python at all
---------------------------------
TradingView's Strategy Tester gives you one number per settings combination and
makes you click for each one. The question that decides whether this strategy is
viable -- how P&L responds to transaction cost and to execution lag -- needs a
grid, and the grid is the answer, not any single cell.

The two knobs that matter are the two the Pine version currently gets wrong:

* ``process_orders_on_close = true`` fills at the same close that produced the
  signal. That is zero execution lag: you compute z from a close and transact at
  that identical close. Not lookahead in the strict sense -- the close is known
  when it happens -- but not reachable either, and in the Kalman index-pair study
  one bar of lag was worth more than every other parameter combined.
* ``default_qty_type = strategy.cash`` sizes in rupees and ignores lot size, so
  the reported P&L belongs to a fractional number of contracts nobody can trade.
  Irrelevant to *returns*, which is what this module measures, but it means the
  Pine equity curve is not a rupee figure you can act on.

Approximation, stated plainly
-----------------------------
NSE index volume is 0 on every bar (measured: 212,626 of 212,626 for NIFTY), so
on a spot index the volume-weighted mean collapses to 0/0. ``volume_weighted=False``
reproduces what the strategy effectively computes there: a simple moving average
for the z mean, and a plain average price since entry for the anchored VWAP.
Run it with ``volume_weighted=True`` on a real-volume symbol to confirm the
weighting does not change the conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class NoaConfig:
    length: int = 21
    policy: str = "Flip Cross"          # "Flip Cross" | "Mean Revert"
    top_upper: float = 2.5
    upper_z: float = 1.5
    lower_z: float = -1.5
    bot_lower: float = -2.5
    flip: float = 0.0
    flip_range: float = 0.5

    # exits
    use_avwap_exit: bool = True
    exit_mode: str = "Cross"            # "Cross" | "Close beyond"
    min_bars_in_trade: int = 1
    exit_in_flip: bool = False          # script 1's exit, for the comparison
    sl_pts: float = 0.0                 # 0 = off
    tp_pts: float = 0.0

    # session
    use_session: bool = True
    entry_after_min: int = 9 * 60 + 20  # earliest entry, minutes past midnight
    flat_after_min: int = 15 * 60 + 20  # force flat

    # execution
    exec_lag: int = 1                   # 0 = fill on the signal bar's close
    cost_bps_per_side: float = 2.0      # 0.02% -> 2 bp, as the Pine sets it
    slippage_bps_per_side: float = 0.2

    long_ok: bool = True
    short_ok: bool = True
    volume_weighted: bool = True


def alpha_beta(z: np.ndarray, gain: float = 100.0) -> np.ndarray:
    """The script's ``kf`` series. Not a Kalman filter -- an alpha-beta (g-h)
    filter with fixed gains and no covariance:

        alpha = sqrt(gain/5000)   position gain
        beta  = gain/10000        velocity gain

    Reproduced exactly, including the single-update ordering the script fixed
    (both the position and velocity corrections must use the *previous* state).
    Note there is no innovation variance here, so nothing standardises the
    output -- ``kf`` is a smoother applied to an already-standardised z, which
    adds lag without adding information. Both scripts default to not using it.
    """
    a = float(np.sqrt(gain / 5000.0))
    b = float(gain / 10000.0)
    n = z.size
    kf = np.full(n, np.nan)
    prev_kf, velo = np.nan, 0.0
    for t in range(n):
        zt = z[t]
        if not np.isfinite(zt):
            continue
        base = zt if not np.isfinite(prev_kf) else prev_kf
        dk = zt - base
        velo = velo + b * dk
        prev_kf = base + dk * a + velo
        kf[t] = prev_kf
    return kf


def zscore(df: pd.DataFrame, cfg: NoaConfig) -> np.ndarray:
    """The script's z: price against a rolling (volume-weighted) mean.

    Two properties worth naming, neither of them bugs but both shaping results:

    * The current bar is inside its own mean and sigma. With length 21 it
      contributes 1/21 of the mean it is measured against, which damps extremes
      slightly. Excluding it would make z larger and fire entries sooner.
    * The mean is volume weighted but the dispersion is not -- the script
      computes ``sma((src - vwMean)^2)``, an unweighted second moment around a
      weighted first moment. Internally inconsistent; ``volume_weighted`` here
      applies the weights to both so the two can be compared.
    """
    src = df["close"].to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    n = cfg.length
    s = pd.Series(src)

    if cfg.volume_weighted and np.nansum(vol) > 0:
        v = pd.Series(np.where(vol > 0, vol, np.nan))
        num = (s * v).rolling(n).mean()
        den = v.rolling(n).mean()
        mean = (num / den).to_numpy()
        dev2 = pd.Series((src - mean) ** 2 * v.to_numpy())
        var = (dev2.rolling(n).mean() / den).to_numpy()
    else:
        mean = s.rolling(n).mean().to_numpy()
        var = pd.Series((src - mean) ** 2).rolling(n).mean().to_numpy()

    sd = np.sqrt(var)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = np.where((sd > 0) & np.isfinite(sd), (src - mean) / sd, np.nan)
    return z


def _crossover(a: np.ndarray, level: float) -> np.ndarray:
    prev = np.concatenate([[np.nan], a[:-1]])
    return (prev <= level) & (a > level)


def _crossunder(a: np.ndarray, level: float) -> np.ndarray:
    prev = np.concatenate([[np.nan], a[:-1]])
    return (prev >= level) & (a < level)


def run(df: pd.DataFrame, cfg: NoaConfig) -> dict:
    """Bar-by-bar replica. Returns per-bar returns, the trade list and metrics.

    Position convention: ``state`` is decided on bar t and, with
    ``exec_lag = 1``, the fill happens at the close of bar t+1. Returns accrue
    from the fill bar onward, so nothing is earned on the bar that produced the
    signal -- which is the whole difference from the Pine version.
    """
    idx = pd.DatetimeIndex(df.index)
    close = df["close"].to_numpy(dtype=float)
    hlc3 = ((df["high"] + df["low"] + df["close"]) / 3.0).to_numpy(dtype=float)
    vol = df["volume"].to_numpy(dtype=float)
    vw = np.where(vol > 0, vol, 1.0)
    n = len(df)

    z = zscore(df, cfg)
    tod = idx.hour * 60 + idx.minute
    day = idx.normalize()
    new_day = np.concatenate([[True], day[1:] != day[:-1]])

    if cfg.policy == "Flip Cross":
        raw_buy = _crossover(z, cfg.flip)
        raw_sell = _crossunder(z, cfg.flip)
    else:
        raw_buy = _crossover(z, cfg.bot_lower) | _crossover(z, cfg.lower_z)
        raw_sell = _crossunder(z, cfg.top_upper) | _crossunder(z, cfg.upper_z)

    can_enter = np.ones(n, dtype=bool)
    force_flat = np.zeros(n, dtype=bool)
    if cfg.use_session:
        can_enter = tod >= cfg.entry_after_min
        force_flat = tod >= cfg.flat_after_min

    flip_hi = cfg.flip + cfg.flip_range
    flip_lo = cfg.flip - cfg.flip_range

    target = np.zeros(n)          # decision on bar t
    anchor_num = 0.0
    anchor_den = 0.0
    avwap = np.nan
    state = 0
    bars_in = 0
    entry_px = np.nan
    trades: list[dict] = []
    entry_i = -1

    for t in range(n):
        if state != 0:
            anchor_num += hlc3[t] * vw[t]
            anchor_den += vw[t]
            avwap = anchor_num / anchor_den if anchor_den else np.nan
            bars_in += 1

        exit_now = False
        reason = ""
        if state != 0:
            if force_flat[t] or (cfg.use_session and new_day[t]):
                exit_now, reason = True, "session"
            elif cfg.exit_in_flip and np.isfinite(z[t]) and flip_lo <= z[t] <= flip_hi:
                exit_now, reason = True, "flip"
            elif (cfg.use_avwap_exit and bars_in > cfg.min_bars_in_trade
                  and np.isfinite(avwap)):
                if cfg.exit_mode == "Cross":
                    prev_rel = close[t - 1] - avwap if t > 0 else 0.0
                    cur_rel = close[t] - avwap
                    hit = (state == 1 and prev_rel >= 0 and cur_rel < 0) or \
                          (state == -1 and prev_rel <= 0 and cur_rel > 0)
                else:
                    hit = (state == 1 and close[t] < avwap) or \
                          (state == -1 and close[t] > avwap)
                if hit:
                    exit_now, reason = True, "avwap"
            if not exit_now and cfg.sl_pts > 0 and np.isfinite(entry_px):
                move = (close[t] - entry_px) * state
                if move <= -cfg.sl_pts:
                    exit_now, reason = True, "sl"
            if not exit_now and cfg.tp_pts > 0 and np.isfinite(entry_px):
                move = (close[t] - entry_px) * state
                if move >= cfg.tp_pts:
                    exit_now, reason = True, "tp"

        if exit_now:
            trades.append({"entry": idx[entry_i], "exit": idx[t], "side": state,
                           "bars": bars_in, "reason": reason,
                           "ret": state * (close[t] / close[entry_i] - 1.0)})
            state, bars_in, avwap = 0, 0, np.nan
            anchor_num = anchor_den = 0.0
            entry_px = np.nan

        if state == 0 and can_enter[t] and not force_flat[t]:
            if cfg.long_ok and raw_buy[t]:
                state, bars_in, entry_i, entry_px = 1, 0, t, close[t]
                anchor_num, anchor_den = hlc3[t] * vw[t], vw[t]
                avwap = hlc3[t]
            elif cfg.short_ok and raw_sell[t]:
                state, bars_in, entry_i, entry_px = -1, 0, t, close[t]
                anchor_num, anchor_den = hlc3[t] * vw[t], vw[t]
                avwap = hlc3[t]
        target[t] = state

    # execution lag: the decision on bar t becomes a holding at t+lag
    if cfg.exec_lag > 0:
        pos = np.concatenate([np.zeros(cfg.exec_lag), target[:-cfg.exec_lag]])
    else:
        pos = target.copy()

    ret = np.zeros(n)
    ret[1:] = close[1:] / close[:-1] - 1.0
    gross = np.concatenate([[0.0], pos[:-1]]) * ret

    turnover = np.abs(np.diff(pos, prepend=0.0))
    cost_rate = (cfg.cost_bps_per_side + cfg.slippage_bps_per_side) / 1e4
    cost = turnover * cost_rate
    net = gross - cost

    return {"index": idx, "z": z, "position": pos, "gross": gross, "net": net,
            "cost": cost, "turnover": turnover, "trades": trades}


def metrics(res: dict, periods_per_year: float) -> dict:
    g = pd.Series(res["gross"], index=res["index"])
    nt = pd.Series(res["net"], index=res["index"])
    trades = res["trades"]
    years = len(nt) / periods_per_year

    def _sh(s):
        sd = s.std(ddof=1)
        return float(s.mean() / sd * np.sqrt(periods_per_year)) if sd > 0 else 0.0

    eq = (1 + nt).cumprod()
    n_tr = len(trades)
    return {
        "trades": n_tr,
        "trades_pa": n_tr / years if years > 0 else 0.0,
        "median_hold": float(np.median([t["bars"] for t in trades])) if trades else 0.0,
        "gross_sharpe": _sh(g),
        "net_sharpe": _sh(nt),
        "gross_pa": float(g.sum() / years) if years > 0 else 0.0,
        "net_pa": float(nt.sum() / years) if years > 0 else 0.0,
        "cost_pa": float(res["cost"].sum() / years) if years > 0 else 0.0,
        "gross_bps_per_trade": float(1e4 * g.sum() / n_tr) if n_tr else 0.0,
        "max_dd": float((eq / eq.cummax() - 1).min()),
        "hit_rate": float(np.mean([t["ret"] > 0 for t in trades])) if trades else 0.0,
    }
