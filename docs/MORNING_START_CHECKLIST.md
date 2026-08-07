# 3ST — Morning Start Checklist

Use this each trading day before using Stock Selection, Backtest, OI Tracker, or Live Desk.

---

## 1. One-time / verify (if something broke overnight)

- [ ] Project folder: `c:\Users\Rustoppers\OneDrive\Desktop\3ST`
- [ ] Root `.env` has `KITE_API_KEY`, `KITE_API_SECRET` (and static IP proxy vars if you use them)
- [ ] UI `.env`: `Pixel Perfect UI\.env` → `VITE_API_BASE_URL=http://127.0.0.1:8001`
- [ ] Python venv exists: `3ST\.venv\`

---

## 2. Start API (Terminal 1)

```powershell
cd "c:\Users\Rustoppers\OneDrive\Desktop\3ST"
.\.venv\Scripts\uvicorn.exe api.main:app --host 127.0.0.1 --port 8001 --reload
```

**Check:** open http://127.0.0.1:8001/health → should show `ok` / Kite status.

**Do NOT** run `npm run dev` from project root — there is no `package.json` there.

---

## 3. Start UI (Terminal 2)

```powershell
cd "c:\Users\Rustoppers\OneDrive\Desktop\3ST\Pixel Perfect UI"
npm run dev
```

**Check:** open http://localhost:8080/ (or the port Vite prints).

---

## 4. Kite login (once per day)

1. UI → **Kite Login** (sidebar footer), or API → http://127.0.0.1:8001/auth/login
2. Complete Zerodha login → redirect / paste `request_token` if using manual flow
3. **Check:** Settings or `/auth/me` shows your user (e.g. OV7159)

Without login: backtest (Kite source), OI Tracker, instrument search, and live data **will fail**.

---

## 5. Daily workflow (recommended order)

| Step | Page | Action |
|------|------|--------|
| 1 | **Stock Selection** | Search instrument (e.g. `NIFTY 50`), set 3ST strategy, **Save Selection** |
| 2 | **Stock Selection** | **Add to Queue** for each symbol you want on Dashboard |
| 3 | **Backtest** | Source **Kite**, **Max history**, Run Backtest — review net/long/short points |
| 4 | **OI Tracker** | Pick NIFTY/BANKNIFTY/SENSEX, enable auto-refresh during market hours |
| 5 | **Dashboard** | Watchlist queue — waiting → triggered on scan |
| 6 | **Live Desk** | Review signals; **ARM** only when ready (orders not fully automated yet) |

---

## 6. Quick smoke tests

- [ ] **Backtest:** NIFTY 50 · 15min · Kite · Max history → chart + trades
- [ ] **OI Tracker:** NIFTY · nearest expiry → Call/Put tables (best during 9:15–15:30 IST)
- [ ] **Dashboard:** at least one watchlist item in `waiting` status

---

## 7. Common issues

| Problem | Fix |
|---------|-----|
| UI shows API errors / 404 on `/backtest/limits` | Restart API on **8001**; hard-refresh UI (`Ctrl+Shift+R`) |
| `[object Object]` toast | Usually validation error — check API message in network tab |
| Backtest flat / short period | Use **Kite** source + **Max available history** (not Yahoo) |
| `npm run dev` fails at root | Run from `Pixel Perfect UI` folder only |
| Port mismatch | UI must point to same port as API (`8001` in both places) |
| Instrument search empty | Kite login + wait for instrument cache refresh |

---

## 8. Stop for the day

- Ctrl+C in both terminals (API + UI)
- **Disarm** on Live Desk if you had armed the system

---

## URLs (bookmark)

| Service | URL |
|---------|-----|
| UI | http://localhost:8080/ |
| API | http://127.0.0.1:8001 |
| API docs | http://127.0.0.1:8001/docs |
| Kite login | http://127.0.0.1:8001/auth/login |

---

*Last updated: 2026-07-09 — includes OI Tracker page and Kite backtest fixes.*
