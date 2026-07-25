# 3ST Instruction Manual

**3ST** is a Kite Connect algo platform with a React UI, execution desk, OI analytics, and ported strategies from [trading-algo](https://github.com/Raahi-Bhushan/trading-algo) (Survivor & Wave).

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Environment configuration](#3-environment-configuration)
4. [Starting the platform](#4-starting-the-platform)
5. [Kite login](#5-kite-login)
6. [Safety: paper, live, ARM / DISARM](#6-safety-paper-live-arm--disarm)
7. [UI map](#7-ui-map)
8. [Execution hub](#8-execution-hub)
9. [Survivor strategy](#9-survivor-strategy)
10. [Wave strategy](#10-wave-strategy)
11. [Rolling Straddle](#11-rolling-straddle)
12. [OI Tracker](#12-oi-tracker)
13. [OI VAR Live Desk](#13-oi-var-live-desk)
14. [Backtest](#14-backtest)
15. [Daily workflow checklist](#15-daily-workflow-checklist)
16. [Git & repository](#16-git--repository)
17. [Troubleshooting](#17-troubleshooting)
18. [File reference](#18-file-reference)
19. [Analytics & safety additions (RRG, FPI, Panic, LTP cache)](#19-analytics--safety-additions-rrg-fpi-panic--ltp-cache)

---

## 1. Prerequisites

| Requirement | Notes |
|-------------|-------|
| **Windows 10/11** | Current setup path: `Desktop\3ST` |
| **Python 3.10+** | Backend API and strategies |
| **Node.js 18+** | Pixel Perfect UI |
| **Git** | Optional; repo at `RajasekaranPeriyasamy-cloud/3ST` |
| **Zerodha Kite Connect app** | API key + secret from [developers.kite.trade](https://developers.kite.trade/) |
| **trading-algo clone** | Required for Survivor/Wave — must live at `Desktop\trading-algo` |

Clone trading-algo (if missing):

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop"
git clone https://github.com/Raahi-Bhushan/trading-algo.git trading-algo
```

---

## 2. Installation

### Backend

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST"
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

### Frontend

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST\Pixel Perfect UI"
npm install
```

Ensure `Pixel Perfect UI\.env` points at the API:

```
VITE_API_BASE_URL=http://127.0.0.1:8001
```

---

## 3. Environment configuration

Edit `.env` in the 3ST root (never commit this file):

```env
KITE_API_KEY=your_api_key
KITE_API_SECRET=your_api_secret
KITE_REDIRECT_URL=http://127.0.0.1:8001/auth/callback
```

| Variable | Purpose |
|----------|---------|
| `KITE_API_KEY` | Kite Connect app key |
| `KITE_API_SECRET` | Kite Connect app secret |
| `KITE_REDIRECT_URL` | Must match **exactly** in Kite developer portal and `.env` |
| `KITE_USE_STATICIP_PROXY=0` | Use direct connection (recommended unless you need static IP proxy) |

Session file (auto-created, gitignored): `data/kite_session.json`

---

## 4. Starting the platform

**Default workflow:** two terminals (API + Vite dev server). Open the UI at **http://127.0.0.1:5173**.

**Terminal 1 — API**

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST"
.\.venv\Scripts\activate
uvicorn api.main:app --reload --host 127.0.0.1 --port 8001
```

**Terminal 2 — UI**

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST\Pixel Perfect UI"
npm run dev
```

Ensure `Pixel Perfect UI\.env` has:

```
VITE_API_BASE_URL=http://127.0.0.1:8001
```

### URLs (default)

| What | URL |
|------|-----|
| UI | http://127.0.0.1:5173 |
| API / Kite auth | http://127.0.0.1:8001 |
| Swagger | http://127.0.0.1:8001/docs |
| Health | http://127.0.0.1:8001/health |

### Optional — single localhost (planned, not default)

One-command / one-URL startup is **spec’d in the Cursor plan** *Single-host 3ST setup* (launcher scripts and FastAPI static UI are scaffolded but **not the default daily workflow yet**).

When you adopt it later:

| Mode | Command | URL |
|------|---------|-----|
| Daily (one process) | `.\scripts\start_3st.ps1` | http://127.0.0.1:8001 |
| Dev (one script, hot reload) | `.\scripts\start_3st_dev.ps1` | UI :5173, API :8001 |
| Stop | `.\scripts\stop_3st.ps1` | — |

Requires `npm run build` in `Pixel Perfect UI` before daily single-port mode. See the plan for architecture and rollout steps.

---

## 5. Kite login

1. Start the API (port **8001** must match `KITE_REDIRECT_URL`).
2. Open http://127.0.0.1:8001/auth/login  
   Or use the **Login** page in the UI.
3. Click **Login with Zerodha** and complete broker login.
4. On success, session is saved to `data/kite_session.json`.

**Token expiry:** Kite access tokens typically expire around **6:00 AM IST** the next day. Re-login each trading morning.

**Required for:** live quotes, Survivor/Wave start, OI Tracker/VAR snapshots, Kite backtests.

---

## 6. Safety: paper, live, ARM / DISARM

All strategies default to **safe mode**.

| State | Behavior |
|-------|----------|
| **Paper + DISARMED** (default) | Real quotes; orders **simulated** locally |
| **Live + DISARMED** | Live mode selected but **no orders** sent to Kite |
| **Live + ARMED** | Real orders allowed |

### Enabling live trading

Use the **Execution** hub (`/execution`) or API:

1. **Set mode to live** — `POST /live/mode` with `{ "mode": "live" }`
2. **ARM** — `POST /live/arm` with `{ "confirm": true }`
3. To stop live orders: **DISARM** — `POST /live/disarm`

Switching back to **paper** automatically disarms.

> **Warning:** Survivor accumulates short option positions with no automatic exit. Wave places paired limit orders rapidly. Always test in **paper** first.

---

## 7. UI map

| Route | Purpose |
|-------|---------|
| `/` | Home / dashboard |
| `/login` | Kite auth status |
| `/execution` | Execution hub — ARM/DISARM, strategy links |
| `/survivor` | Survivor algo control |
| `/wave` | Wave algo control |
| `/rolling-straddle` | Rolling ATM straddle algo |
| `/oi-tracker` | Open interest tracker with breach highlights |
| `/oi-var` | OI VAR live desk (Top/Bottom 10 CE/PE) |
| `/rrg` | Relative Rotation Graph + NSDL FPI sector overlay |
| `/backtest` | Historical backtests (Yahoo / Kite) |
| `/live` | Live positions / orders |
| `/settings` | App settings |

---

## 8. Execution hub

**Path:** `/execution`

Central control panel for automated strategies:

- View global **mode** (paper / live) and **ARM** status
- Jump to Rolling Straddle, Survivor, Wave
- Monitor scheduler health

**Recommended flow:**

1. Confirm Kite login
2. Confirm **paper** mode and **DISARMED**
3. Open a strategy page → configure → **Save** → **Start**
4. Watch status badges and log panel
5. Only after paper validation → switch to live + ARM

---

## 9. Survivor strategy

### What it does

**Survivor** sells index **options** when spot moves beyond reference levels:

| Market move | Action |
|-------------|--------|
| Index **rises** above PE reference by `pe_gap` | **Sell PE** (puts) |
| Index **falls** below CE reference by `ce_gap` | **Sell CE** (calls) |

- Order type: **MARKET sell**
- Instruments: NFO index options (NIFTY / BANKNIFTY / SENSEX)
- **No automatic exit** — manages references and resets only
- Poll interval: **~15 seconds** (spot quote)

### Logic summary

1. Maintain two reference prices: PE ref and CE ref
2. On trigger: compute multiplier = `floor(price_move / gap)`
3. Select OTM strike ~`symbol_gap` points from spot
4. Skip if premium &lt; `min_price_to_sell` or multiplier &gt; threshold
5. After trade, advance reference; optional reset on favorable move

### UI steps (`/survivor`)

1. **Save config** (see parameters below)
2. **Start** — requires Kite session
3. Monitor: **Spot**, **PE ref**, **CE ref**, log entries
4. **Tick now** — manual poll (debug)
5. **Stop** — halts runner (does not close open positions at broker)

### Config parameters

| Field | Default | Meaning |
|-------|---------|---------|
| Underlying | NIFTY | NIFTY, BANKNIFTY, or SENSEX |
| Expiry | auto | Option expiry date |
| PE gap / CE gap | 20 | Points move to trigger sell |
| PE qty / CE qty | 75 | Base quantity per multiplier |
| PE symbol gap / CE symbol gap | 200 | Strike distance from spot |
| Min price to sell | 15 | Minimum option premium (₹) |
| Sell multiplier threshold | 5 | Max gap multiplier |
| Tick interval (s) | 15 | Poll frequency |
| Product type | NRML | Order product |
| Tag | Survivor | Order tag on Kite |

### State persistence

Saved under `data/`:

- `survivor_config.json` — settings
- `survivor_state.json` — PE/CE references, last spot (survives restart)
- `survivor_log.json` — event log

---

## 10. Wave strategy

### What it does

**Wave** runs a **limit-order pair** on a single symbol (usually **NIFTY futures**):

1. Place **sell limit** above spot and **buy limit** below spot
2. When one leg **fills**, cancel the other
3. Place a **new wave** (new pair)
4. Widen gaps when position is imbalanced; enforce portfolio **delta** limits

- Order type: **LIMIT**
- Default symbol: `NIFTY25SEPFUT` — **update on expiry roll**
- Check interval: **~60 seconds** + orderbook poll each tick

### UI steps (`/wave`)

1. Set **Symbol** to current month futures (e.g. `NIFTY25OCTFUT`)
2. **Save config**
3. **Start**
4. Monitor: **Spot**, **Active orders**, log
5. **Stop** when done

### Config parameters

| Field | Default | Meaning |
|-------|---------|---------|
| Symbol | NIFTY25SEPFUT | Futures tradingsymbol |
| Exchange | NFO | Exchange code |
| Buy gap / Sell gap | 25 | Points below/above spot for limits |
| Buy qty / Sell qty | 75 | Quantity per leg |
| Lot size | 75 | Used for imbalance scaling |
| Cool-off (s) | 10 | Wait between price samples inside a wave |
| Check interval (s) | 60 | New wave / restriction cycle |
| Tag | WaveScraper | Filters Kite orderbook |
| Delta limits | ±100 | Portfolio NIFTY/BANKNIFTY delta bands |

### Important notes

- Active orders are mostly **in-memory** — stopping the runner loses tracking (broker orders may still exist)
- In **paper** mode, fills are instant; live order polling behaves differently
- Update `symbol_name` every futures expiry

### State files

- `wave_config.json`, `wave_state.json`, `wave_log.json`

---

## 11. Rolling Straddle

**Path:** `/rolling-straddle`

Native 3ST strategy (not from trading-algo):

- Sells ATM **CE + PE** on index signals from **9:20 IST**
- Rolls strike as spot moves
- CE and PE managed independently with reentry caps

**Controls:** Save config → Start / Stop → Close leg / Close all

See `docs/CONVERSATION_SUMMARY.md` for rolling straddle specifics.

---

## 12. OI Tracker

**Path:** `/oi-tracker`

Live open-interest dashboard:

- Snapshots by underlying and expiry
- **OI % change** with breach highlighting (vibrant up/down colors)
- Requires Kite session for live chain data

Read-only analytics — does not place orders.

---

## 13. OI VAR Live Desk

**Path:** `/oi-var`

Options analytics desk showing **Top/Bottom 10** tables for CE and PE:

| Column | Description |
|--------|-------------|
| Strike | Option strike |
| OI | Open interest |
| LTP | Last traded price |
| VAR (Cr) | `(OI × LTP) / 1e7` — value at risk in crores |
| VWAP | Volume-weighted average price |
| EOD OI chg | End-of-day OI change |

Full chain available; refreshes from Kite during market hours.

---

## 14. Backtest

**Path:** `/backtest`

Run historical simulations:

| Source | Login required | History depth |
|--------|----------------|---------------|
| **Yahoo** | No | ~60 days intraday |
| **Kite** | Yes | Deeper via Kite historical API |

Strategy: Triple HA SuperTrend + ADX (see `strategy_3st.py`).

---

## 15. Daily workflow checklist

### Before market open

- [ ] Start API on port **8001** (`uvicorn api.main:app --reload --host 127.0.0.1 --port 8001`)
- [ ] Start UI (`npm run dev` in `Pixel Perfect UI`)
- [ ] Open http://127.0.0.1:5173
- [ ] Kite login (`/login` or `/auth/login`)
- [ ] Confirm **paper** mode, **DISARMED**
- [ ] Verify `trading-algo` folder exists at `Desktop\trading-algo`
- [ ] Update Wave **futures symbol** if expiry rolled

### During market

- [ ] OI Tracker / OI VAR for analytics
- [ ] Paper-test Survivor or Wave; watch logs
- [ ] Rolling Straddle paper run if using

### Before live

- [ ] Paper session reviewed — orders, references, no errors
- [ ] Risk limits checked (`/risk/limits` or `risk/limits.py`)
- [ ] Switch mode → **live** → **ARM** with confirm
- [ ] Monitor `/live` for positions and orders

### After market

- [ ] **DISARM** and switch to **paper**
- [ ] **Stop** all runners
- [ ] Review logs in `data/*_log.json`

---

## 16. Git & repository

**Remote:** https://github.com/RajasekaranPeriyasamy-cloud/3ST

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST"
git status
git add .
git commit -m "Your message"
git push origin main
```

**Never commit:** `.env`, `data/kite_session.json`, `.venv`, `node_modules`

---

## 17. Troubleshooting

| Problem | Solution |
|---------|----------|
| `trading-algo not found` | Clone to `Desktop\trading-algo` |
| Kite session required | Re-login at `/auth/login` |
| UI blank on :8001 | Single-host mode without UI build | Use default workflow (:5173) or see single-host plan |
| UI cannot reach API | API not running or wrong port | Start API on 8001; check `VITE_API_BASE_URL` in UI `.env` |
| Redirect URL mismatch | Align Kite portal, `.env`, and API port |
| Survivor not trading | Check gaps vs spot move; premium floor; runner RUNNING |
| Wave no new orders | Check symbol name, delta limits, active orders count |
| Live orders blocked | Need `mode=live` **and** ARM with `confirm=true` |
| Static IP / proxy errors | Set `KITE_USE_STATICIP_PROXY=0` in `.env` |
| Git not on PATH | Use `"C:\Program Files\Git\bin\git.exe"` or restart Cursor |
| Token expired (~6 AM IST) | Re-login to Kite |

### Manual API test

```powershell
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/live/survivor/status
curl http://127.0.0.1:8001/live/wave/status
```

---

## 18. File reference

| Path | Role |
|------|------|
| `api/main.py` | FastAPI routes |
| `api/ui_static.py` | Optional: serves built UI on :8001 (single-host plan) |
| `analysis/rrg.py` | RRG engine — weekly RS ratio / momentum from Kite daily data |
| `analysis/fpi_sectors.py` | NSDL fortnightly sector FPI parser + RRG overlay |
| `execution/ltp_cache.py` | Live LTP cache (Aio-Trader KiteFeed WS + REST fallback) |
| `execution/panic.py` | Panic kill-switch — close trades, cancel 3ST orders, DISARM |
| `execution/live_workflow.py` | Live Desk 7-step checklist + execution validation |
| `execution/positions_view.py` | Kite-style grouped positions view |
| `execution/desk_trades.py` | Active-trade view (LTP, P&L, exit levels) |
| `execution/watchlist_exit_runner.py` | Exit monitor (grace, TSL, ST bands, force exit) |
| `data/fpi_sectors_seed.json` | Bundled FPI fallback (only when NSDL fetch + cache both fail) |
| `scripts/start_3st.ps1` | Optional plan: daily single-port launcher |
| `scripts/start_3st_dev.ps1` | Optional plan: dev launcher (API + Vite) |
| `scripts/stop_3st.ps1` | Stop processes on ports 8001 / 5173 |
| `execution/scheduler.py` | Background strategy ticks |
| `execution/survivor_runner.py` | Survivor poll loop |
| `execution/wave_runner.py` | Wave poll loop |
| `execution/kite_strategy_adapter.py` | Bridge to trading-algo broker interface |
| `execution/trading_algo_path.py` | Loads `Desktop/trading-algo` |
| `execution/arming.py` | Global ARM / DISARM |
| `broker/` | Paper + Kite brokers |
| `options/oi_tracker.py` | OI Tracker engine |
| `options/oi_var.py` | OI VAR engine |
| `Pixel Perfect UI/src/routes/` | UI pages |
| `docs/KITE_SETUP.md` | Detailed Kite setup |
| `docs/CONVERSATION_SUMMARY.md` | Recent platform changes |

**Original strategy source (trading-algo):**

- `strategy/survivor.py` — Survivor logic
- `strategy/wave.py` — Wave logic

---

## 19. Analytics & safety additions (RRG, FPI, Panic, LTP cache)

### RRG — Relative Rotation Graph

**Path:** `/rrg` · Engine: `analysis/rrg.py`

Weekly **RS-Ratio / RS-Momentum** rotation chart (RRG-Lite parity) built from **Kite daily** candles resampled to weekly.

- **Benchmark:** NIFTY 50 / NIFTY BANK / SENSEX
- **Symbols:** NSE equities (`RELIANCE`, `TCS`, …) or sector ids (`NIFTY_IT`, `NIFTY_PHARMA`, …)
- **Presets:** Sector rotation, Nifty 50 sample, Bank Nifty sample
- **Quadrants:** Leading (green), Weakening (yellow), Lagging (red), Improving (blue)
- **Params:** window 14, period 52, tail 4, lookback ~900 days (defaults)
- Requires **Kite login** for daily history; daily closes cached per day in memory

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/rrg/config` | Benchmarks, sectors, presets, FPI periods |
| GET | `/rrg/snapshot` | Compute RRG (`benchmark`, `symbols`, `include_fpi`, `fpi_period`) |
| POST | `/rrg/cache/clear` | Clear daily-close cache |

### FPI sector overlay (NSDL)

Engine: `analysis/fpi_sectors.py`

Overlays **NSDL fortnightly sector-wise FII/FPI net equity** onto RRG rows and computes a **confluence** signal (aligned / divergence / watch / contrarian) vs the RRG quadrant.

- Source: NSDL fortnightly report URL (`FPI_DEFAULTS.report_url` in `config.py`); cached 24h in `data/fpi_sectors.json`
- Fallback order: live fetch → fresh cache → stale cache → bundled `data/fpi_sectors_seed.json`
- Periods: fortnight 1, fortnight 2, month total

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/rrg/fpi` | FPI status + sector→RRG mappings |
| GET | `/rrg/fpi/latest` | Latest parsed FPI data |
| POST | `/rrg/fpi/refresh` | Force refetch from NSDL |

### Panic (kill-switch)

Engine: `execution/panic.py` · `POST /live/panic` (requires `{ "confirm": true }`)

1. Square off active Live-Desk trades (live/paper per trade mode)
2. Cancel open exchange orders tagged `3ST*`
3. **DISARM** the desk

Panic runs even when **DISARMED** — `arming.require_armed_for_live()` bypasses the ARM gate while `panic_mode()` is active so square-offs go through.

### LTP cache

Engine: `execution/ltp_cache.py`

Live last-traded-price cache for Live-Desk exits. **Primary:** Aio-Trader **KiteFeed WebSocket** (`ltp` mode) when `aio-trader` is installed; **fallback:** Kite REST quote/LTP on cache miss or when a tick is stale (TTL).

- Started/stopped with the API lifespan; restart after Kite login via `POST /live/ltp-cache/restart`
- Health: `GET /live/ltp-cache`
- Optional dependency: `pip install git+https://github.com/BennyThadikaran/Aio-Trader.git` (REST-only works without it)
- Tunables (`.env`): `LTP_CACHE_WS`, `LTP_CACHE_TTL_SEC`, `LTP_CACHE_REFRESH_SEC`, `LTP_CACHE_REST_FALLBACK`

---

## Architecture overview

```
UI (React)  →  FastAPI  →  Runner  →  KiteStrategyAdapter  →  trading-algo Strategy
                                    ↓
                              PaperBroker | KiteBroker  →  Zerodha Kite API
```

Scheduler ticks running strategies in the background while the API is up.

---

*Last updated: July 2026 — 3ST platform with Survivor, Wave, OI Tracker, OI VAR, Rolling Straddle.*
