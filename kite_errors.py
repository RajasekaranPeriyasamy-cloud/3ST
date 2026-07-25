"""User-facing Kite API error hints."""

from __future__ import annotations

import re
import sys


def friendly_kite_message(raw: str) -> str:
    msg = str(raw or "").strip()
    lower = msg.lower()

    if "not allowed to place orders" in lower and "ip" in lower:
        ip_match = re.search(
            r"IP\s*\(([0-9a-fA-F:.]+)\)",
            msg,
        )
        ip = ip_match.group(1) if ip_match else "your current IP"
        from settings import kite_allowed_egress_ip, kite_use_staticip_proxy

        allowed = kite_allowed_egress_ip()
        from kite_auth import kite_egress_status

        egress = kite_egress_status()
        if egress.get("mode") == "staticip_proxy":
            local = egress.get("local_ipv6_same_prefix") or []
            hint = (
                f" Set KITE_USE_STATICIP_PROXY=0 and whitelist a current local IPv6 on "
                f"developers.kite.trade — candidates: {', '.join(local) or 'none found'}."
                if local
                else " Contact staticip.in — their egress IP must be on your Kite whitelist."
            )
            return (
                f"Kite rejected the order: saw IP {ip}, but your app whitelist is fixed to "
                f"{allowed or 'your whitelisted IP'}. "
                "3ST fell back to staticip.in because the whitelisted IPv6 is not bindable on this PC."
                f"{hint}"
            )
        return (
            f"Kite rejected the order: IP {ip} is not whitelisted for your Kite app. "
            "Fix: open https://developers.kite.trade → Apps → your app → "
            f"Allowed IPs → add {ip} (or your public IPv4). "
            "If Zerodha gave you a static IP, set KITE_USE_STATICIP_PROXY=1 and STATICIP_* in .env. "
            "Until fixed, use Paper Trade mode to test the workflow."
        )

    if "market protection" in lower or "market orders are blocked" in lower:
        return (
            "Kite requires market protection on MARKET orders (especially MCX commodity options). "
            "This is now fixed in 3ST — restart the API and retry BUY/SELL. "
            "Optional: set KITE_MARKET_PROTECTION=-1 in .env (auto) or 2 for 2% band."
        )

    if "insufficient" in lower and ("margin" in lower or "fund" in lower):
        return f"Insufficient margin/funds on Kite: {msg}"

    if ("timed out" in lower or "connecttimeouterror" in lower) and "bind" in lower:
        from kite_auth import kite_egress_status

        egress = kite_egress_status()
        return (
            "Kite order egress timed out via whitelisted IPv6 bind. "
            f"Bind: {egress.get('bind_ipv6') or egress.get('allowed_egress_ip')}. "
            "3ST retries via direct connection automatically on Windows. "
            "If the next error shows a different IP, add that address on developers.kite.trade."
        )

    if "getaddrinfo failed" in lower or "failed to resolve" in lower or "name resolution" in lower:
        from kite_auth import kite_egress_status

        egress = kite_egress_status()
        if egress.get("mode") == "local_bind" and sys.platform == "win32":
            return (
                "Kite order client failed on Windows IPv6 bind (DNS looks fine in nslookup — "
                "this is a known urllib3 issue). Restart the API after updating 3ST. "
                f"Whitelisted bind: {egress.get('bind_ipv6') or egress.get('allowed_egress_ip')}. "
                "If orders still fail, add today's rotating IPv6 from the error to developers.kite.trade, "
                "or use KITE_USE_STATICIP_PROXY=1. Market data uses a direct connection and is unaffected."
            )
        return (
            "Kite API unreachable (DNS/network) — cannot fetch quotes or place orders. "
            "Check internet, try nslookup api.kite.trade, disable bad VPN/proxy, or switch DNS (8.8.8.8). "
            "Use Paper mode until api.kite.trade resolves."
        )

    if "incorrect" in lower and ("access_token" in lower or "api_key" in lower):
        from settings import desk_ui_url

        return (
            "Kite session expired or invalid — market data and orders need a fresh login. "
            f"Open {desk_ui_url()}/login and click Login with Zerodha."
        )

    return msg


def friendly_auth_error(raw: str) -> str:
    """Login / token-exchange errors — clearer than raw urllib traces."""
    msg = str(raw or "").strip()
    lower = msg.lower()
    if "getaddrinfo failed" in lower or "failed to resolve" in lower or "name resolution" in lower:
        from settings import desk_ui_url

        return (
            "Could not reach api.kite.trade (DNS or network). "
            "Check your internet connection, disable VPN/proxy if enabled, wait 30 seconds, "
            f"then open {desk_ui_url()}/login and click Login with Zerodha again. "
            "OAuth tokens are one-time — a failed attempt needs a fresh login."
        )
    if "token" in lower and ("invalid" in lower or "expired" in lower):
        return (
            "Kite login token expired or already used. "
            "Open the desk login page and click Login with Zerodha once more."
        )
    return friendly_kite_message(msg)
