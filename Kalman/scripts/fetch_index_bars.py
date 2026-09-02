"""Pull 5-minute history for the NSE/BSE index universe and cache it.

    python scripts/fetch_index_bars.py --tier all --start 2015-01-09

Every coarser timeframe (15m/30m/60m/2h/4h) is derived from this one series --
see kpairs/bars.py for why. Kite caps a 5-minute request at 100 days, so an
11-year pull is ~41 chunked calls per index at 3 req/s.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kpairs import bars, indices  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", default="all", help="tier1 | tier2 | all")
    ap.add_argument("--start", default="2015-01-09")
    ap.add_argument("--end", default=None)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    members = indices.get_universe(args.tier)
    tokens = indices.resolve_tokens(members)
    print(f"[fetch] tier={args.tier}: {len(tokens)} indices resolved", flush=True)

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end) if args.end else date.today()

    # Cache name carries the tier: a tier1 pull must not satisfy a later
    # 'all' request from cache and silently return 6 of 28 indices.
    long_df = bars.fetch_5m(tokens, start, end, refresh=args.refresh,
                            cache_name=f"idx5m-{args.tier}")
    px5 = bars.to_wide_5m(long_df)
    print(f"\n[fetch] 5m close matrix: {px5.shape[0]:,} bars x {px5.shape[1]} indices")
    print(f"[fetch] {px5.index[0]} -> {px5.index[-1]}")
    print(f"\n[fetch] coverage per index (non-null 5m bars):")
    for lbl, cnt in px5.notna().sum().sort_values(ascending=False).items():
        print(f"        {lbl:<14} {cnt:>8,}")
    print("\n[fetch] timeframe grid")
    print(bars.describe_timeframes(px5).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
