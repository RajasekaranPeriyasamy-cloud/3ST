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
| Delta Velocity | `/delta-velocity` | `analysis/delta_velocity/` (API prefix `/velocity`) |
| Theta Decay | `/theta-decay` | `analysis/theta_decay/` (API prefix `/decay`) |
| RRG, OI Tracker, OI VAR, Gamma Density, Vanna Exposure, Vol Surface, IV Smile, Pricing Engine, Calendar Arb, OI Profile, Analogue Paths | various | `analysis/`, `options/` |
| Volume Footprint | `/volume-footprint` | `analysis/volume_profile/` + `vendor/volume_footprint/` |
| Chain Build-Up | `/chain-buildup` | `analysis/chain_buildup/` (API prefix `/buildup`) |
| Options Arbitrage | `/opt-arb` | `analysis/opt_arb/` (API prefix `/oarb`) |
| Live Market News | `/news` | `analysis/news_desk/` (API prefix `/newsfeed`) |

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
- **LLM credentials for the Equity Report desk only.** `EQUITY_REPORT_PROVIDER` selects `anthropic` (default) or `gemini`; each reads its own key (`ANTHROPIC_API_KEY` / `GEMINI_API_KEY`). Anthropic spends money per report — `EQUITY_REPORT_DAILY_USD_CAP` (default $10) refuses to queue a new report once today's accumulated cost crosses it. `EQUITY_REPORT_STUB=1` returns canned reports so the UI can be worked on with zero spend. A blank key disables the desk cleanly — the page says so rather than failing obscurely.

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

**The frontend is a plain Vite SPA** (migrated 2026-08-08 — see `docs/UI_LOVABLE_EXIT_PLAN.md`). `npm run build` is literally `vite build`, exits zero, and emits a normal `index.html` from the real `index.html` at the project root.

This replaced `@lovable.dev/vite-tanstack-config` + `@tanstack/react-start` + nitro. That stack could not run TanStack Start's prerender step (the Lovable preset routed the server build through nitro, so prerender found no `server.js`, 500'd, and emitted no shell), which is why `scripts/build-desk.mjs` used to boot a nitro server just to scrape a shell out of it and string-patch `hydrateRoot` → `createRoot`. All of that — plus `_shell.html`, `src/server.ts`, `src/start.ts`, and the "`vite build` exits non-zero, that's expected" caveat — is gone. There is no `scripts/` directory in `Pixel Perfect UI/` anymore.

The app is client-rendered only: **no SSR and no server functions.** Don't add `createServerFn` or reintroduce `@tanstack/react-start`; data comes from FastAPI via `src/lib/api.ts`. Document `<head>` (title, meta, fonts, favicon) and the inline theme boot script live in `Pixel Perfect UI/index.html`, not in `__root.tsx`.

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
                  LLM. Runs the vendored `india-equity-report` prompt to produce NSE/BSE
                  research reports. Its own daemon thread (runner.py), its own JSON stores,
                  and no imports from broker/ execution/ risk/ — a slow model call must
                  never be able to delay an order-placing tick.
                  Two backends behind one generate_report(): agent.py (Anthropic,
                  web_search + web_fetch) and gemini_backend.py (Gemini Interactions API).
                  See "Equity Report providers" below before touching either.
                — analysis/theta_decay/ (added 2026-08-13): burn rate + decay capture. Has
                  **no collector, store or runner** — it reads analysis/delta_velocity/'s
                  minute archive and re-derives theta/gamma/vega from the stored full-precision
                  IV on every read (~0.3s/session vectorised). Do not "optimise" that by
                  storing greeks in the snapshot: the collector computes at q=0.012 while the
                  archived IV is solved at q=0, and mixing them shifts ATM theta ~5%. Read
                  features.py's docstring before trusting capture_ratio — burn rate is solid,
                  decay capture is a session-scale statistic with a quality gate.
                — analysis/chain_buildup/ (added 2026-08-27): Option Chain Build-Up desk.
                  Like theta_decay it has **no collector, store or runner** — it buckets the
                  analysis/delta_velocity/ minute archive into 5/15/30/60m OI columns. The
                  archive only holds ATM +/- STRIKE_WIDTH strikes *at capture time*, so its
                  strike set drifts with spot; wider strike ranges opt into Kite historical
                  OI candles (`widen=true`), capped at MAX_WIDEN_LEGS and cached per
                  (token, timeframe, session). Read-only; places no orders.
                — analysis/news_desk/ (added 2026-09-02): Live Market News desk. Ten
                  publisher RSS feeds + NSE corporate announcements, lexicon sentiment by
                  default (NEWS_SENTIMENT_PROVIDER=anthropic is an opt-in, daily-capped
                  upgrade that also produces the category tags). Imports nothing from
                  broker/ execution/ risk/; places no orders.
                  **Its HTTP goes through analysis/news_desk/net.py, never bare requests.**
                  apply_kite_proxy_env() pins HTTP(S)_PROXY process-wide AND deletes
                  NO_PROXY, so any bare requests call routes publisher traffic through
                  metered static-IP order egress and fails with ProxyError — invisible
                  outside the API process. net.py sets trust_env = False.
                  Ticker resolution (tickers.py) is the soft spot: generic single words,
                  ETF rows and sub-6-char lowercase tokens are refused on purpose. Read
                  its module docstring before loosening a guard. Aliases:
                  data/news_aliases.json.
                  Equity search (?symbol=) is deliberately LOOSER than resolution: it
                  matches resolved chips OR a word-boundary text mention built from
                  tickers.search_terms(). Resolution is conservative and only ever ran
                  against the text an item had at ingest, so a chips-only search would
                  silently drop headlines that plainly name the stock. Search also
                  suppresses clustering — every hit shares a primary symbol, so grouping
                  would collapse the whole result set into one row.
                — analysis/opt_arb/ (added 2026-08-25): option-to-option arbitrage scanner.
                  Scan-and-alert only — imports nothing from broker/ execution/ risk/ and
                  places no orders. Prices every leg at bid/ask (never LTP) and nets every
                  row against costs.py before surfacing it. Its universe is self-contained
                  rather than going through INDEX_OPTIONS, deliberately: that constant is
                  what ANALYTICS_HISTORY_SAMPLE_UNDERLYINGS filters against, so adding
                  GOLD/SILVER there would silently start Gamma-Density/OI-VAR sampling them.
vendor/         Third-party code, verbatim, each subpackage with its own LICENSE.
                — vendor/volume_footprint/ (added 2026-08-20): MPL-2.0 Pine port behind the
                  Volume Footprint desk. Do NOT edit in place — it is kept diffable against
                  upstream; 3ST-side wiring belongs in analysis/volume_profile/.
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
| `cas_history.jsonl` | `options/cas_history.py` (append-only; written from the `/cas/*` routes, not the payload builders) |
| `paper_broker.json` | `broker/paper_broker.py` |
| `kite_session.json` | `kite_auth.py` — gitignored |
| `kite_instruments.json` | `instruments.py` cache |
| `equity_reports.json` + `equity_reports/*.md` | `analysis/equity_report/store.py` |
| `equity_pins.json` | `analysis/equity_report/pins.py` |
| `opt_arb_config.json` | `analysis/opt_arb/store.py` |
| `news_items.json`, `news_desk_config.json` | `analysis/news_desk/store.py` |
| `news_aliases.json` | `analysis/news_desk/tickers.py` (user-extensible) |
| `news_llm_spend.json` | `analysis/news_desk/llm.py` (daily cap ledger) |

There is no single source of truth across runners for "what legs are open" today — that's exactly the gap `position_ledger.py` exists to close, once runners migrate to it.

## Equity Report providers

`EQUITY_REPORT_PROVIDER` picks the backend; both satisfy the same
`generate_report()` contract and share the prompt, the `sources.py` allowlist,
the store, the runner and the page.

| | `anthropic` (default) | `gemini` |
| --- | --- | --- |
| Search | `web_search` server tool | **none** — see below |
| Fetch | `web_fetch` server tool | `url_context` |
| Continuation | `pause_turn` re-send | `previous_interaction_id` on `status: incomplete` |
| Cost | ~$1–3/report (unmeasured) | free tier, recorded as $0.00 |

**Gemini has no search.** Measured 2026-08-08 on a free-tier key: plain generation
and `url_context` both work, but `google_search` grounding 429s — it sits behind a
separate quota the free tier doesn't grant. `EQUITY_REPORT_GEMINI_SEARCH` exists to
turn it on if you get a plan that includes it; leave it `0` otherwise or every
report fails.

That is survivable because `sources.seed_urls()` templates the canonical URLs per
ticker, so search was only ever doing *discovery*. What is genuinely lost: recent
news, credit ratings, and analyst consensus. The prompt addendum tells the model to
mark those "not available in this run" instead of inventing them.

**The anti-hallucination backstop lives in `gemini_backend.generate_report_gemini`:**
`url_context_result` reports per-URL success/failure, and if *nothing* fetched the
function raises rather than returning a report whose every number came from model
memory. Several finance sites (Trendlyne, observed) block automated retrieval, so
partial failure is normal and expected.

**Model choice matters more than usual.** Measured against the report template
(RELIANCE, same prompt, `thinking_level: high`):

| model | time | words | mandatory sections |
| --- | --- | --- | --- |
| `gemini-3.1-flash-lite` | 20s | 1,045 | 3/6 |
| `gemini-3.5-flash-lite` (default) | 46s | 3,406 | 5/6 |
| `gemini-3.5-flash` | 194s | 5,311 | 5/6 |
| `gemini-3.6-flash` | 88s | 5,775 | 5/6 |

Flash-Lite 3.1 drops the Bull/Base/Bear table and the split verdict — both mandatory
in the skill's rubric — which is why the default is 3.5-flash-lite despite being
slower.

Gemini needs `google-genai >= 2.3.0` for the Interactions API (`client.interactions.create`);
the older `generate_content` surface does not carry these tools.

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

**The suite is fully green and runs offline in ~25s** (as of 2026-08-09). If you see failures, they are real — the long-standing "6 known-stale date-sensitive failures" are fixed, not tolerated.

**`tests/conftest.py` blocks live Kite in every test.** It patches the client accessors that every market-data path resolves at call time (`_kite_direct_client`, `get_kite_client`, `kite_read_client`), so nothing can reach the broker. This is *not* optional politeness: one gamma-snapshot test used to walk a whole option chain issuing ~80 per-strike 60min historical requests (`gamma_density` → `oi_movers.ensure_session_open_oi` → `fetch_historical_by_token`), taking 80+ seconds and returning different data every run. Blocking it took the suite from **402s to ~25s** and made CI runnable without a broker session.

- Patch the *accessors*, not `kite_client.fetch_*` — modules do `from kite_client import fetch_historical_by_token` at import time and hold their own reference, so patching the wrapper misses them.
- **The same binding trap applies to `settings.data_dir`, and it bites harder.** Stores do `from settings import data_dir` at import time, so `monkeypatch.setattr("settings.data_dir", ...)` leaves them pointed at the real `data/`. A theta-decay fixture did exactly that on 2026-08-13 and appended 1,800 synthetic snapshots into the live delta-velocity archive before anyone noticed. Patch the *module under test's* reference — `monkeypatch.setattr(store, "data_dir", lambda: tmp_path)`. Since 2026-08-27 the conftest guard below enforces this for you, but keep writing the explicit patch: it also documents what the file starts out holding.
- A test that genuinely needs a broker can use `@pytest.mark.live_kite` (nothing does today).
- `tests/test_offline_guard.py` asserts the guard is still live, so renaming an accessor fails loudly instead of silently turning it into a no-op.

**`tests/conftest.py` also blocks writes into the real `data/` directory** (added 2026-08-27). Two layers:

- `isolate_real_data_writes` (autouse) redirects every known store path constant — the `_REDIRECTS` table — at the *store module's own* reference, into a per-test tmp sandbox. A test that patches a constant itself still wins; this is the floor, not a replacement.
- A call-level guard armed by `pytest_collection_finish` wraps `open`/`Path.write_*`/`os.replace`/`rename`/`remove` and raises `RealDataWriteBlocked` on anything still landing under the live `data/`. So a store added later cannot silently join the list — it fails with a message naming the fix. Opt out with `@pytest.mark.writes_real_data` (nothing does today). Collection and import stay unguarded, because a couple of modules create `data/` subdirectories at import time. It stays armed for the whole run rather than per test: `pytest_runtest_teardown` fires *before* fixture finalizers, which is exactly when `monkeypatch` restores the real paths — disarming there would leave a finalizer's write unguarded against a restored constant.
- `real_store_files_untouched` (session-scoped) is the backstop CLAUDE.md's line-count rule asks for: the live store files must be byte-identical after the suite. It only *fails* when the call-level guard also caught this process attempting the write — the desk may legitimately be running alongside pytest, and an otherwise unexplained change is reported as a warning instead of a false failure.
- `tests/test_store_isolation.py` pins all of it, the way `test_offline_guard.py` pins the Kite guard: a renamed store constant makes its `_REDIRECTS` entry a no-op, and the parametrized checks fail loudly rather than letting that store quietly go back to writing live files.

This closed a live bug on 2026-08-27: `test_mcx_rolling_straddle.py`'s `_ensure_state_underlying` and `status_bundle` tests never mentioned a store, but reached `clear_spot_state_for_underlying` → `save_state` + `append_log` two frames down and rewrote the live `data/rolling_straddle_{state,log}.json` on every run. They passed in isolation and failed only with the desk running, when the test and the live runner raced for the same file. The same audit found writes to the live `arm_state.json` (`test_panic.py`), `latency_log.jsonl`, `cas_history.jsonl` and `oi_movers_prev_day_oi.json`.

**Date-sensitive fixtures must derive from `date.today()`**, never hardcode a date that is *current* when written — that is what rotted before. `test_vol_surface.py` builds weekly expiries with `_weekly_expiries()`; `test_mcx_rolling_straddle.py` derives its stale expiry as `today - 90d`. A hardcoded date that is *already past* (and meant to be) is fine; one that is *currently valid* will silently stop testing anything the moment it expires. `test_fpi_sectors.py` reads its expected values out of `data/fpi_sectors_seed.json` rather than hardcoding NSDL numbers that change whenever the seed is refreshed.

Fixing those fixtures uncovered a live bug the stale dates had been masking: `save_config` does not correct a past expiry unless the patch touches `expiry`/`underlying` — see `test_save_config_keeps_expiry_on_unrelated_patch`, which pins current behaviour so a deliberate fix shows up as a failure there.

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
