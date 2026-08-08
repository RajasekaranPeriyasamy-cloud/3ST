# CLAUDE.md

This file provides guidance to Claude (Claude Code / Cowork) when working with code in this repository.

## Overview

3ST is a single-user, self-hosted options-trading desk built on **FastAPI** (backend, `api/main.py`) + **Zerodha Kite Connect** (broker) + **Pixel Perfect UI** (React/Vite frontend). It started as one strategy (Triple SuperTrend + ADX) and has grown into a full desk of ~17 analysis/execution modules:

| Desk | Route | Backend module |
| --- | --- | --- |
| Stock Selection | `/` | `strategy_3st.py`, `backtest_engine.py` |
| Rolling Straddle | `/rolling-straddle` | `execution/rolling_straddle.py` |
| Premium Book | `/premium-book` | `execution/premium_book_runner.py` |
| Live Desk / Watchlist | `/live` | `execution/watchlist_activation.py`, `watchlist_exit_runner.py` |
| Algo Execution (taskbar) | `/execution` | `execution/execution_queue.py` |
| Equity Report | `/equity-report` | `analysis/equity_report/` |
| RRG, OI Tracker, OI VAR, Gamma Density, Vanna Exposure, Vol Surface, IV Smile, Pricing Engine, Calendar Arb, OI Profile, Analogue Paths | various | `analysis/`, `options/` |

Legacy **Streamlit** UI (`app.py`) still exists but is not actively developed — treat `Pixel Perfect UI` + FastAPI as canonical.

**No multi-tenant / no other users.** One Kite account, one broker session, one operator (see `docs/README.md` for the full desk index and `docs/CONVERSATION_SUMMARY.md` for chronological session history — read that file's "Execution architecture — phase reminders" section before touching order-placement code).

## Documentation Map

- `docs/README.md` — desk index (sidebar route → docs folder).
- `docs/CONVERSATION_SUMMARY.md` — chronological dev log, newest session first. This is the most current source of truth for *why* something works the way it does — more current than any dated one-off review doc.
- `docs/review/3ST_Project_Review_Gaps.md` — a gap analysis dated **2026-07-09**. Largely **stale**: it claims "no live order execution" and "risk limits never invoked," both no longer true (order execution is live and risk-gated as of `execution/order_executor.py`). Don't treat it as current state without checking the code first.
- `docs/KITE_SETUP.md`, `docs/MORNING_START_CHECKLIST.md`, `docs/LIVE_TRADING_DESIGN.md` — operational manuals.

Do not restate docs content into a second location — edit the source file and cross-link.

## Security and Deployment Model

- **Single user, single Kite session.** No auth/authz layers beyond the Kite OAuth login itself.
- **ARM / DISARM kill-switch** (`execution/arming.py`) gates every live order. Default is DISARMED. Persisted to `data/arm_state.json` — survives API restarts.
- **Static-IP egress for orders.** Kite whitelists a fixed IP for order placement (data reads are unrestricted). 3ST supports both a direct bind (`KITE_ALLOWED_EGRESS_IP`) and a `staticip.in` proxy (`KITE_USE_STATICIP_PROXY`). Whitelist updates on developers.kite.trade are capped at once per calendar week — misconfiguring this has blocked live orders for days at a time (see CONVERSATION_SUMMARY, 2026-07-15 sessions). Verify via `GET /health` → `kite_egress_mode`.
- **Kite tokens expire ~6 AM IST daily** — expect a re-login every trading day.
- Secrets: `.env` (API key/secret, egress IP, proxy creds) and all session/state files under `data/` are gitignored except `data/fpi_sectors_seed.json` (an intentional offline fallback seed). Never commit `access_token*` or `.kite_session.json`.
- **`ANTHROPIC_API_KEY`** (added 2026-08-07) is used by the Equity Report desk only and spends money per report. `EQUITY_REPORT_DAILY_USD_CAP` (default $10) refuses to queue a new report once today's accumulated cost crosses it; `EQUITY_REPORT_STUB=1` returns canned reports so the UI can be worked on with zero spend. Leaving the key blank disables the desk cleanly — the page says so rather than failing obscurely.

## Development Environment Setup

```powershell
cd C:\Dev\3ST
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# fill in KITE_API_KEY / KITE_API_SECRET / KITE_REDIRECT_URL

uvicorn api.main:app --reload --host 127.0.0.1 --port 8001
```

Frontend (separate terminal):

```powershell
cd "Pixel Perfect UI"
npm install
npm run dev   # serves on :8080, proxies API calls to :8001
```

Or use the combined dev script: `powershell -ExecutionPolicy Bypass -File "C:\Dev\3ST\scripts\start_3st_dev.ps1"` (stop with `scripts\stop_3st.ps1`). First boot can take >45s — the script accounts for this.

**Health check:** `GET http://127.0.0.1:8001/health`. **Swagger:** `/docs`.

**Two surfaces serve the UI, and they are not equivalent.** Port 8080 is the Vite dev server (live source). Port 8001 is FastAPI serving a *prebuilt* bundle from `Pixel Perfect UI/.output/public` (`api/ui_static.py`). A new route added to `src/routes/` appears on 8080 immediately and on 8001 **only after `npm run build`** — verifying a UI change on 8080 alone proves nothing about the app as normally opened. `.output/` is gitignored, so a build artifact that gets overwritten is not recoverable from git.

> ⚠️ **Stop the API before running `npm run build`.** FastAPI mounts `.output/public/assets` via `StaticFiles`, which holds a Windows directory lock; Vite then fails to clean it with `EBUSY: resource busy or locked, rmdir` and can leave a half-written bundle.

**Why `scripts/build-desk.mjs` is not just `vite build`** (rewritten 2026-08-07). TanStack Start's prerender step cannot run in this project, and prerender is what normally emits the SPA shell:

- `start-plugin-core`'s preview-server plugin imports `<serverOutputDir>/server.js` and calls `.default.fetch()`. The Lovable preset routes the server build through **nitro**, which writes `.output/server/index.mjs` instead — so the import fails, `/` 500s, and prerender emits nothing. This is an upstream incompatibility between `@lovable.dev/vite-tanstack-config` and `@tanstack/start-plugin-core`; it is **not** a version drift (`@tanstack/react-start@1.168.27` pins `start-plugin-core: 1.171.19` exactly, and upgrading the Lovable config to 2.9.1 does not help).
- Without a shell the client throws `Invariant failed` and **every page renders blank on :8001**, because the shell is what defines `self.$_TSR.router`. A hand-written shell with only a stylesheet and the entry script is not enough — the `$tsr-stream-barrier` bootstrap is required.

`build-desk.mjs` works around this: it builds nitro with `NITRO_PRESET=node-server`, boots that server once on `DESK_SHELL_PORT` (default 3199), fetches `/`, and keeps only the stylesheets, the `$tsr` bootstrap, and the entry module — dropping the rendered body so the shell is route-agnostic. It also patches `hydrateRoot` → `createRoot`, since there is no server HTML to hydrate against. `vite build` still exits non-zero (the prerender crash); that is expected and the script continues.

Note this only affects the static bundle FastAPI serves. Deploys via Lovable/Cloudflare do not go through this script.

> **Direct URL loads work only for SPA routes whose path does not collide with an API prefix.** `/gamma-density`, `/oi-var`, `/execution` etc. are also API prefixes, so a hard browser load of those returns a JSON 404 — they are reachable by clicking through the sidebar (client-side routing). Give any new SPA page a path that isn't an API prefix, or register the API side with a trailing slash (`"/equity/"`) as `/equity-report` does.

## Architecture

```
api/            FastAPI routes (main.py), SPA static serving (ui_static.py)
broker/         Broker abstraction — base.py (ABC), kite_broker.py, paper_broker.py,
                execution_support.py (symbol locks, position cache, target-qty planning)
risk/           risk/limits.py — the one place order risk gates live
execution/      ~30 modules: 5 parallel runners (Rolling Straddle, Watchlist/Live Desk,
                Survivor, Wave, Premium Book), each with its own store + reconcile logic,
                plus shared primitives: order_executor.py, arming.py, execution_queue.py,
                reconcile.py, ltp_cache.py, panic.py
                — position_ledger.py / order_router.py / signal_bus.py (added 2026-07-25):
                  a shared ledger + risk/ARM-gated router per the Phase 3 plan in
                  CONVERSATION_SUMMARY.md. NOT yet wired into any of the 5 runners —
                  additive and currently inert. Do not assume it is tracking live legs
                  until a runner is migrated to call order_router.submit_intent().
options/, analysis/   Desk engines (chain, greeks, IV, vanna, gamma density, RRG, etc.)
                — analysis/equity_report/ (added 2026-08-07): the only module that calls an
                  LLM. Runs the vendored `india-equity-report` prompt against the Anthropic
                  API with the web_search/web_fetch server tools to produce NSE/BSE research
                  reports. Its own daemon thread (runner.py), its own JSON stores, and no
                  imports from broker/ execution/ risk/ — a slow model call must never be
                  able to delay an order-placing tick.
strategy_3st.py, backtest_engine.py   Core indicator + backtest engine
tests/          40+ pytest files — good coverage of strategy parity, risk limits,
                reconcile, bar-churn, exit-grace edge cases
```

### Data files (`data/*.json`)

Flat-JSON-per-concern, no central database. Each file is owned by exactly one module — check the module before editing a file by hand:

| File | Owner |
| --- | --- |
| `arm_state.json` | `execution/arming.py` |
| `risk_limits.json` | `risk/limits.py` |
| `position_ledger.json` | `execution/position_ledger.py` (not yet populated — see above) |
| `rolling_straddle_{config,state,log}.json` | `execution/rolling_straddle_store.py` |
| `premium_book_{config,state,log}.json` | `execution/premium_book_store.py` |
| `survivor_{config,state,log}.json` | `execution/survivor_store.py` |
| `wave_config.json` | `execution/wave_store.py` |
| `watchlist.json`, `selection.json` | `watchlist_store.py` |
| `paper_broker.json` | `broker/paper_broker.py` |
| `kite_session.json` | `kite_auth.py` — gitignored |
| `kite_instruments.json` | `instruments.py` cache |
| `equity_reports.json` + `equity_reports/*.md` | `analysis/equity_report/store.py` |
| `equity_pins.json` | `analysis/equity_report/pins.py` |

There is no single source of truth across runners for "what legs are open" today — that's exactly the gap `position_ledger.py` exists to close, once runners migrate to it.

## Runtime Constraints

**Single-process, in-memory state.** ARM state, risk limits, the LTP cache, symbol locks, and the position ledger are all module-level singletons backed by flat JSON files, not a database or Redis. This is safe under `uvicorn --reload` (still one process) but **would silently break under multiple workers** (`uvicorn --workers N` / gunicorn with >1 worker) — each worker would have its own ARM state and risk counters, and orders could bypass limits another worker already hit. Do not add multi-worker deployment without moving this state to a shared store first.

`uvicorn --reload` does not always pick up changes under `execution/` reliably mid-session — several CONVERSATION_SUMMARY sessions note "restart API after pull" as the fix for stale-looking behavior. When in doubt, fully stop and restart rather than trusting the reloader.

## Invariants That Must Not Be Broken

- **ARM gate lives in `broker/kite_broker.py`** (`KiteBroker.place_order`/`cancel_order` call `require_armed_for_live()`), not in individual callers. Do not add a new order-placement path that talks to Kite without going through `KiteBroker`, or this gate is bypassed.
- **Risk checks live in `risk/limits.py`, invoked only from `execution/order_executor.py`** (`place_leg_order` / `place_leg_to_target`). Every runner places orders through these two functions — never call `broker.place_order()` directly from a runner. `execution/order_router.py` wraps these same two functions rather than reimplementing risk/ARM logic; keep it that way.
- **Per-symbol locking**: `broker/execution_support.acquire_symbol_lock(exchange, tradingsymbol, product)` must wrap any order placement to prevent concurrent double-orders on the same instrument.
- **Order tag convention**: `execution/order_executor.order_tag(leg_key, kind)` → `3ST-{LEG_KEY}-{YYYYMMDD}-{kind}`, truncated to Kite's 20-character tag limit. Reconcile/orphan-detection logic matches on this prefix — don't change the format without updating every matcher.
- **Bar-close-only exits are the default anti-churn control** (`exit_on_bar_close_only`). A prior whipsaw-loop incident (2026-07-14) came from intrabar zone exits re-triggering same-bar re-entries. TSL/force-exit intentionally stay tick-live; zone exits default to bar-close.
- **Broker is the source of truth on reconcile** (`execution/reconcile.py`, `position_ledger.reconcile_with_broker`). If a broker-positions read fails, abort with zero mutations — never interpret a read failure as "broker is flat," or a transient API error will incorrectly flatten every locally-tracked leg.
- **Orphans are adopted manually, never automatically.** A broker position with no local record is surfaced (UI banner / execution queue) but not silently linked to a runner.
- **Kite egress**: market data reads are always direct; only order placement is gated by the static-IP whitelist. Don't route quote/history calls through the static-IP proxy — it's unnecessary and slower.

## Testing

```bash
pytest tests/ -v
pytest tests/test_risk_limits.py -v
pytest tests/test_order_router.py::test_submit_intent_duplicate_tag_blocked -v
```

CI runs this on every push/PR — `.github/workflows/ci.yml` (added 2026-07-25). The `test` job installs `requirements.txt` and runs the full suite; a `lint` job runs `ruff check .` but is `continue-on-error: true` for now (see Code Style) so pre-existing style debt doesn't block merges — flip it to blocking once that backlog is cleared.

Some tests are date-sensitive (expiry/vol-surface fixtures assume a "today" relative to when they were written) and will start failing as real dates pass — a failure in `test_vol_surface.py` or `test_mcx_rolling_straddle.py` around expiry dates is often this, not a regression. Check the assumed date before assuming a real break. (Known-stale failures as of **2026-08-07** — 6 of them: `test_fpi_sectors.py::test_attach_fpi_overlay_uses_seed`, `test_mcx_rolling_straddle.py::test_save_config_corrects_stale_crudeoilm_expiry`, and all four of `test_vol_surface.py` — `test_surface_shape`, `test_otm_convention`, `test_term_structure_recovered`, `test_max_expiries_limit`. The vol-surface fixtures pin `EXPIRIES = ["2026-07-16", "2026-07-23", "2026-07-30"]`, so every one of them now fails with `RuntimeError: No expiries available for NIFTY`; only two were failing on 2026-07-25 because the drift was partial then. `test_execution_queue.py::test_build_execution_queue_pending_confirm_mode`, listed as stale on 2026-07-25, now **passes** again. The honest fix is to make these fixtures relative to `date.today()` rather than re-dating the list each time it rots.)

**A CI/lint gap already caught a live bug once** — `ruff check . --select F821` (undefined-name) surfaced `execution/watchlist_activation.py` referencing `patch` while the `patch` dict was still being constructed. That raised `UnboundLocalError` on *every* watchlist entry activation (manual live BUY/SELL, and the taskbar's ship/execute action) — after the broker order had already been placed. Fixed 2026-07-25 (`tests/test_watchlist_activation.py` is the regression test). Take this as the reason the `lint` job exists at all, even non-blocking.

## Code Style

`pyproject.toml` configures **ruff** (added 2026-07-25): `E`/`F`/`W`/`I`/`B`/`C4`/`UP`, line-length 110 (soft — `E501` is ignored), target `py312`. Run:

```bash
ruff check .          # lint
ruff check . --fix    # auto-fix safe issues
ruff format .         # format
```

As of 2026-07-25 there's a backlog of ~90 pre-existing findings (mostly unsorted/unused imports and `datetime.utcnow()`-style pyupgrade suggestions, ~59 auto-fixable) that predate this config — that's why CI's `lint` job is non-blocking for now. Don't let new code add to that pile; existing code doesn't need a drive-by cleanup unless you're already touching that file.

- 4-space indentation, type hints throughout, `from __future__ import annotations` at the top of most modules.
- Imports: standard library → third-party → local.
- New JSON-backed stores follow the existing pattern: module-level singleton dict, `_load()`/`_save()` around a `data_dir()`-relative file, `load_persisted_*()` called at import time (see `risk/limits.py`, `execution/arming.py`, `execution/position_ledger.py`).

## Logging

`utils/logging.py` — structured JSON logs via `get_logger(name)` / `log_event(logger, level, message, **fields)`. As of 2026-07-25:

- **Secret redaction is automatic.** Every record — message text, exception text, and any `extra_fields` — is scrubbed for known-sensitive key/value patterns (`api_key`, `api_secret`, `access_token`, `request_token`, `password`, `totp`, `checksum`, `pepper`, etc.) before serialization, both for structured fields and secrets embedded in free text (e.g. an `httpx` error dumping a failed request body). See `tests/test_logging.py`.
- **Console output is always on** (stderr, as before).
- **File persistence is opt-in**: set `LOG_TO_FILE=1` for daily-rotated logs under `log/3st.log` (retained `LOG_RETENTION` days, default 14).
- **`log/errors.jsonl` is always on**, ERROR+ only, auto-truncated to the last 1000 lines on startup — read this first when debugging a post-incident report; it survives a process restart, unlike stderr-only logging.

## Troubleshooting

- **Port confusion**: `.env` defaults to API port 8001; some UI config/docs reference 8000. Confirm `VITE_API_BASE_URL` matches wherever uvicorn is actually bound.
- **"Waiting 09:20" / ticks not updating after underlying switch**: historically caused by stale `morning_bar_seen` / `last_spot` carried over from a different underlying — fixed in `rolling_straddle_store.py`, but worth checking first if a desk looks frozen after a config change.
- **Orders rejected — IP not whitelisted**: check `GET /health` → `kite_egress_mode`, then confirm the *outgoing* IP (not your ISP IP) matches what's whitelisted on developers.kite.trade.
- **API restart required after any `execution/` change** — don't trust `--reload` alone if behavior looks stale. The same applies to `analysis/equity_report/`: a running API will not pick up a new module there, and `GET /health` → `equity_report_runner_alive` is the quickest way to tell whether the restart took.
- **Equity Report jobs stuck at "failed: API restarted while this report was in flight"** — expected. A report is a live model stream; a restart kills it and `store.load_persisted_jobs()` fails anything left `queued`/`running` rather than leaving the UI spinning. Just re-run it.
- **New config not taking effect** — read settings through `settings.env()` (which loads `.env`), never `os.getenv` directly. A raw `os.getenv` depends on the environment of whichever shell launched uvicorn, which differs between `start_3st_dev.ps1`, a bare `uvicorn` call, and a service wrapper. This bit the `EQUITY_REPORT_STUB` flag during development.

## Claude Instructions

- `execution/position_ledger.py`, `order_router.py`, `signal_bus.py` are new (2026-07-25) and **not wired into any runner yet**. Don't assume the execution queue / taskbar reads from the ledger — it still reads `rolling_straddle` and `watchlist` stores directly (`execution/execution_queue.py`). Migrating a runner to the router is a live-trading-behavior change — test in paper mode and confirm with the user before shipping.
- Treat `docs/CONVERSATION_SUMMARY.md` as more current than `docs/review/3ST_Project_Review_Gaps.md` when they conflict.
- This system places real orders with real money. Any change touching `broker/`, `execution/`, or `risk/` should be run through `pytest tests/` before considering it done, and prefer paper-mode verification over live-mode for anything not already covered by a test.
- `pyproject.toml` (ruff), `.github/workflows/ci.yml`, and the redaction/file-persistence additions to `utils/logging.py` were all added 2026-07-25 to close gaps found by comparing this repo against a reference `CLAUDE.md` (OpenAlgo's, already in `_review/openalgo/`). The lint job caught a real bug the same day — see "A CI/lint gap already caught a live bug once" under Testing. When asked to "clean up lint" going forward, prefer fixing violations file-by-file (with tests passing after each) over a single repo-wide `--fix` pass, given how much of this code places real orders.
