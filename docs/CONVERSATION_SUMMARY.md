# 3ST Project — Conversation Summary

**Last updated:** 2026-08-18  
**Project path:** `C:\Dev\3ST`  
**Session focus:** Net GEX chart — price-level overlays, then read-path latency (client + chain memoisation)

This file captures recent development context from Cursor agent sessions. Full chat logs live in Cursor agent-transcripts (not in this repo).

> **When you ask to “review points”** — read **[Execution architecture — phase reminders](#execution-architecture--phase-reminders)** for Phases 3–4 checklist, open decisions, and acceptance criteria.

---

## Session 2026-08-18 — Net GEX read-path latency · it was never the feed

Asked to improve "data feed latency" on the Net GEX session chart. Profiling said the feed was
the *smallest* part of the problem. `/gamma-density/snapshot` measured **5.2–6.0s**, polled by the
UI every 60s, so the chart ran 30–66s stale — but only **~0.46s** of that was actual Kite
request/response time.

### The Kite client was rebuilt on every single call

`kite_auth._kite()` constructed a fresh `KiteConnect` per call, so every market-data read got a new
`requests.Session`, a new connection pool, a fresh TLS handshake, and a re-read of the CA bundle.
cProfile put **0.95s of tottime in `load_verify_locations` alone**, and ~1.4s of each snapshot in
connection setup. Measured directly: **465 ms/call fresh vs 189 ms/call reused.**

`read_only_kite_client()` memoises it — but **only for reads**. `get_kite_client()` and the login
flow still construct fresh clients against `kite_egress_plan()`. That split is deliberate and is the
whole safety argument: reads are always direct egress (CLAUDE.md), so the cached client carries no
egress state and can never be handed to an order path where it would bypass the IP whitelisted on
developers.kite.trade. `tests/test_kite_client_cache.py::test_read_client_never_carries_order_egress`
pins it: with the staticip proxy selected, the read client must still come back unproxied.

Invalidated on `save_session()` / `clear_session()` so a re-login is never masked. Callers still
call `set_access_token` per call, so a new token applies immediately regardless of cache state.

### `get_chain` re-scanned a daily-static file on every poll

`options/chain.py` ran a pandas `.apply` over the instruments dump on every call — **894 ms**,
against a file that changes once a day, from **~19 call sites** across the desk. Now memoised
against the instruments-cache mtime, the same guard `_EXPIRIES_CACHE` already used. Callers get a
structural copy (`_copy_chain`); several enrich leg dicts in place, and poisoning a shared cache
across 19 consumers is not a bug worth discovering in production.

Note the invalidation contract: the chain cache is only as fresh as the instruments dump. Strikes
added intraday appear when the dump is refreshed, exactly as expiry lists already behaved.

### Result

Repeat `get_chain` **894 ms → 0 ms**. The offline test suite, which is an apples-to-apples workload,
went **36.0s → 19.2s**. The endpoint itself measured ~2.0s afterwards, but that reading is *not*
comparable to the 5.5s baseline — the clock crossed into a new trading day mid-session, so the
post-change call had no session candles or history to fetch. Re-measure during market hours.

### Still open — the actual feed

`execution/ltp_cache.py` is a working Aio-Trader KiteFeed WebSocket client with `/ws/ltp` wired in
`api/main.py`, defaulted on via `LTP_CACHE_WS=1` — but **`aio_trader` is not installed**, so it
silently falls back to REST everywhere. The package source is sitting in `_review/Aio-Trader/`.
`KiteFeed.MODE_FULL` carries OI, so subscribing the chain in full mode would remove the 210-key
quote batch entirely. The gamma desk does not consume `ltp_cache` at all today.

Also: `options/analytics_scheduler.py` already computes a full snapshot every 30s and throws it
away after persisting a history tick, while the UI recomputes the identical thing 60s later.
Serving the endpoint from that cached snapshot is the next cheap win.

---

## Session 2026-08-13 — Theta Decay desk · the degenerate decomposition

User asked for "Theta Negative Velocity" as the theta analogue of Delta Velocity, then chose
**burn rate + decay capture** over the literal analogue as the headline. Read-only analysis desk;
nothing under `broker/` / `execution/` / `risk/` touched.

### No second collector, and no collector change either

`analysis/delta_velocity/` already archives `spot`, `strike`, `expiry`, `ts` and full-precision
`iv` per leg per minute, so **theta is fully recoverable from what is already on disk** — the desk
works over sessions collected before it existed. A sibling collector was rejected (it would double
the quote load and duplicate the per-strike IV solve, the slowest analysis work on the desk).

Storing theta in the snapshot was written, then reverted. The collector's `compute_greeks` call
takes the default **q = 0.012**, while the archived `iv` is solved by `vollib.black_scholes` at
**q = 0**. Feeding a q=0 IV into a q=0.012 greeks call describes no self-consistent model of the
observed price; negligible for delta (`qT ≈ 2e-4` at 6 DTE) but a **5% shift on theta**. So
`features.ensure_greeks` always re-derives at q=0 and never trusts a stored column. Vectorising
the greeks made that free — a session went from ~10s to ~0.3s — which removed the only reason to
store them.

### Burn rate works; capture ratio nearly didn't

**Burn rate (`−theta / premium`) is solid** and reproduces the theoretical `1/T` scaling almost
exactly. NIFTY 2026-08-12: 8.13%/day at 6 DTE, 3.79% at 13, 2.48% at 20 — ratios 2.14 and 1.53
against 2.17 and 1.53 predicted. Holds on all three underlyings.

**Decay capture was garbage as first specified**, and the reason is structural rather than a
tuning problem: the archived IV is *inverted from the same price being decomposed*, so
`delta·dS + vega·dσ` alone reproduces a one-minute price change with **R² of 0.95–0.998**. Theta
was left with 0.3–0.4% of the movement and the ratio was noise (buckets came out at −1.26, 0.80,
−8.59).

Lengthening the horizon is what fixes it — theta accumulates linearly while spot and vol noise
partially cancels:

| DTE | 1min | 5min | 15min | 30min | 60min | theta share of \|dP\| |
| --- | --- | --- | --- | --- | --- | --- |
| 6 | −1.14 | −0.85 | −0.22 | 0.52 | 0.59 | 0.44% → 2.05% |
| 13 | 0.58 | 0.73 | 0.76 | 0.91 | **0.95** | 0.34% → 1.54% |
| 20 | 0.44 | 0.56 | 0.63 | 0.81 | 0.71 | 0.31% → 1.37% |

Hence `DEFAULT_HORIZON_MIN = 60`. **Smoothing IV to denoise this is wrong** — it looks like the
obvious fix and biases instead, because a rolling-mean IV lags the true vol path and the
under-attributed vol move lands in `time_pnl`. At a 15-minute horizon, 30-minute IV smoothing
pushed DTE 13 from 0.76 to 1.71, an "improvement" that is entirely artefact.

### Quality gating, and a sample-size trap

Two buckets still came out negative, both diagnosable — theta below the noise floor, or the vol
term too large to subtract. `capture_quality` labels them (`theta_too_small` / `vega_dominated`)
rather than hiding them, gated on `theta_share ≥ 1%` and `vega_share ≤ 35%`, calibrated across the
nine (underlying, DTE) buckets in the 2026-08-12 archive.

A third gate came out of watching the live page mid-session: a bucket holds **one row per
(contract, window)**, so a 92-minute session gave 22 rows — but all 22 are the same hour seen
through 22 strikes, sharing one spot path and one vol move. Gating on row count waved that
through with `vega_share` near 100%. It now counts **distinct time windows** and reports
`too_few_windows`, which is the honest answer for most of a trading day.

### Theta velocity, the literal analogue — shipped, demoted

Kept, with the deterministic component removed (`|θ_t − θ̂_t|` where `θ̂` holds spot and IV at
`t−1`), because unlike delta, theta has a large clock component knowable a day ahead. It measured
weak: correlation **0.12–0.16** against absolute one-minute spot returns, and it **lags by 6–9
minutes**. Behind its own `/decay/velocity` endpoint and a "Load" button, since it costs ~2s of
what would otherwise be a ~1.4s page.

### Notes

- Routes are `/decay/*`, not `/theta/*`: `ui_static.is_api_path` matches on `startswith`, so a
  `/theta` prefix would turn a hard browser load of the SPA page `/theta-decay` into a JSON 404.
- `scipy` added to `requirements.txt` — it arrived transitively via `py_vollib` and is now
  imported directly for the vectorised normal CDF.
- `delta_velocity.chart._atm_strike` / `_step` promoted to public `atm_strike` / `strike_step`
  (aliases kept) rather than importing another desk's privates.
- **A test fixture patched `settings.data_dir` and wrote 1,800 synthetic snapshots into the live
  archive** (`data/delta_velocity/NIFTY/2026-08-12.jsonl`, 383 → 2,184 lines). `store.py` does
  `from settings import data_dir` at import time and holds its own reference — the same binding
  trap `conftest.py` documents for `kite_client`. The file was repaired back to exactly 383 rows
  and the fixture now patches `store.data_dir`. Patch the module that holds the reference.

---

## Session 2026-08-12 — IV Skew desk (Phases 0–1) · the forward bug

User asked for a daily skew monitor after noticing calls apparently priced above puts, and
defined the metric correctly: `25Δ RR = IV(25Δ call) − IV(25Δ put)`. Read-only analysis desk;
nothing under `broker/` / `execution/` / `risk/` touched.

### The finding that reframed the request

**The positive risk reversal was a forward artifact, not a market signal.** `iv_smile.py` and
`vol_surface.py` solve IV from spot at a flat `r = 6.5%`, but NIFTY's parity forward carried
**+54 points at 6 DTE** where 6.5% implies ~26. The residual shows up as a put-call parity
violation — the *same strike* solving to 11.7% off the call and 9.6% off the put — which biases
calls up and puts down by ~1 vol point each. Enough to flip RR from **−0.76 to +0.54**.

Measured live across all five underlyings once the forward-based build existed: indices print
RR ≈ **−0.4 to −1.2** (ordinary defensive skew), and the ATM parity gap collapsed from ~2.1 vol
points to **0.01–0.05**.

**The user's instinct was right on a different underlying.** NATURALGAS is genuinely call-skewed:
**RR +2.0 front month, +5.5 at 42 DTE** — the largest on the desk. Adding MCX (their call, against
the original plan's "out of scope") is what surfaced it.

MCX also validated the parity-forward design: the September crude expiry resolved a forward
**106 points below** the front-month future. Mapping MCX options onto the front-month future
would have been wrong by that much on every September number.

### Shipped

1. **`options/skew_metrics.py`** — pure, offline. Parity forward (median of 3 nearest-ATM strikes,
   so one stale leg cannot move it), Black-76 IV/delta, OTM-wing-only construction, interpolation
   onto the delta axis, RR + butterfly + ATM vol, and an ATM `parity_gap` as the module's own
   correctness check.
2. **`analysis/iv_skew/builder.py`** — two batched quote passes: a sizing probe near the money,
   then a window sized from the *measured* ATM vol. Two Kite calls per snapshot regardless of
   expiry count. Fixed strike counts were shown to fail both ways — ±20 still missed 25Δ on
   48-DTE BANKNIFTY while over-quoting 1-DTE SENSEX by 30 worthless legs.
3. **`GET /skew/config` · `GET /skew/snapshot`** — prefix `/skew`, page `/iv-skew`, following the
   `/velocity` precedent so a hard browser load does not hit the API-prefix collision `/iv-smile`
   is subject to.
4. **`/iv-skew` page** — RR / fly / ATM IV / forward-basis tiles, IV-by-strike with the two 25Δ
   readings drawn as reference lines, RR-by-tenor bars (degraded rows rendered faded), and an
   all-expiries table with per-row warning tooltips.
5. **Docs** — [`docs/iv-skew/README.md`](iv-skew/README.md) + desk index row.

### Quality gating — added after a live failure

First live run printed **RR +6.90 labelled "interpolated"** on 76-DTE BANKNIFTY, off a chain that
had dropped 45 of 81 legs to wide spreads and never solved an ATM put. Two independent fields now
separate the questions: **`quality`** (how 25Δ was obtained — interpolated / extrapolated) and
**`confidence`** (whether the chain was good enough to believe it — clean / degraded). Warnings
cover missing parity check, parity gap, forward disagreement, extrapolated wing, sparse delta
bracket, and thin chain. That row now fails cleanly instead of printing a number.

### Found while verifying live

- **`reference_source` was always "index".** `get_index_spot_detail` returns
  `(price, failure_reason)` — the second value is *not* a source. Reading it as one labelled every
  MCX underlying "Spot" on the page when the reference is the front future, and swallowed the
  failure reason on the error path. Source now derives from `INDEX_OPTIONS` meta the same way
  `chain.py` does internally.
- **A cold snapshot took 16.4s**, 10.6s of it `get_chain` — 9.1s a per-row `pd.to_datetime` at
  `options/chain.py:96` re-guessing the date format over 5,382 rows, every call. Strike→symbol
  resolution is cached in the builder for 10 minutes; warm snapshots now run **~2.6s**.
  `chain.py` itself is untouched — it is on the order-placement leg-resolution path — but **every
  options desk pays that 9s on every chain read**, and it is worth fixing centrally.

### Test status

**676 passed**, offline, ~25s. 50 new (`test_skew_metrics.py`, `test_iv_skew_builder.py`) —
synthetic chains priced with Black-76 so the tests assert the module recovers what was priced in.
Ruff clean on all new files; no additions to the `api/main.py` lint backlog (7 findings before and
after).

### Phase 2 — the daily monitor (shipped same session)

- **`analysis/iv_skew/store.py`** — two tiers: intraday JSONL per underlying per session (metrics
  only, `points` dropped, 90-day retention) and `daily.jsonl`, one row per underlying/expiry/session,
  kept indefinitely. **The roll-up is lazy and idempotent, not scheduled** — a "write at 15:25"
  trigger loses a day to a restart or a holiday, so any completed session without a daily row is
  rolled up on the next read or tick. Each row is the day's last *clean* sample, falling back to the
  last resolved one and carrying `degraded` forward. Pruning refuses to delete a session that has
  not been rolled up.
- **`analysis/iv_skew/runner.py`** — own daemon thread, 5-minute cadence (which also keeps the
  builder's chain cache warm), **per-underlying session windows** so MCX keeps sampling after the
  indices close. One failing underlying does not stop the cycle.
- **`/skew/status` · `/skew/coverage` · `/skew/series` · `/skew/daily`** — the last two read the
  archive only, so history survives the ~6 AM token expiry. Daily series is keyed by **expiry rank**,
  not contract (expiries roll); `dte` rides along so the sawtooth is visible. Degraded sessions are
  excluded by default and **named** in `excluded_degraded`.
- **Page** — daily RR+fly chart with rank selector and a clean-only toggle, plus an intraday path.

**Verified live 2026-08-12 18:36 IST:** with the indices closed the sampler correctly sampled only
CRUDEOIL / CRUDEOILM / NATURALGAS, wrote `data/iv_skew/<U>/2026-08-12.jsonl`, and the intraday chart
picked up successive samples (NATURALGAS RR 2.21 → 2.55). `iv_skew_runner_alive: true` on `/health`.

Two display bugs found by clicking through and fixed:

- **Switching underlying left the previous one's numbers under the new header** for the duration of
  the fetch — NIFTY's RR −0.38 shown as NATURALGAS. On a trading desk that reads as a live quote for
  the wrong instrument, so the snapshot is now cleared on selection change.
- **One degraded expiry flattened the term-structure chart.** NATURALGAS 42 DTE printed RR −12.39
  against clean readings of +2.13 and +0.01; its axis range made the real bars unreadable. The chart
  now plots clean expiries only and captions how many were hidden; the degraded row stays in the
  table with its badge.

### CI had been red on main, and why

Pushing this branch surfaced it: **7 failed, 701 passed**, all seven in
`tests/test_mcx_rolling_straddle.py` with `RuntimeError: Instrument cache empty`. Those tests read
`data/kite_instruments.json`, which is **gitignored** — so they pass on a machine with a Kite session
and cannot pass in CI. `main` had failed its last five runs for the same reason, well before this
work. CLAUDE.md's "the suite runs offline" was therefore true locally and false in CI.

Fixed by seeding a synthetic MCX instrument cache in that module, which keeps the real code path
under test (`list_expiries` → `load_instruments` → `CACHE_FILE`) rather than stubbing it out. Note
`options/chain.py` does `from instruments import CACHE_FILE`, binding it at import time, so **both**
bindings need patching — the same trap `conftest.py` documents for the Kite client accessors.
Verified by running the suite in a clean git worktree with no instrument cache: **708 passed**.

Shipped as [PR #4](https://github.com/RajasekaranPeriyasamy-cloud/3ST/pull/4) — 3 commits, CI green.

### Open — next

- **Phase 2b (decided, not built):** route `iv_smile.py` and `vol_surface.py` through
  `skew_metrics` so they stop pricing off spot. They currently carry the same parity violation and
  will disagree in sign with this desk until they do.
- **Phase 3 (optional):** backfill 8 NIFTY sessions from `data/chain_history/` (±25 strikes, wide
  enough for real RR; its writer is no longer in the tree). A Kite-historical backfill only
  resolves currently-listed contracts, so it gives a DTE-drifting series that breaks at rollovers.
- **Verified end-to-end after an API restart** (2026-08-12 16:34 IST): NIFTY renders
  RR −0.37 / −0.94 / −0.78 across 6/13/20 DTE, all `clean`, parity gap 0.000; NATURALGAS renders
  **RR +2.42, "Calls bid"**, labelled "Front future 266.4", with its 42-DTE row flagged
  `degraded`. Desk came back healthy — Kite authenticated, egress `staticip_proxy`, runners alive,
  still DISARMED.
- **`options/chain.py:96` datetime hot path** — see above; a central fix benefits every options
  desk but touches an order-path module, so it needs its own change with tests.

---

## Session 2026-08-11 — Gamma Concentration tab rebuild · HHI measurement basis

Redesign of the `/gamma-density` → **Concentration** tab against supplied mockups, plus an
audit of the HHI maths behind it. Analysis desk only — nothing under `broker/` /
`execution/` / `risk/` touched.

### The finding that drove the change

`_strike_mass()` measured concentration on `|net_gex|`, where `net_gex = ce_gex + pe_gex`.
Under the `naive` sign mode CE is dealer-long (+1) and PE dealer-short (−1), so **a strike
with a balanced CE/PE book cancels to ~zero mass and drops out of the index entirely**,
while a one-sided strike takes the whole share. That is concentration of the *net dealer
imbalance*, not of dealer gamma — and it was never the same basis as the `call_hhi` /
`put_hhi` printed beside it, which have always been gross.

Empirically it showed: persisted day-end NIFTY HHI ran `0.073, 0.321, 0.059, 0.054, 0.054,
0.066, 0.094` — only 2026-08-04 (0-DTE expiry) ever cleared the `concentrated ≥ 0.25` cut, so
the desk read "dispersed" on essentially every non-expiry day and the band carried no
information.

### Shipped — backend

1. **Gross mass basis is now the default** (`mass_basis`, `gross` | `net`). Both are always
   reported as `hhi_gross` / `hhi_net`; `?mass_basis=net` on `/gamma-density/snapshot`
   restores the old measure. Headline and Call/Put HHI finally share one basis.
2. **Band cuts are per-basis and config-overridable.** Gross defaults 0.18 / 0.08, calibrated
   against BSM gamma × index-scale OI at NIFTY (±20 strikes): 0-DTE 0.18–0.32, 1–2 DTE
   0.08–0.13, weekly/monthly 0.02–0.07. Net keeps the legacy 0.25 / 0.12. Desk vocabulary is
   now **compressed / balanced / dispersed** (`band_label`; Ávila quadrant suffix follows).
3. **Density fallback moved to the aggregate level.** It used to fire *per row* when a strike's
   `net_gex` rounded to 0, contributing a mass in density units — density and GEX differ by
   `S²·0.01` (~6e6 at index scale). `_side_masses` already did this correctly.
4. **Day-end HHI rows carry their measurement basis** — `basis`, `strike_window`, `sign_mode`,
   `updated_at`, plus **both** measures (`hhi_gross` / `hhi_net`). HHI's floor is `1/N`, so a day
   recorded at window 10 was silently being ranked against window-20 history;
   `filter_daily_hhi_basis()` now restricts the 5-/30-session sample to like-for-like rows and
   resolves a row recorded on one basis to the other when it carries it — so switching
   `mass_basis` keeps the cross-session history instead of restarting it. The multi-index strip
   (window 8) was the worst offender and is fixed the same way.
5. **Legacy rows are migrated, not discarded.** `normalize_legacy_daily_hhi_row()` reads any
   untagged row as net-basis at the config default window, flagged `strike_window_assumed` /
   `legacy`; `upsert_daily_hhi()` persists that interpretation on its next locked write
   (idempotent, no new write path, and an unmigrated store behaves identically to a migrated
   one). The assumption is sound: only two paths ever wrote a day-end HHI, and the background
   GEX recorder — which passes no `strike_window`, so always the default — samples continuously
   and therefore almost always owns the last write of the day. Legacy rows carry no gross
   measure, so they serve **net-basis comparisons only**; the gross series builds from the first
   tagged session. `hhi_session_assumed_count` reports how many sample rows are inferred and the
   30-session chart states it. Dry-run against a copy of the live store: all 8 underlyings
   (NIFTY 7 rows, CRUDEOIL 9, NATURALGAS 6, SENSEX 6, BANKNIFTY/GOLD/SILVER 4, CRUDEOILM 2)
   migrate cleanly and become net-usable in full.
6. **New payload fields** for the redesign: `daily_hhi[]`, `hhi_prev_session(_date)`,
   `hhi_dod_pct`, `hhi_mean_5(_band)`, `hhi_mean_30`, `hhi_vs_mean_pct`, `hhi_low_30`/
   `hhi_high_30`, `top5_share`, `pos_/neg_gamma_peak_strike`, `call_band`/`put_band`, snapshot
   `dte`. Prior-session and rolling-mean stats **exclude today** — a mean that moves with
   today's own polls is not a baseline. `top_contributors` now covers the whole window (was
   capped at 25) and carries `share_sq`, since the ladder tooltips read HHI contribution off
   the tail.

### Shipped — UI

`ConcentrationBoard.tsx` split into `components/gamma/concentration/`: hero (giant HHI, band,
D/D, 0→1 gauge with 5-session-mean tick and band-cut ticks — the old gauge divided by 0.5 and
would peg any compressed reading at 100%), cumulative-Γ strike ladder (inline SVG: signed net-γ
bars, running gross-γ curve, spot/pin/cliff/peak markers, per-strike tooltip with HHI
contribution and distance from spot), 30-session bar chart, top-N builders, Call/Put γ HHI, and
an OI-change panel (ATM / put-call / day ΔOI / top movers). Retained by request: multi-index
strip, intraday HHI spark with mean-cross flips, Gini / Ávila quadrant, Pin / Cliff.

A **γ mass** selector in the tab header switches `mass_basis` and refetches. Without it the
net-basis path — and therefore the whole legacy-row migration — would have had no consumer in
the UI, since the board only ever requested the gross default.

### Test status

122 gamma tests pass. Full suite **480 passed, 6 failed** — the same 6 date-drifted fixtures as
the 2026-08-10 session (verified identical on a stashed tree). Band-threshold assertions in
`test_gamma_density.py` were re-pointed at the gross cuts; new coverage in `test_gamma_hhi.py`
(gross-vs-net cancellation, aggregate density fallback, γ peaks, today-excluded session stats,
full contributor list) and `test_gamma_density_history.py` (basis tagging, filtering, legacy
normalization, dual-basis resolution, in-place migration).

Verified end-to-end against a synthetic 0-DTE NIFTY chain via `StaticGammaDensityDataProvider`
(no Kite session was available): HHI 0.186 gross / 0.142 net, top-5 = 91%, peaks, D/D, 29-session
sample, ΔOI movers all render; no console errors, no horizontal overflow at 375px.
`npx vite build` fails at the TanStack **prerender** step on a clean tree too — pre-existing,
unrelated.

---

## Session 2026-08-10 — CAS reject reasons · intraday history + chart

Steps 1–2 of a 5-step plan to close the gap between the desk pre-close forecast and the official CAS indicative. Display-only; nothing under `broker/` / `execution/` / `risk/` touched.

### Why the forecast misses official (the finding that drove the plan)

`proxy_v1` is built entirely from **derivatives and continuous-session cash** (synth F, fut LTP, VWAP). The official CAS indicative is the **equilibrium of the cash auction book** — mechanically a free-float-weighted function of the 50 constituents' own auction prices. Those are driven by different order flow (index-fund / rebalance orders arrive *only* in the auction book). **Reweighting the blend cannot converge** — Phase B constituent rebuild is the only real path.

Recommended form for Phase B, in **return space**, which avoids needing the divisor or free-float share counts at all:

```text
index_cas ≈ index_anchor × (1 + Σ_covered wᵢ·rᵢ / Σ_covered wᵢ)     rᵢ = pᵢ_cas / pᵢ_anchor − 1
```

Only needs NSE's published percentage index weights; the divisor cancels; coverage renormalization beats the scaffold's implicit "uncovered stocks contribute zero." Contribution attribution falls straight out: `contribution_i = index_anchor × wᵢ × rᵢ`, and the contributions sum to the full predicted move exactly.

### Shipped

1. **Reject reasons** — `classify_official_indicative()` in `options/cas_estimate.py` returns `(value, reason)`; `sanitize_official_indicative()` is now a thin wrapper (unchanged behaviour). Payload gains `official_raw` + `official_reject_reason` (`outside_window` / `no_quote` / `missing_field` / `no_spot_anchor` / `out_of_band`, null when accepted). A blank official KPI was previously undiagnosable — absent field and rejected value both produced a bare `null`.
2. **UI bug fix** — the *Official indicative* card printed a stale `last.indicative` value while captioning it "Unavailable / rejected vs spot" (visible in the user's screenshot: 24,583.80 with a contradictory caption). Value and caption now derive from the same state, and the caption names the reject reason plus the raw Kite value.
3. **`options/cas_history.py`** — append-only `data/cas_history.jsonl`, one flat row per poll. Written from the **API route layer** so payload builders stay pure and tests stay off the filesystem. 5s per-underlying throttle, 14-session retention, best-effort (never raises). Schema already carries `constituent_est` / `coverage` for Phase B.
4. **`GET /cas/history`** — not Kite-gated (reads the local file), so the chart survives the daily ~6 AM token expiry.
5. **`CasHistoryChart`** — Highcharts Stock, same pattern as Straddle Watch. Forecast / official (dotted, `connectNulls: false` — gaps are real) / spot / synth F on one absolute axis from 15:00 IST. The page re-reads `/cas/history` on each poll tick instead of rebuilding rows client-side, so chart and KPIs cannot drift.

**One artifact, two consumers:** the same JSONL is the chart series *and* the training set for the later calibration fit (`official_close − estimate_t`). Recording it now is what makes step 5 possible later.

### Open — next steps in the plan

- **Verify the CAS window constants.** `CAS_WINDOW_START/END = 15:15–15:35` does not match commit `b20fe89` ("extend cash session end to 15:40"), and the user's screenshot shows CAS already *Closed* at 15:29:36. If the real order-collection window is 15:30–15:40, the desk polls 15 minutes of dead time and misses where the equilibrium actually settles — **a window offset would masquerade as model error**, so settle this before tuning anything.
- **Equity-quote spike** (step 3) — point `debug_quote_dump()` at ~10 NSE equities during a live auction and check for `indicative_close_price` / `total_imbalance` / `reference_limit_price`. Decides whether Phase B gets true auction prices, imbalance-skewed estimates, or just 15:30 last prints.
- Steps 4–5 (constituent rebuild + contributor endpoint; calibration fit) follow from that spike.

### Test status

54 CAS tests pass (`test_cas_history.py` is new). Full suite: **470 passed, 6 failed** — all 6 are the documented date-sensitive class, not regressions. `tests/test_vol_surface.py` hardcodes expiries `2026-07-16/23/30`, now all in the past, so `_select_expiries` returns empty. Two *more* vol-surface tests (`test_surface_shape`, `test_otm_convention`) have rotted into this since the 5 listed in CLAUDE.md; `test_execution_queue.py::test_build_execution_queue_pending_confirm_mode` now passes.

---

## Session 2026-08-06 (late) — Straddle Watch plan · security/bugbot · sign-off

Late evening IST. User asked to save this conversation for later review.

### Reviews (start of thread)

- **Security review:** 1 medium — unvalidated `underlying` on `GET /chain-history/coverage` can path-traverse outside `data/chain_history/` (allowlist / path containment).
- **Bugbot:** 1 medium — `useTheme()` per-hook state; Sonner `toasterTheme` stale after toggle in `__root.tsx`.

### Straddle Watch product understanding

- Screenshot + recording (`Recording 2026-08-05 183626.mp4`) = **iCharts** Straddle Watch (`icharts.in/opt/StraddleWatch.php`), not Sensibull/Opstra.
- Controls: Latest | Historical · Symbol · Expiry · Call/Put strike · SHOW CHART · 1D/5D/30D.
- Summary: futures quote, Fair, Lot, IV/IVR/IVP, Max Pain, PCR.
- Dual-pane chart: Call/Put/Straddle (+ VWAP/IV optional) over Call/Put OI; navigator; `"Please Wait Loading..."`.

### Decisions locked

| Decision | Choice |
|----------|--------|
| Historical mode | **Paused** — radio disabled stub; Latest only for v1 |
| Visual / chart | **2B** — Highcharts Stock dual-pane + navigator (closer iCharts clone) |

### Implementation reality (already in repo)

Desk largely shipped before this planning pass — do not rebuild from scratch:

- Engine: `options/straddle_watch.py`
- API: `GET /straddle-watch/config`, `GET /straddle-watch/snapshot`
- UI: `/straddle-watch` · `Pixel Perfect UI/src/routes/straddle-watch.tsx` · `components/straddle/StraddleWatchChart.tsx`
- Docs: `docs/straddle-watch/README.md` · index row in `docs/README.md`
- Tests: `tests/test_straddle_watch.py` — **9/9 passed** this session

### Gap plan (not executed yet) — blocking clean sign-off

Plan file: Straddle Watch Gaps (Cursor plan). Remaining:

1. Add missing `StraddleWatchRange` / `StraddleWatchSnapshot` types in `Pixel Perfect UI/src/lib/types.ts` (imported but absent).
2. Default Call/Put strikes to nearest **ATM** (currently list midpoint).
3. Auto-load **1D** once params resolve (today requires SHOW CHART first).

**Sign-off verdict this session:** not yet — backend/tests OK; three UI gaps open. Historical stays out of scope.

### Sign-off

Conversation saved here at user request. No commit from this note-taking pass.

---

## Session 2026-08-06 — CAS Indicative page · chart UI · strike strip

Evening IST session. User asked to save this conversation for later review.

### CAS Index Indicative (NIFTY-first)

- New desk page `/cas-indicative` (tabs: Indicative · Equilibrium · Methodology). BANKNIFTY / SENSEX remain stubs.
- Reuses engine `options/cas_indicative.py` + `GET /cas/indicative`. Compact `CasChip` on Gamma Density / OI Movers links to the page during the CAS window.
- **Display-only** — does not replace spot for GEX / OI math.
- NSE context: cash index is flat in the closing auction; indicative comes from the equilibrium book. Dedicated page for desk focus vs chip glance on other desks.
- Below the CAS strip: **Fut POC** (`compute_session_poc`, all-day session) + **Synth F** (nearest-expiry ATM `K + CE − PE`) + **basis vs spot** (`options/synthetic_future.py`). Stub docs: `docs/cas-indicative/README.md`.
- Full original Hybrid plan + status matrix: [`docs/cas-indicative/PLAN.md`](cas-indicative/PLAN.md). Full original Hybrid plan + shipped status: `docs/cas-indicative/PLAN.md`.

### Gamma / OI Movers chart look

- Align session charts with Straddle Watch’s light look; keep **Plotly** (no Highcharts migration).
- Shared theme helper: `Pixel Perfect UI/src/components/charts/sessionChartTheme.ts` (used by GEX / OI Movers session Plotly components).

### Net GEX by strike

- PIN level drawn dotted like ±1σ / Fut POC; value strip under the chart.
- Fixed a dedupe bug that followed DOM order incorrectly.
- Removed +VE/−VE GEX and ±1σ from the strike value strip and their chart lines (PIN / Fut POC remain the focus).

### Earlier context still relevant (not in a prior 08-03 entry)

- **Live reversal:** provisional pivots emit without waiting for a full confirm pad; API wires TF / gate params (`live` vs research modes).
- **OI Movers session-open lock:** freeze chart CE/PE Open aggregates once per session so ATM±N window rolls cannot drift Open lines mid-session (`ensure_session_open_oi` / chart Open totals).
- **GEX history:** no reverse-fill of pre-first-sample minutes; prefer recording from the open via the scheduler so the series is honest from the start of the session.

### Sign-off

Conversation saved here at user request. No commit from this note-taking pass.

---

## Session notes — 2026-07-21 evening (Analogue Paths · expiry calendar · IV Chart out)

### Arc

1. Planned Markov index desk (DOWN/FLAT/UP) → built → then **replaced with Analogue Paths** (expiry-cycle path fan, matching user’s reference UI).
2. Fixed stale-API HTML for new routes; expiry-day rollover; Sep-2025 weekday revision; fan tooltip; removed IV Chart.

### Analogue Paths (replaces Markov)

| Piece | Location |
|-------|----------|
| Engine | `analysis/analogue_cycles.py` |
| Config | `ANALOGUE_DEFAULTS` in `config.py` |
| API | `GET /analogue/config`, `/analogue/snapshot` |
| UI | `/analogue` — `Pixel Perfect UI/src/routes/analogue.tsx` (sidebar: Analogue Paths) |
| Tests | `tests/test_analogue_cycles.py` (7 passed) |
| SPA prefix | `/analogue` in `api/ui_static.py` |

**Behaviour:** Match historical weekly/monthly expiry cycles with similar % move at the same day-in-cycle → median / 25–75 / 10–90 levels, P(further up/down), path fan chart.

**Controls:** underlying, monthly|weekly, similarity band (±%), optional move override.

**Removed:** `analysis/markov_index.py`, `/markov/*`, Markov UI/types/tests, `MARKOV_INDEX_DEFAULTS`.

### Expiry weekday calendar (ICICI / NSE–BSE)

Source: [Revised expiry days](https://www.icicidirect.com/futures-and-options/articles/revised-expiry-days-for-nse-futures-and-options).

| Underlying | ≤ 31 Aug 2025 | ≥ 1 Sep 2025 |
|------------|---------------|--------------|
| NIFTY | Thursday | Tuesday |
| BANKNIFTY | Wednesday (hist.) | Tuesday |
| SENSEX | Tuesday | Thursday |

Cutover constant: `EXPIRY_WEEKDAY_CUTOVER = 2025-09-01`. Holidays snap back. Live Kite listed expiries still merge in.

### Expiry-day UX (Nifty weekly)

On expiry day, desk **rolls to next expiry** (new cycle starts next session):

- `cycle_pending: true`, `day_in_cycle: 0`, move 0% until next bar  
- Example verified: prev `2026-07-21` → current `2026-07-28`

Earlier bug showed Day 4/5 of the *ending* cycle — fixed.

### Fan chart tooltip

Analogue thin paths were flooding Recharts tooltip (empty hover). Fixed: `tooltipType="none"` on analogues + custom tooltip for current / median / 25th / 75th.

### IV Chart removed

Deleted from desk: sidebar, `/iv-chart` route, API routes, `options/iv_chart.py`, `IV_CHART_DEFAULTS`, types, `tests/test_iv_chart.py`. **IV Smile kept.**

### Ops

1. Restart API after pull (uvicorn often no `--reload`)  
2. UI `:8080` → API `:8001`  
3. Analogue first load pulls long daily history — may take a few seconds  
4. If new routes return HTML: restart API / clear site data for `:8001`

### Follow-ups (optional)

- Prefer listed expiries over reconstructed weekdays wherever Kite cache has them  
- Richer fan band fill (Area) if desired  
- Docs under `docs/iv-chart/` still describe removed desk — archive/delete when convenient  

---

## Session notes — 2026-07-21 (Kite IP · Vanna/Pricing desks · Trade recommendations)

### User reports

1. Live orders rejected: `IP 2409:40f4:3080:c486:c47d:877e:bb29:4c7c is not whitelisted` (rotating ISP IPv6).
2. Staticip.in ACTIVE — outgoing `2401:c080:2400:1643:9cb2:21a0:a6cc:f9db`, host `dc-mum-601.staticip.in`.
3. Vanna Exposure / Pricing Engine UI empty or “Expected JSON … got HTML/text”.
4. Wanted trade planning via Pricing Engine, then Trade ideas with reasoning on Pricing + Vanna pages.

### Kite egress (orders)

| Issue | Fix |
|-------|-----|
| Duplicate `.env` keys later overrode staticip (`KITE_USE_STATICIP_PROXY=0`) | Keep **one** set: `KITE_USE_STATICIP_PROXY=1`, `KITE_ALLOWED_EGRESS_IP=<staticip OUTGOING>`, `KITE_ORDER_DIRECT_FALLBACK=0`, `STATICIP_*` |
| Whitelist on developers.kite.trade | Must include **`2401:c080:2400:1643:9cb2:21a0:a6cc:f9db`** (not ISP `2409:…`) |
| Verified `/health` | `kite_proxy_enabled: true`, `kite_egress_mode: staticip_proxy` — orders then worked |

Market data stays direct; IP whitelist only gates **orders**.

### Missing API routes (root cause of empty desks)

UI existed; FastAPI routes were never registered (SPA returned HTML 200 → client treated as empty/HTML error).

| Desk | Routes added in `api/main.py` |
|------|-------------------------------|
| Vanna | `GET /vanna-exposure/config`, `/vanna-exposure/snapshot` |
| Pricing | `GET /pricing/config`, `/pricing/desk`, `POST /pricing/calculate` |

Also: `_API_PREFIXES` in `api/ui_static.py` includes `/vanna-exposure`, `/pricing`, and other analytics paths so missing routes return JSON 404, not SPA HTML.

### Client resilience (`Pixel Perfect UI/src/lib/api.ts`)

- Resolve API base: env `VITE_API_BASE_URL`, else `:8080` → `:8001`
- Reject HTML bodies; `cache: "no-store"` + one `_cb=` retry (stale HTML cache from earlier SPA trap)
- `pickNearestExpiry`: after 15:30 IST skip today’s expiry (avoids “expiry in the past” post-close)
- **Do not** Vite-proxy page paths like `/vanna-exposure` to `:8001` (steals UI route → blank shell)

**Use UI:** `http://127.0.0.1:8080` (dev). Built SPA pages on `:8001` were not mounted this session.

### Trade ideas — Pricing Engine

| Piece | Location |
|-------|----------|
| Logic | `pricing/recommendations.py` — bull put credit + call debit from BS edge |
| Wire | `build_pricing_desk` → `recommendations[]` |
| Config | `PRICING_ENGINE_DEFAULTS["recommendations"]` |
| UI | Trade ideas card on Live desk (`pricing-engine.tsx`) |
| Tests | `tests/test_pricing_engine.py` (7 passed) |

**Semantics:** edge = LTP − BS fair @ flat ATM IV; + rich (sell), − cheap (buy). Ideas include credit/debit, max P/L, BE, ₹/lot, reasoning, disclaimer.

**Example (session):** Sell PE 24200 / Buy PE 24100 → credit ~47.5, max loss ~52.5, BE ~24152.5, lot 65.

### Trade ideas — Vanna Exposure

| Piece | Location |
|-------|----------|
| Logic | `options/vanna_recommendations.py` — regime / Vanna Line / CE·PE wall tilts |
| Wire | `build_vanna_snapshot` → `recommendations[]` |
| Config | `VANNA_EXPOSURE_DEFAULTS["recommendations"]` |
| UI | Trade ideas card on Vanna page + link to Pricing Engine |
| Tests | `tests/test_vanna_exposure.py` (7 passed) |

**Semantics:** dealer VEX / vol-up flow (not LTP premium math). Size on Pricing Engine.

### Ops checklist after pull

1. `.env`: staticip proxy on; whitelist staticip outgoing IP on Kite  
2. Restart API (`Start_API` / uvicorn `:8001`)  
3. UI on `:8080`; hard-refresh / clear site data for `127.0.0.1:8001` if HTML errors persist  
4. Pricing Engine + Vanna Exposure → Trade ideas cards above matrix / below stats  

### Not done / follow-ups

- Rebuild desk for single-port `:8001` SPA hosting (`npm run build` + mount)  
- Optional: Vanna ideas attach live Pricing spreads  
- Optional: Gamma Density trade ideas (same pattern)

---

## Session notes — 2026-07-15 (IV Smile · IV Chart · Calendar Arbitrage)

### User request

Port three OpenAlgo Flask blueprints from Downloads into 3ST:

- `ivsmile.py` → single-expiry CE/PE IV curve + ATM IV + skew  
- `ivchart.py` → intraday ATM IV time series + greeks  
- `arbitrage.py` → futures calendar-spread universe (near/next/third month)

**Approach:** Plan first, then implement as recommended (FastAPI + Pixel Perfect UI; reuse `options/iv.py`, `chain.py`, Kite batch quotes/history).

### Backend shipped

| Feature | Module | API routes |
|---------|--------|------------|
| IV Smile | `options/iv_smile.py` | `GET /iv-smile/config`, `/iv-smile/snapshot?underlying=&expiry=&strike_count=` |
| IV Chart | `options/iv_chart.py`, `options/greeks.py` | `GET /iv-chart/config`, `/iv-chart/symbols`, `/iv-chart/snapshot?interval=&days=&legs=` |
| Calendar Arb | `options/calendar_arbitrage.py` | `GET /arbitrage/config`, `/arbitrage/universe`, `/arbitrage/snapshot` |

**Config:** `IV_SMILE_DEFAULTS`, `IV_CHART_DEFAULTS`, `CALENDAR_ARBITRAGE_DEFAULTS` in `config.py`.

**Design choices:**

- Black-Scholes IV (existing `options/iv.py`) — not Black-76 from OpenAlgo  
- Expiry format: ISO `YYYY-MM-DD` (not OpenAlgo `DDMMMYY`)  
- Arbitrage MVP: **REST quote poll** (~8s) — no WebSocket depth yet; spread % from bid/ask when available, else LTP mid  
- IV Chart: historical option + underlying candles merged on timestamp; TTE computed per candle via `time_to_expiry_years(..., as_of=candle_time)`

### Frontend shipped

| Route | File | Sidebar |
|-------|------|---------|
| `/iv-smile` | `Pixel Perfect UI/src/routes/iv-smile.tsx` | IV Smile |
| `/iv-chart` | `Pixel Perfect UI/src/routes/iv-chart.tsx` | IV Chart |
| `/arbitrage` | `Pixel Perfect UI/src/routes/arbitrage.tsx` | Calendar Arb |

Types added to `src/lib/types.ts`. Routes registered in `routeTree.gen.ts`.

### Tests

All passing (8 tests):

- `tests/test_iv_smile.py`  
- `tests/test_iv_chart.py`  
- `tests/test_calendar_arbitrage.py`

### Usage

1. Restart API after pull  
2. Kite login required for snapshots (arbitrage universe works offline from instruments cache)  
3. IV Chart: first load fetches Kite historical — may take several seconds  

### Future (not in this session)

- WebSocket depth for arbitrage (OpenAlgo parity)  
- MCX underlyings on IV Smile/Chart UI (backend supports index set; arb already NFO+MCX)  
- IV surface tab merge vs standalone `/iv-smile` (both coexist)

---

## Session notes — 2026-07-15 (Rolling Straddle orders · Kite egress · whitelist)

**Transcript:** `1846333d-7602-418a-a7e9-b8c6a9183e46` (Cursor agent-transcripts)

### User report

- Rolling Straddle **ARMED / LIVE / RUNNING** on **SENSEX**, ATM ~77600, spot updating, but **no orders**.
- Activity log showed repeated **“Kite API unreachable (DNS/network)”** while `nslookup api.kite.trade` succeeded (8.8.8.8 → 104.16.x + IPv6).
- Later: cannot add new IPv6 to Kite whitelist — **exchange lock until Monday 20 Jul 2026** (one update per calendar week).

### Root causes (confirmed by diagnostics)

| # | Issue | Effect |
|---|--------|--------|
| 1 | **Windows + urllib3 + IPv6 `source_address` bind** | Order client reports fake **getaddrinfo failed** even when DNS works in PowerShell |
| 2 | **Direct egress uses rotating ISP IPv6** | Different from `KITE_ALLOWED_EGRESS_IP`; Kite rejects order (IP not whitelisted) |
| 3 | **IPv6 bind → Cloudflare timeout** | Bound path resolves but connect times out on this network |
| 4 | **`morning_bar_seen` reset on underlying switch** after 09:20 | UI showed “Waiting 09:20” / ticks skipped until morning bar re-seen |
| 5 | **ShortSignalsOnly** | CE `long_ready` does **not** enter — only **short** signals on CE/PE |

**Not the problem:** Signal / ATM trigger logic — PE `short_ready` fired and `pe_entry_signal` logged.

### Trigger logic (ShortSignalsOnly @ ATM)

- **PE entry:** `_short_entry_reason` → `short_ready` or `short_entry` on PE option chart at signal strike (open leg keeps entry strike; flat leg uses current ATM).
- **CE entry:** Same short path only — CE `long_ready` is ignored in this mode.
- Gates: ARMED (live), `morning_bar_seen`, not same-bar blocked, max entries, session window, `execution_mode != confirm`.

### Log evidence (2026-07-15)

```
pe_entry_signal  short_ready  atm 77500
pe_entry_failed  IP 2409:40f4:4004:5dd4:a549:6b48:565e:8db9 not whitelisted
```

Earlier ticks: DNS errors every ~60s from bound client before fallback / API reload.

### Code fixes shipped (restart API to load)

| Area | File | Change |
|------|------|--------|
| Windows IPv6 bind | `kite_auth.py` | Manual socket connect adapter (avoids urllib3 DNS break on Win32) |
| Order fallback | `broker/kite_broker.py` | Retry via direct when bind fails (`KITE_ORDER_DIRECT_FALLBACK=auto`) |
| Market data reads | `kite_client.py`, `broker/kite_broker.py` | Quotes/LTP/history/positions/orders via **direct** client only |
| Historical fetch | `kite_client.py` | Always `_kite_direct_client()` (no bound client) |
| Profile/margins | `kite_client.py` | `kite_read_client()` |
| Morning bar on underlying switch | `execution/rolling_straddle_store.py` | If `entry_start` already passed today, keep/set `morning_bar_seen=True` |
| Error messages | `kite_errors.py` | Distinguish nslookup-OK vs Win bind; bind timeout hint |
| Env docs | `.env.example` | `KITE_ORDER_DIRECT_FALLBACK=auto` |

### User actions (until whitelist unlock 20 Jul 2026)

1. **Restart API:** `Stop_3ST.cmd` → `Start_API.cmd`
2. **Set `KITE_ORDER_DIRECT_FALLBACK=0`** in `.env` — avoid direct retry with non-whitelisted rotating IPv6 (will fail until whitelist updated)
3. **Orders must egress via already-whitelisted IPv6:** `KITE_ALLOWED_EGRESS_IP=2409:40f4:400e:6518:8947:4b08:26cd:c16c` (must match developers.kite.trade exactly)
4. **Alternative:** `KITE_USE_STATICIP_PROXY=1` if staticip egress IP is already on whitelist
5. **Until Monday 20 Jul:** Paper mode for signal testing, or fix IPv6 bind routing; cannot add `2409:40f4:4004:...` to whitelist this week

### Config snapshot (session)

- Underlying: **SENSEX**, expiry 2026-07-16, **15min**, **ShortSignalsOnly**, entry **09:20**, execution **auto**, ARMED live
- Egress mode: **local_bind** (`2409:40f4:400e:6518:8947:4b08:26cd:c16c`)
- Broker already had **SENSEX PE** position (qty -240) — some fills occurred outside algo state sync

---


## Session notes — 2026-07-14 evening (CRUDEOIL + UX + infra)

### Trade mode UI — Buy / Short (CE & PE)

- Dropdown label **`Both`** → **Buy / Short (CE & PE)** with helper text (buy = long option, short = sell option).
- Backend value remains `"Both"`; `ShortSignalsOnly` = short-only on both legs.
- Test: `tests/test_rolling_straddle_trade_mode.py` (`test_both_allows_buy_and_short_on_ce_and_pe`).

### Problem: CRUDEOIL selected but Live status shows NIFTY spot (~24035 / ATM 24050)

User switched underlying to **Crude Oil**; config saved correctly but Live status stayed on NIFTY levels. Activity log: `spot_sanity_reject` (Crude ~7780 vs last 24035) → `No CE found for CRUDEOIL strike 24050`.

**Root causes (stacked):**

| # | Bug | Effect |
|---|-----|--------|
| 1 | `save_state()` ignored `last_spot: null` | Only `current_atm` cleared, NIFTY spot stuck on disk |
| 2 | `state_underlying=CRUDEOIL` set without clearing spot | Stale check skipped when marker matched |
| 3 | `_resolve_tick_spot` fell back to wrong-scale `last_spot` | Each tick re-applied NIFTY spot |

**Fixes shipped:**

| Fix | File |
|-----|------|
| `_NULLABLE_STATE_KEYS` includes `last_spot`, etc. | `rolling_straddle_store.py` |
| `clear_spot_state_for_underlying()` on underlying change in `save_config` | `rolling_straddle_store.py` |
| `_spot_plausible_for_underlying()` bands (NIFTY vs CRUDEOIL scale) | `rolling_straddle.py` |
| `_ensure_state_underlying()` on tick + status poll | `rolling_straddle.py` |
| Live status shows **Underlying** row | `rolling-straddle.tsx` |
| Tests | `tests/test_mcx_rolling_straddle.py` |

**MCX Crude is already supported** in the same Rolling Straddle page (not a separate algo): **NRML**, entry **15:30**, force exit **22:45**, spot from front-month future. UI badge now shows **Waiting 15:30** (was hardcoded “9:20”).

### Risk limits resetting after API restart

Limits were in-memory only (defaults: 6 positions, 10 orders/min).

**Fix:** Persist to `data/risk_limits.json`; load on import. User values: 100 / 100 / 500 / 10000. Settings page note added.

### Kite login failures + ARM / status UI stale

- OAuth callback failed: `Failed to resolve api.kite.trade` (DNS/network).
- `/live/rolling-straddle/status` returned 400 when Kite broker sync hung/failed → UI stuck on **Paper / not logged in** while server had session + ARM.
- **Fixes:** OAuth token exchange uses **direct** connection (`_kite(use_proxy=False)`); friendly auth errors; status bundle wraps broker sync in try/except; UI loads `/live/arm` + `/auth/me` independently of status; broker positions **8s timeout**.

Saved session on disk: user **OV7159** — refresh `/login` may show “Already connected” without re-OAuth.

### Save slow + Activity log errors

- **Save** awaited full `refresh()` including slow status poll (30–60s+ when Kite unreachable).
- Log fetch was in same `Promise.all` as status → log blocked until status finished.

**Fixes:** Save returns after POST; log loads independently with Retry; broker timeout on status.

### Start button not working

| Cause | Fix / action |
|-------|----------------|
| **Live mode + Kite not logged in (UI)** | Start **disabled** — login at `/login` or use **Paper** |
| Start called `saveConfig()` + full refresh | Start posts config + start only |
| `start_runner` reconcile hang | try/except on reconcile at start |
| **No spot for CRUDEOIL** | Kite network — ticks fail until `api.kite.trade` reachable |

### Rolling Straddle workflow (quick reference)

1. **Configure** → underlying, expiry, timeframe, trade mode, execution mode  
2. **Save**  
3. **Paper** (test) or **Live** + **Login Kite** + **ARM**  
4. **Start** → runner `running`, scheduler ticks every `tick_interval_sec`  
5. After **entry_start** (15:30 MCX / 09:20 NSE), 3ST signals on **CE chart** and **PE chart** independently  
6. **Buy/Short mode:** long signal → buy option; short signal → sell option on each leg  
7. Exits: zone (bar close default), TSL, force exit — **DISARM blocks all live orders**

### Dev startup (`start_3st_dev.ps1`)

- API often needs **>45s** first boot → script waits **90s API / 120s UI** now.
- Opens separate PowerShell windows for API (8001) and UI (8080).

### Rolling Straddle config snapshot (session end ~16:41)

| Setting | Value |
|---------|-------|
| underlying | CRUDEOIL |
| expiry | 2026-07-16 |
| timeframe | 5min |
| trade_mode | Both (Buy / Short CE & PE) |
| entry_start | 15:30 |
| exit_on_bar_close_only | true |
| execution_mode | auto |

---

## Session notes — 2026-07-14 afternoon (PE whipsaw + ARM)

### Problem: NIFTY 24100 PE rapid buy/sell (~12:42–12:48)

Seven round-trips in ~6 minutes — SELL entry / BUY exit repeating every 1–3 min.

**Root cause (whipsaw loop):**

```
Every 60s tick → LTP crosses ST1 → BUY exit (intrabar)
→ leg flat → zone_active + short_ready still true on last 5m bar → SELL re-entry
→ repeat
```

| Setting | Value | Impact |
|---------|-------|--------|
| Timeframe | 5min | Signals from **closed bar** |
| Tick interval | 60s | Orders could fire **every minute** |
| `reentry_style` | `zone_active` | Re-enter on `short_ready` without fresh edge |
| `max_reentries_pe` | 20 | Up to 21 entries/day |
| `exit_on_bar_close_only` | (added) | Was **off** — LTP zone exits fired intrabar |

**5m bar close times (NSE 9:15):** :00, :05, :10, … :55. At 12:51 → last closed bar **12:45–12:50**, next close **12:55**.

### Fix shipped — anti-churn (2026-07-14 ~13:35)

| Fix | Behavior |
|-----|----------|
| `exit_on_bar_close_only: true` (default) | Zone exits only on **closed bar** `short_zone_exit`; LTP cross logs `pe_exit_deferred_ltp` |
| **Re-entry cooldown** | After exit on bar X, no re-entry until bar **after** X closes (keeps `zone_active`) |
| **One action per bar** | `last_action_bar_ts` blocks duplicate entry/exit same bar |
| UI | **Zone exit timing** dropdown: Bar close only (recommended) vs Intrabar LTP |
| Tests | `tests/test_rolling_straddle_bar_churn.py` |

**Note:** TSL (ATR × 1.2) can still exit **intrabar** on LTP — turn off if churn continues.

### Problem: PE exit “Triggered” but leg still open (~13:40)

User saw ST @ 58 and ATR @ 64 **Triggered**, LTP ~73, leg open.

**Findings:**

| Layer | What happened |
|-------|----------------|
| **UI “Triggered”** | Display only — LTP > ST/ATR levels (not order sent) |
| **13:40 bar** | Close 74.50 > ST 58.04 → bar exit **valid** (`short_zone_exit`) |
| **Backend** | `armed=false` at API — **DISARMED blocks all Kite exits** |
| **API restart** | ARM was **in-memory only** — restart ~13:35 wiped ARM |
| **UI confusion** | Red **ARM** button = “click to arm” (not armed); **DISARM** red = armed |

**13:50 bar:** Close 60.20 < ST 71.10 — exit window on 13:40 bar missed while DISARMED.

### Fix shipped — ARM persistence + exit-blocked UI (2026-07-14 ~14:00)

| Item | Files |
|------|-------|
| ARM/mode persisted to `data/arm_state.json` | `execution/arming.py` |
| Reload on API startup | `api/main.py` lifespan, module load |
| Leg card banner: “Exit triggered but blocked — DISARMED” | `rolling-straddle.tsx` |
| Header badge: “Live orders blocked until ARM” | `rolling-straddle.tsx` |
| ARM button shows **ARMED** when active vs red **ARM** when not | `rolling-straddle.tsx` |
| Log `ce/pe_exit_blocked_disarm` when signal fires but DISARM | `rolling_straddle.py` |
| Tests | `tests/test_arm_persistence.py` |

**After deploy:** ARM once → confirm; survives API restarts. Check header badge says **ARMED** (not just red ARM button).

### Rolling Straddle config snapshot (session end ~14:07)

- NIFTY, expiry 2026-07-14, `ShortSignalsOnly`, 5min, `exit_on_bar_close_only: true`
- `max_reentries_pe: 20` (consider lowering to 1–2)
- `tsl_mode: ATR`, `tsl_value: 1.2`
- Runner: running · Live mode · re-ARM after persistence deploy

---

## Execution architecture — phase reminders

**Problem we solved:** Multiple systems (Rolling Straddle, Watchlist Live Desk, Kite) each had separate position state → orphan drift after restart (e.g. PE -130 on Kite but flat in RS UI).

**Target:** Position Ledger + Order Router + global Execution Taskbar (one panel for all legs).

| Phase | Status | What it delivers |
|-------|--------|------------------|
| **Phase 1** | ✅ Done | Restart-safe RS state, orphan detection, adopt/unlink/close-leg |
| **Phase 2** | ✅ Done | `GET /execution/queue`, global Execution Taskbar, confirm mode |
| **Phase 3** | ⏳ Next | Position Ledger + Order Router — single write path to Kite |
| **Phase 4** | ⏳ Later | Multi-instrument instances (NIFTY + BANKNIFTY RS concurrently) |

### Phase 1 — completed (2026-07-14)

| Item | Files |
|------|-------|
| Preserve open legs on daily reset (`session_date: null` no wipe) | `execution/rolling_straddle_store.py` |
| Reconcile on runner start; restore via `3ST-*-entry` tag | `execution/rolling_straddle.py` |
| Orphans in status API + amber UI banner | `api/main.py`, `rolling-straddle.tsx` |
| `POST …/adopt-leg`, `POST …/unlink-leg` | `api/main.py` |
| Tests | `tests/test_rolling_straddle_reconcile.py` |

**Design rules:** Broker owns qty; ledger owns intent · Never auto-adopt manual Kite trades · Unlink ≠ Close.

### Phase 2 — completed (2026-07-14)

| Item | Files |
|------|-------|
| Unified queue aggregator | `execution/execution_queue.py` |
| `GET /execution/queue` | `api/main.py` |
| `POST /execution/queue/{leg_id}/action` — adopt, unlink, close, ship, execute, dismiss | `api/main.py` |
| Global Execution Taskbar (Pending / Active / Orphans / Errors) | `components/execution/ExecutionTaskbar.tsx`, `hooks/useExecutionQueue.ts`, `__root.tsx` |
| Orphan badge on sidebar (Algo Execution) | `AppSidebar.tsx` |
| RS `execution_mode`: `auto` \| `confirm` (Ship from taskbar) | `rolling_straddle_store.py`, `rolling-straddle.tsx` |
| Watchlist unlink + single-position adopt/close | `watchlist_close.py`, `desk_trades.py` |
| Tests | `tests/test_execution_queue.py` |

### Phase 3 — review points (NOT built yet)

**Goal:** Single write path to Kite; strategies emit intents, router executes.

| Module | Responsibility |
|--------|----------------|
| `execution/position_ledger.py` | CRUD legs, persist `data/position_ledger.json` |
| `execution/order_router.py` | Risk + ARM + idempotency tag + `place_leg_order` |
| `execution/signal_bus.py` | `{ owner, intent, instrument, reason }` events |

**Migration order:**
1. Rolling Straddle `_enter_leg` / `_exit_leg` → router
2. Watchlist activation + exit runner → router
3. Survivor / Wave → router (later)
4. Unified reconcile in `execution/reconcile.py` → patches ledger only
5. Taskbar reads ledger only (not RS + WL stores separately)

**Acceptance criteria:**
- [ ] All live orders pass through `order_router.py` (grep audit)
- [ ] Duplicate entry blocked by tag within same bar
- [ ] Ledger is sole source for taskbar
- [ ] RS state file becomes runner metadata only (legs live in ledger)

**Flow:**
```
Strategy tick → SignalIntent → ledger.upsert_pending()
  → if execution_mode==auto and ARM: router.execute(intent)
    → duplicate tag check → risk → Kite → ledger.mark_open()
```

### Phase 4 — review points (NOT built yet)

**Goal:** N concurrent strategy instances without CE/PE collision.

| Item | Detail |
|------|--------|
| `data/strategy_instances.json` | `rs-nifty-jul14`, `rs-banknifty-jul14`, etc. |
| `execution/scheduler.py` | Loop instances, not single RS config |
| Per-instance state | `rolling_straddle_state_{instance_id}.json` or ledger-only |
| UI `/execution` | Instance list with Start/Stop per row |
| Risk budgets (optional) | `max_open_legs`, `max_margin_cr` per underlying |

**Acceptance criteria:**
- [ ] NIFTY + BANKNIFTY Rolling Straddle run concurrently
- [ ] Taskbar groups by instance / underlying
- [ ] No cross-contamination of CE/PE legs

### Open decisions (confirm before Phase 3)

1. **Confirm mode default** — Rolling Straddle stays `auto` (current default) or switch to `confirm`?
2. **Orphan policy** — Always manual adopt, or auto-adopt if `3ST-*-entry` tag exists? (Phase 1: manual only)
3. **Taskbar placement** — Bottom dock (current) vs right drawer?
4. **Phase 3 storage** — JSON file OK, or SQLite from the start?

### Key behaviors (this session)

| Topic | Behavior |
|-------|----------|
| **DISARM** | Blocks all live Kite orders (entries + exits) via `require_armed_for_live()` |
| **ARM persistence** | Saved in `data/arm_state.json` — survives API restart |
| **UI ARM button** | Red **ARM** = not armed (click to confirm); grey **ARMED** = armed |
| **Exit “Triggered” UI** | LTP cross display ≠ order sent; bar exit waits for 5m close if `exit_on_bar_close_only` |
| **exit_blocked_disarm** | Logged when exit signal active but DISARMED |
| **Stop** | Stops algo tick loop; separate from DISARM |
| **Close leg** | Places Kite exit (requires ARM in live) |
| **Unlink** | Stops 3ST monitoring only — Kite position unchanged |
| **Adopt** | Links broker position for 3ST exit monitoring |
| **Trade mode `ShortSignalsOnly`** | Short signals on both CE and PE; no long entries |
| **Re-entry cooldown** | After exit, wait for next closed 5m bar before `zone_active` re-entry |
| **Kite tokens** | Expire ~6 AM IST daily — re-login at `/login` |

### Dev URLs

| Service | URL |
|---------|-----|
| UI | http://127.0.0.1:8080 |
| API | http://127.0.0.1:8001 |
| Execution queue | `GET /execution/queue` |
| Start stack | `scripts/start_3st_dev.ps1` |

---

### Session notes — 2026-07-14 (execution architecture + morning)

*Earlier same day: Phase 1/2, orphan drift, ShortSignalsOnly, runner stop debugging.*

### Issues debugged (morning session)

| Issue | Root cause | Resolution |
|-------|------------|------------|
| CE short didn’t trigger ~11:42 | RS **runner stopped** (last tick ~10:50) | Restart runner; signal was valid once running |
| PE on Kite, flat in RS UI | Runner restart wiped local PE state; reconcile didn’t restore without algo history | Phase 1: restore via broker + order tags; orphan banner |
| Live Desk managing manual Kite orders | “Link for 3ST exit” / `adopt_open_positions` | Unlink watchlist items without closing Kite |
| `Incorrect api_key or access_token` | Expired Kite session from prior day | Re-login |

### Rolling Straddle config snapshot (session end)

- NIFTY, expiry 2026-07-14, `ShortSignalsOnly`, 1 lot, live MIS, regular ST1
- Runner running; legs varied during session (CE exited via ST LTP; orphans when PE unlinked)

---


## Expiry dropdown blank per instrument (2026-07-13 evening)

### Symptom
Rolling Straddle (and other desks): **Crude Oil Mini** selected but **Expiry** dropdown blank.

### Root cause
1. Radix Select shows **blank** when `value` is set (e.g. saved `2026-07-16`) before the expiry list finishes loading.
2. Stale `/options/expiries` responses from a previous underlying could overwrite the current list (no cancel guard).

API was fine — `GET /options/expiries?underlying=CRUDEOILM` returns `2026-07-16`, `2026-08-17`, `2026-09-17`.

### Fixes

| Area | Change |
|------|--------|
| `Pixel Perfect UI/src/hooks/useOptionExpiries.ts` | New hook — loads expiries per underlying, cancels stale requests, clears while loading |
| `rolling-straddle.tsx` | Select binds value only when expiry is in loaded list; auto-picks nearest expiry |
| `gamma-density.tsx`, `oi-var.tsx`, `oi-tracker.tsx` | Same hook + select pattern |
| `api/main.py` | `/options/expiries` uppercases underlying (`CRUDEOILM`) |
| `lib/types.ts` | `OiUnderlying` extended with MCX: `CRUDEOIL`, `CRUDEOILM`, `NATURALGAS` |

---

## Gamma Density — pluggable broker data (2026-07-13 evening)

New file: **`options/gamma_density_provider.py`**

| Class | Role |
|-------|------|
| `GammaDensityDataProvider` | ABC — chain, spot, batch quotes, expiries |
| `KiteGammaDensityDataProvider` | Default (Kite instruments + `kite.quote`) |
| `CallableGammaDensityDataProvider` | Wrap functions for custom brokers |
| `StaticGammaDensityDataProvider` | Offline / test data |

- `build_gamma_snapshot(..., provider=)` and `set_gamma_density_provider()` mirror RRG’s `RrgDataProvider` pattern.
- API only requires Kite session when `provider.requires_session()` is true.
- Tests: `tests/test_gamma_density_provider.py` + updated `tests/test_gamma_density.py`.

**Plug in a broker:**
```python
from options.gamma_density_provider import CallableGammaDensityDataProvider, set_gamma_density_provider
set_gamma_density_provider(CallableGammaDensityDataProvider(
    name="firstock", get_chain=..., get_spot=..., fetch_quotes=..., list_expiries=...,
))
```

---

## Broker sync — CE open locally but flat at Kite (2026-07-13 evening)

### Symptom
Banner: `Broker sync: ce: algo leg open locally but broker flat`  
CE leg showed **open** in terminal; Kite had no `CRUDEOILM26JUL7150CE` position (user closed at broker). PE synced correctly (`broker_qty: -1`).

### Root cause
`_reconcile_broker_legs()` detected mismatch but only set `broker_qty: 0` — left `status: "open"`.

### Fix (`execution/rolling_straddle.py`)

- When live broker is flat for an algo-managed open leg → **`_flat_leg_from_broker_sync()`** (status flat, clear symbol/qty/entry).
- Logs `ce_broker_sync` / `pe_broker_sync`; skips flatten if Kite positions read fails.
- Paper mode: same flatten when open leg’s exact symbol absent from paper broker.
- Test: `test_reconcile_flattens_algo_leg_when_broker_closed` in `tests/test_rolling_straddle_reconcile.py`.

Runs on every **status poll** (`status_bundle`) and **tick**.

---

## Run 3ST (dev)

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Dev\3ST\scripts\start_3st_dev.ps1"
```

| Service | URL |
|---------|-----|
| UI | http://127.0.0.1:8080 |
| API | http://127.0.0.1:8001 |
| Login | http://127.0.0.1:8080/login |

Stop: `.\scripts\stop_3st.ps1`

---

## CRUDEOILM expiry fix & rolling straddle hardening (2026-07-13)

### Symptom
Activity log spammed:
`No CE found for CRUDEOILM expiry 2026-07-14 strike 7050.0`

### Root cause
`data/rolling_straddle_config.json` had **`expiry: 2026-07-14`**, which does not exist for CRUDEOILM options (likely carried over from another underlying or manually set). Kite instruments show:

| Underlying | Option expiries (nearest first) | Futures (nearest first) |
|------------|--------------------------------|-------------------------|
| **CRUDEOILM** | `2026-07-16`, `2026-08-17`, `2026-09-17` | `2026-07-20`, `2026-08-19`, … |
| CRUDEOIL | same as CRUDEOILM | same |
| NATURALGAS | `2026-07-24`, `2026-08-24`, … | `2026-07-28`, … |

**`2026-07-14` exists for no crude instrument** (options or futures).

### Verified resolution
With corrected expiry **`2026-07-16`**, strike **7050 CE** resolves to **`CRUDEOILM26JUL7050CE`**. Config updated; CE leg trading again.

### Fixes implemented

| Area | Change |
|------|--------|
| `options/chain.py` | New `resolve_expiry()` — validates against `list_expiries()`, falls back to `nearest_expiry()` |
| `execution/rolling_straddle_store.py` | `save_config()` auto-corrects invalid expiry; logs `expiry_corrected` |
| `execution/rolling_straddle.py` | `_ensure_config_expiry()` on every `tick()` and `start_runner()` |
| `Pixel Perfect UI/src/routes/rolling-straddle.tsx` | Auto-select first valid expiry when underlying changes or saved expiry is invalid |
| `tests/test_mcx_rolling_straddle.py` | Tests for `resolve_expiry`, stale-expiry correction on save |

### Related fixes (same multi-session arc)

| Issue | Cause | Fix |
|-------|--------|-----|
| Wrong ATM (~8100) for CRUDEOILM | Chain-mid spot fallback when futures LTP failed | MCX `spot_source=future` — no chain-mid fallback; `_resolve_tick_spot()` rejects absurd jumps |
| Kite orders rejected (`pe_exit_failed`) | Egress via staticip proxy used non-whitelisted IPv6 | `KITE_USE_STATICIP_PROXY=0`; bind exact whitelist IP `2409:40f4:400e:6518:8947:4b08:26cd:c16c` in `kite_auth.py` |
| ST exit logic mismatch | Direction-based vs price-cross | Reverted to ST1 price cross: long entry above ST1, short below; exits opposite |
| 3ST strategy split | Multiple modules | Merged into single `strategy_3st.py`; `strategy_3st_filter.py` is thin re-export shim; Rolling Straddle uses `st1_only=True` |

### Current rolling straddle config (`data/rolling_straddle_config.json`)
- **Underlying:** CRUDEOILM
- **Expiry:** `2026-07-16` (auto-corrected if invalid)
- **Timeframe:** 3min · **ST method:** regular · ST1 only enabled
- **Session:** MCX 15:30 entry / 22:45 force exit · **Product:** NRML

### API restart
```powershell
cd C:\Dev\3ST
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8001
```
Verify: `GET /health` → `kite_egress_mode: local_bind`

---

## OI Profile desk — futures OI + strike butterfly (2026-07-12, latest session)

Ported a downloaded OpenAlgo Flask blueprint `oiprofile.py` (endpoint-only wrapper, engine missing) into a native 3ST feature: **index-futures candles + open interest**, an **OI-by-strike butterfly**, and **daily OI change**. Confirmed scope with the user up front: OI-by-price = two-sided buildup/unwind histogram, true recharts candlesticks, index futures only, build end-to-end.

### Backend
- `kite_client.py` — threaded an `oi=True` flag through `fetch_historical_by_token` / `_fetch_historical_chunks` so futures history returns OI per candle (Kite `historical_data(oi=…)`).
- `instruments.py` — new `resolve_future()` (front-month by default or a pinned expiry) + `list_future_expiries()`. Matches **exact underlying name** (fallback to strict `^{U}\d…FUT` pattern), dedupes by expiry — avoids pulling in `NIFTYNXT50`/sector futures that share the same monthly expiry.
- `options/oi_profile.py` — engine:
  - Candles + OI + bar-over-bar `oi_change`.
  - **OI-by-strike butterfly**: snaps each bar to its nearest strike level (`_strike_step`: NIFTY/FINNIFTY 50, BANKNIFTY/SENSEX 100), splits buildup (OI↑) vs unwinding (OI↓) per strike; POC = strike with most OI activity; top-3 OI walls.
  - **Daily OI change**: day-over-day close/OI deltas classified Long buildup / Short buildup / Short covering / Long unwinding.
- `config.py` — `OI_PROFILE_DEFAULTS` (intervals 1/5/15min, default 5d, max 30d, underlyings NIFTY/BANKNIFTY/FINNIFTY/SENSEX).
- `api/main.py` — `GET /oi-profile/config` + `GET /oi-profile/snapshot?underlying=&expiry=&interval=&days=` (Kite-session gated).

### Frontend
- `src/routes/oi-profile.tsx` — true candlesticks via a `<Customized>` layer drawn against recharts' injected axis scales, OI line overlay + POC line; diverging horizontal **butterfly** with a **Strike** axis; daily OI-change table with colored interpretation badges.
- `src/lib/types.ts` — `OiProfile*` types. `AppSidebar.tsx` — "OI Profile" entry. `routeTree.gen.ts` — `/oi-profile` registered manually (survives without the dev-time generator).
- `tests/test_oi_profile.py` — classify, daily resample, butterfly split/order, end-to-end snapshot (mocked Kite), empty path.

### Follow-up fixes (same session, from live screenshots)
- **Duplicate expiry** in the future dropdown (`2026-07-282026-07-28`) → root-caused to the loose `startswith` matcher pulling `NIFTYNXT50`; fixed with exact-name match + expiry dedupe (backend) and `new Set` dedupe (UI).
- **Weekly expiries**: clarified index **futures are monthly-only** (weeklies exist for options, not futures) — monthly-only list is correct/by-design.
- **Strike alignment**: butterfly rebucketed from evenly-split price buckets onto **real strike levels** so the axis/POC/walls read clean strikes (e.g. 24500/24550).

### Environment note
- In-session shell/terminal was unresponsive again (even `echo` returned no status), so `pytest` / server launch could not be run from the agent. Backend picks up via `uvicorn --reload`; UI hot-reloads. **Still to run locally:** `tests/test_oi_profile.py` and a live `GET /oi-profile/snapshot?underlying=NIFTY` after Kite login. If a contract can't resolve, capture the exact Kite `name`/`tradingsymbol` for that future.

---

## Real-time feed, trade-safety gate & SuperTrend parity (2026-07-12, later session)

Reviewed a downloaded Flask/Flask-SocketIO `websocket_example.py` and adapted its best ideas to 3ST's FastAPI + existing `ltp_cache`. Then compared a downloaded OpenAlgo `supertrend.py` against 3ST's SuperTrend and quantified the ATR-method drift.

### Trade-management safety gate + REST reconfirm (`execution/watchlist_exit_runner.py`)
- `_should_exit` now also returns a `kind`: `risk` / `zone_ltp` (live-price dependent) or `zone_bar` / `force` (not tick-dependent).
- Before auto-closing a **price-based** exit (SL/TSL/target or live-price ST zone), the runner forces **one authoritative REST re-fetch** and re-verifies the exit still holds:
  - REST fails → **defer** the tick (logs `auto_exit_deferred`, cause `reconfirm_unavailable`).
  - REST no longer confirms → **defer** (cause `reconfirm_negated`).
  - REST confirms → close using the reconfirmed price.
- Time-based force-exit and bar-based zone exits still proceed (not tick-dependent), so session square-offs are never missed.
- Scan result now includes `deferred` and `data_health`.

### Feed health metrics + gate (`execution/ltp_cache.py`)
- Added counters: `total_updates`, `last_tick_age`, `reconnects`, `uptime` (bumped in `ingest_ws_ticks` / `_feed_runner` / `start`).
- New methods `health()`, `snapshot()`, `rest_prices()`; module-level `market_health()` and `is_trade_management_safe()`.

### FastAPI WebSocket push (`api/main.py`)
- Native `@app.websocket("/ws/ltp")` streams cached LTPs + health every 1s (no Flask-SocketIO).
- New `GET /market/health` (health + safety gate) for the UI badge.

### Live Desk UI
- `src/hooks/useLtpFeed.ts` — WS client with auto-reconnect backoff; `src/lib/api.ts` `getWsUrl()`.
- `src/components/live/MarketHealthBadge.tsx` — header badge (feed status, last-tick age, reconnects, gated flag).
- `KitePositionsTable` + Active Trades table — live-tick overlay (green dot + smooth mark-to-market P&L).

### Tests
- `tests/test_market_health.py` (8 tests) — health states, snapshot, gate safe/unsafe/REST-reconfirm, reconnect counter, forced REST.
- `tests/test_supertrend_parity.py` (3 tests) — parametrized (`atr=5` and `atr=21`) parity of 3ST `supertrend_regular` vs OpenAlgo `Supertrend`.

### SuperTrend comparison — conclusion
- 3ST uses **true Wilder RMA** ATR (TradingView-faithful); OpenAlgo uses `ewm(alpha=1/p, adjust=True)` — a transient approximation.
- Parity results: ATR drift converges to `0.0000` in the late window; **100% direction agreement** after warmup. Early-window drift is ~9× larger for `atr=21` (0.0551) than `atr=5` (0.0061) — confirms the longer warmup for bigger periods (`(1−1/p)^t` decay). Rule validated: discard ~`3×atr_period` warmup bars (3ST already requires ≥50).
- 3ST is a superset: Wilder ATR + canonical Pine band-lock + HA/regular/hybrid source + triple-ST + ADX. **No code changes needed** to 3ST's indicator.

### Environment note
- In-session shell/terminal was unresponsive again; backend changes were validated via the running `uvicorn --reload` (clean reloads, `Application startup complete`). User ran `pytest` manually — `test_supertrend_parity.py` passed (3). Still to run locally: `tests/test_market_health.py` and `npm run build`.
- `aio-trader` not installed in this env → feed runs REST-only (`market_health` status `rest_only`, gate safe via REST). Point `VITE_API_BASE_URL` at the API port (`:8001` here) so the browser WS connects to `ws://…:8001/ws/ltp`.

---

## Analytics & safety additions (2026-07-12)

Audited the uncommitted in-progress work — all end-to-end wired (backend → API → UI → types → tests). New features documented in `docs/INSTRUCTION_MANUAL.md` §19.

### RRG + FPI overlay (`/rrg`)

- `analysis/rrg.py` — weekly RS-Ratio / RS-Momentum (RRG-Lite parity) from Kite daily candles; per-day close cache.
- `analysis/fpi_sectors.py` — NSDL fortnightly sector FPI net-equity parser; overlays confluence (aligned / divergence / watch / contrarian) on RRG rows. Fallback: live → cache → stale cache → bundled `data/fpi_sectors_seed.json`.
- API: `/rrg/config`, `/rrg/snapshot`, `/rrg/cache/clear`, `/rrg/fpi`, `/rrg/fpi/latest`, `/rrg/fpi/refresh`.
- UI: `routes/rrg.tsx` (recharts RRG chart + sortable/filterable table), sidebar entry, `lib/types.ts` Rrg*/Fpi* types.
- Config constants in `config.py`: `RRG_BENCHMARKS/DEFAULTS/SECTOR_INDICES/PRESETS`, `FPI_SECTOR_TO_RRG`, `FPI_RRG_ALIASES`, `FPI_DEFAULTS`.

### Panic kill-switch (`POST /live/panic`)

- `execution/panic.py` — close active watchlist trades, cancel open `3ST*` exchange orders, DISARM.
- `arming.require_armed_for_live()` bypasses the ARM gate while `panic_mode()` is active (so square-offs run even when DISARMED).

### LTP cache

- `execution/ltp_cache.py` — Aio-Trader KiteFeed WS (`ltp` mode) primary + Kite REST fallback (TTL). Started/stopped with API lifespan; `GET /live/ltp-cache`, `POST /live/ltp-cache/restart`.
- Optional dep `aio-trader` (git install); REST-only if absent. Tunables: `LTP_CACHE_WS/TTL_SEC/REFRESH_SEC/REST_FALLBACK`.

### Polish this session

- Added `data/fpi_sectors_seed.json` (illustrative fallback) + `.gitignore` exception (`data/*` + `!data/fpi_sectors_seed.json`).
- Documented `KITE_USE_STATICIP_PROXY` + LTP cache toggles in `.env.example`.
- Note: shell/terminal was not returning output during this session, so the pytest suite was **not** run — verify with `pytest` once the terminal is working.

---

## Live Desk manual workflow (2026-07-10 evening session)

### User goal

Manual live trading for options (e.g. **MCX Crude Oil CE**):

1. Stock Selection → instrument + 3ST params + **Send to Live Desk**
2. **Manual** BUY/SELL entry (not auto 3ST entry)
3. **Exit via algo:** TSL, ST1 zone (bear/bull bands), force exit time
4. Live Desk → real Kite orders when **LIVE + ARMED**

### Instrument example

- **CRUDEOIL26JUL6850CE** (MCX), NRML product, 3min timeframe, Regular ST (user) / HA for TV parity
- Entry bar close ~**199.40** (3min) — valid entry bar, not a data error

---

## Issues found & fixes (this session)

| Issue | Cause | Fix |
|--------|--------|-----|
| Order exited in ~11s after entry | `scanExits()` ran immediately; last bar already had zone exit | **Entry bar grace** — no zone exit until next bar after `entry_bar_time`; removed instant scan after BUY/SELL |
| TSL @ 210 (wrong) | TSL used **entry bar close 199** not live LTP | TSL ratchets from **trail_extreme + LTP** each poll |
| ST1 @ 197 labeled wrong for CE Short | Showed **Bull (lower) band**, not bear exit | **Bear exit** = upper band; **Bull entry** = lower band for short trades |
| ST1/TSL static | Only last closed bar | **Live ST bands** recalc with LTP as running close every ~20s |
| Force exit 22:45 never fired | `session_end` stuck at **15:30** (NSE default) | `force_exit_due()` handles MCX late session; UI added **session start/end**; MCX auto-preset 09:00–23:30 |
| Intraday vs NRML confusion | All options forced NRML | **product_type** MIS/NRML on Stock Selection; MCX options stay **NRML** (Kite); intraday flat via **3ST force exit** |
| ST1 far from TradingView | Wrong token / 15min vs 1min / Regular vs HA | Symbol-first chart token; show timeframe + entry bar; note HA for PRS Pine parity |

---

## Exit priority (Live Desk active trades)

On each scan (~20s for 1min/3min):

| Priority | Trigger | CE Short example |
|----------|---------|------------------|
| 1 | **Trailing SL** (ATR×1.5 from LTP/extreme) | Exit if LTP ≥ trail |
| 2 | **Target** | If enabled |
| 3 | **ST zone exit** (bear exit / dir flip) | LTP ≥ **Bear exit** (upper band) or ST1 → BULL |
| 4 | **Force exit** | At configured time (e.g. 22:45) |

- **TSL:** active immediately (no bar grace)
- **ST zone:** blocked until **one full bar after entry**
- **Force exit:** not blocked by grace; requires **ARMED** for live square-off

---

## ST exit labels (CE Short vs Long)

| Trade | Exit label | Band | Re-entry label | Band |
|-------|------------|------|----------------|------|
| **Short (CE write)** | **Bear exit** | ST1 **upper** (live) | **Bull entry** | ST1 **lower** (live) |
| **Long** | **Bull exit** | ST1 **lower** (live) | **Bear entry** | ST1 **upper** (live) |

Both bands are **dynamic** — recomputed each poll using last closed bar ATR + **current LTP** (Pine running-candle style).

UI shows: `Bear exit @ XXX live (+YY vs LTP)`, `Bull entry @ ZZZ live`, `TSL @ LTP …`

---

## Key files changed (this session)

| Area | Path |
|------|------|
| Exit monitor | `execution/watchlist_exit_runner.py` — grace, TSL/LTP, live ST bands, force exit |
| Entry / product | `execution/watchlist_activation.py` — entry_bar_time/close, trail_extreme, kite_product |
| Chart token | `execution/watchlist_runner.py` — symbol-first `chart_instrument_meta()` |
| Force exit logic | `backtest_engine.py` — `force_exit_due()` |
| ST upper/lower | `strategy_3st.py` — export `st1_upper`, `st1_lower` bands |
| Active trades API | `execution/desk_trades.py` — bear/bull exit fields |
| Live Desk UI | `Pixel Perfect UI/src/routes/live.tsx` — exit triggers column |
| Stock Selection | `Pixel Perfect UI/src/routes/index.tsx` — session times, product_type, MCX preset |
| Session defaults | `config.py` — `MCX_SESSION` |
| Workflow | `execution/live_workflow.py` — exit params wording |

---

## API endpoints (Live Desk)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/live/active-trades` | LTP, P&L, bear/bull exit, TSL, force exit |
| GET | `/live/positions` | Kite-style grouped positions |
| GET | `/live/workflow` | 7-step checklist |
| POST | `/watchlist/{id}/execute-live` | Manual live BUY/SELL |
| POST | `/watchlist/scan-exits?auto_close=true` | Exit poll (UI + scheduler) |
| POST | `/live/arm` | ARM for exchange orders |

---

## How to run API (restart)

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST"
Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { taskkill /F /PID $_.OwningProcess /T 2>$null }
Start-Sleep -Seconds 2
.\.venv\Scripts\uvicorn.exe api.main:app --host 127.0.0.1 --port 8001 --reload
```

**Verify:** http://127.0.0.1:8001/health

Or use: `.\scripts\start_api.ps1` / `.\scripts\stop_3st.ps1`

---

## Stock Selection checklist (MCX Crude)

1. **Timeframe:** 3min (match TradingView)
2. **SuperTrend method:** Heikin Ashi ST (match PRS Pine) — or Regular if intentional
3. **Session mode:** Intraday
4. **Force exit:** 22:45 (or 22:25 per user setting)
5. **Session end:** **23:30** (must be after force exit)
6. **Trailing SL:** ATR × 1.5
7. **Order product:** NRML (MCX options)
8. **Save** → **Send to Live Desk** → LIVE + **ARM** → BUY/SELL

---

## User ops reminders

- Whitelist **Kite IP** (IPv4/IPv6) for live orders
- Stay **ARMED** for live entry **and** auto-exit square-off
- `market_protection=-1` on MCX commodity options (see `broker/kite_broker.py`)
- Refresh Live Desk after API restart to see live bear/bull exit levels

---

## Current platform (2026-07-10)

| Layer | Stack |
|-------|--------|
| Backend | FastAPI (`api/main.py`), uvicorn port **8001** |
| UI | Pixel Perfect UI (React/Vite), `VITE_API_BASE_URL=http://127.0.0.1:8001` |
| Broker | Kite Connect (live) + shared **PaperBroker** singleton |
| Data | Kite historical/OI; Yahoo fallback for backtest |

---

## Rolling Straddle Algo (implemented)

Dedicated page: **`/rolling-straddle`** · Hub: **`/execution`**

### Strategy rules (agreed)

| Signal | Action |
|--------|--------|
| **Long 3ST** (CE chart or PE chart long) | Buy ATM **CE** |
| **Short 3ST** (PE chart or **CE chart short**) | Buy ATM **PE** |
| Start | After first closed **9:20** bar on chosen timeframe |
| ATM | Rolls with spot; entries use current ATM when flat |
| Legs | CE and PE **independent**; both may be open; **1 reentry per side** |
| Paper | Simulated fills; Live requires **Live mode + ARM** |

### Signal source (important)

3ST runs on **each option leg’s own candles** (ATM CE chart / ATM PE chart), **not** on a wrong index token and not only on NIFTY index.

**Cross-chart triggers (TradingView parity):**

- CE chart **short** → triggers **PE leg** entry (`ce_chart_short_entry`)
- PE chart **long** → triggers **CE leg** entry (`pe_chart_long_*`)
- Open leg keeps **entry strike** for signals after ATM roll (exit uses open contract chart, not rolled ATM)

### Zone exits

| Leg | Exit when |
|-----|-----------|
| CE long | Close/LTP **below ST1** (`long_zone_exit`) or PE short zone |
| PE short | Close/LTP **above ST1** (`short_zone_exit`) or CE long zone |

UI shows **Zone exit (ST1)** level, LTP, and trigger status on each leg card.

### Key backend files

| File | Role |
|------|------|
| `execution/rolling_straddle.py` | Tick loop, entries/exits, per-leg signals |
| `execution/rolling_straddle_store.py` | Config/state/log JSON in `data/` |
| `execution/order_executor.py` | Orders + risk checks |
| `execution/scheduler.py` | Background tick (~60s) |
| `options/legs.py` | `build_atm_leg()` |
| `backtest_rolling_atm.py` | Rolling ATM backtest |

### API routes

- `GET/POST /live/rolling-straddle/config`
- `GET /live/rolling-straddle/status|log`
- `POST /live/rolling-straddle/start|stop|tick|close-all|close-leg`

---

## Live Desk & paper trading fixes

### Empty Positions (fixed)

- **Cause:** Separate `PaperBroker()` instances in API vs algo; stale in-memory state.
- **Fix:** `get_paper_broker()` singleton + `data/paper_broker.json`; reload on read; `sync_paper_from_rolling_straddle()` on `/live/positions` and `/live/orders`.

### Wrong CE fill price in paper (fixed)

- **Cause:** Fallback `spot × 0.01` when LTP missing (~242 instead of ~110).
- **Fix:** `_seed_paper_ltp()` uses Kite live quote for option symbol.

---

## Kite / proxy fix (2026-07-10)

**Symptom:** `ProxyError: Failed to resolve 'dc-mum-601.staticip.in'` on historical candles.

**Cause:** Kite client used Firstock **staticip.in** proxy from `.env` (`STATICIP_*`).

**Fix:**

- Kite connects **direct by default** (`KITE_USE_STATICIP_PROXY=0` or unset).
- Historical/OI fetches retry without proxy on proxy errors.
- Static IP proxy remains for **Firstock** only.

Add to `.env` if needed:

```env
KITE_USE_STATICIP_PROXY=0
```

---

## OI Tracker activity log (2026-07-10)

| File | Role |
|------|------|
| `options/oi_tracker_store.py` | Log persistence (`data/oi_tracker_log.json`) |
| `GET /oi-tracker/log?limit=50` | Activity log API |

Events: `snapshot`, `alert` (breach threshold), `error`. UI card at bottom of **`/oi-tracker`**.

---

## Bugs fixed (session history)

| Issue | Root cause | Fix |
|-------|------------|-----|
| Rolling Straddle status showed “Kite not logged in” | `status_bundle` name collision in `api/main.py` | Alias `rs_status_bundle` |
| CE entry at wrong time / no PE entry | Index token resolved to **NIFTY ETF** (~273) not NIFTY 50 | Removed broken search; per-leg option candles |
| PE exit not firing at 124 | Signals computed on **rolled ATM** PE, not **open leg** strike | `_signal_strike_for_leg()` |
| CE Short not triggering PE | Only PE chart checked for PE entry | Cross-chart: CE short → PE leg |
| npm / PowerShell | `npm.ps1` blocked | Use `npm.cmd run dev` |
| `max_qty` blocked NIFTY lot | Default 25 | Raised to 500 in `risk/limits.py` |

---

## How to run (daily)

### Terminal 1 — API

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST"
.\.venv\Scripts\uvicorn.exe api.main:app --host 127.0.0.1 --port 8001 --reload
```

### Terminal 2 — UI

```powershell
cd "C:\Users\Rustoppers\OneDrive\Desktop\3ST\Pixel Perfect UI"
npm.cmd run dev
```

### Kite login

- `.env`: `KITE_REDIRECT_URL=http://127.0.0.1:8001/auth/callback`
- Login via UI or http://127.0.0.1:8001/auth/login (re-login ~daily after token expiry)

### Rolling Straddle workflow

1. Kite logged in · Paper mode for testing  
2. **`/rolling-straddle`** → set expiry, load 3ST from Stock Selection, Save  
3. **Start** algo (scheduler ticks automatically)  
4. **`/live`** → Positions/Orders (paper)  
5. Live + **ARM** only when ready  

---

## UI routes (sidebar)

| Route | Purpose |
|-------|---------|
| `/` | Stock Selection |
| `/dashboard` | Watchlist queue |
| `/backtest` | Historical backtest |
| `/oi-tracker` | OI % change + activity log |
| `/execution` | Algo hub (global ARM, strategy links) |
| `/rolling-straddle` | Rolling Straddle control + activity log |
| `/live` | Live Desk (ARM, positions, orders) |
| `/settings` | Risk limits, mode |

---

## Data files (`data/`)

| File | Contents |
|------|----------|
| `kite_session.json` | Kite access token |
| `kite_instruments.json` | Instrument cache |
| `rolling_straddle_config.json` | Algo config |
| `rolling_straddle_state.json` | CE/PE legs, ATM, runner |
| `rolling_straddle_log.json` | Algo activity log |
| `arm_state.json` | Persisted ARM/mode (survives API restart) |
| `risk_limits.json` | Persisted risk limits (max positions, orders/min, etc.) |
| `paper_broker.json` | Paper positions/orders |
| `watchlist.json` | Live Desk queue / active / closed trades |
| `selection.json` | Saved Stock Selection (ST params, session, product_type) |

---

## Known gaps / TV differences

1. **PRS extras** — UI Pine may use EMA200, zone conflict (“NO TRADE”); Python uses pure **3ST + ADX** on option candles.
2. **Exits on bar close** — Primary exit uses last **closed** 5m bar; LTP cross of ST1 added as backup.
3. **ATM roll** — Logs roll; does not auto-switch open leg symbol (leg stays on entry strike until exit).
4. **Watchlist Live Desk** — Manual entry + algo exit implemented (this session); scan/queue on Live Desk + scheduler.

---

## Earlier history (2026-07-09 and before)

- Original goal: 3ST backtester (Streamlit + Yahoo).
- Firstock path blocked → Kite Connect + FastAPI + Pixel Perfect UI.
- OI Tracker page, watchlist, backtest, spread preview added.
- Rolling Straddle planned and implemented (see plan: `.cursor/plans/rolling_straddle_algo_be2a65a7.plan.md` — do not edit plan file per user rule).

---

## Related docs

| Document | Path |
|----------|------|
| Interim instruction manual | `docs/INTERIM_INSTRUCTION_MANUAL.md` |
| Kite setup | `docs/KITE_SETUP.md` |
| Morning checklist | `docs/MORNING_START_CHECKLIST.md` |
| Gap review | `docs/review/3ST_Project_Review_Gaps.md` |
