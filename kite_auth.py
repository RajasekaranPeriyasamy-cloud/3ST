"""Kite Connect session: login URL, token exchange, persistence."""

from __future__ import annotations

import json
import logging
import socket
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import requests
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv
from kiteconnect import KiteConnect
from requests.adapters import HTTPAdapter

from settings import (
    apply_kite_proxy_env,
    data_dir,
    kite_allowed_egress_ip,
    kite_credentials,
    kite_proxy_config,
    kite_ready,
    kite_use_staticip_proxy,
    proxy_config,
)

_ROOT = Path(__file__).resolve().parent
IST = ZoneInfo("Asia/Kolkata")

logger = logging.getLogger("3st.kite_auth")

KiteEgressMode = Literal["local_bind", "staticip_proxy", "direct"]

SESSION_FILE = data_dir() / "kite_session.json"
SESSION_KEY_FILE = data_dir() / ".session_key"


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


def _sanitize_proxies(proxies: dict[str, str] | None) -> dict[str, str]:
    if not proxies:
        return {}
    return {k: str(v) for k, v in proxies.items() if k in {"http", "https"} and v}


def _ipv6_bind_available(addr: str) -> bool:
    """True when this machine can source outbound traffic from ``addr``."""
    if not addr or ":" not in addr:
        return False
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        sock.bind((addr, 0))
        sock.close()
        return True
    except OSError:
        return False


def _ipv6_prefix64(addr: str) -> str:
    parts = [p for p in addr.split(":") if p]
    if len(parts) < 4:
        return addr.lower()
    return ":".join(parts[:4]).lower()


def _discover_local_ipv6_global() -> list[str]:
    """Global (non-link-local) IPv6 addresses on this host."""
    import subprocess
    import sys

    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-NetIPAddress -AddressFamily IPv6 | "
                    "Where-Object { $_.IPAddress -notlike 'fe80*' -and $_.IPAddress -notlike '::1' } | "
                    "Select-Object -ExpandProperty IPAddress",
                ],
                text=True,
                timeout=12,
            )
            return [line.strip() for line in out.splitlines() if line.strip() and ":" in line]
        except Exception:
            return []
    try:
        out = subprocess.check_output(["ip", "-6", "addr", "show", "scope", "global"], text=True, timeout=8)
        addrs: list[str] = []
        for line in out.splitlines():
            line = line.strip()
            if "inet6 " in line:
                token = line.split("inet6 ", 1)[1].split("/", 1)[0].strip()
                if token and not token.startswith("fe80"):
                    addrs.append(token)
        return addrs
    except Exception:
        return []


def _resolve_bind_ipv6(allowed: str) -> str | None:
    """
    Pick a bindable local IPv6 for Kite orders.

    1. Exact ``KITE_ALLOWED_EGRESS_IP`` when assigned on this PC.
    2. Any global IPv6 on the same /64 prefix (ISP rotates host suffix).
    """
    if allowed and _ipv6_bind_available(allowed):
        return allowed
    if not allowed or ":" not in allowed:
        return None
    prefix = _ipv6_prefix64(allowed)
    for addr in _discover_local_ipv6_global():
        if _ipv6_prefix64(addr) == prefix and _ipv6_bind_available(addr):
            return addr
    return None


def _manual_bound_socket(
    host: str,
    port: int,
    bind_ip: str,
    timeout: float | None,
) -> socket.socket:
    """
    Open HTTPS socket from a fixed local IP.

    Windows + urllib3 ``source_address`` breaks DNS (getaddrinfo failed) even when
    nslookup works — bind manually instead.
    """
    last_err: OSError | None = None
    families = (socket.AF_INET6, socket.AF_INET) if ":" in bind_ip else (socket.AF_INET,)
    for family in families:
        try:
            infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except socket.gaierror as e:
            last_err = OSError(e.errno, e.strerror)
            continue
        for af, socktype, proto, _canon, addr in infos:
            sock: socket.socket | None = None
            try:
                sock = socket.socket(af, socktype, proto)
                if ":" in bind_ip:
                    if af != socket.AF_INET6:
                        sock.close()
                        continue
                    if sys.platform == "win32":
                        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                    sock.bind((bind_ip, 0))
                else:
                    if af != socket.AF_INET:
                        sock.close()
                        continue
                    sock.bind((bind_ip, 0))
                if timeout is not None:
                    sock.settimeout(timeout)
                sock.connect(addr)
                return sock
            except OSError as e:
                last_err = e
                if sock is not None:
                    sock.close()
    if last_err:
        raise last_err
    raise OSError(f"Could not connect to {host}:{port} from {bind_ip}")


def _source_bind_adapter(source_ip: str, *, pool_connections: int = 10, pool_maxsize: int = 10) -> HTTPAdapter:
    bind_ip = source_ip
    use_manual = sys.platform == "win32" and ":" in bind_ip

    if not use_manual:

        class BoundAdapter(HTTPAdapter):
            def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
                pool_kwargs["source_address"] = (bind_ip, 0)
                return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

        return BoundAdapter(pool_connections=pool_connections, pool_maxsize=pool_maxsize)

    from urllib3.connection import HTTPSConnection
    from urllib3.connectionpool import HTTPSConnectionPool
    from urllib3.exceptions import ConnectTimeoutError, NameResolutionError
    from urllib3.util.timeout import Timeout as Urllib3Timeout

    class BoundHTTPSConnection(HTTPSConnection):
        def _new_conn(self) -> socket.socket:
            timeout = self.timeout
            if isinstance(timeout, Urllib3Timeout):
                timeout = timeout.connect_timeout
            try:
                return _manual_bound_socket(self._dns_host, self.port or 443, bind_ip, timeout)
            except socket.gaierror as e:
                raise NameResolutionError(self.host, self, e) from e
            except OSError as e:
                if "timed out" in str(e).lower():
                    raise ConnectTimeoutError(
                        self,
                        f"Connection to {self.host} timed out via bind {bind_ip}",
                    ) from e
                raise

    class BoundHTTPSConnectionPool(HTTPSConnectionPool):
        ConnectionCls = BoundHTTPSConnection

    class WindowsBoundAdapter(HTTPAdapter):
        def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
            from urllib3.poolmanager import PoolManager

            manager = PoolManager(
                num_pools=connections,
                maxsize=maxsize,
                block=block,
                **pool_kwargs,
            )
            manager.pool_classes_by_scheme = {
                **manager.pool_classes_by_scheme,
                "https": BoundHTTPSConnectionPool,
            }
            self.poolmanager = manager
            return manager

    return WindowsBoundAdapter(pool_connections=pool_connections, pool_maxsize=pool_maxsize)


def _pooled_https_adapter(*, bind_ipv6: str | None = None) -> HTTPAdapter:
    if bind_ipv6:
        return _source_bind_adapter(bind_ipv6)
    return HTTPAdapter(pool_connections=10, pool_maxsize=10)


def kite_egress_plan() -> tuple[dict[str, str], str | None, KiteEgressMode]:
    """
    Choose how live Kite orders reach the API.

    Prefer binding to a whitelisted local IPv6 (exact or same /64 on this host).
    staticip.in egress often differs from the Kite whitelist.
    """
    allowed = kite_allowed_egress_ip()
    bind = _resolve_bind_ipv6(allowed)
    if bind:
        return {}, bind, "local_bind"
    if kite_use_staticip_proxy():
        proxies = kite_proxy_config() or {}
        if proxies:
            return proxies, None, "staticip_proxy"
    return {}, None, "direct"


def kite_egress_status() -> dict[str, Any]:
    proxies, bind_ipv6, mode = kite_egress_plan()
    allowed = kite_allowed_egress_ip()
    local_addrs = _discover_local_ipv6_global()
    same_prefix = [
        a for a in local_addrs
        if allowed and ":" in allowed and _ipv6_prefix64(a) == _ipv6_prefix64(allowed)
    ]
    return {
        "mode": mode,
        "bind_ipv6": bind_ipv6,
        "proxy_enabled": mode == "staticip_proxy",
        "allowed_egress_ip": allowed or None,
        "local_bind_available": bind_ipv6 is not None,
        "local_ipv6_same_prefix": same_prefix,
        "proxy_host": (proxies.get("https") or "").split("@")[-1] if proxies else None,
    }


def _harden_kite_session(
    kite: KiteConnect,
    proxies: dict[str, str],
    *,
    bind_ipv6: str | None = None,
) -> None:
    """Pin Kite HTTP to staticip proxy or a fixed local IPv6 — never rotating home IPv6."""
    clean = _sanitize_proxies(proxies)
    kite.proxies = clean
    kite.reqsession.trust_env = False
    kite.reqsession.proxies.clear()
    if clean:
        kite.reqsession.proxies.update(clean)

        original_request = kite.reqsession.request

        def forced_proxy_request(method, url, **kwargs):
            kwargs["proxies"] = clean
            return original_request(method, url, **kwargs)

        kite.reqsession.request = forced_proxy_request  # type: ignore[method-assign]
        kite.reqsession.mount("https://", _pooled_https_adapter())
    elif bind_ipv6:
        kite.reqsession.mount("https://", _pooled_https_adapter(bind_ipv6=bind_ipv6))
    else:
        kite.reqsession.mount("https://", _pooled_https_adapter())


def _kite(*, use_proxy: bool | None = None) -> KiteConnect:
    load_dotenv(_ROOT / ".env", override=True)
    if not kite_ready():
        raise RuntimeError("Set KITE_API_KEY and KITE_API_SECRET in .env")
    creds = kite_credentials()
    bind_ipv6: str | None = None
    if use_proxy is False:
        proxies: dict[str, str] = {}
    elif use_proxy is True:
        proxies = proxy_config() or {}
    else:
        proxies, bind_ipv6, _mode = kite_egress_plan()
    kite = KiteConnect(api_key=creds["api_key"], proxies=proxies)
    _harden_kite_session(kite, proxies, bind_ipv6=bind_ipv6)
    return kite


# Memoised read-only client. Reads are always direct egress (no proxy, no
# source bind) per the egress split in CLAUDE.md, so this cache carries no
# egress state and can never be handed to an order path -- get_kite_client()
# and the login flow keep constructing fresh clients against the egress plan.
#
# Rebuilding KiteConnect per call cost ~276 ms of pure setup: a new
# requests.Session meant a new connection pool, a fresh TLS handshake and a
# re-read of the CA bundle (load_verify_locations) on every single call.
_READ_CLIENT: KiteConnect | None = None
_READ_CLIENT_API_KEY: str | None = None
_READ_CLIENT_LOCK = threading.Lock()


def reset_kite_client_cache() -> None:
    """Drop the memoised read client (re-login, logout, credential change)."""
    global _READ_CLIENT, _READ_CLIENT_API_KEY
    with _READ_CLIENT_LOCK:
        _READ_CLIENT = None
        _READ_CLIENT_API_KEY = None


def read_only_kite_client() -> KiteConnect:
    """Shared direct-egress KiteConnect for market-data reads.

    Callers still set the access token themselves each call, so a re-login is
    picked up without waiting for cache invalidation. Never use this to place
    or cancel orders: it deliberately ignores kite_egress_plan(), so it is not
    bound to the whitelisted IP.
    """
    global _READ_CLIENT, _READ_CLIENT_API_KEY
    client = _READ_CLIENT
    if client is not None and _READ_CLIENT_API_KEY == _cached_api_key():
        return client

    with _READ_CLIENT_LOCK:
        api_key = _cached_api_key()
        if _READ_CLIENT is not None and _READ_CLIENT_API_KEY == api_key:
            return _READ_CLIENT
        fresh = _kite(use_proxy=False)
        _READ_CLIENT = fresh
        _READ_CLIENT_API_KEY = api_key
        return fresh


def _cached_api_key() -> str | None:
    """API key as currently loaded, without re-reading .env from disk."""
    try:
        return kite_credentials().get("api_key")
    except Exception:
        return None


def _is_proxy_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    needles = ("proxy", "staticip", "getaddrinfo failed", "name resolution", "max retries exceeded")
    return any(n in msg for n in needles)


def _require_proxy_for_live() -> None:
    _proxies, _bind_ipv6, mode = kite_egress_plan()
    if mode == "local_bind":
        return
    if mode == "staticip_proxy":
        apply_kite_proxy_env()
        return
    if kite_use_staticip_proxy() and not kite_proxy_config():
        raise RuntimeError(
            "KITE_USE_STATICIP_PROXY=1 but STATICIP_HOST/USER/PASSWORD are missing. "
            "Live orders must egress via your whitelisted static IP."
        )


def login_url() -> str:
    return _kite(use_proxy=False).login_url()


def _get_fernet() -> Fernet:
    """Load or create the Fernet key used to encrypt kite_session.json."""
    SESSION_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not SESSION_KEY_FILE.exists():
        key = Fernet.generate_key()
        SESSION_KEY_FILE.write_bytes(key)
        try:
            SESSION_KEY_FILE.chmod(0o600)
        except OSError:
            pass
    return Fernet(SESSION_KEY_FILE.read_bytes())


def _session_payload(payload: dict[str, Any]) -> dict[str, Any]:
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
    return out


def _looks_like_plain_json(raw: bytes) -> bool:
    stripped = raw.lstrip()
    return bool(stripped) and stripped[:1] in (b"{", b"[")


def _load_plain_session(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return data


def _load_encrypted_session(raw: bytes) -> dict[str, Any] | None:
    try:
        decrypted = _get_fernet().decrypt(raw)
        data = json.loads(decrypted.decode("utf-8"))
    except InvalidToken:
        logger.error("Failed to decrypt kite session (wrong or missing .session_key)")
        return None
    except Exception as exc:
        logger.error("Failed to decrypt kite session: %s", exc)
        return None
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    return data


def load_session() -> dict[str, Any] | None:
    if not SESSION_FILE.exists():
        return None
    try:
        raw = SESSION_FILE.read_bytes()
    except OSError as exc:
        logger.error("Failed to read kite session file: %s", exc)
        return None

    # Legacy plain JSON — keep working; next save_session() migrates to encrypted.
    if _looks_like_plain_json(raw):
        return _load_plain_session(raw)

    data = _load_encrypted_session(raw)
    if data is not None:
        return data

    # Last-resort plain parse (e.g. odd whitespace / encoding)
    return _load_plain_session(raw)


def save_session(payload: dict[str, Any]) -> None:
    reset_kite_client_cache()
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    out = _session_payload(payload)
    json_bytes = json.dumps(out).encode("utf-8")
    encrypted = _get_fernet().encrypt(json_bytes)
    SESSION_FILE.write_bytes(encrypted)


def clear_session() -> None:
    reset_kite_client_cache()
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
    # OAuth token exchange is not IP-whitelisted — use direct connection (not staticip / IPv6 bind).
    kite = _kite(use_proxy=False)
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
    _require_proxy_for_live()
    kite = _kite()
    _proxies, bind_ipv6, mode = kite_egress_plan()
    if mode == "staticip_proxy" and not kite.proxies:
        raise RuntimeError(
            "Kite static IP proxy is required for live orders but is not active. "
            f"Whitelist expects {kite_allowed_egress_ip() or 'your staticip egress'} on developers.kite.trade."
        )
    if mode == "local_bind" and not bind_ipv6:
        raise RuntimeError(
            "Kite local IPv6 bind is required for live orders but is not configured. "
            f"Set KITE_ALLOWED_EGRESS_IP to your whitelisted address on developers.kite.trade."
        )
    kite.set_access_token(sess["access_token"])
    return kite


#: Cached result of the live token probe. ``/health`` is polled continuously by
#: the desk UI, and a Kite call per poll would burn rate limit to re-learn a fact
#: that changes roughly once a day (tokens expire ~06:00 IST).
_TOKEN_PROBE: dict[str, Any] = {"state": "unknown", "checked_at": None, "error": None, "at": 0.0}
_TOKEN_PROBE_TTL_SEC = 60.0
_TOKEN_PROBE_LOCK = threading.Lock()


def _looks_like_auth_error(message: str) -> bool:
    """Token rejected, as opposed to the API being unreachable.

    The distinction decides what the operator is told to do, so it must not be a
    catch-all: "your session expired, go and log in again" is wrong and wastes a
    trading morning when the real problem is DNS. Kite phrases a rejected token
    as "Incorrect `api_key` or `access_token`."
    """
    lower = str(message).lower()
    if "incorrect" in lower and ("access_token" in lower or "api_key" in lower):
        return True
    return "tokenexception" in lower or ("token" in lower and "expired" in lower)


def token_probe(*, max_age_sec: float = _TOKEN_PROBE_TTL_SEC, force: bool = False) -> dict[str, Any]:
    """Does the stored token actually work? Cached, and honest about not knowing.

    ``session_status`` answers a different and cheaper question — is there a
    session file — and is called from order-adjacent hot paths, so it stays a
    pure file read. This is the live check, and only ``/health`` calls it.

    States, and why there are four rather than a boolean:

    ``no_session``   nothing stored; log in.
    ``valid``        a read succeeded just now.
    ``invalid``      Kite rejected the token; log in again.
    ``unreachable``  the call failed for a reason that is not authentication.
                     Reported as its own state because telling an operator to
                     re-login when the network is down sends them to fix the
                     wrong thing.
    """
    if not load_session():
        return {"state": "no_session", "checked_at": None, "error": None}

    now = time.time()
    with _TOKEN_PROBE_LOCK:
        cached = dict(_TOKEN_PROBE)
    if not force and cached["state"] != "unknown" and (now - cached["at"]) < max_age_sec:
        return {k: cached[k] for k in ("state", "checked_at", "error")}

    state, error = "valid", None
    try:
        # Deliberately routed through kite_client.kite_read_client rather than
        # building a client here: that accessor is one of the three the unit
        # suite's offline guard patches, so this probe is blocked in tests for
        # free. Reaching past it would open a live path the guard cannot see —
        # and widening the guard instead would break the tests that exist to
        # verify read_only_kite_client's own memoisation.
        from kite_client import kite_read_client

        # profile() is the cheapest authenticated read Kite offers and needs no
        # instrument tokens, so it cannot fail for a reason unrelated to auth.
        kite_read_client().profile()
    except Exception as exc:  # noqa: BLE001 — every failure mode is reported, none raised
        error = str(exc)[:200]
        state = "invalid" if _looks_like_auth_error(error) else "unreachable"

    result = {
        "state": state,
        "checked_at": datetime.now(tz=IST).isoformat(timespec="seconds"),
        "error": error,
    }
    with _TOKEN_PROBE_LOCK:
        _TOKEN_PROBE.update({**result, "at": now})
    return result


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
