"""Cost and execution-lag sensitivity for Noa zVWAP + AVWAP exit.

    python scripts/noa_cost_sweep.py --symbol NIFTY --timeframes 15m,30m

The two axes are the two things the Pine version currently assumes away:
transaction cost, and whether you can trade the very close you computed the
signal from. Everything else is held at the script's shipped defaults.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))

from kpairs import bars as B  # noqa: E402
from kpairs.noa import NoaConfig, metrics, run  # noqa: E402

RESULTS = _HERE / "results"
RESULTS.mkdir(exist_ok=True)
pd.set_option("display.width", 240)
pd.set_option("display.max_columns", 40)


def load(symbol: str) -> pd.DataFrame:
    for pat in (f"idx5m-all_*.parquet", "volcheck_*.parquet", "idx5m-tier1_*.parquet"):
        for p in sorted(B.CACHE_DIR.glob(pat)):
            d = pd.read_parquet(p)
            if symbol in set(d["label"]):
                d = d[d["label"] == symbol].copy()
                d["date"] = pd.to_datetime(d["date"])
                out = d.set_index("date")[["open", "high", "low", "close", "volume"]].sort_index()
                print(f"[load] {symbol} from {p.name}: {len(out):,} 5m bars  "
                      f"{out.index[0]:%Y-%m-%d} -> {out.index[-1]:%Y-%m-%d}  "
                      f"volume {'present' if out['volume'].max() > 0 else 'ZERO (index)'}")
                return out
    raise SystemExit(f"{symbol} not in cache")


def resample_ohlcv(df5: pd.DataFrame, tf: str) -> pd.DataFrame:
    """Session-anchored OHLCV aggregation, same grouping as kpairs.bars."""
    k = B.TIMEFRAMES[tf]
    if k == 1:
        return df5.copy()
    idx = pd.DatetimeIndex(df5.index)
    sess = B.session_key(idx)
    pos = pd.Series(1, index=idx).groupby(sess).cumcount().to_numpy()
    grp = pos // k
    key = pd.DataFrame({"s": sess, "g": grp}, index=idx)
    g = df5.groupby([key["s"], key["g"]])
    out = pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(),
        "low": g["low"].min(), "close": g["close"].last(),
        "volume": g["volume"].sum(),
    })
    stamps = pd.Series(idx, index=idx).groupby([key["s"], key["g"]]).last()
    return out.set_index(pd.DatetimeIndex(stamps.to_numpy())).sort_index()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--timeframes", default="15m,30m")
    ap.add_argument("--start", default=None)
    args = ap.parse_args()

    df5 = load(args.symbol)
    if args.start:
        df5 = df5.loc[df5.index >= pd.Timestamp(args.start)]
    has_vol = bool(df5["volume"].max() > 0)

    rows = []
    for tf in args.timeframes.split(","):
        df = resample_ohlcv(df5, tf)
        ppy = B.bars_per_year(pd.DatetimeIndex(df.index))
        base = dict(volume_weighted=has_vol)

        # ---- axis 1: execution lag x cost, at the shipped entry defaults ----
        for lag in (0, 1):
            for cost in (0.0, 2.0, 4.0, 8.0):
                cfg = NoaConfig(exec_lag=lag, cost_bps_per_side=cost,
                                slippage_bps_per_side=0.0 if cost == 0 else 0.2,
                                **base)
                m = metrics(run(df, cfg), ppy)
                rows.append({"symbol": args.symbol, "tf": tf, "variant": "AVWAP exit",
                             "lag": lag, "cost_bps_side": cost, **m})

        # ---- axis 2: which exit, at realistic cost and one bar of lag ----
        for name, kw in [
            ("AVWAP Cross",   dict(use_avwap_exit=True, exit_mode="Cross")),
            ("AVWAP beyond",  dict(use_avwap_exit=True, exit_mode="Close beyond")),
            ("flip band",     dict(use_avwap_exit=False, exit_in_flip=True)),
            ("session only",  dict(use_avwap_exit=False, exit_in_flip=False)),
        ]:
            cfg = NoaConfig(exec_lag=1, cost_bps_per_side=2.0, **base, **kw)
            m = metrics(run(df, cfg), ppy)
            rows.append({"symbol": args.symbol, "tf": tf, "variant": name,
                         "lag": 1, "cost_bps_side": 2.0, **m})

        # ---- axis 3: entry policy ----
        for pol in ("Flip Cross", "Mean Revert"):
            cfg = NoaConfig(exec_lag=1, cost_bps_per_side=2.0, policy=pol, **base)
            m = metrics(run(df, cfg), ppy)
            rows.append({"symbol": args.symbol, "tf": tf, "variant": f"policy {pol}",
                         "lag": 1, "cost_bps_side": 2.0, **m})

    out = pd.DataFrame(rows)
    tag = args.symbol.lower()
    out.to_csv(RESULTS / f"noa_sweep_{tag}.csv", index=False)

    show = ["tf", "variant", "lag", "cost_bps_side", "trades", "trades_pa",
            "median_hold", "gross_bps_per_trade", "gross_pa", "cost_pa",
            "net_pa", "net_sharpe", "max_dd", "hit_rate"]
    print(f"\n===== {args.symbol} =====")
    print(out[show].round(4).to_string(index=False))
    print(f"\n[done] results/noa_sweep_{tag}.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
