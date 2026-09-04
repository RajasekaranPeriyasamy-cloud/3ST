"""The live ``data/`` directory must stay out of reach of the unit suite.

Two tests in ``test_mcx_rolling_straddle.py`` used to rewrite the live
rolling-straddle state and log on every run (2026-08-27). Neither one mentioned
a store: they called ``rolling_straddle._ensure_state_underlying``, which calls
``clear_spot_state_for_underlying`` -> ``save_state`` + ``append_log`` two
frames down. In isolation they passed; with the desk running they raced the
live runner for the same file and failed.

``tests/conftest.py`` closes that off two ways — it redirects the known store
constants (``isolate_real_data_writes``) and refuses any write that still lands
under the real ``data/`` (the guard armed in ``pytest_collection_finish``). Both are
easy to silently disable: rename a constant and the redirect skips it, and the
guard is a handful of monkeypatched builtins. These tests fail loudly instead.

The final backstop — asserting the live files are byte-identical after the whole
session — lives in ``conftest.real_store_files_untouched``; it cannot be a test
because it has to run last.
"""

from __future__ import annotations

import importlib
import json

import pytest

from settings import data_dir
from tests.conftest import _REDIRECTS, _WATCHED_FILES, RealDataWriteBlocked


def test_marker_opts_out(pytestconfig: pytest.Config) -> None:
    """The writes_real_data escape hatch is registered, so opting out is possible."""
    markers = pytestconfig.getini("markers")
    assert any(m.startswith("writes_real_data") for m in markers), (
        "writes_real_data marker missing from pyproject.toml — a test that "
        "genuinely needs to write into data/ would have no way to opt out."
    )


@pytest.mark.parametrize(
    "module_path,attr",
    [(m, a) for m, a, _ in _REDIRECTS if a is not None],
    ids=[f"{m.rsplit('.', 1)[-1]}.{a}" for m, a, _ in _REDIRECTS if a is not None],
)
def test_every_redirected_constant_still_exists(module_path: str, attr: str) -> None:
    """A renamed store constant must not silently drop out of the redirect list.

    ``isolate_real_data_writes`` skips names it cannot find — deliberately, so a
    module that fails to import does not break every test. That forgiveness is
    what makes this check necessary.
    """
    module = importlib.import_module(module_path)
    assert hasattr(module, attr), (
        f"{module_path} no longer defines {attr} — tests/conftest.py's _REDIRECTS "
        "entry is a no-op and that store is writing to the real data/ dir again."
    )


@pytest.mark.parametrize(
    "module_path",
    [m for m, a, _ in _REDIRECTS if a is None],
)
def test_call_time_stores_still_resolve_data_dir(module_path: str) -> None:
    """Stores redirected via their own ``data_dir`` must still hold that name."""
    module = importlib.import_module(module_path)
    assert hasattr(module, "data_dir"), (
        f"{module_path} no longer imports data_dir — tests/conftest.py redirects it "
        "by that name, so the redirect is now a no-op."
    )


@pytest.mark.parametrize("filename", _WATCHED_FILES)
def test_writing_a_watched_file_is_refused(filename: str) -> None:
    """The guard raises rather than letting a write through."""
    target = data_dir() / filename
    with pytest.raises(RealDataWriteBlocked, match="must not"):
        target.write_text("clobbered", encoding="utf-8")
    with pytest.raises(RealDataWriteBlocked, match="must not"):
        with open(target, "a", encoding="utf-8"):
            pass


def test_a_refused_write_never_implicates_the_suite() -> None:
    """The refusal above must not make the session backstop blame this test.

    ``_refuse`` raises, so the bytes never land. Attributing a changed file to a
    refused attempt made ``real_store_files_untouched`` fail every time the desk
    ran alongside pytest: this test deliberately attempts a write to a watched
    file, the desk's runner independently rewrote that same file seconds later,
    and the backstop blamed the test for the runner's bytes.
    """
    from tests.conftest import _BLOCKED, _WROTE, suite_attributed_changes

    filename = _WATCHED_FILES[0]
    target = data_dir() / filename
    with pytest.raises(RealDataWriteBlocked):
        target.write_text("clobbered", encoding="utf-8")

    # The attempt is recorded for diagnostics...
    assert filename in _BLOCKED
    # ...but it is not a completed write, so it cannot explain a changed file.
    assert filename not in _WROTE
    assert suite_attributed_changes([filename]) == []


def test_attribution_reports_a_write_that_actually_landed() -> None:
    """The backstop must still fire for a genuine completed write."""
    from tests.conftest import _WROTE, suite_attributed_changes

    filename = "___attribution_probe.json"
    _WROTE.setdefault(filename, set()).add("tests/probe.py::test_probe")
    try:
        assert suite_attributed_changes([filename]) == [filename]
    finally:
        _WROTE.pop(filename, None)


def test_reading_the_real_data_dir_is_still_allowed() -> None:
    """The guard is about writes only — read-only fixtures must keep working."""
    seed = data_dir() / "fpi_sectors_seed.json"
    if not seed.exists():  # gitignored tree may be sparse on a fresh clone
        pytest.skip("fpi_sectors_seed.json not present")
    assert isinstance(json.loads(seed.read_text(encoding="utf-8")), dict)


def test_ensure_state_underlying_does_not_touch_the_live_store(monkeypatch) -> None:
    """The original bug, pinned.

    ``_ensure_state_underlying`` writes through two layers of indirection. If the
    redirect is ever removed, this fails with RealDataWriteBlocked instead of
    quietly rewriting data/rolling_straddle_state.json.
    """
    from execution import rolling_straddle as rs
    from execution import rolling_straddle_store as store

    monkeypatch.setattr(rs, "get_index_spot", lambda _u: 7780.0)
    live = data_dir() / "rolling_straddle_state.json"
    before = live.read_bytes() if live.exists() else None

    rs._ensure_state_underlying({"underlying": "CRUDEOIL"}, {"last_spot": 24035.15})

    assert store.STATE_FILE != live
    assert store.STATE_FILE.exists(), "the write should have landed in the sandbox"
    assert (live.read_bytes() if live.exists() else None) == before
