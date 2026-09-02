"""Persisted scan configuration and charge-rate overrides.

Follows the store pattern used by ``risk/limits.py`` and
``execution/arming.py``: a module-level singleton, an explicit persist
allow-list, a total loader that never lets a bad file stop the API booting, and
``load_persisted_config()`` at import time.

The charge rates live here rather than only in code because they are the one
part of this desk that goes stale on someone else's schedule — an exchange
revises a transaction charge and every net number on the page is wrong until
the operator can correct it without a deploy.
"""

from __future__ import annotations

import json
from typing import Any

from analysis.opt_arb import costs
from settings import data_dir

CONFIG_FILE = data_dir() / "opt_arb_config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    # Only surface a row worth more than this many rupees, net of charges.
    "min_net_rs": 0.0,
    "lots": 1,
    # Tier A only: drop big/mini pairs whose expiries or futures months differ.
    "require_clean": True,
    # Drop rows the top of book cannot fill at the requested size.
    "require_depth": True,
    "families": ["xcontract", "butterfly", "vertical", "box"],
    "strike_window": 20,
    "rate_pct": 6.5,
    # Underlyings scanned by the single-underlying families.
    "underlyings": [
        {"name": "NIFTY", "exchange": "NFO"},
        {"name": "BANKNIFTY", "exchange": "NFO"},
        {"name": "SENSEX", "exchange": "BFO"},
    ],
}

_PERSIST_KEYS = tuple(DEFAULT_CONFIG)

_CONFIG: dict[str, Any] = dict(DEFAULT_CONFIG)


def config() -> dict[str, Any]:
    out = dict(_CONFIG)
    out["rates"] = costs.all_rates()
    out["rates_asof"] = costs.RATES_ASOF
    return out


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULT_CONFIG[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        return max(int(value), 1) if key == "lots" else int(value)
    if isinstance(default, float):
        return float(value)
    if key == "families":
        known = set(DEFAULT_CONFIG["families"])
        picked = [str(f) for f in value if str(f) in known]
        return picked or list(DEFAULT_CONFIG["families"])
    if key == "underlyings":
        rows = []
        for row in value or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").upper()
            exchange = str(row.get("exchange") or "").upper()
            if name and exchange:
                rows.append({"name": name, "exchange": exchange})
        return rows or list(DEFAULT_CONFIG["underlyings"])
    return value


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update. ``rates`` is handled separately from scan knobs."""
    for key, value in (patch or {}).items():
        if key == "rates":
            for segment, overrides in (value or {}).items():
                if isinstance(overrides, dict):
                    costs.set_rates(segment, overrides)
            continue
        if key not in DEFAULT_CONFIG:
            continue
        try:
            _CONFIG[key] = _coerce(key, value)
        except (TypeError, ValueError):
            continue
    _persist_config()
    return config()


def reset_config() -> dict[str, Any]:
    _CONFIG.clear()
    _CONFIG.update(DEFAULT_CONFIG)
    costs.reset_rates()
    _persist_config()
    return config()


def _persist_config() -> None:
    payload = {k: _CONFIG.get(k, DEFAULT_CONFIG[k]) for k in _PERSIST_KEYS}
    # Persist only rate cards that differ from the shipped defaults, so a future
    # correction to the built-in schedule is picked up instead of being shadowed
    # by a stale copy on disk.
    overrides: dict[str, Any] = {}
    for segment, card in costs.all_rates().items():
        base = costs.DEFAULT_RATES.get(segment)
        if base is None:
            overrides[segment] = card
            continue
        diff = {k: v for k, v in card.items() if getattr(base, k, None) != v}
        if diff:
            overrides[segment] = diff
    payload["rate_overrides"] = overrides
    try:
        CONFIG_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_persisted_config() -> None:
    if not CONFIG_FILE.exists():
        return
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(raw, dict):
        return
    for key in _PERSIST_KEYS:
        if key not in raw:
            continue
        try:
            _CONFIG[key] = _coerce(key, raw[key])
        except (TypeError, ValueError):
            continue
    for segment, overrides in (raw.get("rate_overrides") or {}).items():
        if isinstance(overrides, dict):
            costs.set_rates(segment, overrides)


load_persisted_config()
