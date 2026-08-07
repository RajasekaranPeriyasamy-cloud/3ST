# 3ST Project Review — Consolidated Gap Analysis

**Date:** 2026-07-09  
**Stack:** Python FastAPI + Pixel Perfect UI + Kite Connect  
**Strategy:** 3ST (Triple SuperTrend + ADX)

---

## 1. What Is Working

- Stock Selection with strategy settings (ST method, ST1/ST2/ST3 toggles, ADX, SL/TGT/TSL, session mode)
- Settings persist and flow into backtest when `use_selection=true`
- Kite authentication and instrument search
- Backtest: Kite ~400 days intraday, date pickers, points PnL, long/short split, start open / end close
- Watchlist queue: `waiting → triggered → active → closed`
- Signal scan: 3ST entry detection on waiting items
- Options spread preview and save

---

## 2. Critical Gaps (Blocks Real Trading)

### 2.1 No Live Order Execution

- `broker/kite_broker.py` and `broker/paper_broker.py` exist but nothing calls `place_order()`
- `POST /watchlist/{id}/activate` only updates status
- **Flow:** Scan → triggered → [manual Activate] → active → **no orders**

### 2.2 Step 7 — Options Spread Execution Missing

- Spread preview + storage only; no multi-leg order placement
- Backtest always on underlying OHLC, not spread PnL
- Live scan uses index token for signals; spread legs ignored after trigger

### 2.3 Live vs Backtest Logic Mismatch

| Feature | Backtest | Live scan | Streamlit |
|---------|----------|-----------|-----------|
| Session / force exit | ✓ | ✗ | Partial |
| SL / TGT / TSL | ✓ | ✗ | Partial |
| Zone exits (ST1) | ✓ | ✗ | Partial |
| trade_mode | ✓ | ✗ | ✓ |
| system_mode | ✓ | ✗ | Partial |
| ST enable flags | ✓ | Entries only | ✗ |
| Options spread | ✗ | Index only | ✗ |

### 2.4 Risk Limits API / UI Broken

| UI (`settings.tsx`) | API (`risk/limits.py`) |
|---------------------|------------------------|
| `max_loss_day` | `max_daily_loss` |
| `max_trades_day` | *(not defined)* |

`check_order()` is never invoked from execution paths.

---

## 3. Important Gaps

1. **Hybrid ST** — UI option exists; code treats it same as `heikin_ashi`
2. **Streamlit `app.py`** — stale legacy UI (Yahoo only, missing new features)
3. **Session start/end** — stored but not editable in UI
4. **Scan errors** — API returns `errors[]`; Dashboard doesn't show them
5. **Spread validation** — empty legs/expiry allowed on save
6. **Auth UX** — API has login redirect; UI uses manual token paste
7. **Port confusion** — UI default 8000 vs `.env` 8001
8. **No tests** — no `tests/` directory
9. **No auto-activate** — manual Activate required
10. **Paper broker** — needs manual LTP setup

---

## 4. Nice-to-Have

- Backtest candle/ST chart in UI
- Server-side scan scheduler
- `trade_mode` not saved in selection
- BANKNIFTY index token mapping
- `iron_condor` without 4-leg execution
- Root README
- Production CORS hardening
- Duplicate watchlist prevention

---

## 5. Recommended Priority

| Priority | Task | Effort |
|----------|------|--------|
| **P0** | Fix risk limits field mapping | Small |
| **P0** | Align live scan with backtest engine | Medium |
| **P1** | Activate + ARMED → `place_order()` | Medium |
| **P1** | Implement or remove hybrid ST | Small |
| **P1** | Show scan errors on Dashboard | Small |
| **P2** | Multi-leg spread orders (Step 7) | Large |
| **P2** | Options-spread backtest | Large |
| **P3** | Update/deprecate Streamlit | Medium |
| **P3** | README + port standardization | Small |

---

## 6. Summary

**Configure / backtest** is solid. **Execute** is not: scan → trigger → activate does not place orders; risk settings may not apply; live logic is simpler than backtest.

**Next steps:** Fix risk limits → align live with backtest → wire order placement.

---

*Regenerate PDF: `python docs/review/generate_review_pdf.py`*
