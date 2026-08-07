"""Session-open baseline + intraday history for OI VAR desk."""

from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from settings import data_dir

IST = ZoneInfo("Asia/Kolkata")
SESSION_FILE = data_dir() / "oi_var_session_open.json"
HISTORY_FILE = data_dir() / "oi_var_history.json"


def _today() -> str:
    return date.today().isoformat()


def _key(underlying: str, expiry: str) -> str:
    return f"{underlying.upper()}|{expiry}|{_today()}"


def _load(path) -> dict[str, Any]:
    if not path.exists():
        return {"entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"entries": {}}
    if not isinstance(data, dict):
        return {"entries": {}}
    data.setdefault("entries", {})
    return data


def _save(path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_session_open(underlying: str, expiry: str) -> dict[str, Any] | None:
    data = _load(SESSION_FILE)
    entry = data.get("entries", {}).get(_key(underlying, expiry))
    return entry if isinstance(entry, dict) else None


def ensure_session_open(
    underlying: str,
    expiry: str,
    legs: list[dict[str, Any]],
    *,
    after_hhmm: str = "09:20",
) -> dict[str, Any] | None:
    """Persist first post-open snapshot of OI+LTP per token (once per day)."""
    existing = get_session_open(underlying, expiry)
    if existing and existing.get("by_token"):
        return existing

    now = datetime.now(tz=IST)
    try:
        hh, mm = after_hhmm.split(":")
        gate = time(int(hh), int(mm))
    except Exception:
        gate = time(9, 20)
    if now.time() < gate:
        return None

    by_token: dict[str, dict[str, float | int]] = {}
    for leg in legs:
        token = leg.get("instrument_token")
        if token is None:
            continue
        oi = leg.get("oi")
        ltp = leg.get("ltp") or leg.get("price")
        if oi is None or ltp is None:
            continue
        try:
            by_token[str(token)] = {"oi": int(oi), "ltp": float(ltp)}
        except (TypeError, ValueError):
            continue

    if not by_token:
        return None

    entry = {
        "underlying": underlying.upper(),
        "expiry": expiry,
        "session_date": _today(),
        "captured_at": now.isoformat(timespec="seconds"),
        "by_token": by_token,
    }
    data = _load(SESSION_FILE)
    # Drop other days for this underlying|expiry
    prefix = f"{underlying.upper()}|{expiry}|"
    for k in list(data.get("entries", {}).keys()):
        if k.startswith(prefix) and not k.endswith(_today()):
            del data["entries"][k]
    data["entries"][_key(underlying, expiry)] = entry
    _save(SESSION_FILE, data)
    return entry


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def filter_history_to_session(
    underlying: str,
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only ticks inside today's cash/MCX market window (drop post-close)."""
    from options.gamma_density_history import session_window

    start, end = session_window(underlying)
    today = date.today()
    out: list[dict[str, Any]] = []
    for p in points:
        ts = _parse_ts(p.get("t"))
        if ts is None or ts.date() != today:
            continue
        tt = ts.timetz().replace(tzinfo=None)
        if start <= tt <= end:
            row = dict(p)
            row["ts_ms"] = int(ts.timestamp() * 1000)
            out.append(row)
    out.sort(key=lambda r: r.get("ts_ms") or 0)
    return out


def append_history_point(
    underlying: str,
    expiry: str,
    point: dict[str, Any],
    *,
    max_points: int = 120,
    min_interval_sec: int = 45,
) -> list[dict[str, Any]]:
    """Append one in-session tick; skip after market close. Returns session-filtered series."""
    from options.gamma_density_history import in_session

    data = _load(HISTORY_FILE)
    key = _key(underlying, expiry)
    series: list[dict[str, Any]] = list(data.get("entries", {}).get(key) or [])
    now = datetime.now(tz=IST)

    if in_session(underlying, now):
        if series:
            last_t = _parse_ts(series[-1].get("t"))
            too_soon = (
                last_t is not None
                and (now - last_t).total_seconds() < min_interval_sec
            )
        else:
            too_soon = False
        if not too_soon:
            pt = dict(point)
            pt.setdefault("t", now.isoformat(timespec="seconds"))
            series.append(pt)
            if len(series) > max_points:
                series = series[-max_points:]
            prefix = f"{underlying.upper()}|{expiry}|"
            for k in list(data.get("entries", {}).keys()):
                if k.startswith(prefix) and not k.endswith(_today()):
                    del data["entries"][k]
            data.setdefault("entries", {})[key] = series
            _save(HISTORY_FILE, data)

    return filter_history_to_session(underlying, series)


def get_history(underlying: str, expiry: str) -> list[dict[str, Any]]:
    data = _load(HISTORY_FILE)
    raw = list(data.get("entries", {}).get(_key(underlying, expiry)) or [])
    return filter_history_to_session(underlying, raw)
