# Gamma Density

**UI:** `/gamma-density` · Dealer gamma / GEX-style density analytics.

Related: [Vanna Exposure](../vanna-exposure/) (separate page). See `docs/CONVERSATION_SUMMARY.md` (Gamma Density broker data notes).

## Pin strength

A pin is a magnet with measurable strength and nameable failure conditions, not a
lock. Two building blocks exist so far; the measure itself is not built yet.

**`pin_source` — which rule produced the pin.** `compute_gamma_concentration`
picks the pin three different ways and they are not equally meaningful:

| `pin_source` | Rule | Meaning |
| --- | --- | --- |
| `dominant` | `top1_share ≥ pin_share_threshold` (0.18) | A real gamma pin |
| `wall_mid` | midpoint of call/put walls, rounded to the step | An inference |
| `atm` | the ATM strike | A placeholder, not a pin |
| `fallback` | dominant strike, no ATM available | Degenerate case |

All three emit a bare number, so before `pin_source` existed they were
indistinguishable downstream. This matters because the `atm` placeholder sits
next to spot by construction, so `pin_stable` reports it as rock-steady exactly
when no pin exists — a false positive in the one case you care about. **Gate any
pin-strength read on `pin_source == "dominant"`.**

**`daily_pin` — the calibration trail.** `append_history_point` prunes the
intraday `series` to today on every write, so nothing survives to answer *"when
the pin looked strong at midday, did it hold to close?"*. Without that, any
pin-strength threshold is a prior rather than a finding.

`record_pin_sample()` writes bounded per-session checkpoints (default one per 30
min, 60 sessions retained) into `daily_hhi`'s sibling bucket, holding raw inputs
only — pin, source, share, spot, GEX, regime, HHI, flip, 1σ — plus a `close_spot`
refreshed on every call (last in-session write ≈ close, same convention as
`upsert_daily_hhi`). **No verdict is stored.** `pin_hold_outcomes(series,
hold_steps=…)` derives `held` at read time, so changing the tolerance re-reads
history instead of invalidating it. Sessions with no recorded close are skipped
rather than scored as failures.

**The measure — `options/pin_lock.py`.** Pure functions, no I/O. Emitted on the
snapshot as `pin_lock`; window via `?pin_window=15m|30m|60m|session` (default
`30m`), selectable from the Concentration tab.

Two hard gates — `pin_source == "dominant"` and dealers long gamma for
`PIN_LONG_GAMMA_SHARE` (0.8) of window ticks — then five components: pin
stability against the **modal** pin, spot containment, crossings of spot over the
pin, flip room in σ, and ΔOI on the pin strike. Plus a `breaker` naming the level
whose breach ends the regime, and `reasons` in plain language.

Three rules the implementation holds to:

- **No blended score.** Weights cannot be justified until `daily_pin` has enough
  sessions to fit them; a confident-looking composite over guessed weights is
  worse than five honest readings.
- **`null` means unmeasured, never failed.** An empty session must not read as a
  broken pin — gates return `None`, not `False`, when there is nothing to judge.
- **Containment and crossings are measured over `chart_series` minutes**, not GEX
  ticks. The tick trail is deliberately gappy (`build_chart_series` keeps holes
  honest), so scoring it would understate time spent away from the pin.

The window anchors on the newest sample present rather than wall clock, so a
stale snapshot yields an empty window instead of scoring old data as current.

On the Profile tab the `PIN CANDIDATE` tile leads with `pin_source`: an `atm`
pin now reads *"ATM placeholder — not a gamma pin"* instead of claiming to be
stable.

Known limits of `_pin_stability` (the older `pin_stable` / `pin_stability_pct`):
it compares the last **12 ticks** against the *current* pin. That is tick-counted,
not time-boxed, so a polling gap silently stretches the window; and anchoring on
the current pin makes a pin that just moved look unstable while the new one
establishes. A windowed measure should anchor on the modal pin over a time
window instead.

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
