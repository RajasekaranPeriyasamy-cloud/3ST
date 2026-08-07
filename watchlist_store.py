"""Multi-instrument watchlist — queue items waiting for 3ST signals."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from settings import data_dir

WATCHLIST_FILE = data_dir() / "watchlist.json"

_STATUSES = {"waiting", "triggered", "active", "closed"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> list[dict[str, Any]]:
    if not WATCHLIST_FILE.exists():
        return []
    try:
        data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(data, list):
        return data
    return []


def _save(items: list[dict[str, Any]]) -> None:
    WATCHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATCHLIST_FILE.write_text(json.dumps({"items": items}, indent=2), encoding="utf-8")


def list_items(status: str | None = None) -> list[dict[str, Any]]:
    items = _load()
    if not status:
        return items
    allowed = {s.strip() for s in status.split(",") if s.strip()}
    return [i for i in items if i.get("status") in allowed]


def get_item(item_id: str) -> dict[str, Any] | None:
    for item in _load():
        if item.get("id") == item_id:
            return item
    return None


def add_item(payload: dict[str, Any]) -> dict[str, Any]:
    items = _load()
    item = {
        "id": str(uuid.uuid4()),
        "status": "waiting",
        "signal": None,
        "signal_at": None,
        "created_at": _now(),
        "updated_at": _now(),
        **{k: v for k, v in payload.items() if k not in {"id", "status", "signal", "signal_at", "created_at", "updated_at"}},
    }
    if item.get("product") == "underlying":
        item["spread"] = None
    items.append(item)
    _save(items)
    return item


_NULLABLE_KEYS = frozenset({"spread", "signal", "exit_at", "exit_reason", "exit_price"})


def update_item(item_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    items = _load()
    for idx, item in enumerate(items):
        if item.get("id") != item_id:
            continue
        status = patch.get("status")
        if status is not None and status not in _STATUSES:
            raise ValueError(f"Invalid status '{status}'")
        merged = {
            **item,
            **{k: v for k, v in patch.items() if v is not None or k in _NULLABLE_KEYS},
        }
        merged["updated_at"] = _now()
        items[idx] = merged
        _save(items)
        return merged
    raise KeyError(f"Watchlist item not found: {item_id}")


def remove_item(item_id: str) -> dict[str, Any]:
    items = _load()
    kept = [i for i in items if i.get("id") != item_id]
    if len(kept) == len(items):
        raise KeyError(f"Watchlist item not found: {item_id}")
    _save(kept)
    return {"ok": True, "removed": item_id}


def mark_triggered(item_id: str, signal: str, note: str = "") -> dict[str, Any]:
    return update_item(
        item_id,
        {
            "status": "triggered",
            "signal": signal,
            "signal_at": _now(),
            "signal_note": note,
        },
    )


def mark_active(item_id: str) -> dict[str, Any]:
    return update_item(item_id, {"status": "active"})


def mark_closed(item_id: str) -> dict[str, Any]:
    return update_item(item_id, {"status": "closed", "signal": None})
