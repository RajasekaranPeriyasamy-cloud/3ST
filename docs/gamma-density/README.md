# Gamma Density

**UI:** `/gamma-density` · Dealer gamma / GEX-style density analytics.

Related: [Vanna Exposure](../vanna-exposure/) (separate page). See `docs/CONVERSATION_SUMMARY.md` (Gamma Density broker data notes).

## Concentration tab — HHI measurement basis

The Concentration index is a Herfindahl-Hirschman sum of squared per-strike shares
of dealer gamma, computed over the ATM-trimmed window only.

- **Mass basis** (`mass_basis`, default `gross`): `gross` = `|CE γ| + |PE γ|` at each
  strike; `net` = `|CE γ + PE γ|`. Under the `naive` sign mode CE is dealer-long and
  PE dealer-short, so the net basis cancels a balanced strike to ~zero mass and drops
  it from the index — it measures concentration of the *net imbalance*, not of gamma.
  Gross is also the basis `call_hhi` / `put_hhi` have always used. Both are always
  reported (`hhi_gross` / `hhi_net`); override per request with `?mass_basis=net`,
  or from the **γ mass** selector in the Concentration tab header.
- **Band cuts** are basis-dependent (`hhi_band_cuts` in `/gamma-density/config`).
  Gross defaults 0.18 / 0.08 are calibrated against BSM gamma × index-scale OI at
  NIFTY (spot ~24.6k, step 50, ±20 strikes): 0-DTE pinning 0.18–0.32, 1–2 DTE
  0.08–0.13, weekly/monthly 0.02–0.07. Net keeps the legacy 0.25 / 0.12.
  Override via `hhi_compressed_cut_{gross,net}` / `hhi_balanced_cut_{gross,net}` in
  `GAMMA_DENSITY_DEFAULTS`.
- **HHI depends on `strike_window`** — its floor is `1/N`. Day-end values in
  `data/gamma_density_history.json` → `daily_hhi` therefore carry `basis`,
  `strike_window`, `sign_mode`, `updated_at`, and **both** measures (`hhi_gross`,
  `hhi_net`). The 5-/30-session comparisons only use rows matching the live
  snapshot; a row recorded on one basis is resolved to the other when it carries
  it, so switching basis keeps the history instead of restarting it.
- **Legacy rows** (written before basis tagging) are read — and, on the next write,
  persisted — as net-basis at the config default window, flagged
  `strike_window_assumed` / `legacy`. The assumption is sound because only two paths
  ever wrote a day-end HHI, and the background GEX recorder (which passes no window,
  so always the default) samples continuously and therefore almost always owns the
  last write of the day. They carry no gross measure, so they serve **net-basis
  comparisons only**; the gross series builds from the first tagged session.
  `hhi_session_assumed_count` reports how many rows in the sample are inferred, and
  the 30-session chart says so.
- **Percentiles are inclusive** of the current observation, intraday and cross-session
  alike, so they can never fall below `100/n`. The UI states the sample size.
- **Day-end HHI is the last in-session write**, not a close print — close the desk at
  11:00 and that day's bar is an 11:00 value. `updated_at` records which.
