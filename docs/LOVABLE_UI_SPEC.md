> ⚠️ **HISTORICAL — superseded, do not follow as instructions.**
>
> This was the original prompt used to generate the first version of the UI with
> Lovable. The project no longer uses Lovable (removed 2026-08-08 — see
> [UI_LOVABLE_EXIT_PLAN.md](UI_LOVABLE_EXIT_PLAN.md)), and the real UI in
> `Pixel Perfect UI/` has long since diverged: 29 routes, TanStack Router (not
> React Router), Highcharts/Plotly, and the API on **:8001** (not :8000).
>
> Kept only as a record of the original screen-by-screen intent. For current
> conventions see `Pixel Perfect UI/AGENTS.md`.

# Lovable AI — UI build prompt / screen spec for 3ST Kite Algo Platform
# NEVER put KITE_API_SECRET in the frontend.

## Product
Algo trading control panel for Zerodha Kite Connect.
Name: **3ST Algo Desk**
Stack: React, TypeScript, Vite, Tailwind, React Router, Recharts (or similar).

**Strategy:** 3ST (Triple HA SuperTrend + ADX) for all signals — no EMA200.

## Global rules
- Dark professional trading UI (slate/zinc background, green/red for PnL). No purple gradients, no emoji clutter.
- All data via REST to `${VITE_API_BASE_URL}`.
- Show toast errors from API `detail` field.
- Persist nothing secret in localStorage except UI prefs.
- Use a global **SelectionContext** (React context or Zustand) synced with backend `GET/POST /selection`.

## Pages / routes

### 1. `/` — Page 1: Stock Selection (landing after login)

Primary entry point. User picks what to trade before backtest or live desk.

#### A. Instrument search
- Segment tabs: **Equity | Future | Options**
- Debounced combobox (300ms): `GET /instruments/search?q={query}&segment={equity|future|option}&limit=25`
- On first load with empty cache, prompt user to log in and refresh instruments
- Selected row card: tradingsymbol, exchange, lot size, instrument_token
- **Equity / Future:** chart underlying = selected instrument (for 3ST signals)
- **Options segment pick:** user still selects an **index underlying** (NIFTY / BANKNIFTY / SENSEX) in the spread section below — individual option contracts are legs, not chart underlyings

#### B. Timeframe selection
- Chip group: **5m | 15m | 30m | 60m**
- Source: `GET /instruments/timeframes` or hardcode from health
- Default: 15min

#### C. Product mode toggle
- **Underlying only** — trade equity/future/index on 3ST signals
- **Index Options Spread** — 3ST runs on index spot; execution uses multi-leg spreads

#### D. Multi-leg spread builder (visible when product = `options_spread`)

1. **Underlying** dropdown: NIFTY, BANKNIFTY, SENSEX (`GET /health` → `index_options`)
2. **Expiry** dropdown: `GET /options/expiries?underlying=NIFTY`
3. **Spread templates** (`GET /options/templates`):
   - `bull_call` — Bull Call Spread
   - `bear_put` — Bear Put Spread (bullish defined risk)
   - `bear_call` — Bear Call Spread
   - `bull_put` — Bull Put Spread
   - `iron_condor` — Iron Condor (optional advanced)
4. **Width** (strike steps between legs): number input, default 1
5. **3ST direction mapping:**
   - Long signal → **Long spread template** (default `bull_call`)
   - Short signal → **Short spread template** (default `bear_call`)
   - User can change each dropdown independently
6. **Leg tables** (two tabs: Long spread | Short spread):
   - Columns: Side | Type | Strike | Symbol | LTP | Qty
   - Auto-fill via `POST /options/spreads/preview-directions` with body:
     ```json
     {
       "underlying": "NIFTY",
       "expiry": "2026-03-27",
       "long_template": "bull_call",
       "short_template": "bear_call",
       "width_steps": 1
     }
     ```
   - Allow strike nudge per leg; re-preview single side with `POST /options/spreads/preview` + optional `legs` overrides
7. **Preview panel:** net debit/credit, max loss estimate, lot size, spot

#### E. Save & continue
- Button **Save Selection** → `POST /selection`:
  ```json
  {
    "instrument_token": 256265,
    "exchange": "NSE",
    "tradingsymbol": "NIFTY 50",
    "name": "NIFTY 50",
    "segment": "equity",
    "timeframe": "15min",
    "product": "options_spread",
    "spread": {
      "underlying": "NIFTY",
      "expiry": "2026-03-27",
      "long_template": "bull_call",
      "short_template": "bear_call",
      "width_steps": 1,
      "legs_long": [],
      "legs_short": []
    }
  }
  ```
- On mount: `GET /selection` to restore state
- **Continue to Backtest** → `/backtest`
- **Continue to Live Desk** → `/live`

---

### 2. `/login` — Kite Login
- Show whether backend is configured: GET `/health`
- Button **Open Kite Login** → opens GET `/auth/login-url` → `login_url` in new tab
- Input: `request_token` (paste from redirect URL query)
- Button **Connect** → POST `/auth/session` `{ request_token }`
- On success navigate to `/` (Stock Selection)
- Button Logout → DELETE `/auth/session`

### 3. `/dashboard`
- GET `/auth/me` — user name, user_id, login_time
- GET `/margins` when authenticated — equity net / available cash cards
- GET `/live/arm` — ARM badge (DISARMED red / ARMED green)
- GET `/selection` — summary card of current instrument + timeframe + spread config
- Quick links: Stock Selection, Backtest, Live Desk, Settings

### 4. `/backtest`
On mount: `GET /selection` to pre-fill instrument + timeframe.

Form:
- **Use saved selection** checkbox (default true) → sends `use_selection: true` to backtest API
- Or manual override: instrument_token / NIFTY50 | SENSEX
- Timeframe: 5min | 15min | 30min | 60min
- Source: yahoo | kite (yahoo only for NIFTY50/SENSEX keys)
- use_max checkbox (default true)
- Optional start/end dates
- Strategy params: atr1/2/3, factor1/2/3, adx enabled/period/threshold, trade_mode, system_mode, SL/TGT/TSL modes+values, qty, capital

Button **Run Backtest** → POST `/backtest/run` with JSON body.

Results:
- Metric cards: net_pnl, return_pct, trades, win_rate, profit_factor, max_drawdown_pct
- Equity line chart from `equity[]` (`t`, `v`)
- Candles table (last rows) optional
- Trades table with CSV download

### 5. `/live`
On mount: `GET /selection` — show active instrument, timeframe, spread mapping.

- Mode toggle Paper | Live → POST `/live/mode` `{ mode }`
- ARM button (confirm dialog) → POST `/live/arm` `{ confirm: true }` (only if mode=live)
- DISARM → POST `/live/disarm`
- Show GET `/live/arm`
- Positions table GET `/live/orders`
- Orders table GET `/live/orders`
- Risk summary GET `/risk/limits`
- Kill switch = DISARM (prominent)
- Badge: 3ST Long → `{long_template}` spread | 3ST Short → `{short_template}` spread (from selection)

### 6. `/settings`
- Edit risk limits → POST `/risk/limits`
- Show API base URL from env
- Instrument token status GET `/instruments?refresh=true`
- Clear selection → DELETE `/selection`

## API reference

### Auth & health
- GET `/health`
- GET `/auth/login-url`
- POST `/auth/session` `{ request_token }`
- DELETE `/auth/session`
- GET `/auth/me`

### Instruments & selection
- GET `/instruments?refresh=false`
- GET `/instruments/timeframes`
- GET `/instruments/search?q=&segment=equity|future|option&limit=25`
- GET `/instruments/{instrument_token}`
- GET `/selection`
- POST `/selection`
- DELETE `/selection`

### Options spreads
- GET `/options/expiries?underlying=NIFTY`
- GET `/options/chain?underlying=NIFTY&expiry=2026-03-27`
- GET `/options/templates`
- POST `/options/spreads/preview`
- POST `/options/spreads/preview-directions`

### Trading & backtest
- GET `/margins`
- POST `/backtest/run` — supports `instrument_token`, `use_selection`, 60min timeframe
- GET/POST `/live/arm`, POST `/live/disarm`, POST `/live/mode`
- GET `/live/positions`, GET `/live/orders`
- GET/POST `/risk/limits`

## TypeScript types (suggested)

```ts
type Segment = "equity" | "future" | "option";
type Timeframe = "5min" | "15min" | "30min" | "60min";
type SpreadTemplate = "bull_call" | "bear_put" | "bear_call" | "bull_put" | "iron_condor";
type Product = "underlying" | "options_spread";

interface InstrumentHit {
  instrument_token: number;
  exchange: string;
  tradingsymbol: string;
  name: string;
  segment: string;
  instrument_type: string;
  lot_size: number;
  expiry?: string;
  strike?: number;
}

interface SpreadLeg {
  tradingsymbol: string;
  exchange: string;
  instrument_token: number;
  side: "BUY" | "SELL";
  quantity: number;
  strike: number;
  option_type: "CE" | "PE";
  ltp?: number;
  premium?: number;
}

interface Selection {
  instrument_token: number | null;
  exchange: string | null;
  tradingsymbol: string | null;
  segment: Segment;
  timeframe: Timeframe;
  product: Product;
  spread: {
    underlying: string;
    expiry: string;
    long_template: SpreadTemplate;
    short_template: SpreadTemplate;
    width_steps: number;
    legs_long: SpreadLeg[];
    legs_short: SpreadLeg[];
  } | null;
}
```

## Deliverables from Lovable
1. Responsive layout with left nav (Stock Selection first in nav order)
2. Working fetch wrappers in `src/lib/api.ts`
3. Typed responses matching types above
4. Empty/loading/error states; 401 → redirect to `/login`
5. Export to GitHub; place code under `web/` in the monorepo

## Notes for implementers
- Spread preview requires Kite login (401 otherwise).
- Kite has no combo order API — live spread execution (Phase D) will place legs sequentially with shared tag.
- 3ST signals always computed on the **underlying** OHLC, not option premiums.
