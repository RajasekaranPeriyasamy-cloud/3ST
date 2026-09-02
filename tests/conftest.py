"""Shared pytest fixtures.

Two guards every test gets for free, both against the unit suite reaching out
and touching live state: no Kite, and no writes into the real ``data/`` tree.

**The unit suite must not talk to Kite.** Live calls made it slow, flaky and
impossible to run in CI without a broker session: one gamma-snapshot test
walked a whole option chain and issued ~80 per-strike 60min historical
requests (``gamma_density`` -> ``oi_movers.ensure_session_open_oi`` ->
``fetch_historical_by_token``), which took 80+ seconds against a throttled
Kite and produced different data every run.

Individual modules do ``from kite_client import fetch_historical_by_token``
at import time, so patching ``kite_client.fetch_historical_by_token`` would
miss them — each module holds its own reference. Instead we block the two
client accessors every market-data path resolves *at call time*:
``_kite_direct_client`` and ``get_kite_client``. Nothing can reach the
network without one of them.

Tests that genuinely need a live broker can opt out with
``@pytest.mark.live_kite`` (none do today).

**The unit suite must not write into the real ``data/`` directory either.**
Same binding trap, different blast radius. Stores do
``from settings import data_dir`` at import time and resolve their file
constants once, so ``monkeypatch.setattr("settings.data_dir", ...)`` leaves
them pointed at the live files — CLAUDE.md documents this, and it has bitten
twice. A theta-decay fixture appended 1,800 synthetic snapshots into the live
delta-velocity archive (2026-08-13). And the ``_ensure_state_underlying`` tests
in ``test_mcx_rolling_straddle.py`` rewrote the live rolling-straddle state and
log on every run (2026-08-27) — via ``clear_spot_state_for_underlying`` two
calls below the function under test, which is why nobody spotted the missing
patch. That one only *failed* with the desk running, because then the live
runner and the test raced for the same file; in isolation it passed while
quietly corrupting live state.

``isolate_real_data_writes`` redirects the known store paths at each store
module's *own* reference, and the call-level guard armed by
``pytest_collection_finish`` raises on any write that still lands under the real
``data/`` — so a store added later cannot silently join the list. Tests that
genuinely need to write there can opt out with
``@pytest.mark.writes_real_data`` (none do today).
"""

from __future__ import annotations

import builtins
import hashlib
import os
from pathlib import Path

import pytest

from settings import data_dir

# Every fetcher in kite_client.py obtains its client from one of these.
_CLIENT_ACCESSORS = ("_kite_direct_client", "get_kite_client", "kite_read_client")


class LiveKiteBlocked(RuntimeError):
    """Raised when a unit test tries to reach the broker."""


class RealDataWriteBlocked(RuntimeError):
    """Raised when a unit test tries to write into the live ``data/`` directory."""


@pytest.fixture(autouse=True)
def block_live_kite(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("live_kite"):
        return

    import kite_client

    def _blocked(*_a, **_k):
        raise LiveKiteBlocked(
            f"{request.node.name} tried to open a Kite client. The unit suite runs "
            "offline — stub the caller (see the `patched` fixture in "
            "tests/test_gamma_density.py) or mark the test @pytest.mark.live_kite."
        )

    for name in _CLIENT_ACCESSORS:
        if hasattr(kite_client, name):
            monkeypatch.setattr(kite_client, name, _blocked)


# ---------------------------------------------------------------------------
# Real data/ isolation
# ---------------------------------------------------------------------------

_REAL_DATA_DIR = os.path.normcase(os.path.abspath(str(data_dir())))

# Files whose contents the suite must leave byte-identical. Checked once per
# session (see `real_store_files_untouched`) as a backstop for any write that
# slips past the call-level guard below.
_WATCHED_FILES = (
    "rolling_straddle_state.json",
    "rolling_straddle_config.json",
    "rolling_straddle_log.json",
    "premium_book_state.json",
    "survivor_state.json",
    "wave_state.json",
    "arm_state.json",
    "risk_limits.json",
    "position_ledger.json",
    "watchlist.json",
    "cas_history.jsonl",
    "latency_log.jsonl",
    "oi_movers_prev_day_oi.json",
)

# (module path, attribute, filename under the sandbox) for every store constant
# that is a *write* target. ``attr=None`` means the module resolves ``data_dir()``
# at call time, so its own ``data_dir`` reference is what gets redirected.
#
# Read-only caches are deliberately absent: ``instruments.CACHE_FILE`` (tests read
# the real instrument dump) and ``kite_auth.SESSION_FILE`` (the one test that
# writes it patches its own reference, and Kite is blocked anyway).
#
# ``kite_auth.SESSION_KEY_FILE`` IS here, and is the reason this table cannot be
# assembled by reading the code for obvious writers: nothing writes it on a
# machine that already has one. CI, with no data/ directory, takes the create
# branch on the first save_session and blocks — a failure invisible to every
# developer.
_REDIRECTS: tuple[tuple[str, str | None, str | None], ...] = (
    ("execution.rolling_straddle_store", "CONFIG_FILE", "rolling_straddle_config.json"),
    ("execution.rolling_straddle_store", "STATE_FILE", "rolling_straddle_state.json"),
    ("execution.rolling_straddle_store", "LOG_FILE", "rolling_straddle_log.json"),
    ("execution.premium_book_store", "CONFIG_FILE", "premium_book_config.json"),
    ("execution.premium_book_store", "STATE_FILE", "premium_book_state.json"),
    ("execution.premium_book_store", "LOG_FILE", "premium_book_log.json"),
    ("execution.survivor_store", "CONFIG_FILE", "survivor_config.json"),
    ("execution.survivor_store", "STATE_FILE", "survivor_state.json"),
    ("execution.survivor_store", "LOG_FILE", "survivor_log.json"),
    ("execution.wave_store", "CONFIG_FILE", "wave_config.json"),
    ("execution.wave_store", "STATE_FILE", "wave_state.json"),
    ("execution.wave_store", "LOG_FILE", "wave_log.json"),
    ("execution.arming", "ARM_STATE_FILE", "arm_state.json"),
    ("execution.position_ledger", "LEDGER_FILE", "position_ledger.json"),
    ("execution.latency_log", None, None),
    ("risk.limits", "LIMITS_FILE", "risk_limits.json"),
    ("watchlist_store", "WATCHLIST_FILE", "watchlist.json"),
    ("selection_store", "SELECTION_FILE", "selection.json"),
    ("broker.paper_broker", "_PAPER_FILE", "paper_broker.json"),
    ("options.cas_history", None, None),
    ("options.oi_movers", "SESSION_FILE", "oi_movers_session_open.json"),
    ("options.oi_movers", "PREV_DAY_CACHE", "oi_movers_prev_day_oi.json"),
    ("options.oi_movers", "HISTORY_FILE", "oi_movers_history.json"),
    # kite_auth writes the Fernet key lazily, only when it does not already
    # exist — so a developer machine that has one never takes the write branch
    # and every test passes, while CI (no data/, no session) hits it on the
    # first save_session and blocks. Redirected here rather than patched in the
    # one test that tripped it, because anything touching kite_auth can reach
    # it, and a credential file is the last thing the suite should create.
    ("kite_auth", "SESSION_KEY_FILE", ".session_key"),
    ("options.oi_var_session", "SESSION_FILE", "oi_var_session_open.json"),
    ("options.oi_var_session", "HISTORY_FILE", "oi_var_history.json"),
    ("options.oi_var_store", "BASELINE_FILE", "oi_var_eod_baseline.json"),
    ("options.oi_tracker_store", "LOG_FILE", "oi_tracker_log.json"),
    ("options.gamma_density_history", "HISTORY_FILE", "gamma_density_history.json"),
    ("analysis.fpi_sectors", "CACHE_FILE", "fpi_sectors.json"),
    ("analysis.equity_report.store", "INDEX_FILE", "equity_reports.json"),
    ("analysis.equity_report.store", "BODY_DIR", "equity_reports"),
    ("analysis.equity_report.pins", "PINS_FILE", "equity_pins.json"),
    ("analysis.opt_arb.store", "CONFIG_FILE", "opt_arb_config.json"),
    ("analysis.news_desk.store", "ITEMS_FILE", "news_items.json"),
    ("analysis.news_desk.store", "CONFIG_FILE", "news_desk_config.json"),
    ("analysis.news_desk.tickers", "ALIAS_FILE", "news_aliases.json"),
)


def _under_real_data(target: object) -> bool:
    try:
        path = os.fspath(target)  # type: ignore[arg-type]
    except TypeError:
        return False  # a file descriptor, not a path
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    resolved = os.path.normcase(os.path.abspath(path))
    return resolved == _REAL_DATA_DIR or resolved.startswith(_REAL_DATA_DIR + os.sep)


def _is_write_mode(mode: object) -> bool:
    return any(flag in str(mode) for flag in ("w", "a", "x", "+"))


# Attempts the guard caught, as {filename: {test nodeid, ...}}. Consulted by the
# session-scoped check so a real change can be attributed to this suite rather
# than to the live desk running alongside it.
_BLOCKED: dict[str, set[str]] = {}
_GUARD: dict[str, object] = {"active": False, "node": "<collection>"}


def _refuse(kind: str, target: object) -> None:
    name = os.path.basename(os.fspath(target))  # type: ignore[arg-type]
    node = str(_GUARD["node"])
    _BLOCKED.setdefault(name, set()).add(node)
    raise RealDataWriteBlocked(
        f"{node} tried to {kind} the live file data/{name}. The unit suite must not "
        "write into the real data/ directory: a test that does races the live desk "
        "and corrupts trading state. Point the module under test's OWN reference at "
        'tmp_path, e.g. monkeypatch.setattr(store, "STATE_FILE", tmp_path / "state.json"). '
        "Patching settings.data_dir does NOT work: stores bind it at import time (see "
        "CLAUDE.md). Add the store to _REDIRECTS in tests/conftest.py if every test "
        "should get it for free, or mark the test @pytest.mark.writes_real_data."
    )


def pytest_configure(config: pytest.Config) -> None:
    """Wrap the write syscalls once; ``_GUARD["active"]`` decides when they bite.

    The wrappers stay installed for the whole session (uninstalling per test
    would race pytest's own IO) and are disarmed outside the test phase — see
    ``pytest_collection_finish``.
    """
    real_open, real_path_open = builtins.open, Path.open
    real_write_text, real_write_bytes = Path.write_text, Path.write_bytes
    real_unlink = Path.unlink
    real_replace, real_rename, real_remove = os.replace, os.rename, os.remove

    def _armed() -> bool:
        return bool(_GUARD["active"])

    def open_(file, mode="r", *a, **k):
        if _armed() and _is_write_mode(mode) and _under_real_data(file):
            _refuse(f"open({mode})", file)
        return real_open(file, mode, *a, **k)

    def path_open_(self, mode="r", *a, **k):
        if _armed() and _is_write_mode(mode) and _under_real_data(self):
            _refuse(f"open({mode})", self)
        return real_path_open(self, mode, *a, **k)

    def write_text_(self, *a, **k):
        if _armed() and _under_real_data(self):
            _refuse("write", self)
        return real_write_text(self, *a, **k)

    def write_bytes_(self, *a, **k):
        if _armed() and _under_real_data(self):
            _refuse("write", self)
        return real_write_bytes(self, *a, **k)

    def unlink_(self, *a, **k):
        if _armed() and _under_real_data(self):
            _refuse("delete", self)
        return real_unlink(self, *a, **k)

    def replace_(src, dst, *a, **k):
        if _armed() and _under_real_data(dst):
            _refuse("replace", dst)
        return real_replace(src, dst, *a, **k)

    def rename_(src, dst, *a, **k):
        if _armed() and _under_real_data(dst):
            _refuse("rename", dst)
        return real_rename(src, dst, *a, **k)

    def remove_(path, *a, **k):
        if _armed() and _under_real_data(path):
            _refuse("delete", path)
        return real_remove(path, *a, **k)

    builtins.open = open_
    Path.open = path_open_
    Path.write_text = write_text_
    Path.write_bytes = write_bytes_
    Path.unlink = unlink_
    os.replace = replace_
    os.rename = rename_
    os.remove = remove_

    config._3st_real_io = (  # type: ignore[attr-defined]
        real_open,
        real_path_open,
        real_write_text,
        real_write_bytes,
        real_unlink,
        real_replace,
        real_rename,
        real_remove,
    )


def pytest_unconfigure(config: pytest.Config) -> None:
    saved = getattr(config, "_3st_real_io", None)
    if not saved:
        return
    (
        builtins.open,
        Path.open,
        Path.write_text,
        Path.write_bytes,
        Path.unlink,
        os.replace,
        os.rename,
        os.remove,
    ) = saved


def pytest_collection_finish(session: pytest.Session) -> None:
    """Arm the guard once collection is done, and leave it armed.

    Collection stays unguarded on purpose: a couple of modules create ``data/``
    subdirectories at import time, and refusing that would fail collection
    rather than any individual test.

    Arming for the whole run rather than per test is deliberate. Disarming in
    ``pytest_runtest_teardown`` would open a hole: that hook fires *before*
    fixture finalizers, which is exactly when ``monkeypatch`` undoes the
    redirects — so a finalizer that writes would find the real paths restored
    and the guard already asleep.
    """
    _GUARD["active"] = True


def pytest_runtest_setup(item: pytest.Item) -> None:
    _GUARD["node"] = item.nodeid
    _GUARD["active"] = not item.get_closest_marker("writes_real_data")


def pytest_runtest_logfinish(nodeid: str, location: object) -> None:
    """Re-arm after a ``writes_real_data`` test, once its teardown is fully done."""
    _GUARD["active"] = True
    _GUARD["node"] = "<between tests>"


@pytest.fixture(autouse=True)
def isolate_real_data_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point every known JSON store at a per-test sandbox directory.

    Patches each store module's *own* reference, never ``settings.data_dir`` —
    stores resolve their file constants at import time, so the latter is a no-op
    (CLAUDE.md, "the same binding trap applies to settings.data_dir").

    A test that patches a constant itself still wins: its ``monkeypatch.setattr``
    runs after this fixture.
    """
    sandbox = tmp_path / "_data"
    sandbox.mkdir(exist_ok=True)

    for module_path, attr, filename in _REDIRECTS:
        try:
            module = __import__(module_path, fromlist=["_"])
        except Exception:  # optional dependency missing — nothing to redirect
            continue
        if attr is None:
            if hasattr(module, "data_dir"):
                monkeypatch.setattr(module, "data_dir", lambda _s=sandbox: _s)
            continue
        if hasattr(module, attr):
            monkeypatch.setattr(module, attr, sandbox / str(filename))

    return sandbox


def _digest(path: Path) -> tuple[int, str] | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    return raw.count(b"\n"), hashlib.sha256(raw).hexdigest()


@pytest.fixture(scope="session", autouse=True)
def real_store_files_untouched():
    """Backstop: the suite must leave the live store files byte-identical.

    CLAUDE.md's rule for this class of bug — "if a test writes, assert the real
    file's line count is unchanged afterwards" — applied to the whole session
    rather than to one fixture, because the write that prompted it was two calls
    away from the test that caused it.

    The live desk may legitimately be running while the suite runs, and its
    runners write these same files. So a change is only a *failure* when the
    call-level guard also caught this process attempting it; an otherwise
    unexplained change is reported as a warning naming the other writer.
    """
    root = Path(data_dir())
    before = {name: _digest(root / name) for name in _WATCHED_FILES}
    yield
    changed = [name for name in _WATCHED_FILES if _digest(root / name) != before[name]]
    ours = [name for name in changed if _BLOCKED.get(name)]
    if ours:
        detail = "; ".join(f"data/{n} <- {sorted(_BLOCKED[n])}" for n in ours)
        raise AssertionError(f"The unit suite modified live store files: {detail}")
    if changed:
        import warnings

        warnings.warn(
            "Live store files changed while the suite ran, but no test in this "
            f"process wrote them: {', '.join('data/' + n for n in changed)}. "
            "Expected when the desk is running alongside pytest.",
            stacklevel=1,
        )
