"""Does a Chain Build-Up breach predict anything? Read-only event study.

For every breached cell (adaptive thresholds, so events are calibrated rather
than dominated by the open), measure the UNDERLYING's forward return at
+5/+15/+30/+60 minutes and test it against the unconditional forward return over
the same sessions.

Run::

    python scripts/event_study_chain_buildup.py
    python scripts/event_study_chain_buildup.py --naive-contrast

Result as of 2026-08-27 (42 session-files): **null**. Nothing survives
Benjamini-Hochberg at q<0.10; effects run 0.2-4.1 bps against standard errors of
the same size. Recorded in docs/CONVERSATION_SUMMARY.md. Re-run as the archive
grows — the finding is "failed to reject on a thin sample", not "closed".

The trap this is built to avoid
-------------------------------
Forward spot return is a property of (underlying, session, timestamp). Every
breached cell at 11:05 on one session shares ONE outcome, so treating cells as
independent observations turns ~33 independent moments into ~250 fake ones,
shrinks the standard error by ~2.7x and manufactures a result.

Events are therefore collapsed to one observation per
(underlying, session, bucket, side, class), and inference clusters on
(underlying, session): cluster means are the iid unit, which is the conservative
choice when there are only a few dozen clusters.

``--naive-contrast`` runs the wrong version alongside, because the difference is
the whole lesson. On the 2026-08 archive the naive analysis reports CE
short-covering as significant at *all four* horizons, strengthening monotonically
with horizon (t up to 3.63, p=0.0005) — the most convincing shape a false
positive can take. Under clustering the same data gives p=0.080.

The option's own forward premium DOES vary per leg, so it is reported separately
at cell level.

Writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.chain_buildup import features, service  # noqa: E402
from analysis.delta_velocity import store as dv_store  # noqa: E402

TF = 5
HORIZONS = (5, 15, 30, 60)
BH_Q = 0.10

#: Sign each class is expected to carry for the UNDERLYING. Call writing is
#: resistance (bearish); put writing is support (bullish). Used only to orient
#: the printed sign — it does not affect any p-value.
EXPECTED = {
    ("ce", features.SHORT_BUILDUP): -1,
    ("ce", features.LONG_BUILDUP): +1,
    ("ce", features.SHORT_COVERING): +1,
    ("ce", features.LONG_UNWINDING): -1,
    ("pe", features.SHORT_BUILDUP): +1,
    ("pe", features.LONG_BUILDUP): -1,
    ("pe", features.SHORT_COVERING): -1,
    ("pe", features.LONG_UNWINDING): +1,
}


def collect(underlyings: tuple[str, ...]):
    events: dict[tuple, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))
    baseline: dict[int, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))
    premium: dict[tuple, list[float]] = defaultdict(list)
    n_cells = n_breach = 0

    for u in underlyings:
        for day in dv_store.sessions_available(u):
            rows = service._session_rows(u, day)
            if not rows:
                continue
            cluster = (u, day.isoformat())
            for exp in sorted({str(r["expiry"]) for r in rows if r.get("expiry")}):
                scoped = [r for r in rows if str(r.get("expiry")) == exp]
                try:
                    dte = (date.fromisoformat(exp) - day).days
                except ValueError:
                    continue
                grid = features.build_grid(
                    scoped, timeframe_min=TF, expiry=exp,
                    threshold_mode="adaptive", dte_days=dte,
                )
                spots = [b["spot"] for b in grid["buckets"]]
                nb = len(spots)

                for i in range(nb):
                    if spots[i] is None:
                        continue
                    for h in HORIZONS:
                        j = i + h // TF
                        if j < nb and spots[j] is not None:
                            baseline[h][cluster].append((spots[j] / spots[i] - 1) * 1e4)

                seen: set[tuple] = set()
                for row in grid["rows"]:
                    for side in ("ce", "pe"):
                        cells = row[side]["cells"]
                        for i, c in enumerate(cells):
                            if c["d_oi"] is not None:
                                n_cells += 1
                            if not c["breach"] or not c["cls"]:
                                continue
                            n_breach += 1
                            for h in HORIZONS:
                                j = i + h // TF
                                if j < len(cells) and c["ltp"] and cells[j]["ltp"]:
                                    premium[(side, c["cls"], h)].append(
                                        (cells[j]["ltp"] / c["ltp"] - 1) * 100
                                    )
                            key = (i, side, c["cls"])
                            if key in seen or spots[i] is None:
                                continue
                            seen.add(key)
                            for h in HORIZONS:
                                j = i + h // TF
                                if j < nb and spots[j] is not None:
                                    events[(side, c["cls"], h)][cluster].append(
                                        (spots[j] / spots[i] - 1) * 1e4
                                    )
    return events, baseline, premium, n_cells, n_breach


def cluster_test(ev: dict, base: dict):
    """Cluster means as the iid unit. Returns (n_clusters, mean, se, t, p)."""
    diffs = []
    for cl, vals in ev.items():
        b = base.get(cl)
        if vals and b:
            diffs.append(float(np.mean(vals) - np.mean(b)))
    n = len(diffs)
    if n < 3:
        return n, float("nan"), float("nan"), float("nan"), float("nan")
    arr = np.array(diffs)
    m = float(arr.mean())
    se = float(arr.std(ddof=1) / np.sqrt(n))
    t = m / se if se > 0 else 0.0
    return n, m, se, t, float(2 * (1 - stats.t.cdf(abs(t), df=n - 1)))


def bh_keep(pvals: list[float], q: float = BH_Q) -> list[bool]:
    """Benjamini-Hochberg. With 32 tests, ~1.6 hits at p<.05 are expected from
    noise alone, so an uncorrected 'discovery' here means nothing."""
    order = np.argsort(pvals)
    m = len(pvals)
    cutoff = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / m:
            cutoff = rank
    keep = [False] * m
    for rank, i in enumerate(order, start=1):
        if rank <= cutoff:
            keep[i] = True
    return keep


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlyings", default="NIFTY,BANKNIFTY,SENSEX")
    ap.add_argument("--naive-contrast", action="store_true",
                    help="also run the cell-level (incorrect) inference, for comparison")
    args = ap.parse_args()
    underlyings = tuple(u.strip().upper() for u in args.underlyings.split(",") if u.strip())

    print(f"collecting {', '.join(underlyings)} ...")
    events, baseline, premium, n_cells, n_breach = collect(underlyings)
    if not n_cells:
        print("no archived cells — nothing to test.")
        return
    print(f"cells with a delta: {n_cells:,}   breaches: {n_breach:,} "
          f"({n_breach / n_cells * 100:.1f}%)\n")

    rows = []
    for (side, cls, h), ev in sorted(events.items()):
        n, m, se, t, p = cluster_test(ev, baseline[h])
        rows.append({"side": side, "cls": cls, "h": h, "n_cl": n,
                     "n_ev": sum(len(v) for v in ev.values()),
                     "mean": m, "se": se, "t": t, "p": p})
    valid = [r for r in rows if r["n_cl"] >= 3 and np.isfinite(r["p"])]
    for r, k in zip(valid, bh_keep([r["p"] for r in valid]), strict=True):
        r["bh"] = k

    print("=" * 92)
    print("FORWARD UNDERLYING RETURN AFTER A BREACH, vs the unconditional mean")
    print("bps; sign oriented so POSITIVE = the classic four-quadrant reading was right")
    print("=" * 92)
    print(f"{'side':>4} {'class':>16} {'h':>4} {'clusters':>9} {'events':>7} "
          f"{'edge bps':>9} {'se':>7} {'t':>6} {'p':>7} {'BH':>5}")
    for r in sorted(valid, key=lambda x: (x["side"], x["cls"], x["h"])):
        sign = EXPECTED.get((r["side"], r["cls"]), 1)
        print(f"{r['side']:>4} {r['cls']:>16} {r['h']:>3}m {r['n_cl']:>9} {r['n_ev']:>7} "
              f"{r['mean'] * sign:>9.2f} {r['se']:>7.2f} {r['t'] * sign:>6.2f} "
              f"{r['p']:>7.3f} {'YES' if r['bh'] else '.':>5}")

    print("\n" + "=" * 92)
    print("OPTION'S OWN FORWARD PREMIUM (%), cell level")
    print("mean far from median = a few large winners, not a central tendency")
    print("=" * 92)
    print(f"{'side':>4} {'class':>16} {'h':>4} {'n':>8} {'median %':>9} {'mean %':>8}")
    for (side, cls, h), vals in sorted(premium.items()):
        if len(vals) >= 100:
            print(f"{side:>4} {cls:>16} {h:>3}m {len(vals):>8,} "
                  f"{np.median(vals):>9.2f} {np.mean(vals):>8.2f}")

    if args.naive_contrast:
        print("\n" + "=" * 92)
        print("CONTRAST: same events, cell-level inference (WRONG — shown to make the point)")
        print("=" * 92)
        print(f"{'side':>4} {'class':>16} {'h':>4} | {'clustered t':>12} {'p':>8} "
              f"| {'naive t':>8} {'p':>9}")
        contrast = []
        for r in valid:
            ev = events[(r["side"], r["cls"], r["h"])]
            flat = [v for vals in ev.values() for v in vals]
            base_flat = [v for vals in baseline[r["h"]].values() for v in vals]
            if len(flat) < 30:
                continue
            tt, pp = stats.ttest_ind(flat, base_flat, equal_var=False)
            contrast.append((abs(tt), r, float(tt), float(pp)))
        contrast.sort(key=lambda x: -x[0])
        for _, r, tt, pp in contrast[:8]:
            sign = EXPECTED.get((r["side"], r["cls"]), 1)
            print(f"{r['side']:>4} {r['cls']:>16} {r['h']:>3}m | {r['t'] * sign:>12.2f} "
                  f"{r['p']:>8.3f} | {tt * sign:>8.2f} {pp:>9.4f}")
        print(f"\np<0.05 — clustered: {sum(1 for _, r, _, _ in contrast if r['p'] < 0.05)}"
              f"/{len(contrast)}   cell-level: "
              f"{sum(1 for _, _, _, pp in contrast if pp < 0.05)}/{len(contrast)}")

    surviving = [r for r in valid if r.get("bh")]
    print(f"\ntests run: {len(valid)}   surviving BH at q<{BH_Q}: {len(surviving)}")
    for r in surviving:
        sign = EXPECTED.get((r["side"], r["cls"]), 1)
        print(f"  {r['side'].upper()} {r['cls']} @{r['h']}m: {r['mean'] * sign:+.2f} bps "
              f"(t={r['t'] * sign:.2f}, {r['n_cl']} clusters)")
    if not surviving:
        print("  none — no class/horizon survives multiple-testing control.")
        print("  Treat the breach layer as an attention tool, not a signal.")


if __name__ == "__main__":
    main()
