# Testing 3ST

```bash
pytest tests/ -v
```

68 test files. **The suite is fully green and runs offline in ~25s.** There is
no tolerated-failure list — the old "6 known-stale date-sensitive failures" were
fixed, not accepted. **If you see a failure, it is real.**

CI (`.github/workflows/ci.yml`) runs the same command on every push and PR,
Python 3.13, no broker session. A `lint` job runs `ruff check .` with
`continue-on-error: true` — non-blocking only because ~90 style findings predate
the config.

---

## The suite must never touch Kite

`tests/conftest.py` installs an **autouse** fixture that patches the client
accessors every market-data path resolves at call time:

```python
_CLIENT_ACCESSORS = ("_kite_direct_client", "get_kite_client", "kite_read_client")
```

This is not politeness. One gamma-snapshot test used to walk a whole option
chain issuing ~80 per-strike 60-min historical requests
(`gamma_density` → `oi_movers.ensure_session_open_oi` →
`fetch_historical_by_token`) — 80+ seconds, different data every run. Blocking
it took the suite from **402s to ~25s** and made CI runnable at all.

A test that genuinely needs a broker opts out with `@pytest.mark.live_kite`.
Nothing does today.

`tests/test_offline_guard.py` asserts the guard is still live: the fixture skips
accessor names that no longer exist, so renaming one in `kite_client.py` would
silently disable the guard. That test fails loudly instead.

---

## The import-binding trap (this is the one that bites)

Modules bind names **at import time**. Patching the definition site therefore
misses every module that already holds its own reference.

### Wrong — patches the wrapper, misses the holders

```python
monkeypatch.setattr("kite_client.fetch_historical_by_token", fake)
```

Modules that did `from kite_client import fetch_historical_by_token` keep their
own reference and never see the patch. Patch the **accessors** instead — that is
why `conftest.py` targets `_kite_direct_client` / `get_kite_client` /
`kite_read_client`, which every fetcher resolves at *call* time.

### Wrong, and far more damaging — `settings.data_dir`

```python
monkeypatch.setattr("settings.data_dir", lambda: tmp_path)   # ← does nothing
```

Stores do `from settings import data_dir` at import time. This patch leaves them
pointed at the **real** `data/`. On 2026-08-13 a theta-decay fixture did exactly
this and appended 1,800 synthetic snapshots into the live delta-velocity archive
before anyone noticed.

### Right — patch the module under test's own reference

```python
monkeypatch.setattr(store, "data_dir", lambda: tmp_path)
```

That is the pattern used throughout `tests/test_delta_velocity.py` (lines 175,
196, 206, 315, …). **And if the test writes, assert the real file's line count
is unchanged afterwards.**

---

## Date-sensitive fixtures must derive from `date.today()`

A hardcoded date that is *currently valid* will silently stop testing anything
the moment it expires. That is what rotted before.

| Pattern | Example |
|---|---|
| Derive forward | `tests/test_vol_surface.py:17` — `_weekly_expiries()` builds weeklies from `date.today()` |
| Derive backward | `tests/test_mcx_rolling_straddle.py:151` — stale expiry as `date.today() - timedelta(days=90)` |
| Read from the seed | `tests/test_fpi_sectors.py` reads expected values out of `data/fpi_sectors_seed.json` rather than hardcoding NSDL numbers |

A hardcoded date that is *already past*, and meant to be, is fine.

Fixing these uncovered a live bug the stale dates had masked: `save_config` does
not correct a past expiry unless the patch touches `expiry`/`underlying`. See
`test_save_config_keeps_expiry_on_unrelated_patch`, which **pins current
behaviour** — a deliberate fix should show up there as a failure.

---

## What to run for what

| Change touches | Run at minimum |
|---|---|
| `risk/`, `execution/order_executor.py` | `test_risk_limits.py`, `test_order_executor.py` |
| `execution/arming.py`, `panic.py` | `test_arm_persistence.py`, `test_panic.py` |
| `execution/order_router.py` | `test_order_router.py` |
| a runner's exit logic | `test_exit_grace.py`, `test_force_exit.py` |
| `kite_client.py` accessors | `test_offline_guard.py` |
| `utils/logging.py` | `test_logging.py` (secret redaction) |
| anything at all | `pytest tests/` — it's 25s |

---

## Lint

```bash
ruff check .
```

`pyproject.toml`: `E`/`F`/`W`/`I`/`B`/`C4`/`UP`, line-length 110 (soft — `E501`
ignored), target `py312`.

The `F821` (undefined-name) rule already caught a live bug:
`execution/watchlist_activation.py` referenced `patch` while the `patch` dict was
still being built, raising `UnboundLocalError` on **every** watchlist activation
— manual live BUY/SELL and the taskbar ship action — *after the broker order had
already been placed*. Fixed 2026-07-25; `tests/test_watchlist_activation.py` is
the regression test. That is why the non-blocking lint job exists.

When cleaning up lint, go **file-by-file with tests passing after each**, not one
repo-wide `ruff check . --fix`. Too much of this code places real orders.
