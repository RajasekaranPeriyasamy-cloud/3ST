# Stores and in-process state

3ST has **no database**. Every piece of persistent state is a flat JSON file
under `data/`, owned by exactly one module, held in memory as a module-level
singleton. That design is only safe because the whole system runs in one
process — see "The single-process constraint" below.

---

## Ownership

Each file has exactly one owning module. **Check the owner before editing a file
by hand**, and never write to a file from a second module.

| File | Owner |
|---|---|
| `arm_state.json` | `execution/arming.py` |
| `risk_limits.json` | `risk/limits.py` |
| `position_ledger.json` | `execution/position_ledger.py` — *not yet populated* |
| `rolling_straddle_{config,state,log}.json` | `execution/rolling_straddle_store.py` |
| `premium_book_{config,state,log}.json` | `execution/premium_book_store.py` |
| `survivor_{config,state,log}.json` | `execution/survivor_store.py` |
| `wave_config.json` | `execution/wave_store.py` |
| `watchlist.json`, `selection.json` | `watchlist_store.py` |
| `cas_history.jsonl` | `options/cas_history.py` — append-only, written from the `/cas/*` routes, not the payload builders |
| `paper_broker.json` | `broker/paper_broker.py` |
| `kite_session.json` | `kite_auth.py` — **gitignored** |
| `kite_instruments.json` | `instruments.py` cache |
| `equity_reports.json` + `equity_reports/*.md` | `analysis/equity_report/store.py` |
| `equity_pins.json` | `analysis/equity_report/pins.py` |

Everything under `data/` is gitignored **except** `data/fpi_sectors_seed.json`,
an intentional offline fallback seed. Never commit `access_token*` or
`.kite_session.json`.

There is no single source of truth across runners for "what legs are open"
today. That is the gap `position_ledger.py` exists to close, once a runner
migrates to it.

---

## The store pattern

New JSON-backed stores follow the shape of `risk/limits.py` and
`execution/arming.py`:

```python
from settings import data_dir

LIMITS_FILE = data_dir() / "risk_limits.json"      # module-level path
_LIMITS = RiskLimits()                             # module-level singleton
_PERSIST_KEYS = ("max_qty", "max_open_positions", …)   # explicit allow-list


def _persist_limits() -> None:
    payload = {k: getattr(_LIMITS, k) for k in _PERSIST_KEYS}
    LIMITS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_persisted_limits() -> None:
    if not LIMITS_FILE.exists():
        return
    try:
        raw = json.loads(LIMITS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return          # a corrupt file must not crash import
    …                   # coerce types explicitly, validate enums


load_persisted_limits()   # ← called at import time
```

Four properties worth copying deliberately:

1. **An explicit `_PERSIST_KEYS` allow-list**, not "dump the dataclass". Fields
   outside it are code defaults that reset on restart — in `risk/limits.py` the
   `allowed_products` / `allowed_exchanges` lists are intentionally in that
   category.
2. **Load is total.** Missing file, unreadable file, corrupt JSON, wrong type —
   every one returns quietly and leaves defaults in place. A bad state file must
   never prevent the API from starting.
3. **Values are coerced and validated on read** (`int(...)`, `float(...)`, mode
   restricted to `{"paper", "live"}`). Never trust what's on disk.
4. **`load_persisted_*()` runs at import time**, so state survives an API
   restart and a `uvicorn --reload` cycle.

`settings.data_dir()` returns `<repo>/data` and `mkdir`s it, so stores don't
need to.

### Multi-session archives

Higher-volume desks use a directory tree instead of one file — see
`analysis/delta_velocity/store.py`: `root_dir()` → `underlying_dir()` →
`session_file(underlying, session_date)`, guarded by a module-level
`threading.RLock`, with `prune_raw(retention_days=…)` for retention. Use this
shape for anything appending per-minute snapshots.

---

## The single-process constraint

ARM state, risk limits and counters, the LTP cache, symbol locks, and the
position ledger are all module-level singletons backed by flat files.

This is safe under `uvicorn --reload` (still one process). It **would silently
break under multiple workers** — `uvicorn --workers N`, or gunicorn with >1
worker. Each worker would carry its own ARM state and its own
`_orders_this_minute` counter, so orders could bypass a limit another worker had
already hit, and a symbol lock in one worker would not exclude the other.

**Do not add multi-worker deployment without moving this state to a shared store
first.** The symbol lock in `broker/execution_support.py` is a plain
`threading.Lock`; it has no cross-process meaning.

---

## Reading configuration

Use `settings.env(key, default="")`, never `os.getenv` directly.

`settings.env()` loads `.env`. A bare `os.getenv` inherits whichever shell
launched uvicorn — which differs between `scripts/start_3st_dev.ps1`, a plain
`uvicorn` invocation, and a service wrapper. This exact bug bit the
`EQUITY_REPORT_STUB` flag during development.

Typed accessors already exist for most of it in `settings.py`:
`kite_credentials()`, `kite_proxy_config()`, `equity_report_config()`,
`gemini_config()`, `data_dir()`.

---

## When state looks stale

- **After any `execution/` or `analysis/equity_report/` change:** fully stop and
  restart the API. `--reload` does not reliably pick these up, and several
  sessions in `docs/CONVERSATION_SUMMARY.md` record "restart API after pull" as
  the fix. `GET /health` → `equity_report_runner_alive` tells you whether the
  restart took.
- **"Waiting 09:20" / ticks frozen after switching underlying:** historically
  stale `morning_bar_seen` / `last_spot` carried over from the previous
  underlying. Fixed in `rolling_straddle_store.py`, but check it first when a
  desk looks frozen after a config change.
- **Post-incident debugging:** read `log/errors.jsonl` first. It is always on,
  ERROR+ only, truncated to the last 1000 lines at startup, and survives a
  process restart — unlike stderr. Secrets are redacted automatically by
  `utils/logging.py`, in message text, exception text, and `extra_fields`.
