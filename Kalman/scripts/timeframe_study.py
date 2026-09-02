"""Which timeframe fits a Kalman index-pairs model best? 5m / 15m / 30m / 60m / 2h / 4h.

    python scripts/timeframe_study.py --indices NIFTY,BANKNIFTY,FINNIFTY
    python scripts/timeframe_study.py --tier tier1 --start 2022-05-01

At every timeframe, on identical underlying data (all six derived from one
5-minute pull), it runs:

    fit_scan          model-fit diagnostics only -- no trading rules, no costs
    kalman            walk-forward P&L, intraday-only, index-futures costs
    kalman_zerocost   the same with costs off, to separate "no edge" from
                      "edge eaten by costs"
    kalman_overnight  the same carrying positions through the close
    ols               rolling-OLS hedge baseline, identical machinery after it

Selection and hedge fitting are shared across the four P&L variants, so they
trade exactly the same pairs in exactly the same windows.

Writes results/tf_*.csv and prints the comparison tables.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from kpairs import bars as B  # noqa: E402
from kpairs import indices, tfstudy  # noqa: E402
from kpairs.backtest import CostModel  # noqa: E402

RESULTS = _HERE / "results"
RESULTS.mkdir(exist_ok=True)
pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 50)


def load_5m(tier: str, only: list[str] | None, start: str | None,
            end: str | None, min_coverage: float) -> pd.DataFrame:
    cached = (sorted(B.CACHE_DIR.glob("idx5m-all_*.parquet"))
              or sorted(B.CACHE_DIR.glob(f"idx5m-{tier}_*.parquet"))
              or sorted(B.CACHE_DIR.glob("idx5m*.parquet")))
    if not cached:
        raise SystemExit("no 5m cache -- run scripts/fetch_index_bars.py first")
    long_df = pd.read_parquet(cached[-1])
    print(f"[load] {cached[-1].name}  {len(long_df):,} rows")

    labels = ({s.strip().upper() for s in only} if only
              else {lbl for _, lbl, _ in indices.get_universe(tier).values()})
    long_df = long_df[long_df["label"].isin(labels)]
    if long_df.empty:
        raise SystemExit(f"no cached bars for {sorted(labels)}")

    px5 = B.to_wide_5m(long_df)
    if start:
        px5 = px5.loc[px5.index >= pd.Timestamp(start)]
    if end:
        px5 = px5.loc[px5.index <= pd.Timestamp(end)]

    # An index that only listed part-way through the span cannot sit in the
    # walk-forward without silently shortening every window it appears in.
    cov = px5.notna().mean()
    keep = cov[cov >= min_coverage].index.tolist()
    for lbl in sorted(set(px5.columns) - set(keep)):
        print(f"[load] dropped {lbl}: {cov[lbl]:.0%} coverage over this span")
    px5 = px5[keep]
    if px5.shape[1] < 2:
        raise SystemExit("fewer than two indices survive the coverage screen -- "
                         "shorten --start or lower --min-coverage")
    print(f"[load] {px5.shape[0]:,} 5m bars x {px5.shape[1]} indices "
          f"({', '.join(px5.columns)})  {px5.index[0]} -> {px5.index[-1]}")
    return px5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="tier1", help="tier1 | tier2 | all")
    ap.add_argument("--indices", default=None,
                    help="explicit comma list of labels, overrides --tier")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--min-coverage", type=float, default=0.9)
    ap.add_argument("--formation-sessions", type=int, default=250)
    ap.add_argument("--trading-sessions", type=int, default=60)
    ap.add_argument("--max-pairs", type=int, default=8)
    ap.add_argument("--cost-bps", type=float, default=1.5)
    ap.add_argument("--entry-z", type=float, default=2.0)
    ap.add_argument("--hl-max-sessions", type=float, default=20.0)
    ap.add_argument("--coint-p", type=float, default=0.10)
    ap.add_argument("--tradable-only", action="store_true",
                    help="only pair indices that both have listed futures")
    ap.add_argument("--tag", default="", help="suffix for output filenames")
    ap.add_argument("--skip-fitscan", action="store_true")
    ap.add_argument("--fitscan-pairs", type=int, default=20,
                    help="cap on pairs scanned for model fit (sampled, seeded)")
    ap.add_argument("--timeframes", default=None, help="comma list, e.g. 15m,60m")
    args = ap.parse_args()

    only = args.indices.split(",") if args.indices else None
    px5 = load_5m(args.tier, only, args.start, args.end, args.min_coverage)
    tfs = args.timeframes.split(",") if args.timeframes else list(B.TIMEFRAMES)
    tag = f"_{args.tag}" if args.tag else ""

    print("\n=== timeframe grid ===")
    grid = B.describe_timeframes(px5)
    print(grid.to_string())
    grid.to_csv(RESULTS / f"tf_grid{tag}.csv")

    # ------------------------------------------------------------ model fit
    if not args.skip_fitscan:
        print("\n=== fit scan (no trading rules, no costs) ===", flush=True)
        t0 = time.time()
        fs = tfstudy.fit_scan(px5, tfs,
                              formation_sessions=args.formation_sessions,
                              trading_sessions=args.trading_sessions,
                              tradable_only=False,
                              max_pairs_scanned=args.fitscan_pairs)
        fs.to_csv(RESULTS / f"tf_fitscan_pairs{tag}.csv", index=False)
        agg = (fs.groupby("timeframe")
                 .agg(pairs=("x", "size"),
                      bars_per_session=("bars_per_session", "first"),
                      z_std=("z_std", "median"),
                      z_kurt=("z_kurt", "median"),
                      ac1=("ac1", "median"),
                      frac_white=("lb_p", lambda s: float((s > 0.05).mean())),
                      tail_4sig=("tail_4sig", "median"),
                      half_life_sessions=("half_life_sessions", "median"))
                 .reindex([t for t in B.TIMEFRAMES if t in set(fs["timeframe"])]))
        agg.to_csv(RESULTS / f"tf_fitscan{tag}.csv")
        print(agg.round(4).to_string())
        print(f"    ({time.time() - t0:.0f}s)")

    # ------------------------------------------------------------------ P&L
    base = dict(
        formation_sessions=args.formation_sessions,
        trading_sessions=args.trading_sessions,
        max_pairs=args.max_pairs,
        entry_z=args.entry_z,
        hl_max_sessions=args.hl_max_sessions,
        coint_p_max=args.coint_p,
        # Off by default: the sectoral indices have no listed derivative, and
        # excluding them by default would silently shrink the universe to the
        # four index futures. Turn it on to see what is left once every pair
        # that cannot be an order ticket is removed -- that is a much smaller
        # and much more honest book.
        tradable_only=args.tradable_only,
    )
    live_cost = CostModel(bps_per_turnover=args.cost_bps, roll_bps_per_month=1.5)
    variants = {
        # The headline: Kalman hedge, square off before the close, real costs.
        "kalman": tfstudy.TFConfig(method="kalman", intraday_only=True,
                                   costs=live_cost, **base),
        # Same trades, costs switched off. The gap between this and the row
        # above is the whole cost question; the gap between this and zero is
        # whether there is any signal at all.
        "kalman_zerocost": tfstudy.TFConfig(
            method="kalman", intraday_only=True,
            costs=CostModel(bps_per_turnover=0.0, apply_roll=False), **base),
        # Carry through the close. At 2h and 4h there are too few bars in a
        # session for an intraday-flat rule to hold anything, so this is the
        # only meaningful reading at the coarse end.
        "kalman_overnight": tfstudy.TFConfig(method="kalman", intraday_only=False,
                                             costs=live_cost, **base),
        # Refuse entries triggered by the session's first bar, whose innovation
        # carries the overnight gap the filter has priced as an ordinary bar.
        "kalman_noopen": tfstudy.TFConfig(method="kalman", intraday_only=True,
                                          block_open_entries=True,
                                          costs=live_cost, **base),
        # Rolling-OLS hedge, identical machinery downstream. The baseline the
        # Kalman filter has to beat to justify itself.
        "ols": tfstudy.TFConfig(method="ols", intraday_only=True,
                                costs=live_cost, **base),
    }

    print("\n=== walk-forward sweep ===", flush=True)
    t0 = time.time()
    grids = tfstudy.sweep_grid(px5, variants, tfs, verbose=True)
    print(f"    ({time.time() - t0:.0f}s)")

    tables = {}
    for name, per_tf in grids.items():
        tbl = tfstudy.sweep_table(per_tf)
        tbl.to_csv(RESULTS / f"tf_{name}{tag}.csv")
        tables[name] = tbl
        show = [c for c in ["bars_per_session", "bars_per_year", "sharpe",
                            "sharpe_gross", "cagr", "vol", "max_dd", "n_trades",
                            "hit_rate", "median_hold_sessions", "trades_per_session",
                            "gross_bps_per_trade", "cost_bps_per_trade",
                            "edge_over_cost", "fit_z_std", "fit_z_kurt", "fit_ac1",
                            "fit_gap_inflation", "fit_pairs_per_window",
                            "fit_median_half_life_sessions"] if c in tbl.columns]
        print(f"\n--- {name} ---")
        print(tbl[show].round(3).to_string())

        for tf, r in per_tf.items():
            if not r.daily.empty:
                r.daily.to_csv(RESULTS / f"tf_daily_{name}_{tf}{tag}.csv")
            if not r.trades.empty:
                r.trades.to_csv(RESULTS / f"tf_trades_{name}_{tf}{tag}.csv", index=False)
    first = next(iter(grids.values()))
    for tf, r in first.items():
        if not r.selections.empty:
            r.selections.to_csv(RESULTS / f"tf_pairs_{tf}{tag}.csv", index=False)

    print("\n================ HEAD TO HEAD (annualised Sharpe) ================")
    head = pd.DataFrame({k: v["sharpe"] for k, v in tables.items()})
    head.insert(0, "gross_kalman", tables["kalman"]["sharpe_gross"])
    head.insert(0, "bars/session", tables["kalman"]["bars_per_session"])
    print(head.round(3).to_string())
    head.to_csv(RESULTS / f"tf_headtohead{tag}.csv")

    print(f"\n[done] artefacts in {RESULTS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
