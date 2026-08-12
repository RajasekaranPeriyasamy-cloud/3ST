"""Archive for the IV-skew desk: intraday samples plus a durable daily roll-up.

Two tiers, because they answer different questions and age differently.

**Intraday** — one JSONL per underlying per session date, one line per sample.
Deliberately excludes the per-strike ``points`` array the live snapshot carries:
the desk needs the *metrics* over time, and keeping the full curve would grow the
archive by two orders of magnitude for a chart nobody plots from history.
Retained 90 days.

**Daily** — one row per underlying per expiry per session, appended to a single
file and kept indefinitely. A row is a few hundred bytes, so years of it costs
less than one day of raw. This is the series the daily monitor actually plots.

The roll-up is **lazy and idempotent**, not scheduled. A cron-style "write the
EOD row at 15:25" is a correctness problem waiting to happen — it silently loses
a day to a restart, a holiday, or a clock skew. Instead any archived session
older than today that has no daily row gets rolled up on the next read. Restart
during the close and the row still appears; miss a week and it backfills.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from settings import data_dir

IST = ZoneInfo("Asia/Kolkata")

ROOT_NAME = "iv_skew"
DAILY_FILE = "daily.jsonl"

INTRADAY_RETENTION_DAYS = 90

_LOCK = threading.RLock()


# --- paths ------------------------------------------------------------------


def root_dir():
    path = data_dir() / ROOT_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def underlying_dir(underlying: str):
    path = root_dir() / str(underlying).upper()
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_file(underlying: str, session_date: date | None = None):
    d = session_date or datetime.now(tz=IST).date()
    return underlying_dir(underlying) / f"{d.isoformat()}.jsonl"


def daily_file():
    return root_dir() / DAILY_FILE


# --- intraday ---------------------------------------------------------------


def compact_sample(snapshot: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """Strip a live snapshot down to what is worth keeping per sample."""
    ts = now or datetime.now(tz=IST)
    rows: list[dict[str, Any]] = []
    for rank, exp in enumerate(snapshot.get("expiries") or []):
        row: dict[str, Any] = {
            "expiry": exp.get("expiry"),
            "dte": exp.get("dte"),
            "rank": rank,
            "ok": bool(exp.get("ok")),
            "confidence": exp.get("confidence"),
            "quality": exp.get("quality"),
        }
        if exp.get("ok"):
            row.update(
                {
                    "rr": exp.get("risk_reversal"),
                    "fly": exp.get("butterfly"),
                    "atm_iv": exp.get("atm_iv"),
                    "call_iv": exp.get("call_iv"),
                    "put_iv": exp.get("put_iv"),
                    "forward": exp.get("forward"),
                    "forward_basis": exp.get("forward_basis"),
                    "parity_gap": exp.get("atm_parity_gap"),
                }
            )
        rows.append(row)
    return {
        "ts": ts.replace(microsecond=0).isoformat(),
        "session_date": ts.date().isoformat(),
        "underlying": snapshot.get("underlying"),
        "reference": snapshot.get("reference"),
        "reference_source": snapshot.get("reference_source"),
        "expiries": rows,
    }


def append_sample(underlying: str, sample: dict[str, Any]) -> None:
    """Append one sample. Best-effort: never raises on a malformed row."""
    path = session_file(underlying, _sample_date(sample))
    line = json.dumps(sample, separators=(",", ":"), default=str)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _sample_date(sample: dict[str, Any]) -> date | None:
    raw = sample.get("session_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def load_session(underlying: str, session_date: date | None = None) -> list[dict[str, Any]]:
    """Read one session. A truncated final line is skipped, not fatal —
    a crash mid-write costs that one sample, not the day."""
    path = session_file(underlying, session_date)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with _LOCK:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def sessions_available(underlying: str) -> list[date]:
    out: list[date] = []
    for path in underlying_dir(underlying).glob("*.jsonl"):
        try:
            out.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(out)


def latest_session(underlying: str) -> date | None:
    """Most recent archived session. Callers should default to this, not today —
    before the open there is no file for today and defaulting to it renders an
    empty desk while a full session sits one day back."""
    days = sessions_available(underlying)
    return days[-1] if days else None


def prune_intraday(underlying: str, *, retention_days: int = INTRADAY_RETENTION_DAYS) -> list[str]:
    """Delete intraday files past the retention window.

    Safe only because the daily roll-up is durable — never prune a day that has
    not been rolled up, or the series loses it permanently.
    """
    if retention_days <= 0:
        return []
    today = datetime.now(tz=IST).date()
    rolled = {(r["date"], r["underlying"]) for r in load_daily()}
    removed: list[str] = []
    with _LOCK:
        for d in sessions_available(underlying):
            if (today - d).days <= retention_days:
                continue
            if (d.isoformat(), str(underlying).upper()) not in rolled:
                continue
            try:
                session_file(underlying, d).unlink()
                removed.append(d.isoformat())
            except OSError:
                continue
    return removed


# --- daily roll-up ----------------------------------------------------------


def load_daily() -> list[dict[str, Any]]:
    path = daily_file()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with _LOCK:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _append_daily(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with _LOCK:
        with open(daily_file(), "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")


def rollup_day(underlying: str, session_date: date) -> list[dict[str, Any]]:
    """One row per expiry for a session: the day's last *trustworthy* reading.

    Prefers the last sample where the chain was ``clean``. Falls back to the last
    resolved sample otherwise, carrying its ``degraded`` confidence forward, so a
    thin day is recorded honestly rather than dropped or laundered.
    """
    samples = load_session(underlying, session_date)
    if not samples:
        return []

    # Samples are appended chronologically, so "last wins" is a plain overwrite.
    last_clean: dict[str, dict[str, Any]] = {}
    last_any: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for sample in samples:
        for row in sample.get("expiries") or []:
            expiry = row.get("expiry")
            if not expiry or not row.get("ok") or row.get("rr") is None:
                continue
            counts[expiry] = counts.get(expiry, 0) + 1
            merged = {**row, "ts": sample.get("ts"), "reference": sample.get("reference")}
            last_any[expiry] = merged
            if row.get("confidence") == "clean":
                last_clean[expiry] = merged

    best = {expiry: last_clean.get(expiry, row) for expiry, row in last_any.items()}

    out: list[dict[str, Any]] = []
    for expiry, row in best.items():
        out.append(
            {
                "date": session_date.isoformat(),
                "underlying": str(underlying).upper(),
                "expiry": expiry,
                "rank": row.get("rank"),
                "dte": row.get("dte"),
                "rr": row.get("rr"),
                "fly": row.get("fly"),
                "atm_iv": row.get("atm_iv"),
                "call_iv": row.get("call_iv"),
                "put_iv": row.get("put_iv"),
                "forward_basis": row.get("forward_basis"),
                "parity_gap": row.get("parity_gap"),
                "confidence": row.get("confidence"),
                "quality": row.get("quality"),
                "reference": row.get("reference"),
                "ts": row.get("ts"),
                "samples": counts.get(expiry, 0),
            }
        )
    return sorted(out, key=lambda r: (r.get("rank") if r.get("rank") is not None else 99))


def ensure_rollup(underlyings: list[str] | tuple[str, ...], *, today: date | None = None) -> int:
    """Roll up every archived session older than today that has no daily row.

    Idempotent — safe to call on every read. Only past sessions are rolled: a
    row written mid-session would freeze a partial day as if it were the close.
    """
    cutoff = today or datetime.now(tz=IST).date()
    done = {(r.get("date"), r.get("underlying")) for r in load_daily()}
    written = 0
    for u in underlyings:
        key = str(u).upper()
        for d in sessions_available(key):
            if d >= cutoff or (d.isoformat(), key) in done:
                continue
            rows = rollup_day(key, d)
            if rows:
                _append_daily(rows)
                written += len(rows)
                done.add((d.isoformat(), key))
    return written


def daily_series(
    underlying: str,
    *,
    rank: int = 0,
    clean_only: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    """The daily RR series for one expiry rank, newest last.

    Keyed by **rank** (0 = nearest expiry) rather than by expiry, because expiries
    roll: a series keyed by contract is a handful of disconnected stubs. The cost
    is a sawtooth in DTE, which is why ``dte`` rides along on every point.
    """
    u = str(underlying).upper()
    rows = [r for r in load_daily() if r.get("underlying") == u and r.get("rank") == rank]
    rows.sort(key=lambda r: str(r.get("date")))

    excluded = [r["date"] for r in rows if r.get("confidence") != "clean"]
    if clean_only:
        rows = [r for r in rows if r.get("confidence") == "clean"]
    if limit and len(rows) > limit:
        rows = rows[-limit:]

    return {
        "underlying": u,
        "rank": rank,
        "clean_only": clean_only,
        "points": rows,
        # Never silently drop: the caller sees exactly which sessions were held
        # back and can ask for them.
        "excluded_degraded": excluded,
    }


def coverage(underlying: str) -> dict[str, Any]:
    days = sessions_available(underlying)
    rolled = {r["date"] for r in load_daily() if r.get("underlying") == str(underlying).upper()}
    return {
        "underlying": str(underlying).upper(),
        "sessions": len(days),
        "first": days[0].isoformat() if days else None,
        "last": days[-1].isoformat() if days else None,
        "rolled_up": len(rolled),
        "days": [
            {
                "date": d.isoformat(),
                "samples": len(load_session(underlying, d)),
                "rolled_up": d.isoformat() in rolled,
            }
            for d in days
        ],
    }
