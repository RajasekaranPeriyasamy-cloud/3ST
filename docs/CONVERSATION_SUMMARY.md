# 3ST Project — Conversation Summary

**Last updated:** 2026-07-10  
**Project path:** `C:\Users\Rustoppers\OneDrive\Desktop\3ST`

This file captures recent development context from Cursor agent sessions. Full chat logs live in Cursor agent-transcripts (not in this repo).

---

## Current platform (2026-07-10)

| Layer | Stack |
|-------|--------|
| Backend | FastAPI (`api/main.py`), uvicorn port **8001** |
| UI | Pixel Perfect UI (React/Vite), `VITE_API_BASE_URL=http://127.0.0.1:8001` |
| Broker | Kite Connect (live) + shared **PaperBroker** singleton |
| Data | Kite historical/OI; Yahoo fallback for backtest |

---

## Rolling Straddle Algo (implemented)

Dedicated page: **`/rolling-straddle`** · Hub: **`/execution`**

### Strategy rules (agreed)

| Signal | Action |
|--------|--------|
| **Long 3ST** (CE chart or PE chart long) | Buy ATM **CE** |
| **Short 3ST** (PE chart or **CE chart short**) | Buy ATM **PE** |
| Start | After first closed **9:20** bar on chosen timeframe |
| ATM | Rolls with spot; entries use current ATM when flat |
| Legs | CE and PE **independent**; both may be open; **1 reentry per side** |
| Paper | Simulated fills; Live requires **Live mode + ARM** |

### Signal source (important)

3ST runs on **each option leg’s own candles** (ATM CE chart / ATM PE chart), **not** on a wrong index token and not only on NIFTY index.

**Cross-chart triggers (TradingView parity):**

- CE chart **short** → triggers **PE leg** entry (`ce_chart_short_entry`)
- PE chart **long** → triggers **CE leg** entry (`pe_chart_long_*`)
- Open leg keeps **entry strike** for signals after ATM roll (exit uses open contract chart, not rolled ATM)

### Zone exits

| Leg | Exit when |
|-----|-----------|
| CE long | Close/LTP **below ST1** (`long_zone_exit`) or PE short zone |
| PE short | Close/LTP **above ST1** (`short_zone_exit`) or CE long zone |

UI shows **Zone exit (ST1)** level, LTP, and trigger status on each leg card.

### Key backend files

| File | Role |
|------|------|
| `execution/rolling_straddle.py` | Tick loop, entries/exits, per-leg signals |
| `execution/rolling_straddle_store.py` | Config/state/log JSON in `data/` |
| `execution/order_executor.py` | Orders + risk checks |
| `execution/scheduler.py` | Background tick (~60s) |
| `options/legs.py` | `build_atm_leg()` |
| `backtest_rolling_atm.py` | Rolling ATM backtest |

### API routes

- `GET/POST /live/rolling-straddle/config`
- `GET /live/rolling-straddle/status|log`
- `POST /live/rolling-straddle/start|stop|tick|close-all|close-leg`

---

## Live Desk & paper trading fixes

### Empty Positions (fixed)

- **Cause:** Separate `PaperBroker()` instances in API vs algo; stale in-memory state.
- **Fix:** `get_paper_broker()` singleton + `data/paper_broker.json`; reload on read; `sync_paper_from_rolling_straddle()` on `/live/positions` and `/live/orders`.

### Wrong CE fill price in paper (fixed)

- **Cause:** Fallback `spot × 0.01` when LTP missing (~242 instead of ~110).
- **Fix:** `_seed_paper_ltp()` uses Kite live quote for option symbol.

---

## Kite / proxy fix (2026-07-10)

**Symptom:** `ProxyError: Failed to resolve 'dc-mum-601.staticip.in'` on historical candles.

**Cause:** Kite client used Firstock **staticip.in** proxy from `.env` (`STATICIP_*`).

**Fix:**

- Kite connects **direct by default** (`KITE_USE_STATICIP_PROXY=0` or unset).
- Historical/OI fetches retry without proxy on proxy errors.
- Static IP proxy remains for **Firstock** only.

Add to `.env` if needed:

```env
KITE_USE_STATICIP_PROXY=0
```

---

## OI Tracker activity log (2026-07-10)

| File | Role |
|------|------|
| `options/oi_tracker_store.py` | Log persistence (`data/oi_tracker_log.json`) |
| `GET /oi-tracker/log?limit=50` | Activity log API |

Events: `snapshot`, `alert` (breach threshold), `error`. UI card at bottom of **`/oi-tracker`**.

---

## Bugs fixed (session history)

| Issue | Root cause | Fix |
|-------|------------|-----|
| Rolling Straddle status showed “Kite not logged in” | `status_bundle` name collision in `api/main.py` | Alias `rs_status_bundle` |
| CE entry at wrong time / no PE entry | Index token resolved to **NIFTY ETF** (~273) not NIFTY 50 | Removed broken search; per-leg option candles |
| PE exit not firing at 124 | Signals computed on **rolled ATM** PE, not **open leg** strike | `_signal_strike_for_leg()` |
| CE Short not triggering PE | Only PE chart checked for PE entry | Cross-chart: CE short → PE leg |
| npm / PowerShell | `npm.ps1` blocked | Use `npm.cmd run dev` |
| `max_qty` blocked NIFTY lot | Default 25 | Raised to 500 in `risk/limits.py` |

---

## How to run (daily)

### Terminal 1 — API

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST"
.\.venv\Scripts\uvicorn.exe api.main:app --host 127.0.0.1 --port 8001 --reload
```

### Terminal 2 — UI

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST\Pixel Perfect UI"
npm.cmd run dev
```

### Kite login

- `.env`: `KITE_REDIRECT_URL=http://127.0.0.1:8001/auth/callback`
- Login via UI or http://127.0.0.1:8001/auth/login (re-login ~daily after token expiry)

### Rolling Straddle workflow

1. Kite logged in · Paper mode for testing  
2. **`/rolling-straddle`** → set expiry, load 3ST from Stock Selection, Save  
3. **Start** algo (scheduler ticks automatically)  
4. **`/live`** → Positions/Orders (paper)  
5. Live + **ARM** only when ready  

---

## UI routes (sidebar)

| Route | Purpose |
|-------|---------|
| `/` | Stock Selection |
| `/dashboard` | Watchlist queue |
| `/backtest` | Historical backtest |
| `/oi-tracker` | OI % change + activity log |
| `/execution` | Algo hub (global ARM, strategy links) |
| `/rolling-straddle` | Rolling Straddle control + activity log |
| `/live` | Live Desk (ARM, positions, orders) |
| `/settings` | Risk limits, mode |

---

## Data files (`data/`)

| File | Contents |
|------|----------|
| `kite_session.json` | Kite access token |
| `kite_instruments.json` | Instrument cache |
| `rolling_straddle_config.json` | Algo config |
| `rolling_straddle_state.json` | CE/PE legs, ATM, runner |
| `rolling_straddle_log.json` | Algo activity log |
| `paper_broker.json` | Paper positions/orders |
| `oi_tracker_log.json` | OI snapshot/alert log |

---

## Known gaps / TV differences

1. **PRS extras** — UI Pine may use EMA200, zone conflict (“NO TRADE”); Python uses pure **3ST + ADX** on option candles.
2. **Exits on bar close** — Primary exit uses last **closed** 5m bar; LTP cross of ST1 added as backup.
3. **ATM roll** — Logs roll; does not auto-switch open leg symbol (leg stays on entry strike until exit).
4. **Watchlist Live Desk** — Scan/queue still manual; separate from Rolling Straddle automation.

---

## Earlier history (2026-07-09 and before)

- Original goal: 3ST backtester (Streamlit + Yahoo).
- Firstock path blocked → Kite Connect + FastAPI + Pixel Perfect UI.
- OI Tracker page, watchlist, backtest, spread preview added.
- Rolling Straddle planned and implemented (see plan: `.cursor/plans/rolling_straddle_algo_be2a65a7.plan.md` — do not edit plan file per user rule).

---

## Related docs

| Document | Path |
|----------|------|
| Interim instruction manual | `docs/INTERIM_INSTRUCTION_MANUAL.md` |
| Kite setup | `docs/KITE_SETUP.md` |
| Morning checklist | `docs/MORNING_START_CHECKLIST.md` |
| Gap review | `docs/review/3ST_Project_Review_Gaps.md` |
