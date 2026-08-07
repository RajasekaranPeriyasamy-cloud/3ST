"""Firstock API client — login + candles via whitelisted static IP proxy.

Login contract matches https://firstock.in/api/docs/login/
POST https://api.firstock.in/V1/login
Body: userId, password (SHA256), TOTP, vendorCode, apiKey
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import pyotp
import requests

from config import TIMEFRAMES
from settings import env, firstock_credentials, proxy_config, proxy_ready

BASE_URL = "https://api.firstock.in/V1"


def _proxies() -> dict | None:
    return proxy_config()


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Accept": "application/json"})
    proxies = _proxies()
    if proxies:
        s.proxies.update(proxies)
        # Prefer explicit proxy over any ambient env proxy
        s.trust_env = False
    return s


def _sha256(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _prepare_password(password: str) -> str:
    """
    Firstock expects SHA256 hex of the plain password.
    If .env already stores a 64-char hex digest, do not hash again.
    Docs: https://firstock.in/api/docs/login/
    """
    p = (password or "").strip()
    if len(p) == 64 and all(c in "0123456789abcdefABCDEF" for c in p):
        return p.lower()
    return _sha256(p)


def generate_totp(secret: str | None = None) -> str:
    raw = (secret or firstock_credentials()["totp_secret"] or "").strip().replace(" ", "")
    if not raw:
        raise RuntimeError("FIRSTOCK_TOTP_SECRET is empty. Add the 2FA secret (not the 6-digit code) to .env")
    # Allow otpauth:// URI pasted from authenticator setup
    if raw.lower().startswith("otpauth://"):
        totp = pyotp.parse_uri(raw)
        return totp.now()
    try:
        return pyotp.TOTP(raw).now()
    except Exception as e:
        raise RuntimeError(
            "Invalid FIRSTOCK_TOTP_SECRET. Use the Base32 secret from 2FA setup "
            f"(not the 6-digit code). Detail: {e}"
        ) from e


def _normalize_vendor_code(user_id: str, vendor_code: str) -> str:
    """Vendor codes are often USERID_API — keep user value, only strip whitespace."""
    return (vendor_code or "").strip()


def _http_error_detail(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except Exception:
        body = (resp.text or "")[:500]
    return f"{resp.status_code} {resp.reason} — {body}"


def _raise_if_failed(payload: Any, context: str) -> dict:
    if payload is None:
        raise RuntimeError(f"{context}: empty response")
    if not isinstance(payload, dict):
        raise RuntimeError(f"{context}: unexpected response {type(payload)}")
    status = str(payload.get("status", "")).lower()
    if status in {"failed", "error", "failure"}:
        err = payload.get("error") or payload.get("message") or payload
        raise RuntimeError(f"{context} failed: {err}")
    return payload


def check_egress_ip() -> dict:
    """Return public IP as seen by the internet (through proxy if configured)."""
    proxies = _proxies()
    with _session() as sess:
        r = sess.get("https://api.ipify.org?format=json", timeout=30, proxies=proxies)
        r.raise_for_status()
        data = r.json()
    return {
        "ip": data.get("ip"),
        "proxied": proxies is not None,
        "proxy_host": env("STATICIP_HOST") if proxies else None,
    }


def _apply_proxy_env() -> dict[str, str]:
    """Point requests (and Firstock SDK) through staticip.in via HTTP(S)_PROXY."""
    proxies = _proxies()
    if not proxies:
        raise RuntimeError(
            "STATICIP proxy is not configured. Set STATICIP_HOST/PORT/USER/PASSWORD in .env."
        )
    # Preserve previous values so we can restore after the call
    prev = {
        "HTTP_PROXY": os.environ.get("HTTP_PROXY"),
        "HTTPS_PROXY": os.environ.get("HTTPS_PROXY"),
        "http_proxy": os.environ.get("http_proxy"),
        "https_proxy": os.environ.get("https_proxy"),
    }
    os.environ["HTTP_PROXY"] = proxies["http"]
    os.environ["HTTPS_PROXY"] = proxies["https"]
    os.environ["http_proxy"] = proxies["http"]
    os.environ["https_proxy"] = proxies["https"]
    return prev


def _restore_proxy_env(prev: dict[str, str | None]) -> None:
    for key, value in prev.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def login(
    user_id: str | None = None,
    password: str | None = None,
    totp: str | None = None,
    vendor_code: str | None = None,
    api_key: str | None = None,
    use_env: bool = True,
) -> dict:
    """
    Authenticate with Firstock using the official SDK path
    (https://firstock.in/api/docs/login/).
    Password is passed plain — SDK SHA256-hashes it. TOTP is the 6-digit UI code.
    """
    from firstock import firstock as fs

    creds = firstock_credentials() if use_env else {}
    uid = (user_id or creds.get("user_id") or "").strip()
    pwd = (password if password is not None else creds.get("password", "")).strip()
    vcode = _normalize_vendor_code(uid, vendor_code or creds.get("vendor_code") or "")
    akey = (api_key or creds.get("api_key") or "").strip()
    otp = (totp or "").strip()
    if not otp:
        otp = generate_totp(creds.get("totp_secret"))

    if not all([uid, pwd, vcode, akey, otp]):
        raise RuntimeError("Missing Firstock credentials. Fill .env or the login form.")

    if len(otp) != 6 or not otp.isdigit():
        raise RuntimeError(f"TOTP must be a 6-digit code (got length {len(otp)}).")

    if not proxy_ready():
        raise RuntimeError(
            "STATICIP proxy is not configured. Firstock returns 403 Forbidden when "
            "requests come from a non-whitelisted IP. Set STATICIP_HOST, STATICIP_PORT, "
            "STATICIP_USER, STATICIP_PASSWORD in .env (egress should be 165.101.250.152)."
        )

    # If .env accidentally stores a SHA256 digest, reject — SDK will hash again
    if len(pwd) == 64 and all(c in "0123456789abcdefABCDEF" for c in pwd):
        raise RuntimeError(
            "FIRSTOCK_PASSWORD looks like a SHA256 hash. Put the plain password in .env; "
            "the Firstock SDK hashes it automatically."
        )

    prev_proxy = _apply_proxy_env()
    try:
        # Official SDK: firstock.login(userId, password, TOTP, vendorCode, apiKey)
        # https://firstock.in/api/docs/login/
        result = fs.login(
            userId=uid,
            password=pwd,
            TOTP=otp,
            vendorCode=vcode,
            apiKey=akey,
        )
    except Exception as e:
        raise RuntimeError(f"Firstock SDK login raised: {e}") from e
    finally:
        _restore_proxy_env(prev_proxy)

    if result is None:
        raise RuntimeError("Firstock SDK login returned None.")

    if isinstance(result, dict):
        status = str(result.get("status", "")).lower()
        if status in {"failed", "error", "failure"}:
            err = result.get("error") or result.get("message") or result
            msg = str(err)
            hint = ""
            if "invalid character" in msg.lower() or "decode" in msg.lower():
                hint = (
                    "\n\nThis usually means wrong password, wrong API key, wrong vendor code, "
                    "or an expired/incorrect TOTP — Firstock's upstream returns a non-JSON body."
                )
            raise RuntimeError(
                f"Firstock login failed: {err}{hint}\n"
                "Confirm .env values against Key Generation, and enter a fresh 6-digit TOTP."
            )
        data = result.get("data") if isinstance(result.get("data"), dict) else result
    else:
        data = result

    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected login response: {result}")

    jkey = data.get("jKey") or data.get("jkey") or data.get("susertoken")
    if not jkey:
        raise RuntimeError(f"Login OK but no session token (jKey/susertoken). Response: {result}")

    return {
        "userId": data.get("userId") or data.get("actid") or uid,
        "jKey": jkey,
        "raw": data,
        "proxied": True,
        "via": "firstock.sdk",
    }


def _fmt_ts(dt: datetime) -> str:
    # Firstock docs/examples: "09:15:00 23-04-2025" → HH:MM:SS DD-MM-YYYY
    return dt.strftime("%H:%M:%S %d-%m-%Y")


def _parse_candle_times(values: pd.Series) -> pd.Series:
    """
    Parse Firstock candle timestamps.
    Common forms:
      - 2025-02-10T09:15:00          (ISO)
      - 09:15:00 15-01-2024          (HH:MM:SS DD-MM-YYYY)
      - 15-01-2024 09:15:00
    """
    sample = str(values.dropna().iloc[0]) if values.notna().any() else ""
    formats = (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%H:%M:%S %d-%m-%Y",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y",
    )
    for fmt in formats:
        try:
            return pd.to_datetime(values, format=fmt)
        except (ValueError, TypeError):
            continue
    # Fallback: day-first (Indian DD-MM), never assume US MM-DD
    try:
        return pd.to_datetime(values, dayfirst=True, format="mixed")
    except (ValueError, TypeError) as e:
        raise RuntimeError(
            f"Cannot parse candle time '{sample}'. Expected ISO or HH:MM:SS DD-MM-YYYY. ({e})"
        ) from e


def _candles_to_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi"])

    df = pd.DataFrame(rows)
    df = df.rename(columns={c: c.lower() for c in df.columns})

    if "time" in df.columns:
        df["datetime"] = _parse_candle_times(df["time"])
    elif "epochtime" in df.columns:
        df["datetime"] = pd.to_datetime(df["epochtime"], unit="s")
    else:
        raise RuntimeError(f"Candle payload missing time. Columns: {list(df.columns)}")

    for col in ("open", "high", "low", "close", "volume", "oi"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
    return df.set_index("datetime")[["open", "high", "low", "close", "volume", "oi"]]


def _extract_rows(payload: dict) -> list[dict]:
    data = payload.get("data", [])
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        return data["data"]
    return []


def fetch_candles(
    user_id: str,
    jkey: str,
    exchange: str,
    trading_symbol: str,
    start: date | datetime,
    end: date | datetime,
    timeframe: str,
    chunk_days: int = 5,
    pause_sec: float = 0.3,
) -> pd.DataFrame:
    """Fetch OHLC via /timePriceSeries through the static IP proxy."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unsupported timeframe '{timeframe}'. Use {list(TIMEFRAMES)}")

    interval = TIMEFRAMES[timeframe]

    if isinstance(start, date) and not isinstance(start, datetime):
        start_dt = datetime.combine(start, datetime.min.time()).replace(hour=9, minute=0)
    else:
        start_dt = start

    if isinstance(end, date) and not isinstance(end, datetime):
        end_dt = datetime.combine(end, datetime.min.time()).replace(hour=15, minute=40)
    else:
        end_dt = end

    if start_dt >= end_dt:
        raise ValueError("Start must be before end.")

    frames: list[pd.DataFrame] = []
    cursor = start_dt
    proxies = _proxies()

    with _session() as sess:
        while cursor < end_dt:
            chunk_end = min(cursor + timedelta(days=chunk_days), end_dt)
            req_start = cursor.replace(hour=9, minute=0, second=0, microsecond=0)
            req_end = chunk_end.replace(hour=15, minute=40, second=0, microsecond=0)
            if req_end <= req_start:
                req_end = chunk_end

            body = {
                "userId": user_id,
                "jKey": jkey,
                "exchange": exchange,
                "tradingSymbol": trading_symbol,
                "startTime": _fmt_ts(req_start),
                "endTime": _fmt_ts(req_end),
                "interval": interval,
            }
            r = sess.post(f"{BASE_URL}/timePriceSeries", json=body, timeout=90, proxies=proxies)
            if not r.ok:
                raise RuntimeError(f"timePriceSeries HTTP error: {_http_error_detail(r)}")
            payload = _raise_if_failed(r.json(), "timePriceSeries")
            rows = _extract_rows(payload)
            if rows:
                frames.append(_candles_to_df(rows))

            cursor = chunk_end + timedelta(seconds=1)
            if pause_sec > 0:
                time.sleep(pause_sec)

    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "oi"])

    out = pd.concat(frames).sort_index()
    return out[~out.index.duplicated(keep="last")]


def proxy_status_text() -> str:
    if not proxy_ready():
        return "Proxy OFF — fill STATICIP_USER + STATICIP_PASSWORD in .env (403 without whitelist IP)"
    host = env("STATICIP_HOST")
    port = env("STATICIP_PORT", "443")
    return f"Proxy ON → {host}:{port}"


def mask_secret(value: str, keep: int = 2) -> str:
    if not value:
        return "(empty)"
    if len(value) <= keep * 2:
        return "*" * len(value)
    return value[:keep] + "*" * (len(value) - keep * 2) + value[-keep:]
