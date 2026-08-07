# Straddle Watch

**UI:** `/straddle-watch` · **Code:** `options/straddle_watch.py`, `Pixel Perfect UI/src/routes/straddle-watch.tsx`

Read-only CE/PE/straddle premium + OI desk (iCharts-style Latest mode). Does **not** place orders or touch ARM / runners.

**Session:** cash / index options **09:15–15:40 IST** (NSE Closing Auction Session — equity derivatives close 15:40). MCX underlyings keep 09:00–23:30.

## Controls

| Control | Behavior |
|---------|----------|
| Latest | Active (only mode in v1) |
| Historical | Disabled stub |
| Symbol / Expiry / Call Strike / Put Strike | From `/options/expiries` + `/options/chain` |
| SHOW CHART | Applies params and fetches snapshot |
| 1D / 5D / 30D | Immediate reload for the applied strikes |

Separate Call/Put strikes → ATM **straddle** or **strangle**.

## API

- `GET /straddle-watch/config`
- `GET /straddle-watch/snapshot?underlying=&expiry=&call_strike=&put_strike=&range=1D|5D|30D`

Requires Kite session. Underlying is allowlisted against `INDEX_OPTIONS`.

## Summary strip

Futures quote + Δ, Fair Price (cash index spot / MCX future), Lot Size, IV, IVR, IVP, Max Pain, PCR.

- **IV** — average of selected CE/PE implied vols  
- **IVR / IVP** — rank/percentile vs daily last-IV samples available in the loaded range (null when history is thin)  
- **Max Pain** — standard OI-weighted writer-pain strike on the expiry chain  
- **PCR** — total PE OI / CE OI for that expiry  

## Chart

Highcharts Stock dual pane:

1. Call / Put / Straddle price (VWAP + IV series available, default off)  
2. Call OI / Put OI (axis in L / Cr)  
Shared navigator + legend toggles.

## Tests

```bash
pytest tests/test_straddle_watch.py -v
```
