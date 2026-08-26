"""One-shot seed of `data/volume_tilt_history.json` from Kite minute history.

    python scripts/backfill_volume_tilt.py --underlying SENSEX --start 2026-08-03

**This is not a repeatable tool, and that is not a flaw in the script.**

The volume profile is built from *front-month futures* bars, because cash indices
carry no volume. Kite delists a contract within about a day of its expiry — a
`fetch_historical_by_token` against `NIFTY26AUGFUT` returned ``invalid token`` on
2026-08-26, one day after it expired, even though the locally cached instrument
dump still listed it. So the only sessions reachable are those where the
*currently listed* contract was already the front month.

Two consequences:

* The reachable window **shrinks with every expiry**. Run this and the same
  command next month returns fewer sessions, silently. That is why the durable
  answer is the live sampler in
  :func:`analysis.volume_profile.tilt_history.maybe_sample_tilt_history_periodic`,
  and why this script exists only to seed what history happens to still be alive.
* Substituting a contract that was the *far* month then does not rescue it.
  Measured 2026-08-26: `NIFTY26SEPFUT` traded 312k contracts on 2026-08-05 while
  the front month carried multiples of it, rising to 5.5M on roll day. A profile
  fitted to that is the thin-session case, and its tilt would describe the roll,
  not the session.

The script therefore refuses to guess. It resolves the contract *you* name (or
the front month if you do not), fetches whole sessions, and skips any day whose
bar count or volume says the contract was not carrying the market yet.

Every point is written with ``source="backfill"`` so the desk can say so.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.volume_profile.service import compute_volume_profile  # noqa: E402
from analysis.volume_profile.tilt_history import (  # noqa: E402
    CHECKPOINT_MIN,
    MIN_CHECKPOINT,
    upsert_point,
)
from instruments import resolve_future  # noqa: E402
from kite_client import fetch_historical_by_token  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

#: A session carrying under this share of the **last** session's volume was not
#: the front month yet.
#:
#: Compared against the last session, never the window's median. The median is
#: self-defeating here: if most of the window is far-month, the median *is* the
#: far-month level and nothing looks anomalous. Measured on NIFTY 2026-08-26,
#: a median guard passed 2026-08-03 as "72% of median" when it was 10% of real
#: front-month volume. The last session is safe because ``resolve_future``
#: returns the first expiry >= today, so the resolved contract is front month
#: today by definition.
MIN_VOLUME_SHARE = 0.25

#: Below this a session is a half-day or a muhurat session — the profile would
#: build, but its tilt is not comparable to a full session's.
MIN_SESSION_BARS = 300


def _index_token(underlying: str) -> int | None:
    """Cash index token for basis alignment; None means skip the correction.

    Resolved the same way ``kite_client.fetch_index_minute_spot`` does — via the
    underlying's ``index_token_key`` — so backfilled sessions land on the same
    price axis the live desk uses. MCX underlyings have options written on the
    future and need no correction at all.
    """
    try:
        from config import INDEX_OPTIONS
        from kite_client import resolve_instrument

        meta = INDEX_OPTIONS.get(underlying.upper()) or {}
        key = meta.get("index_token_key")
        if not key:
            return None  # options on the future — exact by construction
        return int(resolve_instrument(key)["instrument_token"])
    except Exception:
        return None


def _front_month_from(underlying: str, expiry: str) -> dt.date | None:
    """First session the resolved contract was the front month, if derivable.

    A monthly contract becomes front month the session after the previous one
    expires. The authoritative source is the previous expiry in the instrument
    dump — but Kite drops expired contracts, so this is only derivable while the
    predecessor is still listed. Returns ``None`` when it is not, and the volume
    guard carries the load instead.

    This is the guard that actually works: it is a calendar fact, not an
    inference from the same volume the far-month sessions distort.
    """
    try:
        from instruments import _future_candidates

        cand = _future_candidates(underlying)
        target = dt.date.fromisoformat(str(expiry)[:10])
        prior = [d.date() for d in cand["_exp"].tolist() if d.date() < target]
        return max(prior) + dt.timedelta(days=1) if prior else None
    except Exception:
        return None


def _day_bars(token: int, day: dt.date) -> list[dict]:
    """One session's minute bars, strictly inside ``day``."""
    df = fetch_historical_by_token(token, "1min", day, day)
    df = df[df.index.date == day]
    return [
        {
            "date": ts.isoformat(),
            "open": float(r.open),
            "high": float(r.high),
            "low": float(r.low),
            "close": float(r.close),
            "volume": float(r.volume),
        }
        for ts, r in df.iterrows()
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--underlying", required=True)
    ap.add_argument("--start", required=True, help="first session date, YYYY-MM-DD")
    ap.add_argument("--end", default=dt.date.today().isoformat())
    ap.add_argument("--expiry", default=None, help="pin a contract; default front month")
    ap.add_argument(
        "--front-from",
        default=None,
        help="first session this contract was the front month; overrides the "
        "derived value when the predecessor has already been delisted",
    )
    ap.add_argument("--purge", action="store_true", help="drop stored sessions for this underlying first")
    ap.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = ap.parse_args()

    u = args.underlying.strip().upper()
    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end)

    if args.purge and not args.dry_run:
        from analysis.volume_profile.tilt_history import purge_underlying

        dropped = purge_underlying(u)
        print(f"{u}: purged {dropped} stored sessions")

    fut = resolve_future(u, expiry=args.expiry)
    token = int(fut["instrument_token"])
    print(f"{u}: contract {fut['tradingsymbol']} (expiry {fut['expiry']}), token {token}")

    idx_token = _index_token(u)
    print(f"  basis alignment: {'index token ' + str(idx_token) if idx_token else 'none'}")

    # Pull every candidate session first, so the volume guard can judge each day
    # against the last session's volume rather than a hardcoded threshold.
    sessions: dict[dt.date, list[dict]] = {}
    day = start
    while day <= end:
        if day.weekday() < 5:
            try:
                bars = _day_bars(token, day)
                if bars:
                    sessions[day] = bars
            except Exception as exc:  # noqa: BLE001
                print(f"  {day}: fetch failed — {str(exc)[:90]}")
        day += dt.timedelta(days=1)

    if not sessions:
        print("  no sessions returned — the contract is probably already delisted.")
        return 1

    ordered = sorted(sessions)
    ref_vol = sum(b["volume"] for b in sessions[ordered[-1]])
    front_from = args.front_from or _front_month_from(u, str(fut["expiry"]))
    if isinstance(front_from, str):
        front_from = dt.date.fromisoformat(front_from)
    print(
        f"  {len(sessions)} sessions fetched · reference volume {ref_vol:,.0f}"
        f" (last session) · front month from "
        f"{front_from.isoformat() if front_from else 'UNKNOWN — volume guard only'}"
    )

    written = skipped = 0
    for day in ordered:
        bars = sessions[day]
        vol = sum(b["volume"] for b in bars)
        if len(bars) < MIN_SESSION_BARS:
            print(f"  {day}: SKIP — {len(bars)} bars (half-day or muhurat)")
            skipped += 1
            continue
        if front_from and day < front_from:
            print(f"  {day}: SKIP — before {front_from}, this contract was the far month")
            skipped += 1
            continue
        if ref_vol > 0 and vol / ref_vol < MIN_VOLUME_SHARE:
            print(f"  {day}: SKIP — volume {vol:,.0f} is {vol / ref_vol:.0%} of front-month level")
            skipped += 1
            continue

        index_bars = None
        if idx_token:
            try:
                index_bars = _day_bars(idx_token, day)
            except Exception:
                index_bars = None

        points = 0
        final_tilt = None
        # Cumulative: each checkpoint is the session *so far*, which is exactly
        # what the live desk shows at that minute.
        for cp in range(MIN_CHECKPOINT, len(bars) + 1, CHECKPOINT_MIN):
            payload, _ = compute_volume_profile(
                u,
                bars=bars[:cp],
                index_bars=index_bars[:cp] if index_bars else None,
                mintick=float(fut.get("tick_size") or 0.05),
            )
            if not payload.get("available"):
                continue
            final_tilt = payload.get("tilt_pp")
            if not args.dry_run:
                upsert_point(
                    u,
                    day.isoformat(),
                    minute=cp,
                    tilt_pp=final_tilt,
                    bars=cp,
                    contract=str(fut["tradingsymbol"]),
                    expiry=str(fut["expiry"]),
                    source="backfill",
                    overlap_pct=payload.get("overlap_pct"),
                )
            points += 1
        print(
            f"  {day}: {len(bars)} bars · vol {vol:>10,.0f} · {points:>2} checkpoints"
            f" · close tilt {final_tilt if final_tilt is None else f'{final_tilt:+.2f}pp'}"
        )
        written += 1

    verb = "would write" if args.dry_run else "wrote"
    print(f"\n{verb} {written} sessions, skipped {skipped}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
