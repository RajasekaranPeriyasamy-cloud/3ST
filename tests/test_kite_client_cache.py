"""Memoised read-only Kite client — identity, invalidation, and egress isolation.

The read client exists purely to stop 3ST rebuilding a KiteConnect (and with it
a TLS connection + CA bundle load) on every market-data call. It must never
become an order path: reads are direct-egress by design, so a cached read
client is not bound to the IP whitelisted on developers.kite.trade.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import kite_auth


@pytest.fixture(autouse=True)
def _fake_creds():
    with (
        patch("kite_auth.kite_ready", return_value=True),
        patch("kite_auth.kite_credentials", return_value={"api_key": "k", "api_secret": "s"}),
        patch("kite_auth.load_dotenv", lambda *a, **k: None),
    ):
        kite_auth.reset_kite_client_cache()
        yield
        kite_auth.reset_kite_client_cache()


def test_read_client_is_memoised():
    first = kite_auth.read_only_kite_client()
    assert kite_auth.read_only_kite_client() is first


def test_reset_forces_a_new_client():
    first = kite_auth.read_only_kite_client()
    kite_auth.reset_kite_client_cache()
    assert kite_auth.read_only_kite_client() is not first


def test_api_key_change_forces_a_new_client():
    first = kite_auth.read_only_kite_client()
    with patch("kite_auth.kite_credentials", return_value={"api_key": "other", "api_secret": "s"}):
        assert kite_auth.read_only_kite_client() is not first


def test_relogin_invalidates_the_cache(tmp_path):
    first = kite_auth.read_only_kite_client()
    with patch.object(kite_auth, "SESSION_FILE", tmp_path / "kite_session.json"):
        kite_auth.save_session({"access_token": "t", "user_id": "u"})
    assert kite_auth.read_only_kite_client() is not first


def test_logout_invalidates_the_cache(tmp_path):
    first = kite_auth.read_only_kite_client()
    with patch.object(kite_auth, "SESSION_FILE", tmp_path / "absent.json"):
        kite_auth.clear_session()
    assert kite_auth.read_only_kite_client() is not first


def test_read_client_never_carries_order_egress():
    """Even with the staticip proxy selected, the read client stays direct.

    If this ever starts returning a proxied/bound client, the cached object
    could be reused for order placement and silently bypass the egress the
    Kite whitelist expects.
    """
    proxies = {"http": "http://u:p@host:443", "https": "http://u:p@host:443"}
    with (
        patch("kite_auth.kite_egress_plan", return_value=(proxies, None, "staticip_proxy")),
        patch("kite_auth.kite_use_staticip_proxy", return_value=True),
        patch("kite_auth.kite_proxy_config", return_value=proxies),
    ):
        client = kite_auth.read_only_kite_client()

    assert client.proxies == {}
    assert client.reqsession.proxies == {}


# _kite_direct_client() itself is not exercised here: tests/conftest.py patches
# that accessor to keep the suite offline, and tests/test_offline_guard.py already
# fails loudly if it is renamed. Its only added behaviour is calling
# read_only_kite_client() and re-applying the token, both covered above.
