"""CAS desk intraday history — one JSONL row per poll, per underlying.

A single artifact (``data/cas_history.jsonl``) serves two consumers:

* ``GET /cas/history`` → the intraday chart (forecast vs official vs spot vs synth F)
* the later calibration fit — ``official_close − estimate_t`` needs exactly this
  series, so recording it now is what makes that work possible at all.

Display-only and best-effort: nothing here may break a CAS payload, so the
public functions swallow their own exceptions and return a falsy value instead.
Writes are driven from the API route layer (not ``fetch_cas_indicative``) to keep
the payload builders pure and unit tests off the filesystem.
"""

from __future__ import annotations

import json
import math
import threading
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from settings import data_dir

IST = ZoneInfo("Asia/Kolkata")

_LOCK = threading.Lock()

# Desk polls at 8s (in CAS) / 15s (outside). Two pollers — the page and a snapshot
# consumer — must not double-write the same instant.
MIN_APPEND_INTERVAL_SEC = 5.0

# Sessions retained on disk; matches the LOG_RETENTION default in utils/logging.
MAX_SESSIONS = 14

_last_append: dict[str, float] = {}
_pruned_this_process = False


def _history_file() -> Path:
    return data_dir() / "cas_history.jsonl"


def _now_ist(when: datetime | None = None) -> datetime:
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> float | None:
    for v in values:
        n = _num(v)
        if n is not None:
            return n
    return None


def _session_key(ts: str | None, when: datetime | None = None) -> str:
    """IST calendar date owning this row — the natural grouping for a trading day."""
    if ts:
        try:
            return _now_ist(datetime.fromisoformat(str(ts))).date().isoformat()
        except (TypeError, ValueError):
            pass
    return _now_ist(when).date().isoformat()


def row_from_payload(payload: dict[str, Any], *, when: datetime | None = None) -> dict[str, Any]:
    """Flatten a ``CasIndicative`` payload into one chartable / fittable row."""
    comps = _dict(payload.get("estimate_components"))
    synth = _dict(payload.get("synthetic_future"))
    poc = _dict(payload.get("session_poc"))
    constituent = _dict(comps.get("constituent"))

    ts = str(payload.get("asof") or "") or _now_ist(when).isoformat(timespec="seconds")
    return {
        "ts": ts,
        "session": _session_key(ts, when),
        "underlying": str(payload.get("underlying") or "").upper(),
        "in_cas_window": bool(payload.get("in_cas_window")),
        "spot": _num(payload.get("spot")),
        "official_indicative": _num(payload.get("official_indicative")),
        "official_raw": _num(payload.get("official_raw")),
        "official_reject_reason": payload.get("official_reject_reason"),
        "estimate": _num(payload.get("estimate")),
        "estimate_method": payload.get("estimate_method"),
        "synth_f": _first(synth.get("F"), comps.get("synth_f")),
        "fut_ltp": _num(comps.get("fut_ltp")),
        "ref_vwap": _num(comps.get("ref_vwap")),
        "ref_vwap_window": comps.get("ref_vwap_window"),
        "fut_poc": _first(poc.get("poc"), comps.get("fut_poc")),
        "total_imbalance": _num(payload.get("total_imbalance")),
        # Null until the Phase B constituent rebuild lands; kept in the schema so
        # the chart and the calibration set do not need a migration later.
        "constituent_est": _num(constituent.get("estimate")),
        "coverage": _num(constituent.get("coverage")),
        "source": payload.get("source"),
    }


def _has_signal(row: dict[str, Any]) -> bool:
    """Skip rows with nothing plottable (e.g. pre-login polls)."""
    return any(row.get(k) is not None for k in ("spot", "estimate", "official_indicative"))


def append_snapshot(
    payload: dict[str, Any],
    *,
    path: Path | None = None,
    when: datetime | None = None,
) -> bool:
    """Append one row. Throttled per underlying; never raises."""
    global _pruned_this_process
    try:
        row = row_from_payload(payload, when=when)
        if not row["underlying"] or not _has_signal(row):
            return False

        now = _time.monotonic()
        key = row["underlying"]
        with _LOCK:
            last = _last_append.get(key)
            if last is not None and (now - last) < MIN_APPEND_INTERVAL_SEC:
                return False
            _last_append[key] = now

        p = path or _history_file()
        if not _pruned_this_process:
            # Once per process, on the first write — cheap and keeps startup fast.
            _pruned_this_process = True
            prune(path=p)

        with _LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
        return True
    except Exception:
        return False


def _read_rows(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or _history_file()
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    rows.append(obj)
    except Exception:
        return []
    return rows


def sessions(path: Path | None = None) -> list[str]:
    """Distinct session dates present on disk, oldest first."""
    return sorted({str(r.get("session")) for r in _read_rows(path) if r.get("session")})


def read_session(
    underlying: str = "NIFTY",
    session: str | None = None,
    *,
    limit: int = 5000,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Rows for one underlying and session (default: latest session on disk).

    Returned oldest-first — chart order. ``limit`` keeps the most recent rows.
    """
    u = str(underlying or "").upper()
    rows = [r for r in _read_rows(path) if str(r.get("underlying") or "").upper() == u]
    if not rows:
        return []

    want = str(session or "").strip()
    if want.lower() == "today":
        want = _now_ist().date().isoformat()
    elif not want:
        available = sorted({str(r.get("session")) for r in rows if r.get("session")})
        want = available[-1] if available else ""

    out = [r for r in rows if str(r.get("session")) == want]
    if limit and limit > 0:
        out = out[-int(limit) :]
    return out


def prune(max_sessions: int = MAX_SESSIONS, path: Path | None = None) -> int:
    """Drop all but the most recent ``max_sessions`` session dates. Returns rows dropped."""
    p = path or _history_file()
    rows = _read_rows(p)
    if not rows:
        return 0
    found = sorted({str(r.get("session")) for r in rows if r.get("session")})
    if len(found) <= max_sessions:
        return 0
    keep_sessions = set(found[-int(max_sessions) :])
    keep = [r for r in rows if str(r.get("session")) in keep_sessions]
    dropped = len(rows) - len(keep)
    try:
        with _LOCK:
            with open(p, "w", encoding="utf-8") as f:
                for r in keep:
                    f.write(json.dumps(r, default=str) + "\n")
    except Exception:
        return 0
    return dropped


def reset_throttle() -> None:
    """Test helper — clear the per-underlying append throttle."""
    global _pruned_this_process
    with _LOCK:
        _last_append.clear()
    _pruned_this_process = False
