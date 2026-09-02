"""Load credentials from .env (never commit real secrets)."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent
# override=True so edited .env is picked up without restarting Python
load_dotenv(_ROOT / ".env", override=True)


def env(key: str, default: str = "") -> str:
    # Strip spaces/tabs/newlines — trailing whitespace breaks SHA256 password match
    return (os.getenv(key) or default).strip().strip("\t").strip()


def firstock_credentials() -> dict:
    return {
        "user_id": env("FIRSTOCK_USER_ID"),
        "password": env("FIRSTOCK_PASSWORD"),
        "api_key": env("FIRSTOCK_API_KEY"),
        "vendor_code": env("FIRSTOCK_VENDOR_CODE"),
        "totp_secret": env("FIRSTOCK_TOTP_SECRET"),
    }


def proxy_config() -> dict | None:
    """
    Build requests proxies for staticip.in.
    Firstock whitelists the proxy egress IP (e.g. 165.101.250.152).
    Optional STATICIP_SCHEME=http|https (default http CONNECT).
    """
    host = env("STATICIP_HOST")
    port = env("STATICIP_PORT", "443")
    user = env("STATICIP_USER")
    password = env("STATICIP_PASSWORD")
    scheme = (env("STATICIP_SCHEME") or "http").lower()
    if scheme not in {"http", "https"}:
        scheme = "http"
    if not host or not user or not password:
        return None
    # Encode special characters in user/password for the proxy URL
    u = quote(user, safe="")
    p = quote(password, safe="")
    url = f"{scheme}://{u}:{p}@{host}:{port}"
    return {"http": url, "https": url}


def kite_use_staticip_proxy() -> bool:
    """Kite Connect does not need staticip by default — that proxy is for Firstock."""
    return env("KITE_USE_STATICIP_PROXY", "0").lower() in {"1", "true", "yes", "on"}


def kite_proxy_config() -> dict | None:
    if not kite_use_staticip_proxy():
        return None
    return proxy_config()


def kite_allowed_egress_ip() -> str:
    """Fixed IP whitelisted on developers.kite.trade (staticip.in egress)."""
    return env("KITE_ALLOWED_EGRESS_IP")


def apply_kite_proxy_env() -> bool:
    """Pin process-wide HTTP(S)_PROXY so Kite cannot bypass staticip.in."""
    if not kite_use_staticip_proxy():
        return False
    proxies = proxy_config()
    if not proxies:
        return False
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ[key] = proxies["https"]
    for key in ("NO_PROXY", "no_proxy"):
        os.environ.pop(key, None)
    return True


def proxy_ready() -> bool:
    return proxy_config() is not None


def credentials_ready() -> bool:
    """True when .env has login fields (TOTP is entered in the UI)."""
    c = firstock_credentials()
    return all([c["user_id"], c["password"], c["api_key"], c["vendor_code"]])


def kite_credentials() -> dict:
    return {
        "api_key": env("KITE_API_KEY"),
        "api_secret": env("KITE_API_SECRET"),
        "redirect_url": env("KITE_REDIRECT_URL", "http://127.0.0.1:8001/auth/callback"),
    }


def desk_ui_url() -> str:
    """UI base URL — OAuth callback redirects here after Kite login."""
    return env("DESK_UI_URL", "http://127.0.0.1:8080").rstrip("/")


def kite_ready() -> bool:
    c = kite_credentials()
    return bool(c["api_key"] and c["api_secret"])


def anthropic_api_key() -> str:
    """Key for the Equity Report desk only — unrelated to any broker credential."""
    return env("ANTHROPIC_API_KEY")


def anthropic_ready() -> bool:
    return bool(anthropic_api_key())


_EFFORT_LEVELS = {"low", "medium", "high", "xhigh", "max"}


def equity_report_config() -> dict:
    """Model/effort/spend settings for the Equity Report desk."""
    effort = env("EQUITY_REPORT_EFFORT", "high").lower()
    if effort not in _EFFORT_LEVELS:
        effort = "high"
    try:
        cap = float(env("EQUITY_REPORT_DAILY_USD_CAP", "10"))
    except ValueError:
        cap = 10.0
    provider = env("EQUITY_REPORT_PROVIDER", "anthropic").lower()
    if provider not in {"anthropic", "gemini"}:
        provider = "anthropic"
    return {
        "provider": provider,
        "model": env("EQUITY_REPORT_MODEL", "claude-opus-5"),
        "effort": effort,
        "daily_usd_cap": max(cap, 0.0),
        # Return canned reports instead of calling the API. Read through env()
        # like every other setting so it comes from .env — an os.getenv read
        # would depend on the launching shell's environment, which differs
        # between start_3st_dev.ps1, a bare uvicorn call, and a service.
        "stub": env("EQUITY_REPORT_STUB", "0").lower() in {"1", "true", "yes", "on"},
    }


_THINKING_LEVELS = {"minimal", "low", "medium", "high"}


def gemini_config() -> dict:
    """Gemini settings for the Equity Report desk's alternate provider.

    `enable_search` is off by default: free-tier keys 429 on `google_search`
    grounding even though plain generation and `url_context` both work.
    """
    level = env("EQUITY_REPORT_GEMINI_THINKING", "medium").lower()
    if level not in _THINKING_LEVELS:
        level = "medium"
    return {
        "api_key": env("GEMINI_API_KEY"),
        # 3.5-flash-lite measured best against the report template on 2026-08-08:
        # ~3.4k words in ~46s hitting 5/6 mandatory sections. 3.1-flash-lite is
        # faster but drops the scenario table and the split verdict; 3.6-flash is
        # richer but runs long (~5.8k words, well past the 3k target).
        "model": env("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        "thinking_level": level,
        "enable_search": env("EQUITY_REPORT_GEMINI_SEARCH", "0").lower()
        in {"1", "true", "yes", "on"},
    }


def gemini_ready() -> bool:
    return bool(gemini_config()["api_key"])


def equity_report_ready() -> bool:
    """True when the configured provider has credentials."""
    if equity_report_config()["provider"] == "gemini":
        return gemini_ready()
    return anthropic_ready()


_NEWS_PROVIDERS = {"lexicon", "anthropic"}


def news_desk_config() -> dict:
    """Ingestion + scoring settings for the Live Market News desk.

    Defaults are deliberately zero-cost and offline-capable: the lexicon engine
    needs no key and no network beyond the feeds themselves, so the desk works
    on a fresh clone. ``anthropic`` is an opt-in upgrade that also produces the
    category tags.
    """
    provider = env("NEWS_SENTIMENT_PROVIDER", "lexicon").lower()
    if provider not in _NEWS_PROVIDERS:
        provider = "lexicon"
    try:
        poll = int(env("NEWS_POLL_SEC", "60"))
    except ValueError:
        poll = 60
    try:
        cap = float(env("NEWS_LLM_DAILY_USD_CAP", "2"))
    except ValueError:
        cap = 2.0
    try:
        batch = int(env("NEWS_LLM_BATCH", "25"))
    except ValueError:
        batch = 25
    return {
        "provider": provider,
        # Haiku by default: this is a classification task on short text, and the
        # per-headline cost is what keeps the daily cap meaningful.
        "model": env("NEWS_LLM_MODEL", "claude-haiku-4-5"),
        "poll_sec": max(15, poll),
        "daily_usd_cap": max(cap, 0.0),
        "batch": max(1, min(batch, 50)),
        "announcements": env("NEWS_ANNOUNCEMENTS", "1").lower() in {"1", "true", "yes", "on"},
    }


def news_llm_ready() -> bool:
    return news_desk_config()["provider"] == "anthropic" and anthropic_ready()


def data_dir() -> Path:
    d = _ROOT / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d
