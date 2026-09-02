"""How much of the traded volume can the quote rule actually attribute?

Phase C classifies volume buyer- or seller-initiated from the bid/ask archived
each minute. That is an approximation at a known resolution, and this reports the
one number that says whether the resolution is good enough:

    unclassified volume / total traded volume

Run::

    python scripts/check_flow_classification.py
    python scripts/check_flow_classification.py --session 2026-08-31

How to read the answer
----------------------
**Single digits during liquid hours** — the minute snapshot is catching the book
often enough. Delta is usable, with the caveat that a minute trading both ways
is attributed to whichever side it closed on.

**Persistently high (say >25%) in liquid hours** — the minute resolution is the
binding constraint, not the classifier, and the fix is a tick collector
(Phase D), not more tuning here.

**100%** — no bid/ask in that session at all. Every session before 2026-08-30
reads this by construction: the collector only began archiving top-of-book then,
and it is forward-only.

Two breakdowns are printed because they distinguish causes that the headline
number cannot. If unclassified is concentrated at the open, it is the book moving
faster than one sample a minute. If it is concentrated on far strikes, it is
those strikes having no book at all — a different problem with a different fix.

Writes nothing.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analysis.chain_buildup import features, service  # noqa: E402
from analysis.delta_velocity import store as dv_store  # noqa: E402


def _pct(part: float, whole: float) -> float:
    return (part / whole * 100.0) if whole else 0.0


def analyse(underlying: str, session: date) -> dict | None:
    rows = service._session_rows(underlying, session)
    if not rows:
        return None

    by_hour: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    by_dist: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    total = unclassified = 0.0
    have_book = 0
    samples = 0

    strikes = sorted({float(r["strike"]) for r in rows if r.get("strike") is not None})
    _, atm = service._atm_from_rows(rows, strikes)

    for expiry in sorted({str(r["expiry"]) for r in rows if r.get("expiry")}):
        scoped = [r for r in rows if str(r.get("expiry")) == expiry]
        for r in scoped:
            samples += 1
            if r.get("bid") is not None and r.get("ask") is not None:
                have_book += 1
        flows = features.flow_by_bucket(scoped, timeframe_min=5, expiry=expiry)
        for (strike, _opt), buckets in flows.items():
            steps = abs(round((strike - atm) / 50.0)) if atm else 0
            band = "ATM±2" if steps <= 2 else "±3-5" if steps <= 5 else "±6+"
            for end, cell in buckets.items():
                traded = cell["buy"] + cell["sell"] + cell["unclassified"]
                if traded <= 0:
                    continue
                total += traded
                unclassified += cell["unclassified"]
                hour = f"{end.hour:02d}:00"
                by_hour[hour][0] += traded
                by_hour[hour][1] += cell["unclassified"]
                by_dist[band][0] += traded
                by_dist[band][1] += cell["unclassified"]

    return {
        "total": total,
        "unclassified": unclassified,
        "samples": samples,
        "have_book": have_book,
        "by_hour": by_hour,
        "by_dist": by_dist,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--underlyings", default="NIFTY,BANKNIFTY,SENSEX")
    ap.add_argument("--session", default=None, help="YYYY-MM-DD (default: latest archived)")
    args = ap.parse_args()
    underlyings = tuple(u.strip().upper() for u in args.underlyings.split(",") if u.strip())

    for u in underlyings:
        try:
            session = (
                date.fromisoformat(args.session)
                if args.session
                else dv_store.latest_session(u)
            )
        except ValueError:
            print(f"{u}: bad --session {args.session!r}")
            continue
        if session is None:
            print(f"{u}: nothing archived")
            continue

        out = analyse(u, session)
        print(f"\n{'=' * 72}\n{u}  {session}\n{'=' * 72}")
        if out is None or out["total"] <= 0:
            print("  no traded volume archived for this session")
            continue

        book_pct = _pct(out["have_book"], out["samples"])
        pct = _pct(out["unclassified"], out["total"])
        print(f"  leg-samples with a book : {out['have_book']:,}/{out['samples']:,} ({book_pct:.1f}%)")
        print(f"  traded volume           : {out['total']:,.0f}")
        print(f"  UNCLASSIFIED            : {out['unclassified']:,.0f}  ({pct:.1f}%)")
        if book_pct == 0:
            print("  -> no top-of-book archived for this session. Sessions before")
            print("     2026-08-30 have none by construction; recording is forward-only.")
            continue

        print("\n  by hour" + " " * 12 + "traded        unclassified")
        for hour in sorted(out["by_hour"]):
            traded, unc = out["by_hour"][hour]
            bar = "#" * int(round(_pct(unc, traded) / 4))
            print(f"    {hour}   {traded:>16,.0f}   {_pct(unc, traded):>5.1f}%  {bar}")

        print("\n  by distance from ATM")
        for band in ("ATM±2", "±3-5", "±6+"):
            if band not in out["by_dist"]:
                continue
            traded, unc = out["by_dist"][band]
            print(f"    {band:>6}  {traded:>16,.0f}   {_pct(unc, traded):>5.1f}%")

        print()
        if pct < 10:
            print("  VERDICT: minute snapshots are catching the book. Delta is usable.")
        elif pct < 25:
            print("  VERDICT: usable, but read delta as indicative on the worst hours above.")
        else:
            print("  VERDICT: minute resolution is the binding constraint, not the")
            print("  classifier. More tuning here will not help — Phase D (tick collector) will.")


if __name__ == "__main__":
    main()
