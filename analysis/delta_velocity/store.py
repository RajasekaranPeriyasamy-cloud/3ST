"""Minute-snapshot archive for the delta-velocity engine.

One JSONL file per underlying per session date, one line per sampled minute
holding spot plus every tracked leg — the same shape as ``data/chain_history``,
which this deliberately mirrors rather than inventing a second convention.

This archive is the whole point of Phase 1. The paper's three-year option-state
corpus cannot be fetched retroactively from Kite (expired contracts have no
resolvable token), so the only way to obtain one is to write it down going
forward, starting now.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from settings import data_dir

IST = ZoneInfo("Asia/Kolkata")

ROOT_NAME = "delta_velocity"
STATE_FILE = "state.json"

# Raw minute snapshots are bulky (~10 MB/day across three underlyings). Keep a
# month of raw for re-derivation, and expect computed features — which are two
# orders of magnitude smaller — to be what is retained long term.
RAW_RETENTION_DAYS = 30

_LOCK = threading.RLock()


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


def append_snapshot(underlying: str, snapshot: dict[str, Any]) -> None:
    """Append one minute's snapshot. Never raises on a malformed leg."""
    path = session_file(underlying, _snapshot_date(snapshot))
    line = json.dumps(snapshot, separators=(",", ":"), default=str)
    with _LOCK:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


def _snapshot_date(snapshot: dict[str, Any]) -> date | None:
    raw = snapshot.get("session_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


def load_session(underlying: str, session_date: date | None = None) -> list[dict[str, Any]]:
    """Read one session's snapshots. A truncated final line is skipped, not fatal.

    A crash mid-write leaves a partial line; losing that one minute is correct
    behaviour, refusing to load the day is not.
    """
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


def to_rows(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten snapshots into per-leg rows for ``features.compute_delta_velocity``."""
    rows: list[dict[str, Any]] = []
    for snap in snapshots:
        ts = snap.get("ts")
        session_date = snap.get("session_date")
        underlying = snap.get("underlying")
        for leg in snap.get("legs") or []:
            rows.append(
                {
                    "ts": ts,
                    "session_date": session_date,
                    "underlying": underlying,
                    "expiry": leg.get("expiry"),
                    "strike": leg.get("strike"),
                    "option_type": leg.get("option_type"),
                    "delta": leg.get("delta"),
                    "iv": leg.get("iv"),
                    "ltp": leg.get("ltp"),
                    # Collected since the first snapshot but not previously
                    # surfaced here; PCR and the OI tiles read them.
                    "oi": leg.get("oi"),
                    "volume": leg.get("volume"),
                    "spot": snap.get("spot"),
                }
            )
    return rows


def sessions_available(underlying: str) -> list[date]:
    out: list[date] = []
    for path in underlying_dir(underlying).glob("*.jsonl"):
        try:
            out.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(out)


def latest_session(underlying: str) -> date | None:
    """Most recent archived session, or None if nothing has been collected.

    Callers that render a session should default to this rather than to today.
    The collector only writes during market hours, so before the open there is
    no file for today and defaulting to it renders an empty page while a full
    session sits on disk one day back — which reads as a broken desk.
    """
    days = sessions_available(underlying)
    return days[-1] if days else None


def coverage(underlying: str) -> dict[str, Any]:
    """What the archive actually holds — the Phase 1 exit criterion."""
    days = sessions_available(underlying)
    per_day: list[dict[str, Any]] = []
    for d in days:
        snaps = load_session(underlying, d)
        per_day.append({"date": d.isoformat(), "minutes": len(snaps),
                        "legs": len(snaps[0].get("legs") or []) if snaps else 0})
    return {
        "underlying": str(underlying).upper(),
        "sessions": len(days),
        "first": days[0].isoformat() if days else None,
        "last": days[-1].isoformat() if days else None,
        "days": per_day,
    }


def prune_raw(underlying: str, *, retention_days: int = RAW_RETENTION_DAYS) -> list[str]:
    """Delete raw snapshot files older than the retention window."""
    if retention_days <= 0:
        return []
    cutoff = datetime.now(tz=IST).date()
    removed: list[str] = []
    with _LOCK:
        for d in sessions_available(underlying):
            if (cutoff - d).days > retention_days:
                path = session_file(underlying, d)
                try:
                    path.unlink()
                    removed.append(d.isoformat())
                except OSError:
                    continue
    return removed


def load_state() -> dict[str, Any]:
    path = root_dir() / STATE_FILE
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(patch: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        state = {**load_state(), **patch}
        path = root_dir() / STATE_FILE
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, default=str)
        tmp.replace(path)
    return state
