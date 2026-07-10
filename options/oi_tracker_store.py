"""Persist OI Tracker activity log."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from settings import data_dir

LOG_FILE = data_dir() / "oi_tracker_log.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path, default: list) -> list:
    if not path.exists():
        return list(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return list(default)
    return data if isinstance(data, list) else list(default)


def _write_json(path, data: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def append_log(event: str, detail: str = "", extra: dict[str, Any] | None = None) -> None:
    rows = _read_json(LOG_FILE, [])
    rows.append(
        {
            "at": _now(),
            "event": event,
            "detail": detail,
            **(extra or {}),
        }
    )
    if len(rows) > 500:
        rows = rows[-500:]
    _write_json(LOG_FILE, rows)


def get_log(limit: int = 100) -> list[dict[str, Any]]:
    rows = _read_json(LOG_FILE, [])
    return rows[-limit:][::-1]
