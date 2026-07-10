"""Kite Connect session: login URL, token exchange, persistence."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from kiteconnect import KiteConnect

from settings import data_dir, kite_credentials, kite_proxy_config, kite_ready, proxy_config

SESSION_FILE = data_dir() / "kite_session.json"


def _json_safe(obj: Any) -> Any:
    """Convert Kite payload values to JSON-serializable forms."""
    if isinstance(obj, datetime):
        return obj.isoformat(timespec="seconds")
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _kite(*, use_proxy: bool | None = None) -> KiteConnect:
    if not kite_ready():
        raise RuntimeError("Set KITE_API_KEY and KITE_API_SECRET in .env")
    creds = kite_credentials()
    if use_proxy is False:
        proxies: dict[str, str] = {}
    elif use_proxy is True:
        proxies = proxy_config() or {}
    else:
        proxies = kite_proxy_config() or {}
    return KiteConnect(api_key=creds["api_key"], proxies=proxies)


def _is_proxy_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = ("proxy", "staticip", "getaddrinfo failed", "name resolution", "max retries exceeded")
    return any(n in msg for n in needles)


def login_url() -> str:
    return _kite().login_url()


def load_session() -> dict[str, Any] | None:
    if not SESSION_FILE.exists():
        return None
    try:
        data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not data.get("access_token"):
        return None
    return data


def save_session(payload: dict[str, Any]) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    safe = _json_safe(payload)
    out = {
        "access_token": safe.get("access_token"),
        "user_id": safe.get("user_id"),
        "user_name": safe.get("user_name"),
        "login_time": safe.get("login_time") or datetime.now().isoformat(timespec="seconds"),
        "raw": {k: v for k, v in safe.items() if k != "access_token"},
    }
    if isinstance(out["raw"], dict):
        out["raw"].pop("access_token", None)
    SESSION_FILE.write_text(json.dumps(out, indent=2), encoding="utf-8")


def clear_session() -> None:
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def exchange_request_token(request_token: str) -> dict[str, Any]:
    """
    Exchange one-time request_token for access_token.
    Docs: https://www.kite.trade/docs/connect/v3/user/
    """
    token = (request_token or "").strip()
    if not token:
        raise RuntimeError("request_token is empty")

    creds = kite_credentials()
    kite = _kite()
    data = kite.generate_session(token, api_secret=creds["api_secret"])
    if not data or not data.get("access_token"):
        raise RuntimeError(f"Token exchange failed: {data}")
    save_session(data)
    safe = _json_safe(data)
    return {
        "user_id": safe.get("user_id"),
        "user_name": safe.get("user_name"),
        "login_time": safe.get("login_time"),
        "exchanges": safe.get("exchanges"),
        "products": safe.get("products"),
    }


def get_kite_client() -> KiteConnect:
    """Return authenticated KiteConnect or raise."""
    sess = load_session()
    if not sess:
        raise RuntimeError("Not logged in. Complete Kite login and POST /auth/session.")
    kite = _kite()
    kite.set_access_token(sess["access_token"])
    return kite


def session_status() -> dict[str, Any]:
    sess = load_session()
    if not sess:
        return {"authenticated": False, "kite_configured": kite_ready()}
    return {
        "authenticated": True,
        "kite_configured": kite_ready(),
        "user_id": sess.get("user_id"),
        "user_name": sess.get("user_name"),
        "login_time": sess.get("login_time"),
    }
