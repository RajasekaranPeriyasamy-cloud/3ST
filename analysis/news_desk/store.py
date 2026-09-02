"""JSON-backed item store for the news desk.

Follows the flat-JSON-per-concern convention (``execution/arming.py``,
``risk/limits.py``): module-level singleton, ``_load``/``_save`` around a
``data_dir()``-relative file, restored at import.

Two threads touch this — the API request handlers and the poll runner — so every
mutation goes through ``_LOCK``, the same way ``analysis/equity_report/store.py``
does.

Sentiment is stored **on the item**, permanently. That is the whole point of
keying items by a stable id: a headline is scored once and never again, which
keeps the optional LLM backend's cost bounded by "new headlines per day" rather
than "polls per day".
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from settings import data_dir

ITEMS_FILE = data_dir() / "news_items.json"
CONFIG_FILE = data_dir() / "news_desk_config.json"

# Keep the newest N items. At ~500 fresh items per poll cycle and heavy overlap
# between polls, this is several days of history.
MAX_ITEMS = 2000

# Items older than this are dropped on prune regardless of count.
MAX_AGE_DAYS = 7

_DEFAULT_CONFIG: dict[str, Any] = {
    "poll_sec": 60,
    "enabled": True,
    # Lookback for the "N more in the last X days" cluster on a row.
    "cluster_days": 7,
}

_LOCK = threading.RLock()
_ITEMS: dict[str, dict[str, Any]] = {}
_CONFIG: dict[str, Any] = dict(_DEFAULT_CONFIG)
_HEALTH: list[dict[str, Any]] = []
_LAST_POLL: dict[str, Any] = {"at": None, "added": 0, "error": ""}


def _now() -> datetime:
    return datetime.now(UTC)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


# --- persistence ------------------------------------------------------------


def _save_items() -> None:
    payload = {"items": list(_ITEMS.values()), "saved_at": _now_iso()}
    ITEMS_FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_config() -> None:
    CONFIG_FILE.write_text(json.dumps(_CONFIG, indent=2), encoding="utf-8")


def load_persisted_items() -> None:
    global _ITEMS
    if not ITEMS_FILE.exists():
        _ITEMS = {}
        return
    try:
        raw = json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _ITEMS = {}
        return
    items = raw.get("items") if isinstance(raw, dict) else None
    _ITEMS = {i["id"]: i for i in (items or []) if isinstance(i, dict) and i.get("id")}


def load_persisted_config() -> None:
    global _CONFIG
    _CONFIG = dict(_DEFAULT_CONFIG)
    if not CONFIG_FILE.exists():
        return
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if isinstance(raw, dict):
        _CONFIG.update({k: v for k, v in raw.items() if k in _DEFAULT_CONFIG})


load_persisted_items()
load_persisted_config()


# --- config -----------------------------------------------------------------


def get_config() -> dict[str, Any]:
    with _LOCK:
        return dict(_CONFIG)


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        for key, value in (patch or {}).items():
            if key in _DEFAULT_CONFIG:
                _CONFIG[key] = value
        _CONFIG["poll_sec"] = max(15, int(_CONFIG.get("poll_sec") or 60))
        _CONFIG["cluster_days"] = max(1, int(_CONFIG.get("cluster_days") or 7))
        _save_config()
        return dict(_CONFIG)


# --- items ------------------------------------------------------------------


def _prune_locked() -> None:
    cutoff = (_now() - timedelta(days=MAX_AGE_DAYS)).isoformat(timespec="seconds")
    fresh = {i_id: item for i_id, item in _ITEMS.items() if item.get("published_at", "") >= cutoff}
    if len(fresh) > MAX_ITEMS:
        newest = sorted(fresh.values(), key=lambda i: i.get("published_at", ""), reverse=True)
        fresh = {i["id"]: i for i in newest[:MAX_ITEMS]}
    _ITEMS.clear()
    _ITEMS.update(fresh)


def upsert_many(items: list[dict[str, Any]]) -> int:
    """Insert new items; return how many were genuinely new.

    An item already present is **not** overwritten — it already carries a
    sentiment we paid for, and the publisher may have since edited the headline
    in ways that would invalidate nothing but cost a re-score.
    """
    added = 0
    with _LOCK:
        for item in items:
            item_id = item.get("id")
            if not item_id or item_id in _ITEMS:
                continue
            _ITEMS[item_id] = item
            added += 1
        if added:
            _prune_locked()
            _save_items()
    return added


def unscored(limit: int = 200) -> list[dict[str, Any]]:
    with _LOCK:
        pending = [i for i in _ITEMS.values() if not i.get("sentiment")]
    pending.sort(key=lambda i: i.get("published_at", ""), reverse=True)
    return pending[:limit]


def apply_sentiment(scores: dict[str, dict[str, Any]]) -> int:
    """Attach scores keyed by item id. Returns the number applied."""
    applied = 0
    with _LOCK:
        for item_id, sentiment in (scores or {}).items():
            item = _ITEMS.get(item_id)
            if item is None or not sentiment:
                continue
            item["sentiment"] = sentiment
            applied += 1
        if applied:
            _save_items()
    return applied


def apply_symbols(resolved: dict[str, list[dict[str, Any]]]) -> int:
    applied = 0
    with _LOCK:
        for item_id, symbols in (resolved or {}).items():
            item = _ITEMS.get(item_id)
            if item is None:
                continue
            item["symbols"] = symbols
            applied += 1
        if applied:
            _save_items()
    return applied


def all_items() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(i) for i in _ITEMS.values()]


def count() -> int:
    with _LOCK:
        return len(_ITEMS)


# --- source health / poll status -------------------------------------------


def set_health(health: list[dict[str, Any]]) -> None:
    global _HEALTH
    with _LOCK:
        _HEALTH = list(health or [])


def get_health() -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(h) for h in _HEALTH]


def set_last_poll(added: int, error: str = "") -> None:
    global _LAST_POLL
    with _LOCK:
        _LAST_POLL = {"at": _now_iso(), "added": added, "error": error}


def get_last_poll() -> dict[str, Any]:
    with _LOCK:
        return dict(_LAST_POLL)


def clear_all() -> None:
    """Drop every item. Used by tests; not exposed through the API."""
    with _LOCK:
        _ITEMS.clear()
        _save_items()
