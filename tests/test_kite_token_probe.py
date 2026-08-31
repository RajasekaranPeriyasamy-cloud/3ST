"""/health must report a token that WORKS, not a session file that exists.

On 2026-08-30 ``kite_authenticated`` read true while every Kite read failed with
``Incorrect `api_key` or `access_token```. That flag is the first thing anyone
checks on a quiet morning, and it was saying the opposite of the truth.

The distinction these tests protect is between a *rejected* token and an
*unreachable* API. Collapsing them would tell an operator to re-login when the
real problem is DNS, which wastes a trading morning fixing the wrong thing.
"""

from __future__ import annotations

import pytest

import kite_auth


@pytest.fixture(autouse=True)
def _fresh_probe_cache(monkeypatch: pytest.MonkeyPatch):
    """The probe caches across calls; every test starts from cold."""
    monkeypatch.setattr(
        kite_auth, "_TOKEN_PROBE",
        {"state": "unknown", "checked_at": None, "error": None, "at": 0.0},
    )


def _with_session(monkeypatch, client):
    """Stub the accessor the probe actually uses.

    It imports ``kite_client.kite_read_client`` inside the function precisely so
    the offline guard can patch it, which means the stub belongs there too — not
    on ``kite_auth.read_only_kite_client``, which the probe no longer touches.
    """
    import kite_client

    monkeypatch.setattr(kite_auth, "load_session", lambda: {"access_token": "t"})
    monkeypatch.setattr(kite_client, "kite_read_client", lambda: client)


class _Client:
    def __init__(self, error: Exception | None = None):
        self._error = error
        self.calls = 0

    def profile(self):
        self.calls += 1
        if self._error:
            raise self._error
        return {"user_id": "AB1234"}


def test_no_session_is_its_own_state(monkeypatch):
    monkeypatch.setattr(kite_auth, "load_session", lambda: None)
    assert kite_auth.token_probe()["state"] == "no_session"


def test_a_working_token_reads_valid(monkeypatch):
    _with_session(monkeypatch, _Client())
    out = kite_auth.token_probe()
    assert out["state"] == "valid" and out["error"] is None and out["checked_at"]


def test_a_rejected_token_reads_invalid(monkeypatch):
    _with_session(monkeypatch, _Client(RuntimeError("Incorrect `api_key` or `access_token`.")))
    assert kite_auth.token_probe()["state"] == "invalid"


@pytest.mark.parametrize(
    "message",
    [
        "HTTPSConnectionPool(host='api.kite.trade', port=443): Max retries exceeded",
        "[Errno 11001] getaddrinfo failed",
        "Read timed out.",
        "502 Bad Gateway",
    ],
)
def test_an_unreachable_api_is_not_reported_as_an_expired_token(monkeypatch, message):
    """"Log in again" is the wrong instruction when the network is down."""
    _with_session(monkeypatch, _Client(RuntimeError(message)))
    out = kite_auth.token_probe()
    assert out["state"] == "unreachable"
    assert out["error"]


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Incorrect `api_key` or `access_token`.", True),
        ("TokenException: token expired", True),
        ("Token is expired", True),
        ("Max retries exceeded with url", False),
        ("getaddrinfo failed", False),
        ("", False),
    ],
)
def test_auth_error_detection(message, expected):
    assert kite_auth._looks_like_auth_error(message) is expected


def test_the_probe_is_cached_so_health_polling_does_not_hammer_kite(monkeypatch):
    """/health is polled continuously; a Kite call per poll would burn rate
    limit to re-learn a fact that changes about once a day."""
    client = _Client()
    _with_session(monkeypatch, client)
    for _ in range(5):
        kite_auth.token_probe()
    assert client.calls == 1


def test_force_bypasses_the_cache(monkeypatch):
    client = _Client()
    _with_session(monkeypatch, client)
    kite_auth.token_probe()
    kite_auth.token_probe(force=True)
    assert client.calls == 2


def test_health_flag_goes_false_only_when_the_token_is_rejected(monkeypatch):
    """The fix, stated as one assertion: a stored session is not enough."""
    from api import main as api_main

    monkeypatch.setattr(api_main, "session_status", lambda: {"authenticated": True})

    monkeypatch.setattr(api_main, "token_probe", lambda: {"state": "valid"})
    assert api_main._kite_authenticated_verified() is True

    monkeypatch.setattr(api_main, "token_probe", lambda: {"state": "invalid"})
    assert api_main._kite_authenticated_verified() is False


def test_health_flag_holds_when_the_probe_cannot_reach_kite(monkeypatch):
    """Unreachable is not disproof. Flipping the flag on a network blip would
    send the operator to re-login for nothing."""
    from api import main as api_main

    monkeypatch.setattr(api_main, "session_status", lambda: {"authenticated": True})
    monkeypatch.setattr(api_main, "token_probe", lambda: {"state": "unreachable"})
    assert api_main._kite_authenticated_verified() is True


def test_health_flag_is_false_without_a_session(monkeypatch):
    from api import main as api_main

    monkeypatch.setattr(api_main, "session_status", lambda: {"authenticated": False})
    monkeypatch.setattr(api_main, "token_probe", lambda: {"state": "no_session"})
    assert api_main._kite_authenticated_verified() is False
