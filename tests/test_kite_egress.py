"""Kite egress selection — local IPv6 bind vs staticip proxy."""

from __future__ import annotations

from unittest.mock import patch

from kite_auth import kite_egress_plan


def test_prefers_local_bind_when_whitelisted_ipv6_available():
    with (
        patch("kite_auth.kite_allowed_egress_ip", return_value="2409:40f4:400e:6518:8947:4b08:26cd:c16c"),
        patch("kite_auth._resolve_bind_ipv6", return_value="2409:40f4:400e:6518:8947:4b08:26cd:c16c"),
        patch("kite_auth.kite_use_staticip_proxy", return_value=True),
        patch("kite_auth.kite_proxy_config", return_value={"http": "http://x", "https": "http://x"}),
    ):
        proxies, bind_ipv6, mode = kite_egress_plan()
    assert mode == "local_bind"
    assert bind_ipv6 == "2409:40f4:400e:6518:8947:4b08:26cd:c16c"
    assert proxies == {}


def test_prefers_same_prefix_ipv6_when_exact_whitelist_missing():
    with (
        patch("kite_auth.kite_allowed_egress_ip", return_value="2409:40f4:400e:6518:8947:4b08:26cd:c16c"),
        patch(
            "kite_auth._resolve_bind_ipv6",
            return_value="2409:40f4:400e:6518:957b:7993:497f:f50c",
        ),
        patch("kite_auth.kite_use_staticip_proxy", return_value=True),
    ):
        proxies, bind_ipv6, mode = kite_egress_plan()
    assert mode == "local_bind"
    assert bind_ipv6 == "2409:40f4:400e:6518:957b:7993:497f:f50c"


def test_falls_back_to_staticip_when_local_bind_unavailable():
    with (
        patch("kite_auth.kite_allowed_egress_ip", return_value="2409:40f4:400e:6518:8947:4b08:26cd:c16c"),
        patch("kite_auth._resolve_bind_ipv6", return_value=None),
        patch("kite_auth.kite_use_staticip_proxy", return_value=True),
        patch(
            "kite_auth.kite_proxy_config",
            return_value={"http": "http://u:p@host:443", "https": "http://u:p@host:443"},
        ),
    ):
        proxies, bind_ipv6, mode = kite_egress_plan()
    assert mode == "staticip_proxy"
    assert bind_ipv6 is None
    assert "host:443" in proxies["https"]
