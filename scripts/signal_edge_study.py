"""Forward-return study for reversal signals — does a variant have edge?

``signal_lag_study.py`` answers *when* a signal becomes actionable. This answers
the question that actually decides whether to trade it: **what happens after the
entry you could really get**, measured from the moment the signal first became
visible rather than from its backdated pivot.

Detection runs through the real ``detect_spot_reversals``, replayed bar by bar
so a signal is only ever scored from information available at the time.

What this found (2026-09-04, NIFTY 5m, 36 sessions, 515 signals)
---------------------------------------------------------------
``pivot-only right=1`` — the fastest variant — returns a **mean of 0.0 bp** at
+15/+30/+60m, win rate 50.7-51.3%, under 1 SE from a coin flip. Being early does
not make it profitable.

Note the trigger lag this reports (median 5m on 5m bars) is the honest one. An
earlier ~1 minute figure came from replaying the desk archive, whose ~30s
sampling makes resampled bucket timestamps land close together; it does not mean
a 5m pivot can be known a minute after it forms. The floor is one TF bar.

An apparent 62.5% win rate for VIX-MACD-aligned signals, found on 6 sessions,
fell to 49.8% on 36. That is what this tool exists to prevent: it is easy to
slice a small sample until something looks like edge.

A bearish asymmetry looked more durable -- 55.4% against 46.4% for bullish, and
stable at 55-58% across every sub-period. The base-rate control dissolved it.
NIFTY fell 0.68% over the sample, in which an arbitrary 60-minute short wins
52.7% with no signal at all. The signal contributed +2.7 points, under 1 SE. The
sub-period "confirmations" were nested windows inside the same downtrend, all
re-measuring the same drift.

That is why EXCESS, not the raw win rate, is the column to read. In a trending
sample the trend-direction side wins without any signal, and a strategy tested
without this control takes credit for the tape.

Read the caveats below before believing any number this prints.

Caveats that apply to every result here
---------------------------------------
* **Spot, not fills.** No spread, brokerage, slippage or theta. A NIFTY option's
  bid-ask through delta is worth several bp on its own, so a strategy starting
  at 0.0 bp is negative after costs.
* **Base rate.** Every bucket is scored against an unconditional entry over the
  same bars, side-weighted to the bucket's own mix. Without it a trending sample
  makes the trend-direction side look skilful.
* **Multiple comparisons.** Every extra split makes a false positive more
  likely. The tool prints how many hypotheses you have tested; treat anything
  under 2 SE as noise, and prefer pre-registering a test against future data.
* **In-sample.** Choosing a variant from these numbers and then quoting them is
  circular. Use ``--since`` to hold out sessions.
* **Returns are basis points, per instrument.** Never pool raw points across
  NIFTY (24k), SENSEX (80k) and commodities — a "point" is not comparable.

Input
-----
Bars as CSV with ``time`` plus OHLC (a TradingView export works as-is)::

    time,open,high,low,close,...
    2026-07-15T14:40:00+05:30,24105.35,24107.05,24093.5,24101.5,...

Usage
-----
::

    python scripts/signal_edge_study.py --bars nifty_5m.csv
    python scripts/signal_edge_study.py --bars nifty_5m.csv --variant current
    python scripts/signal_edge_study.py --bars nifty_5m.csv \
        --condition indiavix_5m.csv --condition-col Histogram
    python scripts/signal_edge_study.py --bars nifty_5m.csv --since 2026-09-01
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from options.gamma_density_history import (  # noqa: E402
    detect_spot_reversals,
    normalize_reversal_tf,
    resample_chart_series,
    reversal_tf_params,
)

#: Detection variants. ``current`` mirrors the desk; the rest strip confirmation
#: to trade earlier, which is exactly the trade-off under test.
VARIANTS: dict[str, dict[str, Any]] = {
    "current": {"min_move_zero": False, "gex_gate": True, "right_bars": None},
    "no-gates": {"min_move_zero": False, "gex_gate": False, "right_bars": None},
    "pivot-only": {"min_move_zero": True, "gex_gate": False, "right_bars": None},
    "pivot-only-r1": {"min_move_zero": True, "gex_gate": False, "right_bars": 1},
}

#: Below this, a bucket is not worth reading at all.
MIN_BUCKET = 30
#: Standard errors from 50% before a win rate is worth a second look.
SIGNIFICANCE_SE = 2.0


def load_bars(path: str | Path) -> pd.DataFrame:
    """CSV -> bars with millisecond timestamps.

    Timestamp resolution is normalised explicitly. Pandas may parse a
    tz-offset CSV as ``datetime64[us]`` rather than ``[ns]``; assuming
    nanoseconds silently produced timestamps 1000x too small, which made every
    conditioning lookup match the final row.
    """
    df = pd.read_csv(path)
    if "time" not in df.columns:
        raise ValueError(f"{path}: expected a 'time' column, got {list(df.columns)}")
    parsed = pd.to_datetime(df["time"])
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_convert("UTC").dt.tz_localize(None)
    df["ts_ms"] = parsed.astype("datetime64[ms]").astype("int64")
    df["day"] = pd.to_datetime(df["time"]).dt.date
    close = "close" if "close" in df.columns else "Close"
    df["px"] = pd.to_numeric(df[close], errors="coerce")
    return df.dropna(subset=["px"]).sort_values("ts_ms").reset_index(drop=True)


class Conditioner:
    """Point-in-time lookup into a second series (VIX, breadth, anything).

    Refuses a stale match rather than silently carrying the last known value
    forward for hours — that is how a broken join looks like a strong result.
    """

    def __init__(self, df: pd.DataFrame, column: str, max_stale_min: int = 15) -> None:
        if column not in df.columns:
            raise ValueError(f"condition column {column!r} not in {list(df.columns)}")
        self.ts = df["ts_ms"].to_numpy()
        self.val = pd.to_numeric(df[column], errors="coerce").to_numpy()
        self.level = df["px"].to_numpy()
        self.max_stale_ms = max_stale_min * 60_000

    def at(self, ts_ms: int) -> tuple[float, float] | None:
        i = int(self.ts.searchsorted(ts_ms, side="right")) - 1
        if i < 0 or ts_ms - self.ts[i] > self.max_stale_ms:
            return None
        v, lvl = self.val[i], self.level[i]
        return (None, None) if pd.isna(v) else (float(v), float(lvl))


def detect_session(
    bars: pd.DataFrame, *, tf: str, variant: dict[str, Any]
) -> list[tuple[int, dict[str, Any]]]:
    """Replay one session; return (first_visible_ts, signal) pairs.

    Detection is re-run on the series-so-far at every bar, so a signal is only
    scored from what was knowable then.
    """
    from options.gamma_density_history import adaptive_reversal_min_move

    tf_key = normalize_reversal_tf(tf)
    p = reversal_tf_params(tf_key)
    right = variant["right_bars"]
    rows = [{"t": "", "ts_ms": int(r.ts_ms), "spot": float(r.px)} for r in bars.itertuples()]
    need = p["swing_bars"] + (right if right is not None else p["swing_bars"]) + 2

    seen: dict[tuple, tuple[int, dict[str, Any]]] = {}
    for i in range(len(rows)):
        visible = rows[: i + 1]
        tf_series = resample_chart_series(visible, tf_key)
        if len(tf_series) < need:
            continue
        min_move = (
            0.0
            if variant["min_move_zero"]
            else adaptive_reversal_min_move(visible[-1]["spot"], tf_series)
        )
        for rev in detect_spot_reversals(
            tf_series,
            swing_bars=p["swing_bars"],
            confirm_bars=p["confirm_bars"],
            lock_ms=p["lock_ms"],
            min_move_pts=min_move,
            gex_gate=variant["gex_gate"],
            oi_gate=False,
            tf=tf_key,
            right_bars=right,
            freeze_threshold=not variant["min_move_zero"],
            max_bar_gap_ratio=3.0,
        ):
            key = (rev.get("ts_ms"), rev.get("side"))
            if key not in seen:
                seen[key] = (visible[-1]["ts_ms"], rev)
    return list(seen.values())


def score(
    bars: pd.DataFrame,
    hits: list[tuple[int, dict[str, Any]]],
    horizons: tuple[int, ...],
    cond: Conditioner | None,
) -> list[dict[str, Any]]:
    """Forward return in basis points from the entry actually obtainable."""
    ts = bars["ts_ms"].to_numpy()
    px = bars["px"].to_numpy()
    last = int(ts[-1])
    out: list[dict[str, Any]] = []

    def price_at(t: int) -> float | None:
        i = int(ts.searchsorted(t, side="left"))
        return float(px[i]) if i < len(px) else None

    for emit_ts, rev in hits:
        entry = price_at(emit_ts)
        if entry is None or entry <= 0:
            continue
        rec: dict[str, Any] = {
            "side": rev.get("side"),
            "emit_ts": emit_ts,
            "pivot_ts": rev.get("ts_ms"),
            "lag_min": round((emit_ts - (rev.get("ts_ms") or emit_ts)) / 60_000, 1),
            # Slippage vs the pivot price: always adverse, by construction.
            "slip_bp": round(
                (
                    (entry - rev["spot"]) if rev["side"] == "bullish" else (rev["spot"] - entry)
                )
                / entry
                * 10_000,
                2,
            )
            if rev.get("spot")
            else None,
        }
        if cond is not None:
            got = cond.at(emit_ts)
            if got is None or got[0] is None:
                continue
            rec["cond"], rec["cond_level"] = got
        scored = False
        for h in horizons:
            target = emit_ts + h * 60_000
            if target > last:
                continue  # horizon runs past the session; not a real exit
            exit_px = price_at(target)
            if exit_px is None:
                continue
            pnl = (exit_px - entry) if rev["side"] == "bullish" else (entry - exit_px)
            rec[f"h{h}"] = pnl / entry * 10_000
            scored = True
        if scored:
            out.append(rec)
    return out


def base_rates(bars_by_day: list[pd.DataFrame], horizons: tuple[int, ...]) -> dict[int, dict]:
    """Unconditional return of entering at an arbitrary bar and holding ``h``.

    Without this control, any strategy tested inside a trending period looks
    good in the trend direction. Measured over 38 sessions in which NIFTY fell
    0.68%, an arbitrary 60-minute SHORT won 52.7% of the time with no signal at
    all -- which accounted for almost the whole apparent edge of the bearish
    pivot signal (55.4%, so +2.7 points, under 1 SE).
    """
    out: dict[int, dict] = {}
    for h in horizons:
        longs: list[float] = []
        for g in bars_by_day:
            px = g["px"].to_numpy()
            ts = g["ts_ms"].to_numpy()
            for i in range(len(px)):
                j = int(ts.searchsorted(ts[i] + h * 60_000, side="left"))
                if j >= len(px):
                    continue
                longs.append((px[j] - px[i]) / px[i] * 10_000)
        if not longs:
            out[h] = {}
            continue
        lw = sum(1 for v in longs if v > 0) / len(longs) * 100
        out[h] = {
            "n": len(longs),
            "long_win": round(lw, 1),
            "short_win": round(100 - lw, 1),
            "long_median_bp": round(statistics.median(longs), 2),
        }
    return out


def bucket(
    name: str,
    rows: list[dict[str, Any]],
    h: int,
    base: dict | None = None,
) -> dict[str, Any] | None:
    vals = [r[f"h{h}"] for r in rows if f"h{h}" in r]
    if len(vals) < MIN_BUCKET:
        return {"name": name, "n": len(vals), "thin": True}
    n = len(vals)
    wins = sum(1 for v in vals if v > 0)
    win_pct = wins / n * 100
    se = (0.25 / n) ** 0.5 * 100
    scored = [r for r in rows if f"h{h}" in r]
    out = {
        "name": name,
        "n": n,
        "thin": False,
        "median_bp": round(statistics.median(vals), 2),
        "mean_bp": round(statistics.mean(vals), 2),
        "win_pct": round(win_pct, 1),
        "se_from_coinflip": round((win_pct - 50) / se, 1),
    }
    if base:
        # Blend the base rate by the bucket's own side mix: a long-only bucket
        # is judged against an unconditional long, a mixed one proportionally.
        bulls = sum(1 for r in scored if r["side"] == "bullish")
        bears = n - bulls
        expected = (bulls * base["long_win"] + bears * base["short_win"]) / n
        out["base_win"] = round(expected, 1)
        out["excess_pts"] = round(win_pct - expected, 1)
        out["excess_se"] = round((win_pct - expected) / se, 1)
    return out


def render(report: dict[str, Any]) -> str:
    o: list[str] = []
    m = report["meta"]
    o.append(f"EDGE STUDY · {m['variant']} · {m['tf']} · {m['sessions']} sessions · "
             f"{m['signals']} signals")
    o.append("=" * 92)
    o.append(f"trigger lag: median {m['lag_median']}m   "
             f"slippage vs pivot: median {m['slip_median']} bp "
             f"({m['slip_adverse_pct']}% adverse)")
    o.append("")
    for h, buckets in report["horizons"].items():
        base = report["base_rates"].get(h) or {}
        if base:
            o.append(f"  +{h}m forward   [base rate with no signal at all: "
                     f"long {base['long_win']}%  short {base['short_win']}%]")
        else:
            o.append(f"  +{h}m forward:")
        for b in buckets:
            if b["thin"]:
                o.append(f"    {b['name']:<32} n={b['n']:>4}  (under {MIN_BUCKET}, not read)")
                continue
            # Judge on excess over the base rate, not the raw win rate: in a
            # trending sample the trend-direction side wins without any signal.
            key = "excess_se" if "excess_se" in b else "se_from_coinflip"
            flag = "  <-- worth a look" if abs(b[key]) >= SIGNIFICANCE_SE else ""
            excess = (
                f"  base {b['base_win']:>4.1f}%  excess {b['excess_pts']:>+5.1f} "
                f"({b['excess_se']:+.1f} SE)"
                if "excess_pts" in b
                else f"  ({b['se_from_coinflip']:+.1f} SE)"
            )
            o.append(
                f"    {b['name']:<32} n={b['n']:>4}  median {b['median_bp']:>6.1f} bp  "
                f"win {b['win_pct']:>4.1f}%{excess}{flag}"
            )
        o.append("")
    tests = report["meta"]["hypotheses_tested"]
    o.append("-" * 92)
    o.append(f"{tests} hypotheses tested in this run. At 2 SE, roughly 1 in 20 shows up by")
    o.append("chance; the more splits, the likelier a false positive. Returns are spot in")
    o.append("basis points, before spread, brokerage, slippage and theta.")
    d = report["meta"].get("drift_pct")
    if d is not None:
        o.append("")
        o.append(f"Underlying moved {d:+.2f}% over the sample. EXCESS is the column that")
        o.append("matters: a raw win rate flatters whichever side the tape was already going.")
    return "\n".join(o)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Forward-return edge study for reversal signals.")
    ap.add_argument("--bars", required=True, help="price CSV (time + OHLC)")
    ap.add_argument("--variant", default="pivot-only-r1", choices=sorted(VARIANTS))
    ap.add_argument("--tf", default="5m", choices=["1m", "5m", "15m"])
    ap.add_argument("--horizons", default="15,30,60", help="forward minutes, comma separated")
    ap.add_argument("--condition", default=None, help="second CSV to split on (e.g. India VIX)")
    ap.add_argument("--condition-col", default="Histogram", help="column in --condition")
    ap.add_argument("--since", default=None, help="only sessions on/after YYYY-MM-DD (holdout)")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    variant = VARIANTS[args.variant]

    bars = load_bars(args.bars)
    if args.since:
        cutoff = pd.to_datetime(args.since).date()
        bars = bars[bars["day"] >= cutoff].reset_index(drop=True)
        if bars.empty:
            print(f"error: no sessions on/after {args.since}", file=sys.stderr)
            return 2

    # The gated variants need a dealer-gamma regime per bar, which a plain price
    # export does not carry. Without it _gex_gate_result rejects every candidate
    # and the run returns nothing -- say why rather than printing "no signals".
    if variant["gex_gate"] and not {"gamma_regime", "total_gex"} & set(bars.columns):
        for msg in (
            f"error: variant {args.variant!r} needs the GEX gate, but this file",
            "       carries no 'gamma_regime' or 'total_gex' column.",
            "       A price-only export drives the ungated variants "
            "(pivot-only, pivot-only-r1, no-gates);",
            "       gated ones need the desk archive - use scripts/signal_lag_study.py.",
        ):
            print(msg, file=sys.stderr)
        return 2

    cond = None
    if args.condition:
        cond = Conditioner(load_bars(args.condition), args.condition_col)

    recs: list[dict[str, Any]] = []
    sessions: list[pd.DataFrame] = []
    for _day, g in bars.groupby("day"):
        g = g.reset_index(drop=True)
        if len(g) < 20:
            continue
        sessions.append(g)
        recs.extend(score(g, detect_session(g, tf=args.tf, variant=variant), horizons, cond))

    base = base_rates(sessions, horizons)

    if not recs:
        print("error: no scored signals", file=sys.stderr)
        return 2

    lags = [r["lag_min"] for r in recs]
    slips = [r["slip_bp"] for r in recs if r.get("slip_bp") is not None]
    splits: list[tuple[str, list[dict[str, Any]]]] = [
        ("ALL", recs),
        ("bullish", [r for r in recs if r["side"] == "bullish"]),
        ("bearish", [r for r in recs if r["side"] == "bearish"]),
    ]
    if cond is not None:
        # Aligned = the conditioner agrees with the trade direction. For India
        # VIX MACD: falling vol supports a long, rising vol supports a short.
        aligned = [
            r for r in recs
            if (r["side"] == "bullish" and r["cond"] < 0) or (r["side"] == "bearish" and r["cond"] > 0)
        ]
        opposed = [r for r in recs if r not in aligned]
        med = statistics.median(r["cond_level"] for r in recs)
        splits += [
            ("condition ALIGNED", aligned),
            ("condition OPPOSED", opposed),
            (f"level < {med:.2f}", [r for r in recs if r["cond_level"] < med]),
            (f"level >= {med:.2f}", [r for r in recs if r["cond_level"] >= med]),
        ]

    report = {
        "meta": {
            "variant": args.variant,
            "tf": args.tf,
            "sessions": bars["day"].nunique(),
            "signals": len(recs),
            "lag_median": round(statistics.median(lags), 1) if lags else None,
            "slip_median": round(statistics.median(slips), 1) if slips else None,
            "slip_adverse_pct": round(sum(1 for s in slips if s > 0) / len(slips) * 100)
            if slips
            else None,
            "hypotheses_tested": len(splits) * len(horizons),
            "drift_pct": round(
                (bars["px"].iloc[-1] / bars["px"].iloc[0] - 1) * 100, 2
            ),
        },
        "base_rates": base,
        "horizons": {
            h: [b for b in (bucket(n, rows, h, base.get(h)) for n, rows in splits) if b]
            for h in horizons
        },
    }

    print(render(report))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nwrote {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
