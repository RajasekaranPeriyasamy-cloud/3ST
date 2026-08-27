"""Does a breached strike hold as a level? Read-only strike-level event study.

Companion to ``event_study_chain_buildup.py``, which asks whether a breach
predicts the underlying's *direction*. This asks the question closer to what the
desk is actually used for: when calls are written at strike K above spot, does K
act as resistance -- is it crossed LESS often than a comparable strike with no
such event? Puts written below spot, symmetrically, as support.

Run::

    python scripts/wall_test_chain_buildup.py

Result as of 2026-08-27 (42 session-files): **null, leaning against the
hypothesis.** Nothing survives Benjamini-Hochberg at q<0.10, and every call-side
point estimate is positive -- breached call strikes were crossed *more* often
than matched controls, not less. Recorded in docs/CONVERSATION_SUMMARY.md.

Outcome is binary and unambiguous: within the next h minutes, does spot reach K?

Two confounds, and only one is obvious
--------------------------------------
**Distance.** A strike 10 points away is crossed constantly; one 300 points away
almost never. Writing concentrates at particular distances, so an uncontrolled
comparison measures where writers stand, not whether their wall holds. Session
volatility does the same thing. Both are absorbed by expressing distance in
expected-move units::

    z = |K - spot_t| / (spot_t * sigma_5m * sqrt(h/5))

with sigma_5m the session's own realised 5-minute return vol. Events are compared
only against non-events in the SAME (side, horizon, z-bin).

**Momentum toward the strike — the one that bites.** Writers write the strike
spot is already approaching. Matching on distance controls for how far K is, not
for which way spot is moving, so the selection alone biases toward "walls break".
Measured here it accounts for about a third of the call-side effect: writing CE
at 30m goes from +9.42pp (t=1.47) to +6.00pp (t=0.99) once momentum is matched
too. Both schemes are printed side by side, because that contrast is the finding.

Inference clusters on (underlying, session): crossings of neighbouring strikes at
one moment are the same event seen twice. This has far more independent variation
than the directional study -- each strike has its own outcome -- but nothing like
the raw row count.

Power, stated up front: 100-241 events per test over 27-33 clusters at standard
errors of 3-6pp puts the minimum detectable effect near 9-17pp. A real 3-5pp
effect would be invisible. This is a failure to reject, not a demonstration of
absence.

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
#: the breach flag alone marks a level. Note the first is a SUBSET of the second
#: and the horizons are nested, so these are nowhere near independent tests --
#: consistent signs across rows are worth much less than they look.
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
    """obs[(scheme, defn, side, h, stratum)] -> {'ev': [(cluster, crossed)], 'ctl': [crossed]}"""
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
                                if not path or move <= 0:
                                    continue
                                z = abs(k - s0) / move
                                if z > Z_MAX:
                                    continue
                                crossed = (
                                    int(max(path) >= k) if side == "ce" else int(min(path) <= k)
                                )
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
                                            rec["ev"].append((cluster, crossed))
                                        else:
                                            rec["ctl"].append(crossed)
    return obs, n_obs


def stratified(obs: dict, scheme: str, defn: str, side: str, h: int):
    """Event crossing residuals against matched controls, grouped by cluster."""
    by_cluster: dict[tuple, list[float]] = defaultdict(list)
    ev_rate: list[int] = []
    ctl_rate: list[float] = []
    n_ev = n_strata = 0
    for key, rec in obs.items():
        if key[:4] != (scheme, defn, side, h):
            continue
        if len(rec["ctl"]) < MIN_CONTROLS or not rec["ev"]:
            continue
        base = float(np.mean(rec["ctl"]))
        n_strata += 1
        for cluster, crossed in rec["ev"]:
            by_cluster[cluster].append(crossed - base)
            ev_rate.append(crossed)
            ctl_rate.append(base)
            n_ev += 1
    diffs = [float(np.mean(v)) for v in by_cluster.values() if v]
    if len(diffs) < 3:
        return None
    arr = np.array(diffs)
    m, se = float(arr.mean()), float(arr.std(ddof=1) / np.sqrt(len(arr)))
    t = m / se if se > 0 else 0.0
    return {
        "n_ev": n_ev, "n_cl": len(arr), "strata": n_strata,
        "ev_rate": float(np.mean(ev_rate)), "ctl_rate": float(np.mean(ctl_rate)),
        "diff": m, "se": se, "t": t,
        "p": float(2 * (1 - stats.t.cdf(abs(t), df=len(arr) - 1))),
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
                    r = stratified(obs, scheme, defn, side, h)
                    if r:
                        results.append({"scheme": scheme, "defn": defn, "side": side, "h": h, **r})
    if not results:
        print("no usable strata.")
        return

    # BH over the primary scheme only; the second is a robustness check on the
    # same events, not twelve more independent questions.
    primary = [r for r in results if r["scheme"] == "distance"]
    for r, k in zip(primary, bh_keep([r["p"] for r in primary]), strict=True):
        r["bh"] = k

    print("=" * 104)
    print("DOES THE STRIKE HOLD?   crossing rate, events vs matched controls")
    print("NEGATIVE diff = the wall held (crossed LESS often than comparable strikes)")
    print("=" * 104)
    print(f"{'matching':>18} {'event':>11} {'side':>4} {'h':>4} {'events':>7} {'clus':>5} "
          f"{'ev%':>6} {'ctl%':>6} {'diff pp':>8} {'se':>6} {'t':>6} {'p':>7} {'BH':>4}")
    for scheme in SCHEMES:
        for r in [x for x in results if x["scheme"] == scheme]:
            flag = "YES" if r.get("bh") else ("." if scheme == "distance" else "")
            print(f"{scheme:>18} {r['defn']:>11} {r['side']:>4} {r['h']:>3}m {r['n_ev']:>7,} "
                  f"{r['n_cl']:>5} {r['ev_rate'] * 100:>6.1f} {r['ctl_rate'] * 100:>6.1f} "
                  f"{r['diff'] * 100:>8.2f} {r['se'] * 100:>6.2f} {r['t']:>6.2f} "
                  f"{r['p']:>7.3f} {flag:>4}")
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
                    print(f"  {defn:>11} {side} {h:>3}m: {a['diff'] * 100:+6.2f}pp "
                          f"-> {b['diff'] * 100:+6.2f}pp")

    surv = [r for r in primary if r.get("bh")]
    print(f"\ntests (primary scheme): {len(primary)}   surviving BH q<{BH_Q}: {len(surv)}")
    for r in surv:
        verdict = "HELD" if r["diff"] < 0 else "BROKE MORE OFTEN"
        print(f"  {r['defn']} {r['side'].upper()} @{r['h']}m: {r['diff'] * 100:+.2f}pp -> {verdict}")
    if not surv:
        print("  none — no support for 'a breached strike is a level that holds'.")
        print("  Call-side estimates lean the other way, inside noise. Treat the")
        print("  breach layer as an attention tool, not a level.")


if __name__ == "__main__":
    main()
