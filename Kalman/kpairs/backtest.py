"""Per-pair P&L, an NSE-realistic cost model, and performance metrics.

The P&L identity
----------------
The reference article computes strategy returns as

    signal.shift(1) * (y.pct_change() - beta * x.pct_change())

There are three separate problems with that line.

1. **Unit mismatch.** ``beta`` came from regressing *raw price* on *raw price*,
   so it is a price ratio -- for BANKNIFTY (~57,000) against NIFTY (~25,000) it
   is around 2.3. Multiplying a percentage return by 2.3 claims the short leg is
   2.3x the notional of the long leg, when the hedge actually calls for 2.3
   index *units*, i.e. roughly equal rupee exposure. The hedge ratio only
   doubles as a value weight when it comes from a **log-price** regression,
   which is why everything in this package runs on log prices.

2. **Lookahead in the hedge.** ``beta`` is indexed at t, the same bar as the
   return being earned. The position was sized using a hedge ratio the filter
   only produces after seeing bar t's price. Use ``beta_{t-1}``.

3. **No capital base.** Dividing nothing by anything, the "return" is a return
   on the long leg's notional alone, ignoring the capital tied up in the short.
   Two pairs with hedge ratios of 0.3 and 3.0 get compared on different
   denominators.

What this module does instead, per unit of pair capital:

    w_y[t] = pos[t] / (1 + beta[t])
    w_x[t] = -pos[t] * beta[t] / (1 + beta[t])
    ret[t] = w_y[t-1] * r_y[t] + w_x[t-1] * r_x[t] - cost[t]

with ``beta`` the filter's *prior* estimate and ``cost`` charged on realised
turnover in both legs.

Cost model
----------
Costs are charged per unit of notional turned over, summed across both legs, so
a full round trip on one leg costs ``2 x bps_per_turnover``.

For an **index-futures** pair (NIFTY / BANKNIFTY / FINNIFTY / MIDCPNIFTY /
SENSEX / BANKEX) the round-trip budget per leg is roughly:

    STT, sell side only                 2.0 bp
    exchange txn + SEBI + stamp + GST   ~0.4 bp
    brokerage (flat Rs 20 on a ~19 lakh lot)  ~0.01 bp
    half-spread, both sides             ~0.4 bp  (0.05 tick on ~25,000)
    --------------------------------------------
    round trip                          ~2.8 bp   ->  1.4 bp per turnover unit

That is roughly a quarter of a single-stock-futures pair and an order of
magnitude below a sectoral-index proxy, which has no listed derivative and must
be traded through thin ETFs or a 10-15 leg replicating basket. Hence three
presets: ``index_futures()``, ``stock_futures()``, ``sector_proxy()``.

This is the number that decides the timeframe question. Gross edge per trade
falls roughly with the square root of the bar interval while cost per trade is
flat, so there is a frequency below which a real signal still loses money.
Finding that crossover is the point of the sweep -- which is why the sweep runs
at 0 bp as well, to separate "no edge" from "edge eaten by costs".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class CostModel:
    """All-in transaction cost, expressed in basis points of notional traded."""

    bps_per_turnover: float = 1.5
    # Futures expire; a holding period spanning expiry rolls. Charged pro-rata
    # on gross notional across a ~21-session month.
    roll_bps_per_month: float = 1.5
    apply_roll: bool = True
    # Sessions per month, for pro-rating the roll. At intraday frequency the
    # roll is spread over bars, not sessions, so this is scaled by the caller.
    sessions_per_month: float = 21.0
    bars_per_session: float = 1.0

    @classmethod
    def index_futures(cls) -> "CostModel":
        """NIFTY / BANKNIFTY / FINNIFTY / SENSEX -- tick-tight, STT-light."""
        return cls(bps_per_turnover=1.5, roll_bps_per_month=1.5)

    @classmethod
    def stock_futures(cls) -> "CostModel":
        """Single-stock futures -- wider spreads, same taxes."""
        return cls(bps_per_turnover=6.0, roll_bps_per_month=4.0)

    @classmethod
    def sector_proxy(cls) -> "CostModel":
        """A sectoral index with no derivative: ETF or replicating basket.

        Deliberately punitive. A NIFTY IT / NIFTY PHARMA spread traded through
        sector ETFs pays a 15-40 bp round trip on each leg before tracking
        error, and through a basket it pays 10-15 single-stock tickets a side.
        """
        return cls(bps_per_turnover=20.0, roll_bps_per_month=0.0, apply_roll=False)

    def turnover_cost(self, turnover: np.ndarray) -> np.ndarray:
        return turnover * (self.bps_per_turnover / 1e4)

    def carry_cost(self, gross: np.ndarray) -> np.ndarray:
        """Roll cost, pro-rated per bar rather than per session.

        At 5-minute frequency there are ~75 bars per session, so charging a
        session's worth of roll on every bar would overstate it 75x. The
        divisor is bars-per-month.
        """
        if not self.apply_roll:
            return np.zeros_like(gross)
        bars_per_month = max(1.0, self.sessions_per_month * self.bars_per_session)
        return gross * (self.roll_bps_per_month / 1e4) / bars_per_month


@dataclass
class PairBacktest:
    x: str
    y: str
    index: pd.DatetimeIndex
    ret_gross: np.ndarray
    ret_net: np.ndarray
    position: np.ndarray
    beta: np.ndarray
    zscore: np.ndarray
    turnover: np.ndarray
    cost: np.ndarray
    trades: list[dict] = field(default_factory=list)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "position": self.position,
                "beta": self.beta,
                "z": self.zscore,
                "ret_gross": self.ret_gross,
                "ret_net": self.ret_net,
                "turnover": self.turnover,
                "cost": self.cost,
            },
            index=self.index,
        )


def run_pair(
    px_x: pd.Series,
    px_y: pd.Series,
    position: np.ndarray,
    beta: np.ndarray,
    zscore: np.ndarray,
    *,
    x_name: str = "X",
    y_name: str = "Y",
    costs: CostModel | None = None,
    freeze_hedge: bool = True,
    beta_clip: tuple[float, float] = (0.05, 20.0),
) -> PairBacktest:
    """Compute the P&L path for one pair.

    Parameters
    ----------
    freeze_hedge
        ``True`` holds the hedge ratio fixed at its value on the entry bar for
        the life of the trade; ``False`` re-hedges to the filter's latest beta
        every day. Freezing is what a desk actually does, and the comparison
        between the two is one of the more useful results this harness produces:
        daily re-hedging pays transaction costs on beta's *noise* for a hedge
        improvement that is largely inside the estimation error.
    beta_clip
        Guard rail. A filter that has lost the plot can emit beta of 400, which
        turns a market-neutral pair into a 400x levered short. Positions with a
        clipped beta are still traded, but the clip means one broken pair cannot
        dominate the portfolio.
    """
    costs = costs or CostModel()

    joined = pd.concat([px_x, px_y], axis=1, keys=["x", "y"]).dropna()
    idx = pd.DatetimeIndex(joined.index)
    n = len(idx)
    if n != len(position):
        raise ValueError(f"position length {len(position)} != aligned price length {n}")

    rx = joined["x"].pct_change().fillna(0.0).to_numpy()
    ry = joined["y"].pct_change().fillna(0.0).to_numpy()

    b = np.clip(np.nan_to_num(beta, nan=1.0), beta_clip[0], beta_clip[1])

    # Hold beta at its entry value for the life of each trade.
    if freeze_hedge:
        b_eff = np.empty(n)
        current = b[0]
        prev_pos = 0.0
        for t in range(n):
            if position[t] != 0.0 and prev_pos == 0.0:
                current = b[t]
            b_eff[t] = current
            prev_pos = position[t]
    else:
        b_eff = b

    denom = 1.0 + b_eff
    w_y = position / denom
    w_x = -position * b_eff / denom

    wy_lag = np.concatenate([[0.0], w_y[:-1]])
    wx_lag = np.concatenate([[0.0], w_x[:-1]])

    ret_gross = wy_lag * ry + wx_lag * rx

    turnover = np.abs(np.diff(w_y, prepend=0.0)) + np.abs(np.diff(w_x, prepend=0.0))
    gross_exposure = np.abs(wy_lag) + np.abs(wx_lag)
    cost = costs.turnover_cost(turnover) + costs.carry_cost(gross_exposure)
    ret_net = ret_gross - cost

    trades = _extract_trades(idx, position, ret_net, zscore, b_eff)

    return PairBacktest(
        x=x_name, y=y_name, index=idx,
        ret_gross=ret_gross, ret_net=ret_net,
        position=position, beta=b_eff, zscore=np.asarray(zscore, dtype=float),
        turnover=turnover, cost=cost, trades=trades,
    )


def _extract_trades(
    idx: pd.DatetimeIndex,
    position: np.ndarray,
    ret_net: np.ndarray,
    zscore: np.ndarray,
    beta: np.ndarray,
) -> list[dict]:
    """Split the return path into discrete round trips.

    Per-trade statistics are the only place a win rate means anything. The
    reference implementation computes ``(net_returns[trades.shift(1)] > 0).mean()``
    which is the fraction of *days immediately following a trade event* that
    were positive -- a number that has no interpretation as a hit rate.
    """
    trades: list[dict] = []
    open_i: int | None = None
    for t in range(len(position)):
        p = position[t]
        if open_i is None and p != 0.0:
            open_i = t
        elif open_i is not None and p == 0.0:
            seg = ret_net[open_i:t]
            trades.append({
                "entry": idx[open_i], "exit": idx[t - 1] if t > 0 else idx[t],
                "side": "long_spread" if position[open_i] > 0 else "short_spread",
                "bars": t - open_i,
                "pnl": float(np.sum(seg)),
                "entry_z": float(zscore[open_i]) if np.isfinite(zscore[open_i]) else np.nan,
                "beta": float(beta[open_i]),
            })
            open_i = None
    if open_i is not None:
        seg = ret_net[open_i:]
        trades.append({
            "entry": idx[open_i], "exit": idx[-1], "side":
                "long_spread" if position[open_i] > 0 else "short_spread",
            "bars": len(position) - open_i, "pnl": float(np.sum(seg)),
            "entry_z": float(zscore[open_i]) if np.isfinite(zscore[open_i]) else np.nan,
            "beta": float(beta[open_i]), "open_at_end": True,
        })
    return trades


def metrics(
    returns: pd.Series,
    trades: list[dict] | None = None,
    periods_per_year: float = TRADING_DAYS,
) -> dict[str, float]:
    """Standard performance statistics on a per-bar return series.

    ``periods_per_year`` must match the bar frequency of ``returns``. It is a
    required input rather than a constant because this harness compares 5m
    against 4h, and Sharpe scales with its square root: annualise a 5-minute
    series with 252 instead of ~18,750 and you understate its Sharpe by 8.6x.
    Pass the empirical value from ``bars.bars_per_year``, never a theoretical
    one -- half-days and feed gaps make the two differ by a few percent, and
    the whole point of the sweep is to compare timeframes on equal terms.

    Sharpe is reported *excess of zero* rather than excess of the Indian
    risk-free rate: a futures-implemented market-neutral book earns roughly the
    call rate on its margin, so the two conventions land in the same place.
    Max drawdown is computed on the compounded equity curve, not on ``cumsum``
    as the reference implementation does; on a series with any meaningful
    compounding the two differ materially.
    """
    r = pd.Series(returns).dropna()
    if r.empty or r.std(ddof=1) == 0:
        return {"n_days": int(r.size), "cagr": 0.0, "vol": 0.0, "sharpe": 0.0,
                "sortino": 0.0, "max_dd": 0.0, "calmar": 0.0, "hit_rate": 0.0,
                "n_trades": 0, "avg_hold": 0.0, "profit_factor": 0.0, "skew": 0.0,
                "worst_day": 0.0, "turnover_pa": 0.0}

    ppy = float(periods_per_year)
    equity = (1.0 + r).cumprod()
    years = len(r) / ppy
    cagr = float(equity.iloc[-1] ** (1.0 / years) - 1.0) if years > 0 else 0.0
    vol = float(r.std(ddof=1) * np.sqrt(ppy))
    sharpe = float(r.mean() / r.std(ddof=1) * np.sqrt(ppy))
    downside = r[r < 0].std(ddof=1)
    sortino = float(r.mean() / downside * np.sqrt(ppy)) if downside and downside > 0 else 0.0
    dd = float((equity / equity.cummax() - 1.0).min())
    calmar = float(cagr / abs(dd)) if dd < 0 else 0.0

    out = {
        "n_days": int(len(r)), "periods_per_year": ppy, "cagr": cagr, "vol": vol, "sharpe": sharpe,
        "sortino": sortino, "max_dd": dd, "calmar": calmar,
        "skew": float(r.skew()), "worst_day": float(r.min()),
    }

    if trades:
        pnls = np.array([t["pnl"] for t in trades], dtype=float)
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        out["n_trades"] = int(pnls.size)
        out["hit_rate"] = float((pnls > 0).mean())
        out["avg_hold"] = float(np.mean([t["bars"] for t in trades]))
        out["avg_win"] = float(wins.mean()) if wins.size else 0.0
        out["avg_loss"] = float(losses.mean()) if losses.size else 0.0
        out["profit_factor"] = float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
    else:
        out.update({"n_trades": 0, "hit_rate": 0.0, "avg_hold": 0.0,
                    "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0})
    return out


def yearly_table(returns: pd.Series, periods_per_year: float = TRADING_DAYS) -> pd.DataFrame:
    """Calendar-year return, vol, Sharpe and max drawdown.

    A headline CAGR over 16 years can hide the fact that the entire edge came
    from two years. This is the table that tells you.
    """
    r = pd.Series(returns).dropna()
    rows = []
    for year, grp in r.groupby(r.index.year):
        eq = (1 + grp).cumprod()
        rows.append({
            "year": int(year),
            "bars": len(grp),
            "return": float(eq.iloc[-1] - 1.0),
            "vol": float(grp.std(ddof=1) * np.sqrt(periods_per_year)) if len(grp) > 2 else 0.0,
            "sharpe": float(grp.mean() / grp.std(ddof=1) * np.sqrt(periods_per_year))
                      if len(grp) > 2 and grp.std(ddof=1) > 0 else 0.0,
            "max_dd": float((eq / eq.cummax() - 1.0).min()),
        })
    return pd.DataFrame(rows).set_index("year")
