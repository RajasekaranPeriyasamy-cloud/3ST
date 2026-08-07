# CAS Indicative / Pre-close Forecast

**UI:** `/cas-indicative` · **Code:** `options/cas_indicative.py`, `options/cas_estimate.py`, `Pixel Perfect UI/src/routes/cas-indicative.tsx`  
**Plan / status:** [PLAN.md](PLAN.md)

Read-only desk for **NIFTY** (BANKNIFTY / SENSEX stubs). Display-only — does **not** change GEX / OI / spot math.

## Objective 1 (current) — estimate before 15:15

Ship a **pre-close forecast** of the Nifty close using Fut LTP, Synth F, and a VWAP proxy. Available and meaningful **before** the CAS window (not only 15:15–15:35). This is a desk estimate — **not** official CAS equilibrium.

Full per-stock Nifty-50 CAS rebuild is **later** (Phase B scaffold only).

## How the index close works

CAS discovers **stock** auction closes (15:20–15:30; reference = 15:00–15:15 VWAP, ±3% band). The exchange’s indicative / closing **index** is rebuilt from those stock prices (free-float weights / divisor). There is no separate “Nifty order book” for the close.

## What the desk shows

| Metric | Source |
|--------|--------|
| **Pre-close forecast** (`estimate`, hero when official null) | Proxy blend `proxy_v1` (see below); later `constituent_v1` |
| Official indicative | Kite index `indicative_*` **only if** within 3% of continuous spot; else null (live inside CAS) |
| LTP / spot | Continuous index (desk math) |
| Fut POC / Synth F | `session_poc` / `synthetic_future` |

### Proxy formula (`proxy_v1`)

```text
estimate = 0.40 * synth_F + 0.35 * fut_ltp + 0.25 * ref_vwap
# renormalize if a leg is missing; clamp to ref_vwap ± 3%
```

### VWAP reference ladder (`ref_vwap_window`)

| Mode | When | Window |
|------|------|--------|
| `pre_close_1515` | `now ≥ 15:15` | Full 15:00–15:15 IST |
| `running_1500` | 15:00 ≤ now &lt; 15:15 | 15:00 → now (incomplete reference) |
| `session` | before 15:00, or empty 15:00+ bars | Session VWAP 09:15 → now |

Ref VWAP prefers index minute candles (`fetch_index_minute_spot`), else front-month future minutes. Mode is exposed on `estimate_components.ref_vwap_window`.

### Official sanity

`abs(indicative - spot) / spot ≤ 0.03` — rejects garbage (e.g. 15 / 1866) so Δ and basis-vs-CAS are not poisoned.

### Phase B (later — scaffold)

Constituent rebuild (`Σ wᵢpᵢ / divisor`) is scaffolded behind a ≥90% weight coverage gate. Full path waits for a live CAS-window **stock** quote-field spike. We do **not** scrape NSE HTML. Not part of Objective 1.

## Window

- **Forecast:** any time legs resolve (Synth F / Fut LTP / VWAP) — primary product before 15:15.
- **Official CAS:** cash auction ~**15:15–15:35 IST**. Sanitized official indicative only in-window; outside: last sane official tick when available + Outside CAS badge.

## API

- `GET /cas/indicative?underlying=NIFTY` — single payload (`estimate`, `estimate_components`, `estimate_method`, `official_indicative`, sanitized `indicative`)
- `GET /cas/indicative` — batch `{ items: [...] }`
- `GET /cas/debug-quote?underlying=NIFTY` — field spike (enable with `CAS_DEBUG_QUOTE=1`)

Requires Kite session. No NSE HTML scrape.

## UI

Tabs: **Indicative** · **Equilibrium** · **Methodology** (formula + component strip). Copy frames the hero as a **pre-close forecast / estimate**, not official CAS. Compact `CasChip` on Gamma Density / OI Movers links here during the window (shows sanitized official when present).

## Tests

```bash
pytest tests/test_cas_indicative.py tests/test_cas_estimate.py tests/test_synthetic_future.py -v
```
