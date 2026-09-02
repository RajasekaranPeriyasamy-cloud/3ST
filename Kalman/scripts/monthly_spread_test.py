"""Monthly index credit spreads: path-dependent test on NIFTY + India VIX.

    python scripts/monthly_spread_test.py

Why monthly is the cleaner measurement
--------------------------------------
India VIX is a **30-day** implied volatility. At a 21-session horizon it matches
the instrument being sold. The weekly version had to scale it to 5 days by
sqrt(52), which assumes a flat term structure -- and the term structure is not
flat: short-dated IV sits below 30-day in calm markets and spikes above it in
stress, so the weekly implied move is biased in opposite directions depending on
the regime you are in. Nothing in the weekly test corrects for that.

Monthly also cuts the roll count from 52 a year to 12. Every other result in this
project came back the same way -- gross positive, net negative, killed by
turnover -- so a 4x reduction in the number of times you pay four legs of spread
is the single biggest structural lever available.

What is real here and what is modelled
--------------------------------------
Real: the underlying path, the realised move, India VIX along the path, and
therefore the expiry payoff of every spread.

Modelled: the option prices. Black-Scholes at the prevailing VIX with a flat
smile. That ignores skew, so put credits are understated and call credits
overstated -- the put side is better than these numbers and the call side worse.
It also ignores the bid-ask you actually cross, which the cost input stands in
for.
"""

from __future__ import annotations

import sys
from math import erf, exp, log, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]      # C:\Dev\3ST
sys.path.insert(0, str(_ROOT))
RESULTS = Path(__file__).resolve().parents[1] / "results"
RESULTS.mkdir(exist_ok=True)

TRADING_DAYS = 252
COST_PER_LEG = 0.0025          # fraction of spread width, per leg, per round trip


def _N(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def bs(S: float, K: float, T: float, sig: float, call: bool) -> float:
    if T <= 0 or sig <= 0:
        return max(0.0, (S - K) if call else (K - S))
    d1 = (log(S / K) + 0.5 * sig * sig * T) / (sig * sqrt(T))
    d2 = d1 - sig * sqrt(T)
    return S * _N(d1) - K * _N(d2) if call else K * _N(-d2) - S * _N(-d1)


def spread_value(S: float, T: float, sig: float, legs: list[tuple[float, bool, int]]) -> float:
    """Net value of the short spread: what it would cost to buy it back."""
    return sum(sign * bs(S, K, T, sig, call) for K, call, sign in legs)


def simulate(
    d: pd.DataFrame,
    *,
    horizon: int = 21,
    k_short: float = 1.25,
    k_width: float = 1.0,
    both_sides: bool = True,
    trend_tilt: bool = False,
    take_profit: float = 0.0,      # close at this fraction of max credit; 0 = off
    stop_mult: float = 0.0,        # close if the spread costs this x the credit; 0 = off
    cost_per_leg: float = COST_PER_LEG,
    overlap: bool = False,
) -> pd.DataFrame:
    """One row per cycle. Return is expressed on maximum risk (the width)."""
    px = d["nifty"].to_numpy(float)
    vx = d["vix"].to_numpy(float) / 100.0
    ema = d["nifty"].ewm(span=20).mean().to_numpy()
    dates = d["date"].to_numpy()
    n = len(d)
    step = 1 if overlap else horizon

    rows = []
    for i in range(0, n - horizon, step):
        S0, v0 = px[i], vx[i]
        if not (np.isfinite(S0) and np.isfinite(v0) and v0 > 0):
            continue
        T0 = horizon / TRADING_DAYS
        imp = S0 * v0 * sqrt(T0)
        if imp <= 0:
            continue
        up = px[i] > ema[i]

        width = k_width * imp
        legs: list[tuple[float, bool, int]] = []
        sell_puts = both_sides or (trend_tilt and up) or (not both_sides and not trend_tilt)
        sell_calls = both_sides or (trend_tilt and not up)
        if trend_tilt:
            sell_puts, sell_calls = up, not up

        if sell_puts:
            Kp = S0 - k_short * imp
            legs += [(Kp, False, +1), (Kp - width, False, -1)]
        if sell_calls:
            Kc = S0 + k_short * imp
            legs += [(Kc, True, +1), (Kc + width, True, -1)]
        if not legs:
            continue

        credit = spread_value(S0, T0, v0, legs)
        if credit <= 0:
            continue

        # --- walk the path, applying management rules -----------------------
        exit_j, exit_val, why = horizon, None, "expiry"
        for j in range(1, horizon + 1):
            T = (horizon - j) / TRADING_DAYS
            val = spread_value(px[i + j], T, vx[i + j], legs)
            if take_profit > 0 and val <= credit * (1.0 - take_profit):
                exit_j, exit_val, why = j, val, "profit"
                break
            if stop_mult > 0 and val >= credit * stop_mult:
                exit_j, exit_val, why = j, val, "stop"
                break
        if exit_val is None:
            exit_val = spread_value(px[i + horizon], 0.0, vx[i + horizon], legs)

        n_legs = len(legs)
        cost = n_legs * cost_per_leg * width
        pnl = credit - exit_val - cost
        rows.append({
            "date": dates[i], "credit": credit, "width": width,
            "credit_pct": credit / width, "exit_val": exit_val,
            "bars_held": exit_j, "why": why,
            "ret": pnl / width, "up": up,
        })
    return pd.DataFrame(rows)


def summarise(r: pd.DataFrame, label: str, risk_frac: float, cycles_pa: float) -> dict:
    if r.empty:
        return {"variant": label, "cycles": 0}
    ret = r["ret"].to_numpy()
    eq = np.cumprod(1.0 + ret * risk_frac)
    dd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    sd = ret.std(ddof=1)
    return {
        "variant": label,
        "cycles": len(ret),
        "credit_pct": float(r["credit_pct"].mean()),
        "win": float((ret > 0).mean()),
        "mean_ret": float(ret.mean()),
        "ann": float(ret.mean() * cycles_pa * risk_frac),
        "sharpe": float(ret.mean() / sd * sqrt(cycles_pa)) if sd > 0 else 0.0,
        "max_dd": dd,
        "worst": float(ret.min()),
        "held": float(r["bars_held"].mean()),
        "pct_early": float((r["why"] != "expiry").mean()),
    }


def main() -> int:
    d = (pd.read_csv(_ROOT / "data" / "vrp_daily.csv", parse_dates=["date"])
         .dropna().sort_values("date").reset_index(drop=True))
    print(f"NIFTY + India VIX, {d['date'].iloc[0]:%Y-%m-%d} -> {d['date'].iloc[-1]:%Y-%m-%d}, "
          f"{len(d):,} sessions")

    # ---- VRP at the horizon VIX is actually quoted for --------------------
    print("\n=== variance risk premium by horizon ===")
    r = np.log(d["nifty"]).diff()
    for H, name in [(5, "weekly"), (21, "monthly"), (63, "quarterly")]:
        rv = r.shift(-1).rolling(H).std().shift(-(H - 1)) * sqrt(TRADING_DAYS) * 100
        x = pd.DataFrame({"iv": d["vix"].to_numpy(), "rv": rv.to_numpy()}).dropna()
        vrp = x["iv"] - x["rv"]
        print(f"  {name:<10} H={H:>2}  mean IV {x['iv'].mean():5.2f}  mean RV {x['rv'].mean():5.2f}  "
              f"VRP {vrp.mean():+5.2f}  positive {float((vrp > 0).mean()):5.1%}")

    # ---- breach rates ------------------------------------------------------
    H = 21
    fwd = (d["nifty"].shift(-H) / d["nifty"] - 1.0) * 100
    imp = d["vix"] / sqrt(TRADING_DAYS / H)
    x = pd.DataFrame({"fwd": fwd, "imp": imp}).dropna()
    print(f"\n=== monthly breach rates, {len(x):,} overlapping cycles ===")
    print(f"  mean implied monthly move {x['imp'].mean():.2f}%   "
          f"mean realised |move| {x['fwd'].abs().mean():.2f}%")
    for k in (1.0, 1.25, 1.5, 2.0):
        c = float((x["fwd"] > k * x["imp"]).mean())
        p = float((x["fwd"] < -k * x["imp"]).mean())
        print(f"  {k:>4.2f}x   call side {c:6.2%}   put side {p:6.2%}   either {c + p:6.2%}")

    # ---- the sweep ---------------------------------------------------------
    risk = 0.20                       # same risk-per-cycle as the weekly test
    cycles_pa = TRADING_DAYS / H      # 12
    out = []
    for k in (1.0, 1.25, 1.5, 2.0):
        out.append(summarise(simulate(d, horizon=H, k_short=k, both_sides=True),
                             f"condor {k}x", risk, cycles_pa))
    out.append(summarise(simulate(d, horizon=H, k_short=1.25, both_sides=False, trend_tilt=True),
                         "tilted 1.25x", risk, cycles_pa))
    for tp in (0.5, 0.75):
        out.append(summarise(simulate(d, horizon=H, k_short=1.25, both_sides=True, take_profit=tp),
                             f"condor 1.25x TP{int(tp * 100)}", risk, cycles_pa))
    out.append(summarise(simulate(d, horizon=H, k_short=1.25, both_sides=True,
                                  take_profit=0.5, stop_mult=2.0),
                         "condor 1.25x TP50 SL2x", risk, cycles_pa))
    out.append(summarise(simulate(d, horizon=H, k_short=1.25, both_sides=True, stop_mult=2.0),
                         "condor 1.25x SL2x", risk, cycles_pa))

    t = pd.DataFrame(out).set_index("variant")
    print(f"\n=== monthly spreads, non-overlapping, risk {risk:.0%} of capital per cycle ===")
    print(t.round(4).to_string())
    t.to_csv(RESULTS / "monthly_spread_test.csv")

    # ---- cost sensitivity on the best structure ---------------------------
    print("\n=== cost sensitivity, condor 1.25x TP50 ===")
    rows = []
    for cpl in (0.0, 0.0015, 0.0025, 0.005, 0.01):
        s = summarise(simulate(d, horizon=H, k_short=1.25, both_sides=True,
                               take_profit=0.5, cost_per_leg=cpl),
                      f"{cpl * 1e4:.0f} bp/leg", risk, cycles_pa)
        rows.append(s)
    print(pd.DataFrame(rows).set_index("variant")[
        ["credit_pct", "win", "ann", "sharpe", "max_dd"]].round(4).to_string())

    # ---- weekly vs monthly, same structure and cost ------------------------
    print("\n=== same structure at both horizons ===")
    rows = []
    for Hh, nm in [(5, "weekly"), (21, "monthly"), (63, "quarterly")]:
        s = summarise(simulate(d, horizon=Hh, k_short=1.25, both_sides=True, take_profit=0.5),
                      nm, risk, TRADING_DAYS / Hh)
        s["rolls_pa"] = TRADING_DAYS / Hh
        rows.append(s)
    print(pd.DataFrame(rows).set_index("variant")[
        ["rolls_pa", "cycles", "credit_pct", "win", "ann", "sharpe", "max_dd", "worst"]
    ].round(4).to_string())

    print(f"\n[done] {RESULTS / 'monthly_spread_test.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
