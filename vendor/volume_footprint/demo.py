"""Runnable demo for the Volume Footprint port.

    python demo.py                          synthetic bars, geometric engine
    python demo.py --csv data.csv           your own OHLCV
    python demo.py --engine intrabar        synthesise 1-min intrabars too
    python demo.py --plot out.png           save a matplotlib profile

CSV columns expected: time,open,high,low,close,volume (header row required).
"""

from __future__ import annotations

import argparse
import csv
import math
import random
from datetime import datetime, timedelta

from volume_footprint import (
    Bar,
    BarSeries,
    Settings,
    VolumeEngine,
    apply_engine,
    compute,
    format_dashboard,
    format_profile,
    format_table,
)
from volume_footprint.engines import intrabar_split


def synth_bars(n: int = 60, seed: int = 7, mintick: float = 0.05) -> BarSeries:
    """A synthetic session with a genuine trend leg and a rotation.

    Built so the readings are actually interesting: the first two thirds trend
    up (the profile should go OFF BALANCE TO BUY), the last third rotates
    around a level (OVL climbs back toward balance).
    """
    rng = random.Random(seed)
    t0 = datetime(2026, 8, 19, 9, 15)
    price = 100.0
    rows: list[Bar] = []

    for i in range(n):
        trend = 0.10 if i < int(n * 0.66) else 0.0
        drift = trend + rng.gauss(0.0, 0.16)
        o = price
        c = o + drift
        wick = abs(rng.gauss(0.0, 0.13)) + 0.05
        h = max(o, c) + wick * rng.uniform(0.3, 1.4)
        lo = min(o, c) - wick * rng.uniform(0.3, 1.4)
        vol = round(1500 + 900 * abs(drift) / 0.2 + rng.uniform(-250, 450))

        q = lambda x: round(x / mintick) * mintick  # noqa: E731 - snap to the lattice
        rows.append(
            Bar(
                time=t0 + timedelta(minutes=5 * i),
                open=q(o),
                high=q(h),
                low=q(lo),
                close=q(c),
                volume=float(max(vol, 100)),
            )
        )
        price = c

    return BarSeries(bars=rows, mintick=mintick, symbol="SYNTH")


def synth_intrabars(series: BarSeries, per_bar: int = 5, seed: int = 11) -> list[list[Bar]]:
    """Fabricate lower-timeframe candles that respect each parent bar's OHLC.

    Only for demonstrating the Intrabar engine offline. Real use feeds real LTF
    data; the point here is that the *same* parent candle can hide very
    different order flow, which is precisely why the Geometric engine is an
    estimate.
    """
    rng = random.Random(seed)
    out: list[list[Bar]] = []
    for bar in series.bars:
        path = [bar.open]
        for _ in range(per_bar - 1):
            path.append(rng.uniform(bar.low, bar.high))
        path.append(bar.close)
        # Force the extremes to be printed somewhere inside the path.
        path[rng.randrange(1, len(path) - 1)] = bar.high
        path[rng.randrange(1, len(path) - 1)] = bar.low

        ibs: list[Bar] = []
        share = bar.volume / per_bar
        for j in range(per_bar):
            o, c = path[j], path[j + 1]
            ibs.append(
                Bar(
                    time=bar.time,
                    open=o,
                    high=max(o, c),
                    low=min(o, c),
                    close=c,
                    volume=share,
                )
            )
        out.append(ibs)
    return out


def load_csv(path: str, mintick: float, symbol: str) -> BarSeries:
    bars: list[Bar] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            bars.append(
                Bar(
                    time=row.get("time"),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return BarSeries(bars=bars, mintick=mintick, symbol=symbol)


def main() -> None:
    ap = argparse.ArgumentParser(description="Volume Footprint demo")
    ap.add_argument("--csv")
    ap.add_argument("--mintick", type=float, default=0.05)
    ap.add_argument("--symbol", default="SYNTH")
    ap.add_argument(
        "--engine", choices=["geometric", "intrabar"], default="geometric",
        help="footprint needs a real per-tick feed, so it is not demoable offline",
    )
    ap.add_argument("--window", type=int, default=6)
    ap.add_argument("--ticks", type=int, default=9)
    ap.add_argument("--concentration", type=float, default=3.0)
    ap.add_argument("--period", type=int, default=23)
    ap.add_argument("--plot")
    args = ap.parse_args()

    series = (
        load_csv(args.csv, args.mintick, args.symbol)
        if args.csv
        else synth_bars(mintick=args.mintick)
    )

    if args.engine == "intrabar":
        series = apply_engine(
            series, VolumeEngine.INTRABAR, intrabars=synth_intrabars(series)
        )
        engine_name = "Intrabar (synthetic LTF)"
    else:
        series = apply_engine(series, VolumeEngine.GEOMETRIC)
        engine_name = "Geometric"

    st = Settings(
        window_bars=args.window,
        view_ticks=args.ticks,
        bell_div=args.concentration,
        profile_period=args.period,
    )
    res = compute(series, st)

    bar = series.last
    print(f"\n{series.symbol}   engine: {engine_name}   bars: {len(series)}")
    print(
        f"last  O {bar.open:.2f}  H {bar.high:.2f}  L {bar.low:.2f}  "
        f"C {bar.close:.2f}  V {bar.volume:,.0f}"
    )

    print("\n=== FOOTPRINT TABLE " + "=" * 40)
    print(format_table(res))

    print("\n=== DASHBOARD " + "=" * 46)
    print(format_dashboard(res))

    print("\n=== VOLUME PROFILE " + "=" * 41)
    print(format_profile(res))

    # Conservation check, the same arithmetic the RES reading reports on.
    for col in res.columns[:1]:
        acc = col.split.frame_buy + col.split.off_buy
        print(
            f"\nmass check (newest column): frame+off = {acc:,.6f}  "
            f"vs bar buy = {col.buy_total:,.6f}  "
            f"drift = {abs(acc - col.buy_total):.3e}"
        )

    if args.plot:
        from volume_footprint import plot_profile

        plot_profile(res, args.plot, title=f"{series.symbol} volume profile")
        print(f"\nprofile written to {args.plot}")


if __name__ == "__main__":
    main()
