"""Where does the Z-VWAP edge actually live? Decomposition, with an OOS split.

    python scripts/noa_edge_decomp.py --symbol NIFTY --tf 15m

The strategy's problem is not that it has no edge -- gross is +7% to +11% a year.
The problem is that the edge per trade (2-4 bp) is the same size as the friction
(~3 bp round trip). So the only improvements that matter raise bp per trade or
cut the trade count without losing the good trades.

This slices gross bp/trade by dimensions chosen BEFORE looking at the data --
side, hour, volatility regime, signal strength, holding period -- and reports
each on the first half and the second half separately. A split that only works
in one half is data mining, and at 4,800 trades there is more than enough rope.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE / "scripts"))

from kpairs import bars as B  # noqa: E402
from kpairs.noa import NoaConfig, run, zscore  # noqa: E402
from noa_cost_sweep import load, resample_ohlcv  # noqa: E402

pd.set_option("display.width", 200)


def trade_table(df: pd.DataFrame, cfg: NoaConfig) -> pd.DataFrame:
    """One row per round trip, with the conditioning variables attached."""
    res = run(df, cfg)
    idx = pd.DatetimeIndex(df.index)
    close = df["close"].to_numpy(float)
    z = res["z"]

    # realised vol regime, measured on information available at entry
    ret = pd.Series(close).pct_change()
    rv = ret.rolling(50).std().to_numpy() * np.sqrt(252 * 25)   # annualised-ish

    rows = []
    for t in res["trades"]:
        i = idx.get_indexer([t["entry"]])[0]
        if i < 0:
            continue
        rows.append({
            "entry": t["entry"], "side": "long" if t["side"] == 1 else "short",
            "bars": t["bars"], "gross_bps": t["ret"] * 1e4,
            "hour": t["entry"].hour, "year": t["entry"].year,
            "z_entry": z[i] if np.isfinite(z[i]) else np.nan,
            "rv": rv[i] if i < len(rv) else np.nan,
        })
    return pd.DataFrame(rows)


def split_report(tr: pd.DataFrame, by: str, label: str, bins=None) -> None:
    """Mean gross bp per trade by bucket, first half vs second half."""
    t = tr.dropna(subset=[by]).copy()
    if bins is not None:
        t["bucket"] = pd.cut(t[by], bins)
    else:
        t["bucket"] = t[by]
    mid = t["entry"].quantile(0.5)
    t["half"] = np.where(t["entry"] <= mid, "H1", "H2")

    g = (t.groupby(["bucket", "half"], observed=True)["gross_bps"]
           .agg(["size", "mean"]).unstack("half"))
    if g.empty:
        return
    g.columns = [f"{a}_{b}" for a, b in g.columns]
    g["all_n"] = t.groupby("bucket", observed=True)["gross_bps"].size()
    g["all_bps"] = t.groupby("bucket", observed=True)["gross_bps"].mean()
    print(f"\n--- gross bp/trade by {label} ---")
    print(g.round(2).to_string())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--tf", default="15m")
    args = ap.parse_args()

    df5 = load(args.symbol)
    df = resample_ohlcv(df5, args.tf)
    hv = bool(df["volume"].max() > 0)

    # the best exit found in the cost sweep, one bar of lag, costs off so we are
    # looking at raw signal quality rather than at the cost model
    cfg = NoaConfig(exec_lag=1, cost_bps_per_side=0.0, slippage_bps_per_side=0.0,
                    use_avwap_exit=False, exit_in_flip=False, volume_weighted=hv)
    tr = trade_table(df, cfg)
    print(f"\n{args.symbol} {args.tf}: {len(tr):,} round trips, "
          f"{tr['entry'].min():%Y-%m} to {tr['entry'].max():%Y-%m}")
    print(f"overall gross {tr['gross_bps'].mean():+.2f} bp/trade")

    split_report(tr, "side", "side")
    split_report(tr, "hour", "entry hour")
    split_report(tr, "z_entry", "|z| at entry", bins=[-99, -1, -0.25, 0.25, 1, 99])
    split_report(tr, "rv", "realised vol quintile at entry",
                 bins=list(tr["rv"].quantile([0, .2, .4, .6, .8, 1.0]).values))
    split_report(tr, "bars", "holding period (bars)", bins=[0, 5, 10, 20, 40, 999])

    # --- holding period: the direct attack on the cost problem --------------
    print("\n=== exit after N sessions instead of at the session close ===")
    print("(cost 1.4 bp/side, the realistic NIFTY futures figure)")
    rows = []
    bps = len(df) / pd.DatetimeIndex(df.index).normalize().nunique()
    ppy = B.bars_per_year(pd.DatetimeIndex(df.index))
    from kpairs.noa import metrics
    for hold_sessions in (1, 2, 3, 5, 10):
        c = NoaConfig(exec_lag=1, cost_bps_per_side=1.4, slippage_bps_per_side=0.2,
                      use_avwap_exit=False, exit_in_flip=False, use_session=True,
                      volume_weighted=hv)
        # widen the session flatten so a position can survive the close
        c.flat_after_min = 15 * 60 + 20
        c.tp_pts = 0.0
        r = run(df, c)
        m = metrics(r, ppy)
        rows.append({"rule": f"session close", "trades_pa": m["trades_pa"],
                     "gross_bps": m["gross_bps_per_trade"], "net_pa": m["net_pa"],
                     "sharpe": m["net_sharpe"]})
        break
    print(pd.DataFrame(rows).round(3).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
