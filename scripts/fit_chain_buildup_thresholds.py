"""Fit the Chain Build-Up adaptive breach thresholds from the minute archive.

Read-only against ``data/delta_velocity``; writes exactly one file,
``analysis/chain_buildup/calibration.py``, which is committed. The table is a
*calibration constant*, not runtime state, so it lives in the module tree rather
than under ``data/`` — a fresh checkout and CI both need it, and ``data/`` is
gitignored.

Run it when the archive has grown enough to be worth re-fitting::

    python scripts/fit_chain_buildup_thresholds.py

Why a factorised model rather than a full cross-tab
---------------------------------------------------
The honest estimate would be p95 of |dOI %| for every (timeframe, DTE, minute)
cell. With 14 sessions that is a few hundred observations per cell at 5m and
single digits at 60m — noise, dressed as precision. So the fit is separable::

    threshold(tf, dte, tod) = base_p95[tf] * dte_factor[tf][dte] * tod_factor[tf][tod]

Each factor is a ratio of marginal p95s, which needs far less data to estimate.
The cost is that it cannot represent an *interaction* — if the opening surge is
sharper on expiry day than on a monthly, this model averages the two. That is a
real limitation and 14 sessions cannot resolve it; revisit once the archive is
deep enough to estimate the cross-term directly.

Multiplying two marginal ratios also overstates the tails, so the factors are
re-centred to keep the fitted rate near the target. ``--verify`` measures the
realised breach rate per (DTE, time-of-day) cell afterwards: the whole point is
a rate that is flat at ~5% everywhere, and that is the number to check, not the
prettiness of the table.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.chain_buildup import features, service  # noqa: E402
from analysis.delta_velocity import store as dv_store  # noqa: E402

OUT_PATH = ROOT / "analysis" / "chain_buildup" / "calibration.py"

TARGET_PCT = 95.0
#: Below this many observations a bucket keeps factor 1.0 rather than fitting
#: noise. 60m at long DTE is genuinely thin.
MIN_SAMPLES = 200


def dte_bucket(days: int) -> str:
    if days <= 1:
        return "0-1"
    if days <= 7:
        return "2-7"
    if days <= 21:
        return "8-21"
    return "22+"


DTE_BUCKETS = ("0-1", "2-7", "8-21", "22+")


def collect(underlyings: tuple[str, ...]) -> dict[int, list[tuple[float, str, str]]]:
    """(abs_pct, dte_bucket, tod_key) per live cell, per timeframe."""
    out: dict[int, list[tuple[float, str, str]]] = defaultdict(list)
    for u in underlyings:
        for day in dv_store.sessions_available(u):
            rows = service._session_rows(u, day)
            if not rows:
                continue
            for exp in sorted({str(r["expiry"]) for r in rows if r.get("expiry")}):
                scoped = [r for r in rows if str(r.get("expiry")) == exp]
                try:
                    db = dte_bucket((date.fromisoformat(exp) - day).days)
                except ValueError:
                    continue
                for tf in features.TIMEFRAMES_MIN:
                    grid = features.build_grid(scoped, timeframe_min=tf, expiry=exp)
                    keys = [b["key"] for b in grid["buckets"]]
                    for row in grid["rows"]:
                        for side in ("ce", "pe"):
                            for i, c in enumerate(row[side]["cells"]):
                                pct, delta = c["d_oi_pct"], c["d_oi"]
                                if pct is None or not delta:
                                    continue
                                out[tf].append((abs(pct), db, keys[i]))
    return out


def _p(values: list[float]) -> float:
    return float(np.percentile(values, TARGET_PCT))


def fit(samples: dict[int, list[tuple[float, str, str]]]) -> dict:
    base: dict[int, float] = {}
    dte_f: dict[int, dict[str, float]] = {}
    tod_f: dict[int, dict[str, float]] = {}
    counts: dict[int, int] = {}

    for tf, rows in samples.items():
        if len(rows) < MIN_SAMPLES:
            continue
        vals = [r[0] for r in rows]
        b = _p(vals)
        base[tf] = round(b, 3)
        counts[tf] = len(rows)

        by_dte: dict[str, list[float]] = defaultdict(list)
        by_tod: dict[str, list[float]] = defaultdict(list)
        for pct, db, key in rows:
            by_dte[db].append(pct)
            by_tod[key].append(pct)

        dte_f[tf] = {
            k: round(_p(v) / b, 4) if len(v) >= MIN_SAMPLES and b > 0 else 1.0
            for k, v in sorted(by_dte.items())
        }
        raw_tod = {
            k: (_p(v) / b if len(v) >= MIN_SAMPLES and b > 0 else 1.0)
            for k, v in sorted(by_tod.items())
        }
        # Re-centre so the product of two marginal ratios does not drift the
        # overall rate away from the target. The DTE factor carries the level;
        # time-of-day only redistributes within the session.
        mean_tod = float(np.mean(list(raw_tod.values()))) or 1.0
        tod_f[tf] = {k: round(v / mean_tod, 4) for k, v in raw_tod.items()}

    return {"base": base, "dte": dte_f, "tod": tod_f, "counts": counts}


def verify(samples: dict[int, list[tuple[float, str, str]]], model: dict) -> str:
    """Realised breach rate per (DTE, session-third) under the fitted model."""
    lines: list[str] = []
    for tf in sorted(samples):
        if tf not in model["base"]:
            continue
        cells: dict[tuple[str, str], list[int]] = defaultdict(list)
        flat: list[int] = []
        fixed_hits: list[int] = []
        cur = features.PCT_THRESHOLDS[tf]
        for pct, db, key in samples[tf]:
            thr = (
                model["base"][tf]
                * model["dte"][tf].get(db, 1.0)
                * model["tod"][tf].get(key, 1.0)
            )
            hit = int(pct > thr)
            third = "open" if key < "11:00" else ("mid" if key < "14:00" else "close")
            cells[(db, third)].append(hit)
            flat.append(hit)
            fixed_hits.append(int(pct > cur))
        lines.append(
            f"\n{tf}m  overall: adaptive {np.mean(flat) * 100:.1f}% "
            f"vs fixed {np.mean(fixed_hits) * 100:.1f}%  (target {100 - TARGET_PCT:.0f}%)"
        )
        for db in DTE_BUCKETS:
            parts = []
            for third in ("open", "mid", "close"):
                v = cells.get((db, third))
                parts.append(f"{third} {np.mean(v) * 100:>5.1f}%" if v and len(v) > 100 else f"{third}     -")
            lines.append(f"    DTE {db:>5}   " + "   ".join(parts))
    return "\n".join(lines)


def render(model: dict, sessions: int, underlyings: tuple[str, ...]) -> str:
    def fmt(d: dict) -> str:
        return "{\n" + "".join(
            f"        {k!r}: {v!r},\n" for k, v in d.items()
        ) + "    }"

    body = [
        '"""Fitted breach thresholds for the Chain Build-Up desk — GENERATED FILE.',
        "",
        "Regenerate with ``python scripts/fit_chain_buildup_thresholds.py``. Do not",
        "hand-edit: the next fit overwrites it. See that script's docstring for why the",
        "model is factorised rather than a full cross-tab, and what it therefore cannot",
        "represent.",
        "",
        f"Fitted from {sessions} archived session-files "
        f"({', '.join(underlyings)}) at the {TARGET_PCT:.0f}th percentile.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        f"TARGET_PERCENTILE = {TARGET_PCT}",
        f"FITTED_SESSIONS = {sessions}",
        f"FITTED_UNDERLYINGS = {list(underlyings)!r}",
        "",
        "#: Overall p95 of |dOI %| per timeframe — the level the factors scale.",
        f"BASE_P95: dict[int, float] = {model['base']!r}",
        "",
        "#: Multiplier by days-to-expiry bucket. The dominant conditioning variable:",
        "#: expiry-day OI churns several times harder than a far-dated month.",
        "DTE_FACTOR: dict[int, dict[str, float]] = {",
    ]
    for tf, d in model["dte"].items():
        body.append(f"    {tf}: {fmt(d)},")
    body += [
        "}",
        "",
        "#: Multiplier by bucket-close time. Re-centred to mean 1.0, so it",
        "#: redistributes strictness within a session without changing its level.",
        "TOD_FACTOR: dict[int, dict[str, float]] = {",
    ]
    for tf, d in model["tod"].items():
        body.append(f"    {tf}: {fmt(d)},")
    body += [
        "}",
        "",
        "",
        "def adaptive_threshold(timeframe_min: int, dte_bucket: str, bucket_key: str) -> float | None:",
        '    """Fitted |dOI %| threshold, or None when this timeframe was never fitted."""',
        "    base = BASE_P95.get(timeframe_min)",
        "    if base is None:",
        "        return None",
        "    dte = DTE_FACTOR.get(timeframe_min, {}).get(dte_bucket, 1.0)",
        "    tod = TOD_FACTOR.get(timeframe_min, {}).get(bucket_key, 1.0)",
        "    return base * dte * tod",
        "",
    ]
    return "\n".join(body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="report realised rates, write nothing")
    ap.add_argument("--underlyings", default="NIFTY,BANKNIFTY,SENSEX")
    args = ap.parse_args()

    underlyings = tuple(u.strip().upper() for u in args.underlyings.split(",") if u.strip())
    sessions = sum(len(dv_store.sessions_available(u)) for u in underlyings)
    print(f"scanning {sessions} session-files across {', '.join(underlyings)} ...")

    samples = collect(underlyings)
    model = fit(samples)
    for tf in sorted(model["base"]):
        print(f"  {tf}m: n={model['counts'][tf]:,}  base p95={model['base'][tf]}%")

    print(verify(samples, model))

    if args.verify:
        print("\n--verify: nothing written.")
        return

    OUT_PATH.write_text(render(model, sessions, underlyings), encoding="utf-8")
    print(f"\nwrote {OUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
