"""Phase 0 — measure how late reversal signals actually appear.

The desk re-runs ``detect_spot_reversals`` over the whole session on every poll
and reconciles against frozen signals, so a pivot that fails a gate is not
discarded: it is silently retried until it passes. When it finally passes, the
chip appears *now* but is labelled with its backdated pivot and confirm times.
Operators see "conf 09:49" land at 10:20 and reasonably read it as a bug.

This replays an archived session tick by tick, re-running the real detector on
the series-so-far at each step, and records the first tick at which each signal
would have become visible. That gives the three numbers the tuning work needs:

  * **shape lag**   — pivot -> first visible (structural: swing bars + TF close)
  * **conf lag**    — labelled confirm -> first visible (what the operator feels)
  * **gate cost**   — the same replay with the GEX gate off, differenced

Read-only. Nothing here writes to ``data/``.

Fidelity caveats, so the numbers are not over-read
--------------------------------------------------
  * The live detection series is 1-minute *candles* merged with sparse GEX
    ticks. This replays the archive alone (~30s GEX cadence), because
    ``build_chart_series`` filters to today and cannot rebuild a past session.
    On 5m/15m resampling the difference is small; on 1m it flatters slightly.
  * The OI gate is not measured: the archive stores ``pin_strike`` but not the
    walls or per-strike rows the gate needs. That matches the desk's default
    (Require OI off), but it means OI-gate lag is unmeasured, not zero.
  * Same-side lock is applied inside detect, so suppressed repeats never
    appear here -- as intended.

Usage
-----
::

    python scripts/signal_lag_study.py --list
    python scripts/signal_lag_study.py --key "NIFTY|2026-09-01|2026-08-27"
    python scripts/signal_lag_study.py --all --tf 5m
    python scripts/signal_lag_study.py --all --tf 1m --json lag_1m.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from options.gamma_density_history import (  # noqa: E402
    IST,
    adaptive_reversal_min_move,
    detect_spot_reversals,
    normalize_reversal_tf,
    resample_chart_series,
    reversal_tf_params,
)
from settings import data_dir  # noqa: E402

ARCHIVE = "gamma_density_history.json"

# Detection row keys the pipeline actually reads.
_ROW_KEYS = ("spot", "total_gex", "flip_level", "gamma_regime", "pin_strike", "atm_iv")


def archive_path() -> Path:
    return data_dir() / ARCHIVE


def load_archive(path: Path | None = None) -> dict[str, Any]:
    p = path or archive_path()
    raw = p.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Recover from the trailing-garbage shape rather than refusing to run.
        obj, end = json.JSONDecoder().raw_decode(raw)
        print(
            f"warn: archive has {len(raw) - end} trailing bytes; using the valid prefix",
            file=sys.stderr,
        )
        return obj


def session_keys(store: dict[str, Any], min_points: int = 60) -> list[str]:
    series = store.get("series", {})
    return sorted(k for k, v in series.items() if len(v or []) >= min_points)


def to_rows(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Archive ticks -> detection rows, with ts_ms derived from the timestamp."""
    rows: list[dict[str, Any]] = []
    for p in points:
        t = p.get("t")
        if not t:
            continue
        try:
            dt = datetime.fromisoformat(str(t))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        row: dict[str, Any] = {"t": t, "ts_ms": int(dt.timestamp() * 1000)}
        for k in _ROW_KEYS:
            if p.get(k) is not None:
                row[k] = p[k]
        if row.get("spot") is None:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["ts_ms"])
    return rows


def _sig_id(rev: dict[str, Any]) -> tuple[Any, Any]:
    return (rev.get("ts_ms"), rev.get("side"))


def replay(
    rows: list[dict[str, Any]],
    *,
    tf: str,
    gex_gate: bool,
    provisional: bool = False,
    knobs: dict[str, Any] | None = None,
) -> dict[tuple, dict[str, Any]]:
    """Re-run detection at every tick; record when each signal first appears.

    This mirrors the live path exactly: detect over the session-so-far, every
    poll. The first tick a signal shows up on is when the operator would have
    seen it.
    """
    tf_key = normalize_reversal_tf(tf)
    params = reversal_tf_params(tf_key)
    first_seen: dict[tuple, dict[str, Any]] = {}

    for i in range(len(rows)):
        visible = rows[: i + 1]
        now_ms = visible[-1]["ts_ms"]
        tf_series = resample_chart_series(visible, tf_key)
        if len(tf_series) < params["swing_bars"] * 2 + 2:
            continue
        spot = visible[-1].get("spot")
        if spot is None:
            continue
        try:
            revs = detect_spot_reversals(
                tf_series,
                swing_bars=params["swing_bars"],
                confirm_bars=params["confirm_bars"],
                lock_ms=params["lock_ms"],
                min_move_pts=adaptive_reversal_min_move(float(spot), tf_series),
                gex_gate=gex_gate,
                oi_gate=False,
                provisional_ungated=provisional,
                tf=tf_key,
                **(knobs or {}),
            )
        except Exception as exc:  # noqa: BLE001 - one bad tick must not end the study
            print(f"warn: detect failed at {visible[-1]['t']}: {exc}", file=sys.stderr)
            continue

        for rev in revs:
            key = _sig_id(rev)
            if key in first_seen:
                continue
            first_seen[key] = {"emit_ts_ms": now_ms, "rev": rev}
    return first_seen


def _mins(a: Any, b: Any) -> float | None:
    if a is None or b is None:
        return None
    try:
        return round((int(a) - int(b)) / 60000.0, 1)
    except (TypeError, ValueError):
        return None


def _hhmm(ts_ms: Any) -> str:
    if ts_ms is None:
        return "-"
    return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=IST).strftime("%H:%M")


def study_session(rows: list[dict[str, Any]], *, tf: str) -> dict[str, Any]:
    """Replay with the GEX gate on and off; difference is the gate's cost."""
    gated = replay(rows, tf=tf, gex_gate=True)
    ungated = replay(rows, tf=tf, gex_gate=False)
    # Phase 2: same hard gate, but a blocked pivot is surfaced muted instead of
    # dropped. Measures what provisional-first actually buys.
    prov = replay(rows, tf=tf, gex_gate=True, provisional=True)

    signals: list[dict[str, Any]] = []
    for key, hit in sorted(gated.items(), key=lambda kv: kv[0][0] or 0):
        rev = hit["rev"]
        emit = hit["emit_ts_ms"]
        pivot = rev.get("ts_ms")
        conf = rev.get("confirmed_ts_ms")
        base = ungated.get(key, {}).get("emit_ts_ms")
        signals.append({
            "pivot": _hhmm(pivot),
            "confirmed": _hhmm(conf),
            "emitted": _hhmm(emit),
            "side": rev.get("side"),
            "move_pts": rev.get("move_pts"),
            "shape_lag_min": _mins(emit, pivot),
            "conf_lag_min": _mins(emit, conf),
            "gate_cost_min": _mins(emit, base),
            "gex_confirm": rev.get("gex_confirm"),
        })

    suppressed = [k for k in ungated if k not in gated]
    # How much earlier does provisional-first surface a signal the hard gate
    # eventually accepts?
    earlier: list[float] = []
    for key, hit in gated.items():
        pv = prov.get(key)
        if pv is None:
            continue
        gain = _mins(hit["emit_ts_ms"], pv["emit_ts_ms"])
        if gain is not None and gain > 0:
            earlier.append(gain)
    return {
        "n_provisional": len(prov),
        "n_rescued_by_provisional": len([k for k in suppressed if k in prov]),
        "provisional_earlier_min": earlier,
        "tf": normalize_reversal_tf(tf),
        "ticks": len(rows),
        "span": f"{rows[0]['t'][11:16]}-{rows[-1]['t'][11:16]}" if rows else "-",
        "signals": signals,
        "n_gated": len(gated),
        "n_ungated": len(ungated),
        "n_suppressed_by_gate": len(suppressed),
    }


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, int(round(p * (len(s) - 1))))
    return s[idx]


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    conf = [s["conf_lag_min"] for r in results for s in r["signals"] if s["conf_lag_min"] is not None]
    shape = [s["shape_lag_min"] for r in results for s in r["signals"] if s["shape_lag_min"] is not None]
    gate = [s["gate_cost_min"] for r in results for s in r["signals"] if s["gate_cost_min"] is not None]
    earlier = [v for r in results for v in r.get("provisional_earlier_min", [])]
    return {
        "sessions": len(results),
        "signals": sum(len(r["signals"]) for r in results),
        "suppressed_by_gate": sum(r["n_suppressed_by_gate"] for r in results),
        "rescued_by_provisional": sum(r.get("n_rescued_by_provisional", 0) for r in results),
        "provisional_earlier": {
            "n": len(earlier),
            "median": round(statistics.median(earlier), 1) if earlier else None,
            "max": max(earlier) if earlier else None,
        },
        "conf_lag_min": {
            "median": round(statistics.median(conf), 1) if conf else None,
            "p90": _pct(conf, 0.90), "max": max(conf) if conf else None,
        },
        "shape_lag_min": {
            "median": round(statistics.median(shape), 1) if shape else None,
            "p90": _pct(shape, 0.90), "max": max(shape) if shape else None,
        },
        "gate_cost_min": {
            "median": round(statistics.median(gate), 1) if gate else None,
            "p90": _pct(gate, 0.90), "max": max(gate) if gate else None,
        },
    }


def render(results: list[dict[str, Any]], keys: list[str]) -> str:
    o: list[str] = []
    for key, r in zip(keys, results, strict=True):
        o.append(f"{key}   tf={r['tf']}  {r['ticks']} ticks  {r['span']}")
        o.append("-" * 92)
        if not r["signals"]:
            o.append("  (no signals)")
            o.append("")
            continue
        o.append(f"  {'side':<9}{'pivot':>7}{'conf':>7}{'emitted':>9}"
                 f"{'shape':>8}{'conf->emit':>12}{'gate cost':>11}{'move':>8}")
        for s in r["signals"]:
            o.append(
                f"  {str(s['side']):<9}{s['pivot']:>7}{s['confirmed']:>7}{s['emitted']:>9}"
                f"{(s['shape_lag_min'] or 0):>7.0f}m{(s['conf_lag_min'] or 0):>11.0f}m"
                f"{(s['gate_cost_min'] or 0):>10.0f}m{(s['move_pts'] or 0):>8.0f}"
            )
        o.append(f"  gated {r['n_gated']}   ungated {r['n_ungated']}   "
                 f"suppressed by gate {r['n_suppressed_by_gate']}")
        o.append("")

    s = summarise(results)
    o.append("=" * 92)
    o.append(f"SUMMARY  {s['sessions']} sessions, {s['signals']} signals, "
             f"{s['suppressed_by_gate']} suppressed entirely by the GEX gate")
    pe = s["provisional_earlier"]
    o.append(f"         provisional-first rescues {s['rescued_by_provisional']} of those, "
             f"and surfaces {pe['n']} accepted signals earlier "
             f"(median {pe['median']}m, max {pe['max']}m)")
    o.append("-" * 92)
    o.append(f"{'metric':<26}{'median':>10}{'p90':>10}{'max':>10}")
    for label, k in (
        ("shape lag (pivot->seen)", "shape_lag_min"),
        ("conf lag (conf->seen)", "conf_lag_min"),
        ("GEX gate cost", "gate_cost_min"),
    ):
        m = s[k]
        f = lambda v: "-" if v is None else f"{v:.0f}m"  # noqa: E731
        o.append(f"{label:<26}{f(m['median']):>10}{f(m['p90']):>10}{f(m['max']):>10}")
    return "\n".join(o)


#: Phase 3 knobs, measured one at a time against the current behaviour so each
#: change can be judged on its own rather than as a bundle.
VARIANTS: dict[str, dict[str, Any]] = {
    "baseline": {},
    "gap-bound": {"max_bar_gap_ratio": 3.0},
    "frozen-threshold": {"freeze_threshold": True},
    # Both of these remove behaviour that is simply wrong -- a pivot proved
    # across a sampling hole, and a pivot admitted by a threshold that fell
    # after the fact. Neither trades proof for latency.
    "fixes only": {"max_bar_gap_ratio": 3.0, "freeze_threshold": True},
    "right=1": {"right_bars": 1},
    "all three": {"max_bar_gap_ratio": 3.0, "freeze_threshold": True, "right_bars": 1},
}


def compare_variants(store: dict[str, Any], keys: list[str], *, tf: str) -> list[dict[str, Any]]:
    """Per variant: signal count and the lag distribution, over every session."""
    out: list[dict[str, Any]] = []
    for name, knobs in VARIANTS.items():
        shape: list[float] = []
        conf: list[float] = []
        n_sig = 0
        for key in keys:
            rows = to_rows(store["series"][key])
            if len(rows) < 60:
                continue
            seen = replay(rows, tf=tf, gex_gate=True, knobs=knobs)
            n_sig += len(seen)
            for hit in seen.values():
                rev = hit["rev"]
                sl = _mins(hit["emit_ts_ms"], rev.get("ts_ms"))
                cl = _mins(hit["emit_ts_ms"], rev.get("confirmed_ts_ms"))
                if sl is not None:
                    shape.append(sl)
                if cl is not None:
                    conf.append(cl)
        out.append({
            "variant": name,
            "signals": n_sig,
            "shape_median": round(statistics.median(shape), 1) if shape else None,
            "shape_p90": _pct(shape, 0.90),
            "shape_max": max(shape) if shape else None,
            "conf_p90": _pct(conf, 0.90),
            "conf_max": max(conf) if conf else None,
        })
    return out


def render_compare(rows: list[dict[str, Any]], tf: str) -> str:
    o = [f"PHASE 3 VARIANTS  tf={tf}  (each knob measured alone)", "=" * 84]
    o.append(f"{'variant':<20}{'signals':>9}{'shape med':>11}{'shape p90':>11}"
             f"{'shape max':>11}{'conf p90':>10}{'conf max':>10}")
    o.append("-" * 84)
    base = rows[0]
    for r in rows:
        d = ""
        if r["variant"] != "baseline" and base["signals"]:
            delta = r["signals"] - base["signals"]
            d = f"  ({delta:+d})"
        f = lambda v: "-" if v is None else f"{v:.0f}m"  # noqa: E731
        o.append(f"{r['variant']:<20}{r['signals']:>9}{f(r['shape_median']):>11}"
                 f"{f(r['shape_p90']):>11}{f(r['shape_max']):>11}"
                 f"{f(r['conf_p90']):>10}{f(r['conf_max']):>10}{d}")
    return chr(10).join(o)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Phase 0 - reversal signal lag study (offline replay).")
    ap.add_argument("--archive", default=None, help="path to gamma_density_history.json")
    ap.add_argument("--key", action="append", help="session key; repeatable")
    ap.add_argument("--all", action="store_true", help="every session with enough ticks")
    ap.add_argument("--tf", default="5m", choices=["1m", "5m", "15m"])
    ap.add_argument("--min-points", type=int, default=60)
    ap.add_argument("--list", action="store_true", help="list session keys and exit")
    ap.add_argument("--compare", action="store_true", help="Phase 3 variant comparison")
    ap.add_argument("--json", dest="json_out", default=None)
    args = ap.parse_args(argv)

    store = load_archive(Path(args.archive) if args.archive else None)
    keys_available = session_keys(store, args.min_points)

    if args.list:
        for k in keys_available:
            print(f"{k:40s} {len(store['series'][k]):5d} pts")
        return 0

    keys = args.key or (keys_available if args.all else keys_available[-1:])
    keys = [k for k in keys if k in store.get("series", {})]
    if not keys:
        print("error: no matching session keys (try --list)", file=sys.stderr)
        return 2

    if args.compare:
        print(render_compare(compare_variants(store, keys, tf=args.tf), args.tf))
        return 0

    results = []
    for k in keys:
        rows = to_rows(store["series"][k])
        if len(rows) < args.min_points:
            print(f"skip {k}: only {len(rows)} usable rows", file=sys.stderr)
            continue
        results.append(study_session(rows, tf=args.tf))

    if not results:
        print("error: nothing to study", file=sys.stderr)
        return 2

    keys = keys[: len(results)]
    print(render(results, keys))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"keys": keys, "results": results, "summary": summarise(results)}, indent=2),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
