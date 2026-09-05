# AGENTS.md

## Cursor Cloud specific instructions

This repo is a single Indian-markets algo trading platform (3ST — Triple Heikin-Ashi SuperTrend + ADX). There is no database; runtime state is local JSON under `data/` (auto-created, gitignored).

### Services

| Service | Dir | Start command | Port |
|---------|-----|---------------|------|
| FastAPI backend | repo root | `./.venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8001 --reload` | 8001 |
| React UI (Vite / TanStack Start) | `Pixel Perfect UI/` | `npm run dev` | 8080 |
| Legacy Streamlit UI (optional) | repo root | `./.venv/bin/streamlit run app.py` | 8501 |

Python deps live in a root `.venv` (created by the update script). Always invoke Python tools via `./.venv/bin/...` — there is no repo-root `package.json`, so never run `npm` from the root (only inside `Pixel Perfect UI/`).

### Env files (not committed; both gitignored)

- Root `.env` — copied from `.env.example`. Holds `KITE_API_KEY` / `KITE_API_SECRET` etc. Empty values are fine for local dev; the API boots without them.
- `Pixel Perfect UI/.env` — must contain `VITE_API_BASE_URL=http://127.0.0.1:8001` so the UI targets the API port. If this is missing the UI falls back to port 8000 and API calls 404.

The update script recreates these from templates only if absent, so it will not clobber real credentials.

### Kite (Zerodha) login

Kite Connect requires real broker credentials + interactive OAuth, so instrument search, OI Tracker, live desk, and `source=kite` backtests will fail without login. This is expected in a fresh cloud VM.

Non-obvious gotchas:
- `settings.py` calls `load_dotenv(".env", override=True)`, so values in root `.env` OVERRIDE process env vars. Injecting `KITE_API_KEY`/`KITE_API_SECRET` only as environment variables is not enough — they must be written into root `.env` (and the placeholder empty `KITE_API_KEY=` lines removed/replaced), otherwise the empty `.env` values win and `kite_configured` stays false.
- The Kite login flow is `GET /auth/login` → "Login with Zerodha" → `kite.zerodha.com` → after entering Zerodha user ID + password + TOTP it redirects to `KITE_REDIRECT_URL` (`http://127.0.0.1:8001/auth/callback`). That exact redirect URL must also be registered in the Kite Connect developer app settings, or the callback fails. Completing this step needs the human's Zerodha account credentials + 2FA; it cannot be done with only the API key/secret.

### What works WITHOUT Kite login (use for smoke tests)

- `GET /health` → `{"ok":true,...}`
- Yahoo-source backtest (fetches live Yahoo data): `POST /backtest/run` with `{"instrument":"NIFTY50","timeframe":"15min","source":"yahoo","use_max":true}`. In the UI: Backtest page → uncheck "Use saved selection" → set Data source to Yahoo → Run Backtest. Yahoo only supports NIFTY50/SENSEX and ~60 days of intraday history.

### Lint / build

- Frontend lint: `npm run lint` in `Pixel Perfect UI/`. NOTE: the repo currently has ~279 pre-existing prettier/eslint errors (formatting) unrelated to setup — a clean run is not expected. `npm run format` (prettier --write) would fix them but that changes committed files.
- Frontend build: `npm run build` in `Pixel Perfect UI/`.
- There is no Python test suite in the repo.
