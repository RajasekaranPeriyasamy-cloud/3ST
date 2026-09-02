"""Does a breached strike hold as a level? Read-only strike-level event study.

Companion to ``event_study_chain_buildup.py``, which asks whether a breach
predicts the underlying's *direction*. This asks the question closer to what the
desk is actually used for: when calls are written at strike K above spot, does K
act as resistance -- is spot repelled by it, relative to a comparable strike with
no such event? Puts written below spot, symmetrically, as support.

Run::

    python scripts/wall_test_chain_buildup.py

Result as of 2026-08-27 (42 session-files): **null, leaning against the
hypothesis.** Nothing survives Benjamini-Hochberg at q<0.10 under either outcome
measure, and every call-side point estimate says breached call strikes were
approached *harder* than matched controls, not less. Recorded in
docs/CONVERSATION_SUMMARY.md.

Two outcome measures, deliberately
----------------------------------
**Binary** -- within h minutes, does spot reach K.

**Continuous** -- the percentile rank of ``approach = excursion / distance``
among matched controls in the same stratum. Under the null that rank is uniform
on [0,1] with mean 0.5; below 0.5 means spot moved less far toward K than
comparable strikes did. Rank rather than a raw mean because excursion
distributions are strongly right-skewed, so a mean difference is dominated by a
handful of large moves whose size says nothing about whether a level held.

The continuous version was added on the theory that binary throws away
information and should therefore buy power. **It does not** -- median |t| ratio
0.99 on the same events. The ``--power`` section below explains why, and that
explanation is the most useful output of this script.

Two confounds, and only one is obvious
--------------------------------------
**Distance.** A strike 10 points away is crossed constantly; one 300 points away
almost never. Writing concentrates at particular distances, so an uncontrolled
comparison measures where writers stand, not whether their wall holds. Session
volatility does the same thing across days. Both are absorbed by expressing
distance in expected-move units::

    z = |K - spot_t| / (spot_t * sigma_5m * sqrt(h/5))

with sigma_5m the session's own realised 5-minute return vol. Events are compared
only against non-events in the SAME (side, horizon, z-bin).

**Momentum toward the strike -- the one that bites.** Writers write the strike
spot is already approaching. Matching on distance controls for how far K is, not
for which way spot is moving, so selection alone biases toward "walls break".
Measured here it accounts for about a third of the call-side effect: writing CE
at 30m goes from +9.42pp (t=1.47) to +6.00pp (t=0.99) once momentum is matched
too. Both schemes print side by side, because that contrast is the finding.

Inference clusters on (underlying, session): crossings of neighbouring strikes at
one moment are the same event seen twice.

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
HORIZONS = (15, 30, 60)
Z_BIN = 0.25
Z_MAX = 4.0          # beyond ~4 expected moves nothing crosses; no variation to test
MIN_CONTROLS = 30    # per stratum before it is usable
BH_Q = 0.10

#: "writing" is the hypothesis of interest; "any_breach" the weaker claim that
#: the breach flag alone marks a level. The first is a SUBSET of the second and
#: the horizons are nested, so these are nowhere near independent tests --
#: consistent signs across rows are worth far less than they look.
EVENT_DEFS = {
    "writing": lambda c: bool(c["breach"]) and c["cls"] == features.SHORT_BUILDUP,
    "any_breach": lambda c: bool(c["breach"]),
}

SCHEMES = ("distance", "distance+momentum")


def session_sigma(spots: list[float | None]) -> float | None:
    rets = [
        spots[i + 1] / spots[i] - 1
        for i in range(len(spots) - 1)
        if spots[i] and spots[i + 1]
    ]
    if len(rets) < 20:
        return None
    s = float(np.std(rets, ddof=1))
    return s if s > 0 else None


def collect(underlyings: tuple[str, ...]):
    """obs[(scheme, defn, side, h, stratum)] ->
    {'ev': [(cluster, crossed, approach)], 'ctl': [(crossed, approach)]}"""
    obs: dict[tuple, dict[str, list]] = defaultdict(lambda: {"ev": [], "ctl": []})
    n_obs = 0

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
                sigma = session_sigma(spots)
                if sigma is None:
                    continue
                nb = len(spots)

                for row in grid["rows"]:
                    k = row["strike"]
                    for side in ("ce", "pe"):
                        for i, c in enumerate(row[side]["cells"]):
                            s0 = spots[i] if i < nb else None
                            if s0 is None or c["d_oi"] is None:
                                continue
                            # A wall only means something out of the money.
                            if (side == "ce" and k <= s0) or (side == "pe" and k >= s0):
                                continue
                            for h in HORIZONS:
                                steps = h // TF
                                if i + steps >= nb:
                                    continue
                                path = [s for s in spots[i + 1 : i + 1 + steps] if s]
                                move = s0 * sigma * float(np.sqrt(h / TF))
                                gap = abs(k - s0)
                                if not path or move <= 0 or gap <= 0:
                                    continue
                                z = gap / move
                                if z > Z_MAX:
                                    continue
                                excursion = (
                                    max(path) - s0 if side == "ce" else s0 - min(path)
                                )
                                approach = excursion / gap
                                crossed = int(approach >= 1.0)
                                zbin = round(z / Z_BIN) * Z_BIN

                                back = spots[i - 3] if i >= 3 else None
                                if back:
                                    drift = (s0 - back) / move
                                    if side == "pe":
                                        drift = -drift
                                    mbin = max(-1.0, min(1.0, round(drift * 2) / 2))
                                else:
                                    mbin = 0.0

                                n_obs += 1
                                for defn, fn in EVENT_DEFS.items():
                                    is_ev = fn(c)
                                    for scheme, stratum in (
                                        ("distance", (zbin,)),
                                        ("distance+momentum", (zbin, mbin)),
                                    ):
                                        rec = obs[(scheme, defn, side, h, stratum)]
                                        if is_ev:
                                            rec["ev"].append((cluster, crossed, approach))
                                        else:
                                            rec["ctl"].append((crossed, approach))
    return obs, n_obs


def _cluster_test(by_cluster: dict, null: float = 0.0):
    vals = [float(np.mean(v)) for v in by_cluster.values() if v]
    if len(vals) < 3:
        return None
    arr = np.array(vals)
    m = float(arr.mean())
    se = float(arr.std(ddof=1) / np.sqrt(len(arr)))
    t = (m - null) / se if se > 0 else 0.0
    return {"n_cl": len(arr), "mean": m, "se": se, "t": t,
            "p": float(2 * (1 - stats.t.cdf(abs(t), df=len(arr) - 1)))}


def analyse(obs: dict, scheme: str, defn: str, side: str, h: int):
    """Binary and rank outcomes on the same matched events."""
    binary: dict[tuple, list[float]] = defaultdict(list)
    ranks: dict[tuple, list[float]] = defaultdict(list)
    raw_ranks: dict[tuple, list[float]] = defaultdict(list)
    ev_rate: list[int] = []
    ctl_rate: list[float] = []
    n_ev = n_strata = 0

    for key, rec in obs.items():
        if key[:4] != (scheme, defn, side, h):
            continue
        if len(rec["ctl"]) < MIN_CONTROLS or not rec["ev"]:
            continue
        ctl_cross = float(np.mean([x for x, _ in rec["ctl"]]))
        ctl_app = np.array([a for _, a in rec["ctl"]])
        n = len(ctl_app)
        n_strata += 1
        for cluster, crossed, approach in rec["ev"]:
            binary[cluster].append(crossed - ctl_cross)
            r = (float(np.sum(ctl_app < approach)) + 0.5 * float(np.sum(ctl_app == approach))) / n
            ranks[cluster].append(r)
            raw_ranks[cluster].append(r)
            ev_rate.append(crossed)
            ctl_rate.append(ctl_cross)
            n_ev += 1

    b = _cluster_test(binary, null=0.0)
    r = _cluster_test(ranks, null=0.5)
    if not b or not r:
        return None
    return {"n_ev": n_ev, "n_cl": b["n_cl"], "strata": n_strata,
            "ev_rate": float(np.mean(ev_rate)), "ctl_rate": float(np.mean(ctl_rate)),
            "binary": b, "rank": r, "groups": [v for v in raw_ranks.values() if v]}


def variance_decomposition(groups: list[list[float]]) -> dict | None:
    """Split the standard error into between-session and within-session parts.

    This is why the continuous outcome bought nothing. Precision here is governed
    by the number of CLUSTERS and by how few events each holds — not by how
    precisely any single event is measured. A sharper per-observation statistic
    moves neither term.
    """
    groups = [g for g in groups if g]
    k = len(groups)
    if k < 3:
        return None
    m_bar = float(np.mean([len(g) for g in groups]))
    within = float(np.mean([np.var(g, ddof=1) for g in groups if len(g) > 1] or [0.0]))
    means = np.array([np.mean(g) for g in groups])
    var_of_means = float(np.var(means, ddof=1))
    between = max(0.0, var_of_means - within / m_bar)
    icc = between / (between + within) if (between + within) > 0 else 0.0
    return {
        "k": k, "m_bar": m_bar, "within": within, "between": between, "icc": icc,
        "se_now": float(np.sqrt(var_of_means / k)),
        "se_4x_events": float(np.sqrt((between + within / (4 * m_bar)) / k)),
        "se_4x_sessions": float(np.sqrt(var_of_means / (4 * k))),
    }


def bh_keep(pvals: list[float], q: float = BH_Q) -> list[bool]:
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
    ap.add_argument("--no-power", action="store_true", help="skip the variance decomposition")
    args = ap.parse_args()
    underlyings = tuple(u.strip().upper() for u in args.underlyings.split(",") if u.strip())

    print(f"collecting {', '.join(underlyings)} ...")
    obs, n_obs = collect(underlyings)
    if not n_obs:
        print("no archived strike-observations — nothing to test.")
        return
    print(f"strike-observations: {n_obs:,}\n")

    results = []
    for scheme in SCHEMES:
        for defn in EVENT_DEFS:
            for side in ("ce", "pe"):
                for h in HORIZONS:
                    a = analyse(obs, scheme, defn, side, h)
                    if a:
                        results.append({"scheme": scheme, "defn": defn, "side": side,
                                        "h": h, **a})
    if not results:
        print("no usable strata.")
        return

    # BH over the primary scheme only; the second is a robustness check on the
    # same events, not twelve more independent questions.
    primary = [r for r in results if r["scheme"] == "distance"]
    for r, k in zip(primary, bh_keep([r["rank"]["p"] for r in primary]), strict=True):
        r["bh"] = k

    print("=" * 108)
    print("DOES THE STRIKE HOLD?   events vs matched controls")
    print("binary: NEGATIVE diff = crossed less often.  rank: BELOW 0.500 = approached less far.")
    print("Either way, below the null = the wall held.")
    print("=" * 108)
    print(f"{'matching':>18} {'event':>11} {'side':>4} {'h':>4} {'events':>7} {'clus':>5} "
          f"{'diff pp':>8} {'t':>6} | {'mean rank':>10} {'t':>6} {'p':>7} {'BH':>4}")
    for scheme in SCHEMES:
        for r in [x for x in results if x["scheme"] == scheme]:
            flag = "YES" if r.get("bh") else ("." if scheme == "distance" else "")
            print(f"{scheme:>18} {r['defn']:>11} {r['side']:>4} {r['h']:>3}m {r['n_ev']:>7,} "
                  f"{r['n_cl']:>5} {r['binary']['mean'] * 100:>8.2f} {r['binary']['t']:>6.2f} | "
                  f"{r['rank']['mean']:>10.4f} {r['rank']['t']:>6.2f} {r['rank']['p']:>7.3f} "
                  f"{flag:>4}")
        print()

    print("HOW MUCH OF THE EFFECT WAS SELECTION (writers write into approaching spot)")
    for defn in EVENT_DEFS:
        for side in ("ce", "pe"):
            for h in HORIZONS:
                a = next((r for r in results if (r["scheme"], r["defn"], r["side"], r["h"])
                          == ("distance", defn, side, h)), None)
                b = next((r for r in results if (r["scheme"], r["defn"], r["side"], r["h"])
                          == ("distance+momentum", defn, side, h)), None)
                if a and b:
                    print(f"  {defn:>11} {side} {h:>3}m: {a['binary']['mean'] * 100:+6.2f}pp "
                          f"-> {b['binary']['mean'] * 100:+6.2f}pp")

    if not args.no_power:
        print("\n" + "=" * 108)
        print("WHY THE CONTINUOUS OUTCOME BOUGHT NOTHING — where the standard error lives")
        print("=" * 108)
        print(f"{'event':>11} {'side':>4} {'h':>4} {'clus':>5} {'ev/clus':>8} {'ICC':>6} "
              f"{'binary |t|':>11} {'rank |t|':>9} | {'SE now':>7} {'4x events':>10} "
              f"{'4x sessions':>12}")
        ratios = []
        for r in [x for x in results if x["scheme"] == "distance+momentum"]:
            d = variance_decomposition(r["groups"])
            if not d:
                continue
            bt, rt = abs(r["binary"]["t"]), abs(r["rank"]["t"])
            if bt > 0:
                ratios.append(rt / bt)
            print(f"{r['defn']:>11} {r['side']:>4} {r['h']:>3}m {d['k']:>5} {d['m_bar']:>8.1f} "
                  f"{d['icc']:>6.2f} {bt:>11.2f} {rt:>9.2f} | {d['se_now']:>7.4f} "
                  f"{d['se_4x_events']:>10.4f} {d['se_4x_sessions']:>12.4f}")
        if ratios:
            print(f"\nmedian rank/binary |t| ratio: {np.median(ratios):.2f} — "
                  f"{'continuous is sharper' if np.median(ratios) > 1.1 else 'no gain'}")
        print("Read the last two columns: quadrupling events per session barely moves the")
        print("standard error, quadrupling SESSIONS halves it. Precision is bound by the")
        print("number of clusters and by how few events each holds — a sharper")
        print("per-observation statistic moves neither. More sessions is the only lever,")
        print("and splitting events into subsets (e.g. by dOI/volume) costs power at this")
        print("sample rather than buying it.")

    surv = [r for r in primary if r.get("bh")]
    print(f"\ntests (primary scheme): {len(primary)}   surviving BH q<{BH_Q}: {len(surv)}")
    for r in surv:
        verdict = "WALL HELD" if r["rank"]["mean"] < 0.5 else "APPROACHED HARDER"
        print(f"  {r['defn']} {r['side'].upper()} @{r['h']}m: rank {r['rank']['mean']:.4f} "
              f"-> {verdict}")
    if not surv:
        print("  none — no support for 'a breached strike is a level that holds',")
        print("  under either outcome measure. Call-side estimates lean the other way,")
        print("  inside noise. Treat the breach layer as an attention tool, not a level.")


if __name__ == "__main__":
    main()
