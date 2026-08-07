# Kite Connect Setup (3ST Algo Platform)

Docs: [Kite Connect v3](https://www.kite.trade/docs/connect/v3/) · [Login flow](https://www.kite.trade/docs/connect/v3/user/) · [Historical](https://www.kite.trade/docs/connect/v3/historical/)

## 1. Developer app

1. Create an app at the [Kite Developer Portal](https://developers.kite.trade/).
2. Note `api_key` and `api_secret`.
3. Set **Redirect URL** to `http://127.0.0.1:8000/auth/callback` (must match `.env` exactly).

## 2. Environment

Add to `.env` (never commit):

```
KITE_API_KEY=your_key
KITE_API_SECRET=your_secret
KITE_REDIRECT_URL=http://127.0.0.1:8000/auth/callback
```

## 3. Run API

```bash
cd 3ST
.\.venv\Scripts\activate
uvicorn api.main:app --reload --port 8000
```

## 4. Login flow (easiest)

1. Open **http://127.0.0.1:8000/auth/login** in your browser
2. Click **Login with Zerodha**
3. After Zerodha login, you land on a **Logged in successfully** page — done

Alternative (Swagger): `GET /auth/login-url` → copy token → `POST /auth/session`

Session stored in `data/kite_session.json` (gitignored). Token expires ~**6:00 AM IST** next day.

## 5. Lovable UI

1. Paste [LOVABLE_UI_SPEC.md](LOVABLE_UI_SPEC.md) into Lovable
2. Set `VITE_API_BASE_URL=http://127.0.0.1:8000`
3. Sync to GitHub → clone into `web/`
4. **Never** put `KITE_API_SECRET` in Lovable or frontend env

## 6. ARM / DISARM

- Default: **DISARMED**
- Paper mode: simulated fills only
- Live mode: still requires `POST /live/arm` with `confirm: true` before Kite orders

## 7. Backtest sources

- `source: "yahoo"` — no login, ~60 days intraday
- `source: "kite"` — requires session; deeper history via historical API
