"""Intraday bar fetch and session-anchored resampling.

One fetch, six timeframes
-------------------------
Everything is pulled once at **5 minutes** and every coarser timeframe is
derived from it by grouping whole 5-minute bars. This is not a shortcut, it is
the only way to make the comparison fair: if 5m came from Kite's ``5minute``
endpoint and 15m from its ``15minute`` endpoint, any difference in how the two
endpoints handle the 15:15-15:30 stub, a halted session, or a special muhurat
session would show up in the results as a "timeframe effect". Deriving them all
from one series guarantees that the only thing changing across the sweep is the
sampling interval.

The NSE cash session is 09:15-15:30 = 375 minutes = 75 five-minute bars.

    timeframe   5m bars   bars/session   session coverage
    5m           1        75             exact
    15m          3        25             exact
    30m          6        12 + stub      12 x 30m + 1 x 15m tail
    60m         12         6 + stub       6 x 60m + 1 x 15m tail
    2h          24         3 + stub       3 x 2h  + 1 x 15m tail
    4h          48         1 + stub       1 x 4h  + 1 x 2h15m tail

Note the stubs. 375 does not divide by 30, 60, 120 or 240, so every timeframe
above 15m ends the session with a short bar. Two ways to handle it and both are
defensible: drop the stub (loses the close, and the close is where the
information is) or keep it (an uneven bar). This module **keeps** it and marks
it, because for a mean-reversion strategy the session close is the single most
informative bar of the day. ``drop_stubs=True`` is available if you want to see
how much the choice matters -- it is worth checking, and it is the kind of
decision most backtests make silently.
"""

from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

CACHE_DIR = Path(__file__).resolve().parents[1] / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Kite historical API per-request day caps, by interval.
KITE_CHUNK_DAYS = {"minute": 60, "5minute": 100, "15minute": 200,
                   "30minute": 200, "60minute": 400, "day": 2000}
_REQUEST_SLEEP = 0.55          # 3 req/s documented; sustained pulls trip a burst throttle
EARLIEST_INTRADAY = date(2015, 1, 9)   # measured: first NSE index intraday candle

SESSION_START = "09:15"
SESSION_END = "15:30"
SESSION_MINUTES = 375

# timeframe label -> number of 5-minute bars per aggregated bar
TIMEFRAMES: dict[str, int] = {
    "5m": 1,
    "15m": 3,
    "30m": 6,
    "60m": 12,
    "2h": 24,
    "4h": 48,
}


# --------------------------------------------------------------------------
# fetch
# --------------------------------------------------------------------------
def _client():
    from kite_client import _kite_direct_client

    return _kite_direct_client()


def _hist_with_retry(kite, token: int, start, end, interval: str,
                     *, tries: int = 6) -> list:
    """Kite historical call with exponential backoff on throttling.

    Kite's documented historical limit is 3 req/s, but sustained pulls trip a
    burst throttle well inside that and return ``NetworkException: Too many
    requests``. Swallowing that (as a bare try/except around the call does)
    silently drops a 100-day chunk out of the middle of the series and leaves a
    hole no downstream code can see. Retry, then raise.
    """
    delay = 1.0
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return kite.historical_data(token, start, end, interval)
        except Exception as exc:  # noqa: BLE001
            last = exc
            msg = str(exc).lower()
            throttled = "too many" in msg or "429" in msg or "timed out" in msg
            if not throttled and attempt >= 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2.0, 30.0)
    raise RuntimeError(f"historical_data failed after {tries} tries: {last}")


def fetch_5m(
    tokens: dict[str, dict],
    start: date = EARLIEST_INTRADAY,
    end: date | None = None,
    *,
    refresh: bool = False,
    cache_name: str = "idx5m",
) -> pd.DataFrame:
    """Long-format 5-minute bars for every index in ``tokens``.

    Cached **per index** under ``cache/parts/`` as well as combined, so an
    interrupted pull resumes instead of re-fetching the indices it already has.
    An 11-year, 28-index pull is ~1,150 API calls and the better part of an
    hour; losing all of it to one throttle at index 9 is not acceptable.
    """
    end = end or date.today()
    path = CACHE_DIR / f"{cache_name}_{start:%Y%m%d}_{end:%Y%m%d}.parquet"
    if path.exists() and not refresh:
        print(f"[bars] cache hit {path.name}")
        return pd.read_parquet(path)

    parts_dir = CACHE_DIR / "parts"
    parts_dir.mkdir(exist_ok=True)

    kite = _client()
    chunk = KITE_CHUNK_DAYS["5minute"]
    parts: list[pd.DataFrame] = []
    n = len(tokens)

    for i, (label, meta) in enumerate(sorted(tokens.items()), start=1):
        part_path = parts_dir / f"{cache_name}_{label}_{start:%Y%m%d}_{end:%Y%m%d}.parquet"
        if part_path.exists() and not refresh:
            d = pd.read_parquet(part_path)
            parts.append(d)
            print(f"[bars] {i:>2}/{n} {label:<12} {len(d):>7,} bars  (cached)", flush=True)
            continue

        rows: list[pd.DataFrame] = []
        cursor = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.min.time())
        failed = False
        while cursor < end_dt:
            chunk_end = min(cursor + timedelta(days=chunk - 1), end_dt)
            try:
                raw = _hist_with_retry(kite, meta["token"], cursor, chunk_end, "5minute")
            except Exception as exc:  # noqa: BLE001
                print(f"[bars] {label} {cursor:%Y-%m} GAVE UP {type(exc).__name__}: "
                      f"{str(exc)[:70]}", flush=True)
                failed = True
                break
            if raw:
                d = pd.DataFrame(raw)
                d["date"] = pd.to_datetime(d["date"]).dt.tz_localize(None)
                rows.append(d[["date", "open", "high", "low", "close", "volume"]])
            cursor = chunk_end + timedelta(days=1)
            time.sleep(_REQUEST_SLEEP)

        if not rows:
            print(f"[bars] {i:>2}/{n} {label:<12} NO DATA", flush=True)
            continue
        d = pd.concat(rows).drop_duplicates("date").sort_values("date")
        d["label"] = label
        if not failed:
            d.to_parquet(part_path, index=False)   # only cache a complete series
        parts.append(d)
        print(f"[bars] {i:>2}/{n} {label:<12} {len(d):>7,} bars  "
              f"{d['date'].iloc[0]:%Y-%m-%d} -> {d['date'].iloc[-1]:%Y-%m-%d}"
              f"{'  [PARTIAL]' if failed else ''}", flush=True)

    if not parts:
        raise RuntimeError("no bars fetched")
    out = pd.concat(parts, ignore_index=True)
    out.to_parquet(path, index=False)
    print(f"[bars] wrote {path.name}  ({len(out):,} rows)")
    return out


# --------------------------------------------------------------------------
# reshape / resample
# --------------------------------------------------------------------------
def to_wide_5m(long_df: pd.DataFrame, field: str = "close") -> pd.DataFrame:
    """Long -> wide: rows are 5-minute timestamps, columns are index labels."""
    w = long_df.pivot_table(index="date", columns="label", values=field, aggfunc="last")
    w.index = pd.DatetimeIndex(w.index)
    return w.sort_index()


def session_key(index: pd.DatetimeIndex) -> np.ndarray:
    """Integer session id per bar -- 0 for the first trading day, and so on."""
    days = index.normalize()
    uniq = pd.DatetimeIndex(days.unique()).sort_values()
    lookup = {d: i for i, d in enumerate(uniq)}
    return np.array([lookup[d] for d in days], dtype=np.int64)


def resample(px5: pd.DataFrame, timeframe: str, *, drop_stubs: bool = False) -> pd.DataFrame:
    """Aggregate a wide 5-minute close matrix to ``timeframe``.

    Grouping is **anchored to each session's first bar**, not to the wall clock.
    ``pandas.resample('2h')`` would anchor to midnight and produce a first bar
    running 08:00-10:00 that mixes the pre-open gap with the first 45 minutes of
    trade -- a bar that never existed and that straddles the overnight jump.
    """
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unknown timeframe {timeframe!r}; use {list(TIMEFRAMES)}")
    k = TIMEFRAMES[timeframe]
    if k == 1:
        out = px5.copy()
        out.attrs["timeframe"] = timeframe
        out.attrs["stub"] = False
        return out

    idx = pd.DatetimeIndex(px5.index)
    sess = session_key(idx)
    # position of each bar within its own session
    pos = np.zeros(len(idx), dtype=np.int64)
    _, starts = np.unique(sess, return_index=True)
    for s_i in range(len(starts)):
        lo = starts[s_i]
        hi = starts[s_i + 1] if s_i + 1 < len(starts) else len(idx)
        pos[lo:hi] = np.arange(hi - lo)

    grp = pos // k
    frame = pd.DataFrame({"sess": sess, "grp": grp}, index=idx)

    # close of each group; label the bar with the group's LAST timestamp, which
    # is the moment the information is actually available.
    agg = px5.groupby([frame["sess"], frame["grp"]]).last()
    stamps = pd.Series(idx, index=idx).groupby([frame["sess"], frame["grp"]]).last()
    sizes = px5.groupby([frame["sess"], frame["grp"]]).size()

    out = agg.set_index(pd.DatetimeIndex(stamps.to_numpy())).sort_index()
    is_stub = (sizes.to_numpy() < k)

    if drop_stubs:
        out = out.loc[~is_stub]
        is_stub = is_stub[~is_stub]

    out.attrs["timeframe"] = timeframe
    out.attrs["stub_fraction"] = float(np.mean(is_stub)) if len(is_stub) else 0.0
    return out


def bars_per_year(index: pd.DatetimeIndex, trading_days: int = 250) -> float:
    """Empirical annualisation factor -- bars per session x sessions per year.

    Measured from the data rather than assumed, because the theoretical count
    is wrong in practice: NSE has half-days, muhurat sessions, and a handful of
    extended or curtailed days per year, and index intraday feeds have gaps.
    Getting this number wrong is the single easiest way to declare the wrong
    timeframe the winner -- Sharpe scales with its square root, so a 3x error
    in bars/year is a 1.7x error in every Sharpe you print. (This is exactly
    the bug in OpenAlgo's vectorbt example, which fetches 15m bars and passes
    ``freq="5min"`` to ``Portfolio.from_signals``.)
    """
    idx = pd.DatetimeIndex(index)
    if len(idx) < 10:
        return float(trading_days)
    per_day = idx.normalize().value_counts()
    return float(per_day.median() * trading_days)


def session_boundaries(index: pd.DatetimeIndex) -> np.ndarray:
    """Boolean mask: True on the last bar of each session.

    Used to force an intraday book flat before the close.
    """
    days = pd.DatetimeIndex(index).normalize()
    return np.asarray(days != np.roll(days, -1))


def session_opens(index: pd.DatetimeIndex) -> np.ndarray:
    """Boolean mask: True on the first bar of each session.

    The bar whose innovation contains the overnight gap. Worth isolating: the
    filter has no notion of a session, so it prices that bar's forecast error
    with the same variance it uses at 11:40, and the resulting z is routinely
    3-5x too large. Most of the fat tail in an intraday innovation series lives
    on these bars.
    """
    days = pd.DatetimeIndex(index).normalize()
    return np.asarray(days != np.roll(days, 1))


def describe_timeframes(px5: pd.DataFrame) -> pd.DataFrame:
    """Bar counts, spans and annualisation factors for every timeframe."""
    rows = []
    for tf in TIMEFRAMES:
        r = resample(px5, tf)
        idx = pd.DatetimeIndex(r.index)
        rows.append({
            "timeframe": tf,
            "bars": len(r),
            "bars_per_session": round(len(r) / idx.normalize().nunique(), 2),
            "bars_per_year": round(bars_per_year(idx)),
            "stub_fraction": round(r.attrs.get("stub_fraction", 0.0), 3),
            "start": idx[0], "end": idx[-1],
        })
    return pd.DataFrame(rows).set_index("timeframe")
