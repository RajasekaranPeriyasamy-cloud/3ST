"""The offline guard must not silently become a no-op.

``tests/conftest.py`` blocks live Kite by patching client accessors *by name*,
skipping any that no longer exist. That is deliberately forgiving — but it means
renaming an accessor in ``kite_client.py`` would turn the guard off without
breaking anything visibly, and the suite would quietly go back to issuing real
broker requests (slow, flaky, and impossible in CI).

These tests fail loudly in that case.
"""

from __future__ import annotations

import pytest

from tests.conftest import _CLIENT_ACCESSORS


def test_guard_covers_every_client_accessor() -> None:
    """Each guarded name still exists on kite_client."""
    import kite_client

    missing = [name for name in _CLIENT_ACCESSORS if not hasattr(kite_client, name)]
    assert not missing, (
        f"kite_client no longer defines {missing} — the offline guard in "
        "tests/conftest.py is silently skipping them. Update _CLIENT_ACCESSORS."
    )


@pytest.mark.parametrize("accessor", _CLIENT_ACCESSORS)
def test_client_accessor_is_blocked(accessor: str) -> None:
    """Calling one raises instead of opening a real session."""
    import kite_client

    with pytest.raises(RuntimeError, match="offline"):
        getattr(kite_client, accessor)()


def test_marker_opts_out(pytestconfig: pytest.Config) -> None:
    """The live_kite escape hatch is registered, so opting out is possible."""
    markers = pytestconfig.getini("markers")
    assert any(m.startswith("live_kite") for m in markers), (
        "live_kite marker missing from pyproject.toml — tests that genuinely "
        "need a broker session would have no way to opt out."
    )
