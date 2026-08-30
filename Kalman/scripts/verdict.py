"""Consolidate a completed sweep into one answer table.

    python scripts/verdict.py --tag deep3

Joins the model-fit scan and the P&L variants into a single view, and scores
each timeframe on the two questions separately, because they are separate
questions and the sweep exists to show where they disagree.

Fit score (higher is better), all components measured out of sample:
    calibration   1 - |log(z_std)|        1.0 when the filter's own forecast
                                          variance is right
    tails         1 / (1 + kurt/10)       heavy innovations mean the Gaussian
                                          observation model is wrong
    whiteness     1 - min(|ac1|, 0.5)/0.5 leftover autocorrelation is structure
                                          the state-space model missed
    gaps          1 / gap_inflation       how much the session-open bar's z is
                                          overstated by a filter with no notion
                                          of an overnight gap

These are deliberately crude and equally weighted -- the point is to rank, and
any sensible weighting gives the same ordering. The components are printed
alongside so you can disagree with the aggregation and still use the parts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE))
RESULTS = _HERE / "results"
ORDER = ["5m", "15m", "30m", "60m", "2h", "4h"]

pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 60)


def _read(name: str, tag: str) -> pd.DataFrame | None:
    p = RESULTS / f"tf_{name}_{tag}.csv" if tag else RESULTS / f"tf_{name}.csv"
    if not p.exists():
        return None
    return pd.read_csv(p, index_col=0)


def fit_score(row: pd.Series) -> dict[str, float]:
    z_std = row.get("z_std", np.nan)
    kurt = row.get("z_kurt", np.nan)
    ac1 = row.get("ac1", np.nan)
    gap = row.get("gap_inflation", np.nan)

    calib = 1.0 - abs(np.log(z_std)) if z_std and z_std > 0 else np.nan
    tails = 1.0 / (1.0 + max(kurt, 0.0) / 10.0) if np.isfinite(kurt) else np.nan
    white = 1.0 - min(abs(ac1), 0.5) / 0.5 if np.isfinite(ac1) else np.nan
    gaps = 1.0 / gap if np.isfinite(gap) and gap > 0 else np.nan

    parts = {"calibration": calib, "tails": tails, "whiteness": white, "gaps": gaps}
    vals = [v for v in parts.values() if np.isfinite(v)]
    parts["fit_score"] = float(np.mean(vals)) if vals else np.nan
    return parts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="deep3")
    ap.add_argument("--min-trades", type=int, default=150,
                    help="ignore timeframes with fewer round trips than this")
    args = ap.parse_args()
    tag = args.tag

    fit = _read("fitscan", tag)
    kal = _read("kalman", tag)
    zero = _read("kalman_zerocost", tag)
    over = _read("kalman_overnight", tag)
    noopen = _read("kalman_noopen", tag)
    ols = _read("ols", tag)
    if kal is None:
        raise SystemExit(f"no results for tag {tag!r} -- run timeframe_study.py first")

    # --- fit -------------------------------------------------------------
    if fit is not None:
        # gap_inflation lives on the P&L tables (it needs the session index);
        # borrow it so the fit score can see it.
        if "fit_gap_inflation" in kal.columns:
            fit = fit.join(kal["fit_gap_inflation"].rename("gap_inflation"))
        scored = pd.DataFrame([fit_score(r) for _, r in fit.iterrows()], index=fit.index)
        fit_out = fit.join(scored).reindex([t for t in ORDER if t in fit.index])
        print("=== MODEL FIT (no trading rules, no costs) ===")
        cols = ["bars_per_session", "z_std", "z_kurt", "ac1", "tail_4sig",
                "half_life_sessions", "calibration", "tails", "whiteness",
                "gaps", "fit_score"]
        print(fit_out[[c for c in cols if c in fit_out.columns]].round(3).to_string())
        fit_out.to_csv(RESULTS / f"verdict_fit_{tag}.csv")
        best_fit = fit_out["fit_score"].idxmax()
        print(f"\n  best statistical fit: {best_fit}")
    else:
        fit_out, best_fit = None, None

    # --- economics --------------------------------------------------------
    econ = pd.DataFrame(index=[t for t in ORDER if t in kal.index])
    econ["bars/session"] = kal["bars_per_session"]
    econ["trades"] = kal.get("n_trades")
    econ["trades/session"] = kal.get("trades_per_session")
    econ["hold_sessions"] = kal.get("median_hold_sessions")
    econ["gross_bps/trade"] = kal.get("gross_bps_per_trade")
    econ["cost_bps/trade"] = kal.get("cost_bps_per_trade")
    econ["edge/cost"] = kal.get("edge_over_cost")
    econ["sharpe_gross"] = kal.get("sharpe_gross")
    econ["sharpe_net"] = kal.get("sharpe")
    if zero is not None:
        econ["sharpe_zerocost"] = zero["sharpe"]
    if over is not None:
        econ["sharpe_overnight"] = over["sharpe"]
    if noopen is not None:
        econ["sharpe_noopen"] = noopen["sharpe"]
    if ols is not None:
        econ["sharpe_ols"] = ols["sharpe"]
    econ["max_dd"] = kal.get("max_dd")
    econ["hit_rate"] = kal.get("hit_rate")

    print("\n=== ECONOMICS (walk-forward, out of sample) ===")
    print(econ.round(3).to_string())
    econ.to_csv(RESULTS / f"verdict_econ_{tag}.csv")

    # --- verdict ----------------------------------------------------------
    print("\n=== VERDICT ===")
    # A trade-count floor, because without one this ranking is meaningless at
    # the coarse end. An intraday-flat rule at 4h has two bars a session and one
    # of them must be flat, so it produced 39 round trips in ten years. Its
    # Sharpe has a standard error far wider than the number itself, and it will
    # win a naive argmax roughly half the time by luck.
    thin = econ[econ["trades"].fillna(0) < args.min_trades]
    if len(thin):
        print("  excluded for < %d trades: %s" % (
            args.min_trades,
            ", ".join(f"{tf} ({int(econ.loc[tf, 'trades'])})" for tf in thin.index)))
    econ = econ[econ["trades"].fillna(0) >= args.min_trades]
    if econ.empty:
        print("  every timeframe is below the trade floor -- nothing to rank.")
        return 0

    tradable = econ[econ["sharpe_net"] > 0]
    if tradable.empty:
        print("  No timeframe clears zero net of costs on this universe.")
        best_gross = econ["sharpe_gross"].idxmax()
        print(f"  Best gross Sharpe: {best_gross} "
              f"({econ.loc[best_gross, 'sharpe_gross']:+.2f} gross, "
              f"{econ.loc[best_gross, 'sharpe_net']:+.2f} net)")
        if (econ["sharpe_gross"] <= 0).all():
            print("  Gross is negative everywhere too -- this is an absence of "
                  "signal, not a cost problem.")
        else:
            print("  Gross is positive somewhere and net is not: the signal exists "
                  "and the cost model eats it. Look at edge/cost.")
    else:
        best_net = tradable["sharpe_net"].idxmax()
        print(f"  Best net Sharpe: {best_net} ({tradable.loc[best_net, 'sharpe_net']:+.2f})")

    if best_fit is not None:
        best_gross = econ["sharpe_gross"].idxmax()
        if best_fit != best_gross:
            print(f"\n  Fit and economics disagree: the model fits best at {best_fit}, "
                  f"the money is best at {best_gross}.")
            print("  That is the normal outcome and it is informative -- the finest "
                  "timeframe usually fits worst (microstructure) while the coarsest "
                  "has the least data. The tradable answer is where gross edge per "
                  "trade clears cost per trade with room to spare.")
        else:
            print(f"\n  Fit and gross economics agree on {best_fit}.")

    if "edge/cost" in econ:
        ok = econ[econ["edge/cost"] > 1.0]
        print(f"\n  Timeframes where gross edge per trade exceeds the round-trip "
              f"cost: {', '.join(ok.index) if len(ok) else 'none'}")

    print(f"\n[done] verdict_fit_{tag}.csv / verdict_econ_{tag}.csv in {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
