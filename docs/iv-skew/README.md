# IV Skew

**UI:** `/iv-skew` · **API prefix:** `/skew` · 25Δ risk reversal and butterfly, per expiry.

**Code:** `options/skew_metrics.py` (pure math) · `analysis/iv_skew/builder.py` (live chain) ·
`Pixel Perfect UI/src/routes/iv-skew.tsx`. Session notes:
[`docs/CONVERSATION_SUMMARY.md`](../CONVERSATION_SUMMARY.md) (2026-08-12).

## What it measures

```text
25Δ RR  = IV(25Δ OTM call) − IV(25Δ OTM put)     positive -> upside tail is the expensive one
25Δ Fly = (IV(25Δ call) + IV(25Δ put))/2 − ATM IV  positive -> both tails bid over the body
```

Fixed **delta**, not fixed strike distance: a ±5% skew is not comparable across days as vol
and DTE move. Note the sign convention is the opposite of `iv_smile._iv_skew`, which reports
`put − call` at ±5%. The two are not interchangeable.

## Why it is priced off the forward, not spot

`iv_smile.py` and `vol_surface.py` solve IV from spot at a flat `r = 6.5%`. The market's own
forward carries more than that — measured 2026-08-12 on NIFTY, **+54 points at 6 DTE** where
6.5% implies ~26 — and the residual lands in the IV solve as a put-call parity violation. The
same strike solved to **11.7% off the call and 9.6% off the put**.

That is not skew. One strike has one implied vol. It biases calls up and puts down by roughly a
vol point each, which was enough to **flip the sign of the risk reversal**:

| NIFTY 2026-08-12 09:31 | 6 DTE | 13 DTE | 20 DTE |
| --- | --- | --- | --- |
| RR, spot-based Black-Scholes | +0.54 | +0.04 | +0.19 |
| RR, forward-based Black-76 | **−0.76** | **−0.89** | **−0.81** |

So the forward is recovered from the options themselves by put-call parity
(`F = K + (C − P)·e^{rT}`, median of the 3 nearest-ATM strikes so one stale leg cannot move it)
and every IV is Black-76 on that forward. The ATM **parity gap** is displayed as the desk's own
correctness check: on a correct forward it is ~0 (measured 0.01–0.05 vol points on indices,
against ~2.1 in spot space).

**MCX falls out for free.** Options there are options on futures, and parity recovers *that
contract month's* future without mapping expiries onto futures contracts. Measured the same day:
the September crude expiry resolved a forward **106 points below** the front-month future.
Mapping MCX options onto the front-month future — the obvious implementation — would have been
wrong by that much on every September number.

## Strike window is sized, not fixed

A fixed strike count fails in both directions on the same desk: ±20 strikes still had not reached
25Δ on 48-DTE BANKNIFTY (deepest call Δ 0.29), while the same 20 on 1-DTE SENSEX quotes 30
worthless legs to reach a delta the first three strikes already passed.

So the builder runs **two batched quote passes** — a small sizing probe near the money to measure
the forward and ATM vol, then a window sized from that measured vol to reach `wing_delta` (0.10,
deliberately deeper than the 0.25 target so 25Δ is interpolated rather than extrapolated). Both
passes batch across every expiry, so a snapshot costs exactly two Kite calls regardless of how
many expiries are requested.

## Reading the quality flags

Two independent fields, and they answer different questions:

- **`quality`** — how the 25Δ number was obtained: `interpolated` (the window observed both
  sides of it), `extrapolated` (it did not — do not trade on this), `unavailable`.
- **`confidence`** — whether the chain underneath was good enough to believe it: `clean`,
  `degraded` (one or more warnings), `unavailable`.

`confidence` exists because of a real failure: on 2026-08-12, 76-DTE BANKNIFTY dropped 45 of 81
legs to wide spreads, never solved an ATM put, and still printed **RR +6.90 labelled
"interpolated"**. The warnings that now catch that shape:

| Warning | Means |
| --- | --- |
| ATM call or put did not solve | no parity check available on this expiry |
| ATM call and put solve N vol points apart | forward or a near-ATM quote is off |
| near-ATM strikes disagree on the forward by N bps | stale or crossed quotes |
| 25Δ was not reached by the strike window | the wing IV is extrapolated |
| wing is sparse near 25Δ | interpolated across too wide a delta bracket |
| N of M strikes yielded no usable vol point | chain too thin to read |

Legs whose bid/ask straddle is wider than `max_relative_spread` of their own mid are dropped
rather than priced — on MCX wings that is a 5-paisa bid against a 40-paisa offer, and the IV it
solves to is an artifact of the spread.

## The daily monitor

`analysis/iv_skew/runner.py` samples every **5 minutes** on its own daemon thread — not
`execution/scheduler.py` and not `options/analytics_scheduler.py`, because an analysis sample must
never be able to delay an order-placing tick. Five minutes, not one: skew is a slow variable, and
the cadence keeps the builder's chain cache warm so each cycle costs the warm ~2.6s per underlying
rather than the cold ~16s.

Session windows are **per underlying** — cash hours for the indices, MCX hours for crude and
natural gas, which trade until 23:30. A closed index does not stop crude from being sampled, and
one underlying raising does not stop the rest of the cycle.

Two storage tiers, because they age differently:

| Tier | Path | Contents | Retention |
| --- | --- | --- | --- |
| Intraday | `data/iv_skew/<UNDERLYING>/<date>.jsonl` | one line per sample, metrics only | 90 days |
| Daily | `data/iv_skew/daily.jsonl` | one row per underlying/expiry/session | indefinite |

Samples deliberately drop the per-strike `points` array the live snapshot carries — the monitor
needs the metrics over time, and keeping the full curve would grow the archive by two orders of
magnitude for a chart nobody plots from history.

**The roll-up is lazy and idempotent, not scheduled.** A "write the EOD row at 15:25" trigger
silently loses a day to a restart, a holiday, or clock skew. Instead, any archived session older
than today with no daily row is rolled up on the next read or sampler tick: restart across the
close and the row still appears; miss a week and it backfills. Each row is the day's **last clean
sample**, falling back to the last resolved one — carrying its `degraded` confidence forward, so a
thin day is recorded honestly rather than dropped or laundered. Intraday pruning refuses to delete
a session that has not been rolled up, since the daily row is then the only durable copy.

The daily series is keyed by **expiry rank** (0 = nearest), not by contract: expiries roll, so a
series keyed by contract is a handful of disconnected stubs. The cost is a sawtooth in DTE, which
is why `dte` rides along on every point. Degraded sessions are excluded by default and **named in
`excluded_degraded`** rather than silently dropped — `clean_only=false` includes them.

## Performance

A cold snapshot profiled at **16.4s**, of which **10.6s was `get_chain`** — 9.1s of that a
per-row `pd.to_datetime` in `options/chain.py:96` re-guessing the date format across 5,382 rows,
on every call. Strike→tradingsymbol resolution only changes when the instrument dump refreshes,
so the builder caches it for 10 minutes (`clear_chain_cache()` to drop it). Warm snapshots run
**~2.6s**, which is what the 60s auto-refresh actually pays.

The `get_chain` cost itself is untouched: that module is on the order-placement leg-resolution
path, so it is not this desk's to change. **Every options desk pays that 9s on every chain read** —
worth fixing centrally, separately, with the execution paths in view.

## Config

`IV_SKEW_DEFAULTS` in `config.py` — underlyings (3 indices + 3 MCX), `target_delta`,
`wing_delta`, window floor/cap, and the four quality thresholds.

## Endpoints

| Route | Notes |
| --- | --- |
| `GET /skew/config` | underlyings, target delta, refresh seconds |
| `GET /skew/snapshot?underlying=&expiry=&max_expiries=&target_delta=` | live per-expiry skew |
| `GET /skew/status` | sampler health + last cycle's per-underlying result |
| `GET /skew/coverage?underlying=` | sessions archived, and which are rolled up |
| `GET /skew/series?underlying=&session_date=` | intraday samples for one session |
| `GET /skew/daily?underlying=&rank=&clean_only=&limit=` | the daily RR series |

`/skew/series` and `/skew/daily` read the archive only — no Kite — so the history survives the
daily ~6 AM token expiry.

Prefix is `/skew`, not `/iv-skew`, so the SPA page at `/iv-skew` does not collide with an API
prefix — a hard browser load of a colliding path returns a JSON 404. `/iv-smile` *is* subject to
that collision; `/velocity` avoids it the same way this does.

## Tests

`tests/test_skew_metrics.py` (25) prices a known vol curve with Black-76 and asserts the module
recovers it. `tests/test_iv_skew_builder.py` (28) drives the builder against a synthetic chain
with faked quotes. `tests/test_iv_skew_store.py` (29) covers the archive, the roll-up
(last-clean-wins, degraded fallback, no mid-session rows, backfill, idempotence), retention safety
and the runner's session gating. All fully offline; dates derive from `date.today()` so they
cannot rot.
