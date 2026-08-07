# 3ST Algo Desk — Interim Instruction Manual

**Version:** Interim v0.3 (2026-07-10)  
**Project path:** `c:\Users\Rustoppers\OneDrive\Desktop\3ST`  
**Stack:** FastAPI (Python) + Pixel Perfect UI (React/Vite) + Zerodha Kite Connect

This manual is the **interim operating guide** until live order execution and full production hardening are complete. Use it for daily startup, configuration, backtest, OI tracking, and watchlist workflow.

---

## Table of contents

1. [Purpose and scope](#1-purpose-and-scope)
2. [Prerequisites](#2-prerequisites)
3. [Environment setup](#3-environment-setup)
4. [Starting the application](#4-starting-the-application)
5. [Kite authentication](#5-kite-authentication)
6. [Application pages](#6-application-pages)
7. [Standard daily workflow](#7-standard-daily-workflow)
8. [Morning start checklist](#8-morning-start-checklist)
9. [Configuration reference](#9-configuration-reference)
10. [Troubleshooting](#10-troubleshooting)
11. [Known limitations (interim)](#11-known-limitations-interim)
12. [Reference URLs](#12-reference-urls)

---

## 1. Purpose and scope

### What this system does today

| Capability | Status |
|------------|--------|
| Instrument search & strategy configuration | Ready |
| Multi-instrument watchlist (signal queue) | Ready |
| Backtest (points PnL, Kite/Yahoo data) | Ready |
| OI Tracker (Call/Put OI % change + activity log) | Ready |
| **Rolling Straddle** (automated ATM CE/PE, paper/live) | Ready (paper tested) |
| **Algo Execution hub** (`/execution`) | Ready |
| Options spread preview & save | Ready |
| Live signal scan (3ST entries) | Ready |
| Live Desk paper positions/orders | Ready |
| Watchlist live order placement | **Not implemented** |
| Risk limits UI ↔ API sync | **Partially broken** |

### Strategy

**3ST** — Triple SuperTrend + ADX on configurable timeframes (5/15/30/60 min). PnL in **points** (× lot size for F&O when applicable).

---

## 2. Prerequisites

- Windows PC with Python 3.11+ and Node.js installed
- Zerodha Kite Connect API key and secret in root `.env`
- Static IP proxy configured (if required by your Kite app whitelist)
- Virtual environment at `3ST\.venv\` with dependencies installed
- UI dependencies installed in `Pixel Perfect UI\node_modules\`

---

## 3. Environment setup

### Root `.env` (API)

Location: `c:\Users\Rustoppers\OneDrive\Desktop\3ST\.env`

Required for Kite:

```
KITE_API_KEY=your_api_key
KITE_API_SECRET=your_api_secret
KITE_REDIRECT_URL=http://127.0.0.1:8001/auth/callback
```

Optional: static IP proxy for **Firstock** (`STATICIP_*`). Kite uses direct connection by default:

```
KITE_USE_STATICIP_PROXY=0
```

Set to `1` only if your Kite app requires the staticip proxy (unusual).

### UI `.env`

Location: `Pixel Perfect UI\.env`

```
VITE_API_BASE_URL=http://127.0.0.1:8001
```

**Critical:** API port and `VITE_API_BASE_URL` must match. Default in code is `8000`; your setup uses **8001**.

---

## 4. Starting the application

Always use **two terminals**.

### Terminal 1 — Backend API

```powershell
cd "c:\Users\Rustoppers\OneDrive\Desktop\3ST"
.\.venv\Scripts\uvicorn.exe api.main:app --host 127.0.0.1 --port 8001 --reload
```

**Verify:** http://127.0.0.1:8001/health

### Terminal 2 — Frontend UI

```powershell
cd "c:\Users\Rustoppers\OneDrive\Desktop\3ST\Pixel Perfect UI"
npm run dev
```

**Verify:** http://localhost:8080/ (or port shown by Vite)

> **Do not** run `npm run dev` from the project root — there is no root `package.json`.

### Stopping

- Press `Ctrl+C` in both terminals
- On Live Desk, **Disarm** if the system was armed

---

## 5. Kite authentication

Required once per day (session expires).

### Method A — Browser login (recommended)

1. Open http://127.0.0.1:8001/auth/login  
2. Complete Zerodha login in the browser  
3. Confirm redirect / success message  

### Method B — UI manual token

1. Open **Kite Login** in the sidebar  
2. Paste `request_token` from the redirect URL after Zerodha login  

### Verify login

- http://127.0.0.1:8001/health → `kite_authenticated: true`  
- Settings page or `/auth/me` shows your user id  

Without login, these features fail: Kite backtest, instrument search, OI Tracker, live margins/positions.

---

## 6. Application pages

### Stock Selection (`/`)

- Search instruments (e.g. **`NIFTY 50`**, not bare `NIFTY`)
- Configure **3ST Strategy & Session:**
  - SuperTrend method: Heikin Ashi / Regular / Hybrid*
  - ST1 / ST2 / ST3 enable checkboxes + ATR/Factor
  - ADX filter, SL / Target / Trailing SL
  - Intraday vs Positional, force exit time
- **Save Selection** — draft for backtest
- **Add to Queue** — sends instrument to Dashboard watchlist

\* *Hybrid is labeled in UI; verify behavior matches your expectation.*

### Dashboard (`/dashboard`)

- **Signal queue** — multiple instruments in `waiting` status
- Periodic scan promotes items to `triggered` when 3ST entry fires
- Does **not** auto-place orders

### Backtest (`/backtest`)

- **Use saved selection** — pulls strategy from Stock Selection
- **Data source:**
  - **Kite** — up to ~400 days (15/30/60 min); requires login
  - **Yahoo** — ~60 days; NIFTY50/SENSEX only
- **Max available history** or custom **Start/End dates**
- **Trade mode:** Long only / Short only / Both
- Results: net points, long/short points, start open, end close, equity chart, trades table

### OI Tracker (`/oi-tracker`)

- Underlying: **NIFTY**, **BANKNIFTY**, **SENSEX**
- Nearest or selected **expiry**
- **Call** and **Put** tables: latest OI + % change (5/10/15/30 min)
- Threshold breach highlighting; toast + optional sound alert
- Auto-refresh (default 60s); best during market hours 9:15–15:30 IST
- **Activity log** — snapshots, alerts, errors (`GET /oi-tracker/log`)

### Algo Execution (`/execution`)

- Global **ARM / DISARM** and mode (Paper / Live)
- Links to **Rolling Straddle** and Watchlist Live Desk
- Open position count from `/live/positions`

### Rolling Straddle (`/rolling-straddle`)

Automated ATM CE/PE on 3ST signals from **9:20** onward.

| Rule | Behavior |
|------|----------|
| CE leg | Buy ATM CE on **long** 3ST (CE or PE chart) |
| PE leg | Buy ATM PE on **short** 3ST (PE or **CE chart short**) |
| Signals | Each leg uses **its own option candle chart** at ATM (or entry strike if open) |
| Exit | Zone break vs **ST1** (shown on leg card as “Zone exit (ST1)”) |
| Reentry | 1 per side per day (configurable) |
| Paper | Default; fills use live Kite LTP |

**Workflow:** Configure expiry → Save → **Start** → monitor leg cards + activity log. Use **Close leg** / **Close all** to flatten.

### Live Desk (`/live`)

- **Paper / Live** mode toggle
- **ARM / DISARM** kill switch (live Kite orders when armed + live mode)
- **Positions** and **Orders** — paper uses shared broker (`data/paper_broker.json`)
- Triggered watchlist queue → manual **Activate** (separate from Rolling Straddle)

### Settings (`/settings`)

- Paper vs live mode, risk limits (note: some fields may not sync correctly — see limitations)

### Kite Login (`/login`)

- Manual token entry fallback

---

## 7. Standard daily workflow

```
Login → Stock Selection → Backtest → OI Tracker
                ↓
     Rolling Straddle (paper test) OR Dashboard queue → Live Desk
```

| Step | Action |
|------|--------|
| 1 | Start API + UI (Section 4) |
| 2 | Kite login (Section 5) |
| 3 | Stock Selection: pick symbol, strategy, **Save Selection** |
| 4 | Backtest with **Kite + Max history** to validate strategy |
| 5 | OI Tracker during market hours |
| 6 | **Rolling Straddle:** `/rolling-straddle` → expiry, Save, **Start** (paper) |
| 7 | **Live Desk:** confirm paper positions; **ARM** only for live |
| 8 | *(Optional)* Dashboard queue + scan for manual watchlist workflow |

---

## 8. Morning start checklist

Copy this each trading morning:

- [ ] Start API on port **8001**
- [ ] Start UI from **`Pixel Perfect UI`** folder
- [ ] Confirm `VITE_API_BASE_URL=http://127.0.0.1:8001`
- [ ] Kite login completed
- [ ] `/health` shows authenticated
- [ ] Save Selection + Add to Queue for today’s symbols
- [ ] Smoke test: Backtest (Kite, 15min) + OI Tracker (NIFTY)
- [ ] Dashboard watchlist not empty

---

## 9. Configuration reference

### Kite historical data limits (intraday)

| Timeframe | Approx. max history |
|-----------|---------------------|
| 15 / 30 / 60 min | ~400 days |
| 5 min | ~100 days |

Years of history require **daily** candles (not yet in UI).

### OI Tracker defaults

| Setting | Value |
|---------|-------|
| Strikes each side | 5 |
| History window | 40 minutes |
| OI intervals | 5, 10, 15, 30 min |
| Refresh | 60 seconds |
| Alert threshold | >50% cells breached |

### Index search tips

| Goal | Search term |
|------|-------------|
| NIFTY index backtest | `NIFTY 50` |
| Bank Nifty options | `BANKNIFTY` |
| Sensex | `SENSEX` |

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| UI API errors / 404 | API not running or wrong port | Restart API on 8001; check UI `.env` |
| Flat backtest chart | Yahoo source or stale API | Kite source + Max history; restart API; hard-refresh UI |
| `npm run dev` ENOENT | Wrong directory | `cd "Pixel Perfect UI"` first |
| No instruments | Not logged in | Complete Kite login |
| OI tables empty / N/A | Outside market hours | Retry 9:15–15:30 IST |
| `[object Object]` toast | API validation error | Check browser Network tab for `detail` |
| Backtest limits 404 | Old API process | Restart uvicorn with `--reload` |
| Scan shows nothing | No queue items or no signal | Add to Queue; wait for bar close + ADX/ST rules |
| Rolling Straddle empty positions | API not restarted after paper fix | Restart uvicorn; refresh Live Desk |
| `ProxyError staticip.in` on ticks | Kite using Firstock proxy | Set `KITE_USE_STATICIP_PROXY=0`; restart API |
| PE exit not firing | Was using wrong strike chart after ATM roll | Fixed — restart API; open leg uses entry strike |
| CE Short no PE entry | Was PE-chart-only | Fixed — CE chart short triggers PE leg |

**Hard refresh UI:** `Ctrl+Shift+R`

---

## 11. Known limitations (interim)

These are documented in `docs/review/3ST_Project_Review_Gaps.pdf` — summary:

1. **Watchlist Activate** — status only; does not place broker orders
2. **Options spread execution** — preview/save only; no multi-leg orders
3. **Live scan ≠ backtest** — watchlist scan checks entry only; no full session/SL/TGT in scan
4. **Risk limits** — UI field names may not match API (`max_loss_day` vs `max_daily_loss`)
5. **Streamlit `app.py`** — legacy Yahoo UI; use Pixel Perfect UI instead
6. **Browser polling only** — scans stop when UI tab is closed
7. **PRS vs Python 3ST** — no EMA200 / NO TRADE zone conflict in algo (pure 3ST + ADX on option charts)

**Rolling Straddle** supports paper and live (when ARMED). Do not ARM live until you accept broker risk.

---

## 12. Reference URLs

| Resource | URL |
|----------|-----|
| UI (main) | http://localhost:8080/ |
| API | http://127.0.0.1:8001 |
| API Swagger | http://127.0.0.1:8001/docs |
| Kite login | http://127.0.0.1:8001/auth/login |
| Health check | http://127.0.0.1:8001/health |

### Related docs

| Document | Path |
|----------|------|
| **Conversation summary (recent sessions)** | `docs/CONVERSATION_SUMMARY.md` |
| Morning checklist (short) | `docs/MORNING_START_CHECKLIST.md` |
| Gap analysis PDF | `docs/review/3ST_Project_Review_Gaps.pdf` |
| Gap analysis markdown | `docs/review/3ST_Project_Review_Gaps.md` |

---

## Document control

| Field | Value |
|-------|-------|
| Title | 3ST Algo Desk — Interim Instruction Manual |
| Status | Interim / for internal use |
| Next review | After live Rolling Straddle soak test |

*End of interim manual.*
