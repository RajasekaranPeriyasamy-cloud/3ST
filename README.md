# 3ST · Kite Algo Platform

Python trading core for **Triple HA SuperTrend + ADX** (EMA200 removed) with:

- **FastAPI** backend for Lovable / React UI — [docs/LOVABLE_UI_SPEC.md](docs/LOVABLE_UI_SPEC.md)
- **Zerodha Kite Connect** auth, historical backtests, ARM/DISARM — [docs/KITE_SETUP.md](docs/KITE_SETUP.md)
- **Yahoo Finance** fallback backtests (no broker login)
- Legacy **Streamlit** UI: `streamlit run app.py`

## Quick start (API)

```bash
cd 3ST
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Add to `.env`:

```
KITE_API_KEY=...
KITE_API_SECRET=...
KITE_REDIRECT_URL=http://127.0.0.1:5173/
```

```bash
uvicorn api.main:app --reload --host 127.0.0.1 --port 8001
```

- Swagger: http://127.0.0.1:8001/docs  
- Health: http://127.0.0.1:8001/health  

See **`docs/CONVERSATION_SUMMARY.md`** for Rolling Straddle, paper trading, and recent fixes.

## Lovable UI

1. Open Lovable and paste the prompt from [docs/LOVABLE_UI_SPEC.md](docs/LOVABLE_UI_SPEC.md)
2. Set `VITE_API_BASE_URL=http://127.0.0.1:8000`
3. Sync to GitHub → place under `web/`
4. Never put `KITE_API_SECRET` in the frontend

## Layout

| Path | Role |
|------|------|
| `api/main.py` | FastAPI routes |
| `kite_auth.py` | Login / session |
| `kite_client.py` | Historical + margins |
| `instruments.py` | Token cache |
| `broker/` | Paper + Kite brokers |
| `execution/arming.py` | ARM / DISARM |
| `risk/limits.py` | Risk gates |
| `execution/rolling_straddle.py` | Rolling ATM CE/PE algo |
| `execution/scheduler.py` | Background algo ticks |
| `options/oi_tracker.py` | OI Tracker snapshots |
| `strategy_3st.py` / `backtest_engine.py` | Signals + backtest |
| `yahoo_client.py` | Yahoo OHLC |

## Safety

- Live orders default **DISARMED**
- Paper mode never hits Kite order APIs
- Session file: `data/kite_session.json` (gitignored)
