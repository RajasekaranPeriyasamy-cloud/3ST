# 3ST Project — Conversation Summary

**Last updated:** 2026-09-02  
**Project path:** `C:\Dev\3ST`  
**Session focus:** Live Market News desk — RSS + NSE filings, sentiment, ticker resolution

This file captures recent development context from Cursor agent sessions. Full chat logs live in Cursor agent-transcripts (not in this repo).

> **When you ask to “review points”** — read **[Execution architecture — phase reminders](#execution-architecture--phase-reminders)** for Phases 3–4 checklist, open decisions, and acceptance criteria.

---

## Session 2026-09-02 (last) — Live Market News desk

New desk: `analysis/news_desk/` + `/newsfeed/*` + the SPA page `/news`. Eleven
public sources (ten publisher RSS feeds and NSE corporate announcements), each
headline scored for sentiment, tagged with a category, and matched to NSE
tradingsymbols with live LTP attached at read time.

**Why not the repo that prompted it.**
`RelativelyBurberry/Indian-Stock-News-Sentiment-Analysis` is a Kaggle notebook,
not a dashboard. Its method (label -> compound score -> per-stock aggregation) is
worth copying; its ingestion is a reverse-engineered private Groww endpoint with
no documented URLs, and its scorer is FinBERT — torch + transformers into a repo
that places real orders, on a Python 3.14 venv where cp314 wheels are not
assured. Public RSS keeps working; an undocumented endpoint breaks on the
vendor's schedule.

**The one that would have bitten silently.** All eleven sources went healthy
standalone and `ProxyError` the moment the desk ran under uvicorn.
`settings.apply_kite_proxy_env()` pins `HTTP(S)_PROXY` process-wide so no Kite
call escapes the whitelisted static IP — and it deliberately **deletes
`NO_PROXY`**, so nothing opts out via env. Every `requests` call in the process
inherits it, ours included, and publisher traffic was being pushed through
metered order egress. `analysis/news_desk/net.py` exists solely to hold
`trust_env = False`; `test_news_sessions_bypass_the_kite_static_ip_proxy` pins
it, because no test running outside the API process can catch this.

**Ticker resolution is the soft part, and the guards are load-bearing.** A naive
name match produced `GLOBAL`, `RETAIL`, `MOMENTUM`, `CONSUMER` and `DEFENCE` on
ordinary market copy — all real NSE names — and 17.5k index keys, half of them
ETF paper. Three rules took it to 8.7k keys and clean output: drop fund/ETF rows,
refuse single generic English words, and require keys of five characters or fewer
to appear **in caps** (a headline writes `BEML`, prose writes "beml"). Known
cost: `BSE` is blocklisted, so a genuine BSE Ltd story gets no chip. Aliases live
in `data/news_aliases.json` and are expected to grow — the instrument master says
`INTERGLOBE AVIATION`, every headline says "IndiGo", and `TATAMOTORS` no longer
exists post-demerger (`TMPV` / `TMCV`).

**Scoring is free by default.** `NEWS_SENTIMENT_PROVIDER=lexicon` is a
finance-tuned word list — offline, deterministic, no key — so the desk works on a
fresh clone and the suite scores real fixture headlines with no network.
`anthropic` is opt-in, batched, daily-capped, and always falls back to the
lexicon: an unscored item is retried every poll forever, which on a paid backend
is the expensive failure mode. Sentiment is stored on the item and keyed by a
stable id, so a headline is scored once, ever.

**Equity search.** A search box on the page, autocompleting NSE equities off the
existing `/instruments/search`, plus a free-text `q=` fallback. Two decisions
worth keeping:

*Search is looser than resolution, on purpose.* `?symbol=` matches a resolved
chip **or** a word-boundary text mention assembled from `tickers.search_terms()`
(symbol, cleaned registered name, every alias pointing at it). Resolution is
conservative and only ever ran against the text an item carried at ingest, so a
chips-only search silently drops headlines that plainly name the stock — measured
on live data, 1 of 11 HEROMOTOCO hits was text-only. A stray row in a search
someone asked for is a much cheaper mistake than a wrong ticker chip on the feed.

*Search suppresses clustering.* Clustering keys on primary symbol, so every hit
for one stock would collapse into a single "+10 more" row and the search would
look broken. `clustered: false` comes back in the payload and the UI says
"not grouped".

**Follow-up the same day — the daily cap now survives a restart.** `llm._SPEND`
was an in-memory dict, so the "$2 daily cap" was really a per-*process* cap: the
desk restarts several times on a working day and each restart handed it a fresh
budget. It is now persisted to `data/news_llm_spend.json`, written on every
recorded spend (the window between spending money and recording it is exactly
where a crash loses the tally), pruned to 30 days, and registered in the conftest
write guard. `test_spend_survives_a_restart` and
`test_cap_blocks_scoring_after_a_restart` pin it. A corrupt or unwritable ledger
degrades to the old per-process behaviour and logs, rather than taking scoring
down. Note the cap is checked *before* the `anthropic` import, so an over-cap
call cannot reach the network even where the SDK is absent.

Not wired: BSE announcements. Its `AnnGetData` endpoint returned zero rows for
every parameter combination tried, and NSE already carries the `symbol` field
that was the point of using an exchange feed.

The desk imports nothing from `broker/`, `execution/` or `risk/` and places no
orders. Suite green at 1270.

---

## Session 2026-08-31 — Gamma Density de-duplication

The page had grown by accretion: Profile was the original board, Concentration
was where newer instruments landed, and both had come to answer "where is the
pin?" and "how concentrated?". A census of *readouts* (chart overlays excluded —
a pin line on a chart is context, not repetition) found the pin stated **6**
times, gamma regime **5**, flip and band label **4** each.

**The organizing rule now is one tab per question:** Profile owns *evidence*
(price context, both charts, per-strike detail, reference levels, OI, momentum);
Concentration owns *judgement* (regime, HHI, pin, magnet, confluence). Each fact
appears once, in the tab that owns it.

What went:

- **Four Profile StatCards** — HHI Concentration, Conviction, Pin Candidate,
  Dominant Strike. All superseded, and `docs/gamma-density/MANUAL.md` already
  told the reader to prefer the Concentration versions. A page that documents
  which of its own tiles to ignore is telling you to delete them.
- **The `Structure & shape` card.** Its spot/pin/cliff triple is strictly worse
  than the regime panel's σ ladder, which carries all nine levels with distances;
  its Top1/Top5/Eff and band already sat in the HHI hero. Gini + the Ávila
  quadrant were the only unique content and moved into the hero's tile row,
  displacing the two γ-peak tiles (those are on the σ ladder *with* their σ
  distance, which is the part that decides whether a peak is reachable).
- **`market_read.levels_line`** is no longer rendered — it restated the Key
  Levels badges and Reference-levels closes directly above it. Kept on the
  payload; `test_gamma_reference_levels.py` pins it.

**The conviction collision.** `build_gamma_market_read` appended the legacy
`compute_gamma_conviction` score to `regime_line`, so the page showed
"conviction 48" a few inches from the expiry magnet's "conviction 73" — two
different formulas under one word. The clause is gone; the magnet's version
stays because it ships its inputs, weights and a `calibrated: False` flag.

**Not done, deliberately:** merging the 30-session and intraday HHI charts. They
already sit side by side at equal weight under "How today compares"; a toggle
would hide one behind a click. That was a bad idea in the plan, dropped once the
layout was actually read.

### Three defects the reorganisation surfaced

Reading the rendered page rather than the source found bugs a static audit had
missed:

1. **`+360.7 steps`** in Volume confluence. Not a maths error — `36` and `0.7`
   were rendered adjacent with only a margin between them and no separator, so
   they read as one absurd number. `+2364.7` was `236` and `4.7`. Now `·`-joined,
   matching the regime panel's `−36 · 0.72 step`.
2. **The pin gap hung off the POC row**, whose label made it read as the POC's
   own offset — while the row actually labelled `Pin vs POC` showed only an
   in/outside-value badge and no distance. Moved to the row that names it.
3. **The same pair carried opposite signs on one screen.** `options/regime.py`
   computes `gap_pts = poc − pin` (pinned by `test_regime.py`), but the UI
   labelled that row `Pin vs POC` — reading "the pin is 36 *below* the POC" when
   it sat above. Relabelled `POC vs pin` rather than flipping a tested payload
   field.

Suite green at 1175. `npm run typecheck` clean; eslint delta zero on every file
touched (the frontend's pre-existing prettier debt was left alone per CLAUDE.md).

## Session 2026-08-31 — 23 tsc errors to zero, and what two of them were hiding

`vite build` does not typecheck, so `npx tsc --noEmit` was the only thing
looking at these and nothing was reading its output. Six commits, every one a
frontend type fix, and the point of the entry is that **two of the twenty-three
were live defects** rather than notation.

### The two that mattered

**The execution taskbar named the wrong exit rung.** `exit_triggers` was typed
`Record<string, unknown>`, and the taskbar read
`exit_triggers?.next_exit?.label`. Writing the real type made `.price`
typecheck and `.label` fail — and that asymmetry *was* the finding.
`_leg_exit_params` builds each ladder rung as
`{order, category, price, triggered, rule, enabled, ...}`; a row names itself
with `category`, and the only `*_label` keys sit one level up. So `.label`
always missed, the label fell through to `zone_exit_label`, and the price on
the next line came from `next_exit`. Measured by calling the producer on a short
leg (entry 214.5, ATR trail 208, ST1 205, LTP 190):

    next_exit       = {"category": "Entry", "price": 214.5, ...}   # no "label"
    zone_exit_label = "ST1"

    before: "ST1 @ 214.50"    <- ST1's name against Entry's price. ST1 is at 205.
    after : "Entry @ 214.50"

Fixed in the UI, not by adding `label` to the producer: `rolling_straddle.py`
places orders and this is a display concern. The taskbar's *actions* were never
driven by this string, so no runner behaviour moved.

**`RollingStraddleConfig` was missing `timeframe`** — eight errors — while
`/live/rolling-straddle/config` returns `"5min"` and the page renders it
unconditionally. Fixing it exposed a ninth: `entry_mode` sat on
`StrategySettings`, the base both `Selection` and `RollingStraddleConfig`
extend, so the straddle config demanded a key its own store never writes. Every
backend reference is in desk_trades / live_workflow / watchlist_activation /
watchlist_runner / reconcile and none in `rolling_straddle*`, so it moved to
`Selection`.

**The correction inside that fix is the reusable part.** I moved `entry_mode`
across as *required* first, which would have reproduced the identical defect one
interface along: `WatchlistItem extends Selection`, and the live watchlist holds
31 items with the key absent from every one. Every backend read is
`item.get("entry_mode") or <default>`. Typing a field required because the
happy-path payload happens to carry it is the bug this whole batch is made of.

### The rest, and the one pattern under them

Four more, each a different face of *the type was never written down*:

- `/pricing/calculate` returned `Record<string, number | string | null>`, so
  `string` was in the union at all eight `fmt()` / `edgeTone()` call sites.
- `ActiveTradeRow` declared `signal` twice, identically. Both lines arrived in
  the same 2026-07-25 commit.
- survivor.tsx kept a private three-entry lot-size table while
  `RollingUnderlying` has six; rolling-straddle.tsx already had a complete copy.
  Now one `src/lib/instruments.ts`, exhaustive over the union on purpose.
- rrg.tsx typed `QUADRANT_LABEL` as `Record<string, string>` while
  `QUADRANT_ORDER` beside it was already `Record<RrgQuadrant, number>` — two of
  three maps strict, and the count-badge path went through the loose one.

**A `Record<string, unknown>` standing in for a real payload is not laxness, it
is a place where a typo renders "—" forever.** `bs.delta` and `bs.dleta` type
identically under it. Both live defects above lived in exactly such a bag; the
other four were the compiler complaining about notation. Casting at the eight
pricing call sites would have silenced those errors and kept the cause.

### Verification, and where it stopped

Degraded paths are where the optionality is actually decided, so the payload
types were probed against the running API rather than read off the source:

- **No solvable IV** — greeks fall back to `{delta, gamma, theta, vega}` all
  null and the seven second-order greeks are *absent*, not null. So the core
  four are required and the rest optional, the opposite grouping to what the
  success payload alone suggests.
- **Heston with `tte_years=0`** returns `{price: null, error: "invalid inputs"}`
  and nothing else, so every other field on that block is optional too.

Type-only commits were argued as no-ops by construction, and that claim was
checked rather than asserted: `types.ts` declares no `const`/`function`/`class`/
`enum`, so it emits no JavaScript at all. The two commits that touch
JS-emitting files were verified as such — rebuilt, restarted, and read off the
prebuilt :8001 bundle (survivor "Qty OK (lot 65)", rolling-straddle "NIFTY
lot = 65", rrg's quadrant filter listing all four labels from the map).

**What was not verified: the taskbar's exit column on screen.** The queue held no
active leg and the Kite token was expired, which is why that evidence comes from
the producer instead. Worth one glance on a live leg.

### The lot-size coincidence, stated because it nearly read as a bug

survivor's short table looked like a live defect and was not — but only by luck.
It read `LOT_SIZES[u] ?? 1`, and every MCX lot size in `INDEX_OPTIONS` is 1, so
the missing rows and the fallback returned the same number. Worth recording as a
near miss rather than a clean bill: the table would have been wrong the moment
an MCX lot size stopped being 1.

### Open

- `execution_queue.py:114` reads `params.get("next_exit")` inside
  `if not params:`, where `params` is `{}` by construction — that stub's
  `next_exit` is always None. Harmless, and it only fires before a ladder
  exists, but it reads as though it carried a value across.
- The backend defaults `entry_mode` to `"manual"` in three places and
  `"signal"` in `watchlist_runner.py:99`. That flag decides whether an item arms
  itself on a signal or waits to be fired by hand.
- survivor's dropdown offers three underlyings while rolling-straddle offers all
  six and `survivor_store.validate` accepts all six, so MCX is reachable on that
  desk only by hand-editing `data/survivor_config.json`. Whether the survivor
  runner *should* trade MCX is a desk decision, not a typing one.
- MCX membership is still open-coded in ~6 components (oi-movers, oi-tracker,
  iv-skew, gamma-density, useOptionExpiries, AnalyticsDeskContext).
  `src/lib/instruments.ts` is the obvious home if that is ever tidied.
- **`tsc --noEmit` is at zero and nothing enforces it.** CI runs pytest and
  ruff; the frontend is unchecked, which is how 23 errors accumulated unseen.
  A typecheck job would hold the line now that the line is worth holding.

---

## Session 2026-08-31 (later) — health that means something, 1m/3m, virtualised wings

Three pieces, and each one's most useful output was a bug it exposed rather than
the feature itself.

### /health now verifies the token, not the file

`kite_authenticated` read a session *file*. On 2026-08-30 it reported true while
every Kite read failed with ``Incorrect `api_key` or `access_token` `` — the one
flag anyone checks on a quiet morning, saying the opposite of the truth. This
closes the open item recorded in the previous entry.

`kite_auth.token_probe()` does the live check with `profile()`, the cheapest
authenticated read Kite offers, which needs no instrument tokens and so cannot
fail for a reason unrelated to auth. Four states rather than a boolean:

| state | meaning |
| --- | --- |
| `no_session` | nothing stored |
| `valid` | a read succeeded just now |
| `invalid` | Kite rejected the token — log in again |
| `unreachable` | the call failed for a reason that is NOT authentication |

`unreachable` is the one that earns its keep. Collapsing it into `invalid` would
tell an operator to re-login when the real problem is DNS, and send them to fix
the wrong thing on a trading morning. So `kite_authenticated` goes false **only**
on `invalid`; an unreachable probe leaves it at the stored value and reports
itself in the new `kite_token` block.

Cached 60s — /health is polled continuously and the answer changes about once a
day (tokens expire ~06:00 IST), so a call per poll would burn rate limit to
re-learn a known fact. Verified live: three consecutive /health calls share one
`checked_at`.

**`session_status()` is deliberately untouched.** It answers the cheaper question
— is there a session file — and is called from `ltp_cache`, `execution_queue` and
`live_workflow`. Giving it a network call would put Kite latency on the order
path to answer a health question.

**The mistake worth recording:** the probe first called
`kite_auth.read_only_kite_client()` directly, which the offline guard does not
cover, so I widened the guard to include it. That broke the six tests in
`test_kite_client_cache.py` that exist to verify that accessor's memoisation. The
failure was the right signal: *constructing* a client is harmless, *issuing a
request* is not, and the guard is correctly placed at the latter. Routing the
probe through `kite_client.kite_read_client` instead means it is blocked in tests
for free, with no guard change at all.

### 1m and 3m timeframes

Both are native Kite candle intervals, so the widening path and futures strip
need no client-side rollup. Thresholds measured rather than guessed: refitting
over 48 session-files puts p95 at **3.73%** (1m) and **6.54%** (3m), and the
fixed constants sit slightly tighter at 3.5 and 6.5, matching how the existing
ones are set. Measured breach rates land at 5.4% and 5.0% against a 5% target —
better calibrated than the coarser timeframes. A test now asserts a timeframe
appears in **both** `PCT_THRESHOLDS` and `BASE_P95`, because one the fitted table
misses silently degrades to `fixed`: honest, but not what "we support 1m" should
mean.

**The convention 1m makes visible.** The 09:15:00 open snapshot shares a bucket
with 09:16:00, because 09:16 closes the 09:15-09:16 window — so twelve
minute-samples give **eleven** 1m buckets, not twelve. I read it as an off-by-one
first. It is the same half-open rule every timeframe uses, only noticeable when a
bucket is one minute wide, and it is now pinned by a test that says so.

### Virtualised wing columns

Only the columns in view enter the DOM; the table keeps its declared width and
the rest are leading/trailing spacer cells, so scrollbar geometry, sync-scroll
and jump-to-latest are unchanged. Measured at 1m on a 213-column session:

| | before | after |
| --- | --- | --- |
| columns rendered | 172 | 14 |
| wing cells | 7,224 | 546 |
| DOM nodes | 16,984 | 1,716 |
| sort re-render | ~1000ms | 241ms |

Six columns of overscan, windows computed per wing from its own scrollLeft and
clientWidth, and the window state written only when the slice actually changes —
scroll fires continuously, and re-rendering per event is the cost this removes.

**The bug it exposed matters more than the speed.** Jump-to-latest depended on
`buckets.length`, and a session grows a bucket every interval — at 1m, every
minute. So each 60-second poll dragged the scroll back to the right edge and
threw away wherever the operator was looking. Anchoring belongs to "which grid am
I looking at", so it keys on underlying/session/timeframe/expiry now; an appended
bucket is not a new view. That would have made 1m unusable however fast it
rendered.

### Verification honesty

The DOM counts, timings and the window tracking scroll (scrollLeft 0 -> 09:16,
mid -> 11:00, far -> 12:46, always 14 columns) are all measured.
**Sync-scroll between the two wings is not.** The browser pane used to drive
these checks reports `clientWidth: 0`, so nothing layout-dependent can be trusted
in it — which is also why three earlier attempts to test scrolling produced a
frozen window and sent me chasing a phantom bug. The sync logic is untouched by
the virtualisation, but untouched is not tested. Worth a look on a real screen
with 1m open and "Sync scroll" ticked.

### Open

- Sync-scroll unverified, as above.
- Delta on the **futures strip** still needs the future's top of book archived
  the way the option legs' now is — a collector change.
- `StraddleWatchSnapshot` / `StraddleWatchRange` are imported by
  `StraddleWatchChart.tsx` and `straddle-watch.tsx` and exported by nothing.
  Pre-existing, and `vite build` will not catch it because it does not typecheck.

---

## Session 2026-08-28 to 08-31 — gamma levels and order flow on the Build-Up ladder

Two features on `/chain-buildup`, both built in phases so the forward-only parts
landed before the parts that consume them. Ends with the one measurement that
closes a question Phase C had left open by construction.

### Gamma levels on the strike ladder

Call wall, put wall, pin, gamma flip and futures POC, behind `/buildup/levels` and
`analysis/chain_buildup/levels.py`. Four of the five already existed in
`volume_profile.service.gamma_levels`; the work was in what could honestly be
shown *when*.

**Separate endpoint, not a grid field.** The grid is a pure archive read; levels
reach `build_gamma_snapshot` -> `oi_movers` -> Kite historical, the path that once
cost 80 seconds on a page load. Kept apart, a gamma outage greys one panel instead
of blanking the ladder. It reuses `gamma_levels` for its shared 45-second cache and
its `include_history=False`, so opening the page cannot append to the gamma trail
or move a pin the sampler already recorded.

**Strike levels land on a row; price levels do not.** Call wall, put wall and pin
*are* strikes. Flip and POC are continuous prices falling between strikes, and
snapping them to the nearest row would move them up to half a strike step and
assert a precision they do not have. They carry the pair they sit between and draw
as a rule.

**An archived session never borrows today's numbers.** Walls had no history at all
before this work; pin/flip come from `daily_pin` (2026-08-21 onward); POC from the
tilt trail. Where a level cannot be resolved for the session asked for, it is
omitted *with a reason the page names* — drawing today's call wall on a two-week-old
ladder invites reading a level into a session that never had it.

**A pin is often not a pin.** `pin_source` is frequently `wall_mid`, the midpoint of
the two walls. The badge says "(derived)" rather than asserting a gamma pin the data
does not support.

**Phase 2, forward-only.** `append_history_point` now records `call_wall` /
`put_wall`. Every other level on that tick had history; the walls alone were
live-only, so no desk could draw them on a past day and no study could ask whether
they held. BANKNIFTY added to `TILT_HISTORY_UNDERLYINGS` — the only cash index with
no futures POC, a gap invisible until something asked for it.

**Phase 3 — levels as of every bucket.** The ladder has a time axis, so levels are
drawn as paths across it. Held forward between samples and never interpolated
(averaging two flips gives a price the desk never published — the same reason a
bucket's OI is its last value, not its mean), and held forward only
`MAX_CARRY_MIN`: the trail samples every ~75s, so a wider gap is a gap in the
*recording*, not a level sitting still. Coverage is reported per level, because
"the wall did not move" and "the wall was never recorded" must not look alike.

Bug found by checking a level that returned nothing rather than assuming it was
absent from the data: the trail stores `pin_strike` and `flip_level` where the
snapshot says `pin` and `flip`. Mapping one and assuming the other produced an
empty flip track on a session whose trail carried all 300 of them. The mapping is
an explicit dict now — the shape that cannot be half-done.

### Order flow: volume, then delta

**Volume was free.** The archive already stored cumulative day volume per leg
(monotonic, 99.9% coverage, ~400 points/session), so a bucket's traded volume is a
difference, and it telescopes exactly like delta-OI: measured on one ATM CE the
per-bucket volumes sum to 300,214,135 against a final cumulative of 300,214,135.
Volume gets its own colour scale — it is an order of magnitude larger than delta-OI
on the same strike, and one shared ceiling would leave every OI cell white.

**Phase B found data being thrown away.** `fetch_quote_batch` had been returning
5-level depth on every leg every minute all along, and `_mid_or_last` read the top
of it for an IV mid then discarded the rest. Now archived, at zero extra API cost.

And the discovery that matters more than it looks: **the archived `ltp` has never
been the last traded price.** `_mid_or_last` returns the bid/ask MID wherever depth
is two-sided, which is most of the time. That is the better IV input and a
defensible choice, but a trade classifier must work against a price that actually
traded — a mid never does. `last_price` was added alongside rather than renaming
`ltp`, because 15 days of archive are written against its current meaning and every
historical read would have silently changed. It also means the four-quadrant
classification's "delta-price" has always been delta-mid.

**Phase C — quote rule with a tick-rule fallback.** Direction is decided per
*minute* and only then summed: a bucket whose book flips mid-way must split, not be
attributed to whichever side it closed on. Unclassified volume is a first-class
output — `buy + sell + unclassified` always equals gross traded volume — because
folding it into either side would make delta look better-founded than it is.

### The measurement that closes Phase D

`scripts/check_flow_classification.py` answers whether a once-a-minute book is
enough to classify direction. On 2026-08-31 NIFTY:

| bucket | traded | unclassified |
| --- | --- | --- |
| 09:00 | 1,203,231,965 | 0.3% |
| 10:00 | 930,160,205 | 0.1% |
| 11:00 | 536,963,635 | 0.2% |
| overall | 2,670,355,805 | **0.2%** |

Flat across moneyness: 0.3% / 0.1% / 0.1% for ATM+/-2, +/-3-5, +/-6+.

**Minute snapshots are catching the market. Delta is usable, and Phase D — a tick
collector — is not needed.** That was the open question and it is settled by
measurement rather than assumption. The remaining caveat is structural and
unchanged: a minute that traded both ways is attributed to whichever side it
closed on.

### The bug worth carrying forward

Monday's page reported **100% of volume unclassified** while the archive held a bid
on 22,198 of 22,200 legs. `store.to_rows` flattens snapshots through an explicit
field list, and Phase B added bid/ask/last_price to the collector without naming
them there. Every consumer reads through that function, so the quote rule genuinely
saw no book and correctly said so.

The uncomfortable part: the honest-unavailability banner — built precisely so a grid
of zeros could not be mistaken for a balanced market — reported the truth about what
it could see, and in doing so made a plumbing bug look like a data-availability
fact. **A safeguard that accurately reports its own blindness can still hide the
reason for it.**

The regression test compares the collector's leg fields against `to_rows`' output
rather than listing today's fields, so the next addition fails there instead of
silently returning nothing. Listing them would have rebuilt the same trap a release
later.

### Two smaller ones

- `/buildup/status` walks every archived session file — 2.4s in-process, **32s**
  when the API is also sampling — and the page awaited it alongside the grid, so a
  slow decorative call held the whole ladder blank. Widening `STRIKE_WIDTH` made
  those files 2.3x bigger and pushed it over the edge. Status now loads
  independently; it only feeds the session dropdown.
- `flow.underlying_flow`'s failure path returned a different dict shape from its
  success path, so a consumer reading `buckets` raised on a degraded feed. Found by
  running it against an expired Kite token. Both shapes are identical now, with a
  test pinning the parity.

### Open

- Delta on the **futures strip** still does not exist, and the reason is now
  specific rather than general: that strip is built from Kite OHLCV candles, which
  carry no bid/ask. Giving it a real delta means archiving the future's top of book
  the way the option legs' now is — a collector change, not a change in `flow.py`.
- `StraddleWatchSnapshot` and `StraddleWatchRange` are imported by
  `StraddleWatchChart.tsx` and `straddle-watch.tsx` and exported by nothing. Three
  type errors, pre-existing, and `vite build` will not catch them because it does
  not typecheck.
- `/health` reports `kite_authenticated: true` when a session *file* exists, not
  when the token works. On 2026-08-30 it said authenticated while every read failed
  with `Incorrect api_key or access_token`. Worth making that flag mean what it
  says, since it is the first thing anyone checks on a quiet morning.

---

## Session 2026-08-27 (later) — the unit suite was writing into live `data/`

`pytest tests/ -q` failed on
`tests/test_mcx_rolling_straddle.py::test_ensure_state_underlying_heals_legacy_nifty_spot_on_crude`
**only when the desk was running**. In isolation it passed. That asymmetry is the whole
story: the test was writing the live rolling-straddle store on every run, and the failure
appeared only once the live runner was writing the same file and the two raced.

### The write was two frames below the function under test

The test calls `rolling_straddle._ensure_state_underlying(cfg, state)` — nothing in the name
or the assertions mentions persistence. But it reaches
`clear_spot_state_for_underlying()` -> `save_state()` + `append_log()`, so both
`data/rolling_straddle_state.json` and `data/rolling_straddle_log.json` were rewritten.
Confirmed from the artifacts: the live log carried `underlying_reset` rows stamped inside
the test run, and `last_spot` / `current_atm` / `state_underlying` had been reset.

This is the same class as the theta-decay incident (2026-08-13, 1,800 synthetic snapshots
appended to the live delta-velocity archive), and it is why the sibling
`test_save_config_underlying_change_resets_spot_state` — which *does* patch `CONFIG_FILE`,
`STATE_FILE` and `LOG_FILE` — was safe while its neighbours were not. **A test cannot be
audited by reading its own body.** That is the argument for a guard rather than another
round of per-test patches.

### The audit found six files, not one

Instrumented `open` / `Path.write_*` / `os.replace` / `rename` / `remove` for the whole
suite and recorded every write resolving under the real `data/`:

| test file | live file written |
| --- | --- |
| `test_mcx_rolling_straddle.py` (5 tests) | `rolling_straddle_state.json`, `rolling_straddle_log.json` |
| `test_panic.py` (2) | `arm_state.json` — the live kill-switch |
| `test_order_router.py`, `test_order_executor.py`, `test_watchlist_activation.py` (8) | `latency_log.jsonl` |
| `test_session_poc.py`, `test_cas_indicative.py` (4) | `oi_movers_prev_day_oi.json`, `cas_history.jsonl` |

Three offenders were in the reported file beyond the two named — `test_resolve_tick_spot_*`
and `test_status_bundle_*` reach `append_log` the same way. `test_panic.py` rewriting the
real `arm_state.json` is the one to worry about: that file is the ARM/DISARM state the
kill-switch persists across API restarts.

### Fix: two layers in `tests/conftest.py`, plus a session backstop

Shared conftest rather than a per-module fixture, because a module fixture would have fixed
one of the six.

- **`isolate_real_data_writes`** (autouse) redirects a `_REDIRECTS` table of ~33 store
  constants into a per-test sandbox, patching each **store module's own** reference.
  `execution/latency_log.py` and `options/cas_history.py` resolve `data_dir()` at call time,
  so their `data_dir` name is redirected instead of a constant. Read-only caches are
  deliberately absent: `instruments.CACHE_FILE` (tests read the real instrument dump) and
  `kite_auth.SESSION_FILE`.
- **A call-level guard** armed by `pytest_collection_finish` raises `RealDataWriteBlocked` on
  anything still landing under live `data/`, with a message naming the fix. This is what
  makes the list unforgettable — a store added later fails loudly instead of quietly joining
  the offenders. Opt out with `@pytest.mark.writes_real_data` (nothing does). Collection and
  import stay unguarded: a couple of modules `mkdir` under `data/` at import time, and
  refusing that would fail collection rather than any test.
- **`real_store_files_untouched`** (session-scoped) is CLAUDE.md's "assert the real file's
  line count is unchanged" rule applied to 13 live files at session scope — the write that
  prompted it was two calls away from the test that caused it, so per-fixture was the wrong
  altitude. It fails **only** when the call-level guard also caught this process attempting
  the write; the desk may legitimately be running alongside pytest, and an otherwise
  unexplained change is a warning naming the other writer, not a false failure. Without that
  distinction the check would go red every time the analytics scheduler ticked.

`tests/test_store_isolation.py` (new, 49 tests) pins all of it the way
`tests/test_offline_guard.py` pins the Kite guard: the redirect *skips names it cannot find*,
so a renamed store constant would silently turn its entry into a no-op — each one is asserted
to still exist. Plus `test_ensure_state_underlying_does_not_touch_the_live_store` as the
direct regression, and `test_writing_a_watched_file_is_refused` proving the guard bites.

### Verified, both ways

`1043 passed` (994 -> 1043 from the new file), full suite, **desk running** (`52s`, three
times) and **desk stopped** (`30s`) — every watched live file byte-identical afterwards, 22
of them on the stopped run. The guard itself was verified by temporarily deleting the three
`rolling_straddle_store` redirect lines: all five tests then failed with
`RealDataWriteBlocked` rather than writing. `ruff check` clean on every changed file.

### `stop_3st.ps1` does not actually stop the desk

Worth knowing independently of this work. `scripts/stop_3st.ps1` kills whatever is
*listening* on 8001/8080 — but the API is launched through `scripts/start_api.bat`, which
wraps uvicorn in a `:restart` loop that waits 5 seconds and starts it again,
unconditionally. The `cmd.exe` running that batch is never killed, so the desk is back
about five seconds later.

This produced a convincing false positive: the first "desk stopped" run came back with
`gamma_density_history.json` and `oi_var_history.json` modified, which with the desk
supposedly down could only mean the suite wrote them. Bisecting blamed
`test_oi_var.py::test_moneyness` — a pure arithmetic test that touches no store. The real
writer was the resurrected analytics scheduler ticking during that test's 1.9s window.
**A bisect that lands on an implausible test is evidence the writer is out of process.**
Killing the `start_api.bat` supervisor first, then confirming the API stayed down for the
whole run, gave the clean result above.

### One real bug the false positive uncovered

Chasing it exposed a genuine hole in the guard as first written: it disarmed in
`pytest_runtest_teardown`, which fires *before* fixture finalizers — precisely when
`monkeypatch` restores the real store constants. A finalizer that wrote would have found the
live paths back in place and the guard already asleep. The guard now arms once at
`pytest_collection_finish` and stays armed, disarming only for a `writes_real_data` test and
re-arming at `pytest_runtest_logfinish`, after that test's teardown is fully done.

One artifact of the investigation: the audit instrumentation *recorded* writes rather than
blocking them, so those runs appended 18 rows to `data/rolling_straddle_log.json`. Restored
from a pre-work backup (8 rows, newest `15:14:43`). The two `underlying_reset` rows already
in that file from an earlier test run were left alone.

Docs: the Testing section of `CLAUDE.md` now documents both layers; its `settings.data_dir`
binding-trap bullet points at the guard while still asking for the explicit per-test patch,
which documents what each file starts out holding.

---

## Session 2026-08-27 (later) — delta-velocity raw archive is now pruned

`store.prune_raw` had existed since Phase 1 and had never been called, so the raw
archive grew without bound; widening `STRIKE_WIDTH` to 12 made it grow ~2.3x
faster (~27 MB/day). `runner._maybe_prune` now enforces the retention window.

Three properties chosen deliberately, because this is a scheduled deleter:

- **Once per calendar day, outside the `in_session` gate.** Pruning is
  housekeeping, not sampling; gating it on market hours would mean a desk left
  running over a weekend never reclaims anything. Per-tick would walk every
  session file of every underlying every 10 seconds.
- **`DELTA_VELOCITY_RETENTION_DAYS` is the off switch, and 0 means keep
  everything.** An operator who wants the whole corpus should not have to edit
  code to keep it. Anything unparseable also disables: for a deleter, the
  fail-safe direction is to keep data, not to guess. Read through
  `settings.env()` rather than `os.getenv`, per CLAUDE.md.
- **A 7-day floor.** A mistyped `DELTA_VELOCITY_RETENTION_DAYS=3` on a scheduled
  deleter would take most of the corpus before anyone read the log. Requests
  below the floor are clamped and warned about rather than honoured.

A prune failure is caught per underlying and logged — housekeeping must never
take the sampler down with it.

**Nothing is deleted yet.** The archive starts 2026-08-10, so the oldest session
is 17 days old against a 30-day window; the first real deletion falls on
2026-09-09. Verified after the restart that all 14 sessions per underlying are
still present, and that the test suite (which exercises the prune paths against
tmp_path) left the live archive byte-identical.

---

## Session 2026-08-27 (later) — delta-velocity collector widened to ATM +/- 12

`STRIKE_WIDTH` 5 -> 12 in `analysis/delta_velocity/collector.py`. Leg count is
`(2W+1) * 2 types * EXPIRIES * len(UNDERLYINGS)`, so 198 -> 450 worst case against
the 500-instrument quote ceiling; measured against the live instrument dump it
resolves to **404 legs** (BANKNIFTY lists fewer strikes at that spacing), leaving
96 spare. Still one batch call per minute — the API cost is unchanged, only the
response size.

**This is the one action from three sessions of analysis that had to happen
now rather than later.** Every downstream study was constrained by the narrow
archive: the Chain Build-Up desk could only reach past ATM+/-5 through per-leg
Kite historical calls, and all three of its validation attempts came back
power-bound. Width is forward-only — no later work widens data that was never
written — so the cost of deferring it is permanent.

### The truncation guard was quietly unfair, and widening made that matter

`sample_once` capped the batch with `leg_keys[:_QUOTE_CEILING]`. Because
`leg_keys` is built underlying by underlying, that slices the **last underlying
off entirely** — SENSEX would vanish from the archive while NIFTY kept full
width, and nothing downstream could tell the difference between "not written"
and "not traded". Harmless at 198 legs, a live hazard at 450.

Replaced with symmetric narrowing: drop one strike from each side, rebuild, and
retry until it fits. All three underlyings degrade the same way, and the width
that was actually used is visible in the snapshot. `tests/test_delta_velocity.py`
pins both the ceiling arithmetic and the even-narrowing behaviour, so raising
`STRIKE_WIDTH` past the ceiling fails a test instead of silently losing an
underlying.

### Retention — flagged here, wired in the next commit

`store.prune_raw` existed and honoured `RAW_RETENTION_DAYS = 30`, but nothing
called it, so the archive had grown unbounded since Phase 1 and this change made
it ~2.3x faster (~27 MB/day). Wiring a pruner deletes the operator's data on a
schedule, so it was raised as a decision rather than taken as a side effect of a
width change — and then wired on request; see the entry above.

---

## Session 2026-08-27 (later) — Chain Build-Up: continuous outcome, and why more data is the only lever

Third validation attempt on the breach layer, and the one that produced something
worth keeping — though not the thing it was aimed at.

The binary wall test discards everything except whether spot crossed K, so the
obvious refinement was a continuous outcome: the percentile rank of
`approach = excursion / distance` among matched controls, where 0.5 is the null
and below 0.5 means spot was repelled. Rank rather than a raw mean because
excursion is strongly right-skewed and a mean difference is dominated by a few
large moves that say nothing about a level holding.

**Null again.** 0 of 12 survive Benjamini-Hochberg at q<0.10; every rank sits
within 0.05 of 0.500. The faint call-side tilt *above* 0.5 persists — same
direction as the binary test, still not significant.

### The refinement bought nothing, and that is the finding

Median |t| ratio, rank against binary, same events and strata: **0.99.** No gain.
The prediction that a continuous outcome would "roughly halve the standard
errors" was wrong, and the variance decomposition (now printed by the script)
says exactly why:

| | events/cluster | ICC | SE now | SE if 4x events | SE if 4x sessions |
| --- | --- | --- | --- | --- | --- |
| writing CE 15m | 3.7 | 0.31 | 0.0447 | 0.0379 | **0.0224** |
| any_breach CE 15m | 7.0 | 0.16 | 0.0299 | 0.0247 | **0.0149** |
| any_breach CE 60m | 7.4 | 0.30 | 0.0337 | 0.0306 | **0.0168** |

Two things bind, and a sharper per-observation statistic moves neither:

- **Intra-cluster correlation of 0.16-0.40.** A third of the variance is
  between-session — different days, different regimes — and no within-session
  precision touches it.
- **Only 3-7 events per cluster.** The cluster mean is limited by how *few*
  events there are, not by how precisely each is measured.

Quadrupling events per session buys ~15%; quadrupling sessions buys the full 50%.

### Two corrections to the roadmap

- **More sessions is not one option among several, it is the whole list.** It had
  been ranked below methodological refinement. Backwards. Moving the minimum
  detectable effect from ~9-17pp to ~4-8pp needs ~4x the clusters — roughly 56
  sessions per underlying, about 2.5 more months at the current collection rate.
- **Conditioning on dOI/volume would make this worse, not better.** It was the
  recommended next test. Splitting 3-7 events per cluster into a high-build-ratio
  subset leaves 1-3, and by the table above that direction costs power. Good idea
  at 4x the sample; bad one now.

### Standing conclusion

Three attempts, three nulls, and a fourth would fail for arithmetic reasons
rather than because the hypothesis is wrong. **Stop testing this until the
archive is deeper.** The useful action is the forward-only one from the original
plan: widen `STRIKE_WIDTH` in `analysis/delta_velocity/collector.py`, since every
week of delay is a week of narrow data.

The label on the desk is unchanged and now well-supported: the breach layer is an
**attention tool**, not a signal and not a level. The adaptive calibration is what
makes it a well-behaved one, and that part *is* measured. `features.py` carries
all three results next to `THRESHOLD_MODES`.

---

## Session 2026-08-27 (later) — Chain Build-Up: the wall test, also null

Second validation attempt on the breach layer, asking the question closer to
what the desk is used for than the directional study was: when calls are written
at strike K above spot, does K hold as resistance? `scripts/wall_test_chain_buildup.py`.

Outcome is binary — within h minutes, does spot reach K. 125,008 strike
observations, 100-241 events per test, 27-33 clusters.

**Null again, and the point estimates lean against the hypothesis.** Nothing
survives Benjamini-Hochberg at q<0.10, and all six call-side rows are *positive*
— breached call strikes were crossed MORE often than matched controls, not less.
The put side sits at zero.

| event | side | h | ev% | ctl% | diff | t |
| --- | --- | --- | --- | --- | --- | --- |
| writing | CE | 30m | 36.4 | 29.4 | +9.42pp | 1.47 |
| writing | CE | 60m | 40.2 | 34.3 | +9.09pp | 1.50 |
| writing | PE | 30m | 21.3 | 28.8 | -0.52pp | -0.09 |

### Two confounds, and only one is obvious

**Distance** is the obvious one: a strike 10 points away is crossed constantly,
one 300 points away almost never, and writing concentrates at particular
distances. Absorbed by expressing distance in expected-move units,
`z = |K - spot| / (spot * sigma_5m * sqrt(h/5))`, and comparing events only
against non-events in the same (side, horizon, z-bin).

**Momentum toward the strike** is the one that bites, and it is why the naive
version of this test would have "found" that walls break. Writers write the
strike spot is already approaching; matching on distance controls for how far K
is, not which way spot is moving. Re-matched on prior-15-minute drift toward the
strike, about a third of the call-side effect disappears:

| | distance | + momentum |
| --- | --- | --- |
| writing CE 30m | +9.42pp (t=1.47) | +6.00pp (t=0.99) |
| any_breach CE 30m | +5.63pp (t=1.45) | +3.15pp (t=0.82) |

Six-for-six on sign looked like it might be something. It is not worth much:
`writing` is a subset of `any_breach` and the horizons are nested, so those are
closer to one or two independent signs than six.

### Power is the binding limit in both studies

100-241 events over 27-33 clusters at SEs of 3-6pp puts the minimum detectable
effect near **9-17pp**. A genuine 3-5pp "walls hold" effect would be invisible
here. Same story as the directional study (MDE ~1-6 bps). Both are failures to
reject on a thin sample, not demonstrations of absence.

One asymmetry worth revisiting rather than believing: calls tilt positive, puts
sit at zero — consistent with put OI being more hedging and call OI more
overwriting, but at t~1 that is a hypothesis.

### Standing conclusion

Both validation attempts are null, so the label holds: **the breach layer is an
attention tool, not a signal or a level.** The adaptive calibration is what makes
it a well-behaved one, and that part *is* measured. `features.py` carries both
results next to `THRESHOLD_MODES`.

Two methodological improvements would do more than more data alone:

- **A continuous outcome** — "how close did spot get, in expected-move units"
  instead of binary crossing — uses every observation's full information rather
  than discarding it at a threshold, and could roughly halve the standard errors
  with no new sessions.
- **Condition on dOI / volume**, the position-building ratio: the hypothesis
  worth testing is that walls built by genuine position-building hold, and
  pooling them with churn is what flattens the result.

---

## Session 2026-08-27 (later) — Chain Build-Up: fitted thresholds, and a null result

Two follow-ons to the Build-Up desk, both driven by measurement against the
`analysis/delta_velocity` archive (42 session-files: 14 sessions x NIFTY /
BANKNIFTY / SENSEX, 2026-08-10 to 08-27).

### The fixed thresholds were well-chosen; the *conditioning* was missing

Fitting `|dOI %|` against the archive (`scripts/fit_chain_buildup_thresholds.py`)
showed the hand-picked constants inherited from OI Tracker all sit near p92-p94,
with correct scaling across timeframes. The level was never the problem:

| tf | p95 | current | breach rate it produces |
| --- | --- | --- | --- |
| 5m | 9.5 | 8.0 | 6.3% |
| 15m | 20.8 | 15.0 | 8.2% |
| 30m | 33.3 | 25.0 | 7.6% |
| 60m | 46.7 | 35.0 | 8.1% |

What *is* wrong is that one number cannot mean the same thing twice:

- **By DTE** — breach rate 13.7% on expiry day against 0.2% at 22+ DTE, a 70x
  spread. Wallpaper at one end, a dead feature at the other.
- **By time of day** — 20.2% at 09:25 against 0.2% at 12:35. **09:20-09:45 is
  6.5% of cells but 24.9% of all breaches**: a quarter of what the desk flagged
  was the open being the open, in the columns the eye lands on first.
- **By moneyness** — p95 runs 8.9-11.0 from ATM to +/-6 at 5m. Flat. Conditioning
  on moneyness was in the plan and the data killed it, so it was not built.

`threshold_mode=adaptive` reads a fitted p95 per (timeframe, DTE, time-of-day)
from the generated `analysis/chain_buildup/calibration.py`. Under it every
(DTE x session-third) cell lands between 3.5% and 6.3% against a 5% target —
that flattening is the acceptance test, and `--verify` reports it rather than the
prettiness of the table.

The model is factorised (`base x dte_factor x tod_factor`) rather than a full
cross-tab: 14 sessions gives single-digit samples per cross-tab cell at 60m,
which is noise dressed as precision. It therefore **cannot represent an
interaction** — if the opening surge is sharper on expiry day than on a monthly,
this averages them. Revisit when the archive is deep enough.

`calibration.py` is generated *and committed*: it is a calibration constant, not
runtime state, and `data/` is gitignored, so a store there would leave CI and a
fresh checkout with nothing to read.

### The event study came back null, and that is the finding

`scripts/event_study_chain_buildup.py` measures forward underlying returns at
+5/+15/+30/+60m after every breach, against the unconditional mean over the same
sessions. **0 of 32 tests survive Benjamini-Hochberg at q<0.10.** Effects run
0.2-4.1 bps against standard errors of the same magnitude. One test reaches raw
p<0.05 (PE short-covering @30m) and points the *opposite* way to the classic
reading; noise alone is expected to produce ~1.6 such hits.

**The methodological point is worth more than the verdict.** Forward spot return
belongs to a (underlying, session, timestamp), not to a cell — every breached
cell at 11:05 on one session shares one outcome. Collapsing events and clustering
on (underlying, session) leaves ~33 independent moments; treating cells as
independent inflates that to ~250 and shrinks the standard error ~2.7x. Measured
on this archive, the naive version reports CE short-covering as significant at
**all four horizons, strengthening monotonically** (t up to 3.63, p=0.0005) —
the most convincing shape a false positive can take. `--naive-contrast` prints
both, deliberately.

**This does not prove the layer useless.** Power is limited: ~30-37 clusters with
SEs of 0.4-2.3 bps puts the minimum detectable effect around 1-6 bps, and the
test only asks about *direction of the underlying* — it says nothing about
whether a breached call strike holds as resistance, which is a strike-level
question and closer to what the desk is for. Sample is one month and one
volatility regime.

**Treat `breach` as an attention tool, not a signal.** `features.py` carries the
same warning next to `THRESHOLD_MODES` so it is not re-derived as one. Re-run the
study as the archive deepens; the honest label is "failed to reject on a thin
sample", not "closed".

---

## Session 2026-08-27 — Option Chain Build-Up desk (`/chain-buildup`)

New read-only desk: a strike x time-bucket grid of OI build-up, CE left / PE right, at
5 / 15 / 30 / 60-minute buckets. Backend `analysis/chain_buildup/`, API prefix `/buildup`,
SPA page `/chain-buildup`. Nothing under `broker/` / `execution/` / `risk/` touched — it
places no orders.

### No collector, and that was the point

The desk was scoped as "record chain build-up going forward", which reads as a new
collector + store + runner. It does not need one: `analysis/delta_velocity/collector.py`
already samples **every minute of every cash session** for NIFTY / BANKNIFTY / SENSEX and
archives per-leg `oi` and `ltp` to `data/delta_velocity/<U>/<date>.jsonl`. Bucketing that
is a pure read — no Kite call, no rate-limit exposure, no second daemon thread. Same
dependency `analysis/theta_decay/` already takes on the same archive.

Also found and worth knowing: `data/chain_history/NIFTY/` holds 8 sessions
(2026-07-27 → 08-05) of wider full-chain snapshots. **Its writer is no longer in the tree**
and the `/chain-history/coverage` route went with it (that route also carried an unfixed
path-traversal finding from the 2026-08-06 security review). Dead data, not a source — but
it is the format `delta_velocity/store.py` deliberately mirrors, so the same parser reads it.

### Two sources, and the split matters

| | archive (default) | Kite historical (`widen=true`) |
| --- | --- | --- |
| cost | free | one `fetch_historical_by_token` per leg |
| span | 09:15 → now, survives expiry | only while the contract is listed |
| width | `dv_collector.STRIKE_WIDTH` (ATM ±5) **at capture time** | whole listed chain |

The archive's strike set **drifts with spot through the day**, so the union across a session
is wider than any single minute of it — a strike present at 09:15 can be absent by 14:00.
Rows render blank for the gap rather than vanishing, and the delta on the returning bucket
spans the whole absence (honest, and it keeps the telescoping property).

Widening is the expensive path this repo has been bitten by before — a gamma snapshot once
walked a chain issuing ~80 sequential historical requests, 80+ seconds. Three controls:
opt-in per request (`widen=false` refuses rather than silently costing a minute), a
`MAX_WIDEN_LEGS = 160` cap with truncation reported in `meta` rather than passed off as full
coverage, and a per-`(token, timeframe, session)` cache. Concurrency is 3 workers, matching
Kite's published 3 req/s historical limit. Measured: ATM ±10 on NIFTY = 12 extra legs, ~2s.

### Conventions the grid depends on

- **A bucket's OI is its last value, never a mean.** OI is a level; averaging 100 and 900
  reports 500, which was true at no instant. This is also exactly what Kite's `oi` on a
  candle means, so the two sources agree cell for cell.
- **ΔOI is against the previous bucket; the first bucket is against the baseline.** The row
  then telescopes: buckets sum to `latest_oi - baseline`, so the per-bucket columns and the
  cumulative column cannot disagree. "Every bucket vs baseline" is offered as `cum` on the
  same cells rather than as a second differencing scheme.
- **Bucket edges anchor at 09:15, not at the first snapshot** — otherwise a collector that
  started late shifts every column label and two sessions stop being comparable.
- **Baseline `prev_close` reads the previous *archived* session**, not a Kite previous-day
  candle: consistent with the rest of the grid and still correct after expiry. When there is
  no earlier session it says so in `meta.notes` instead of silently falling back to
  session-open and mislabelling the column.
- **Shading scales to p95, not max.** One expiry-day print an order of magnitude above the
  rest would otherwise wash the whole grid to white; the UI clamps above p95.

### BUG found while wiring the widening path

Merging the two sources raised `TypeError: can't subtract offset-naive and offset-aware
datetimes`. The archive writes `+05:30`-aware stamps; a Kite candle can arrive naive
depending on how pandas carried its index, and bucketing subtracts one from the other.
Fixed at the single parse boundary — `features.parse_ts` now normalises everything to
**naive IST wall-clock** (aware stamps converted to IST first). Regression tests cover the
mixed-source case and a foreign offset.

Also stamped Kite candles at their **close** time in the adapter: Kite labels a candle by its
open, and `bucket_end` maps a timestamp to the bucket closing at or after it, so passing the
open landed a 09:20 candle one column early.

### UI

Layout resolves two constraints the operator gave that sound contradictory: baseline OI sits
next to the strike, *and* time reads left → right. Result — the centre block is
`Δ% │ ΔOI │ CE-base │ STRIKE │ PE-base │ ΔOI │ Δ%` and never scrolls, while the two wings
scroll horizontally with their scroll positions mirrored. Reading outward from the strike on
each side: baseline OI, cumulative ΔOI against it, that change as a percent — the three
numbers read as one sentence, and the Δ pair carries the same fill/hatch shading as the wings. Hue encodes the **side** (CE red / PE green), not direction;
fill intensity is magnitude; unwinding is **hatched** rather than merely paler, because a
faint fill is indistinguishable from a small build-up and that is the one confusion this grid
cannot afford. Each cell carries a corner tag for the four-quadrant class (LB / SB / SC / LU)
derived from ΔOI against Δprice per option.

Three panes stay row-aligned by fixed row heights and a single sorted `rows` array, which is
also what makes the strike sort toggle (low→high / high→low) safe.

### Route naming

API `/buildup`, page `/chain-buildup` — `is_api_path` matches on `startswith`, and
"/chain-buildup" does not start with "/buildup". Verified: a **direct browser load** of
`/chain-buildup` on :8001 returns the SPA, not a JSON 404.

### Test-isolation gap found (pre-existing, not from this work)

Running `pytest tests/` **while the desk is up** fails
`test_mcx_rolling_straddle.py::test_ensure_state_underlying_heals_legacy_nifty_spot_on_crude`;
it passes in isolation. `rs._ensure_state_underlying` calls `clear_spot_state_for_underlying()`
→ `save_state()` + `append_log()`, and that test patches neither `store.STATE_FILE` nor
`store.LOG_FILE`, so both writes land in the **live** `data/rolling_straddle_{state,log}.json`
and race the runner. Confirmed by `underlying_reset` entries appearing in the live log at the
test run's timestamps. Sibling tests in the same file already patch all three paths correctly.

---

## Session 2026-08-26 (later) — POC trail: where the control price used to be

A shaded grey band plus dotted grey lines on the profile chart showing the levels
the POC held today, each labelled with its time range. New `POC trail` toggle.

### The closing POC hides the session

Measured 2026-08-26: NIFTY's POC held **~24,346 from 09:30 to 14:30 — 84% of the
session** — then migrated 71 points to ~24,278 in the final hour. A reader looking
only at the closing 24,278 would never know 24,346 was the control price for five
of the six hours. SENSEX was bimodal, flipping between ~77,911 (4h30, 72%) and
~77,724 (1h45, 28%).

### Stored, not recomputed

Rebuilding the profile at all 25 checkpoints costs **~2.7s**, which is not a page
load. The sampler and the backfill already compute the profile at every
checkpoint and were discarding the POC, so it is now kept in `poc_curve`
alongside the tilt curve — a separate map so pre-existing rows stay readable.
Backfill re-run for both underlyings to populate it.

Consequence: the trail exists only for sampled underlyings and only from the day
recording began. `available: false` with `not_sampled` / `no_trail_yet` rather
than drawing a partial trail as if it were the whole session.

### Runs vs levels

Consecutive checkpoints within **half a strike step** are one level, so a POC
drifting ~5 points across a morning stays one line instead of fragmenting into
four. Runs that revisit the same price are then merged into a single level with a
spell count: SENSEX's six chronological runs collapse to two control prices. The
chart draws `levels`; the tooltip keeps the run sequence.

**A level equal to the current POC is dropped** — the solid POC rule already
marks it, and drawing both would read as two different things at one price. What
remains is exactly the part the closing POC hides.

Line opacity is weighted by dwell share so a five-hour level does not look like a
15-minute blip.

### A formatting bug caught in verification

The tooltip printed 270 minutes as "5h30". `Math.round(270/60)` is 5 while
`270 % 60` is 30 — the hours rounded up while the remainder did not. NIFTY's 315
minutes rendered correctly by luck, which is why reading the code would not have
caught it. Now `Math.floor`; SENSEX reads 4h30.

### Verified

`pytest tests/` — 965 passed, 2 new: one pins that a drifting POC stays one level
while a revisited price merges into one level with two spells, the other that the
trail refuses rather than drawing a partial one. Both underlyings confirmed on
port 8001, zero label collisions.

---

## Session 2026-08-26 (later) — Peak time tags: every peak answers "when"

Asked why some peaks carried a time and others nothing. They were behaving as
designed — a time printed only when the middle-50% of a band's volume fit inside
`CONCENTRATED_IQR_MIN` — but the blank read as arbitrary rather than deliberate,
which is a fair criticism of where the line sat.

Two changes:

* **Peaks past the threshold now show their range** (`-1pp 14:05-15:10`) instead
  of nothing. The threshold now decides *moment vs range*, never *something vs
  nothing*, so there is no unexplained blank to wonder about. The information was
  always in the tooltip; this puts it inline.
* **Threshold raised 45 -> 60 minutes.** At 45 two peaks with near-identical
  windows (45 vs 50 min) landed on opposite sides of the line.

**Stated at the time and worth keeping:** with ranges shown, raising the
threshold slightly *reduces* precision rather than adding it — a 51-minute peak
that would have shown an honest `10:26-11:17` now prints a single `10:26`. Live
SENSEX after the change shows exactly that on its 51- and 58-minute peaks. 60 was
the operator's call, taken as "within the hour is a defensible moment"; anything
wider still gives its range rather than a false point.

`test_concentration_boundary_is_inclusive_and_wide_windows_still_refuse` pins the
boundary as inclusive at 60 and asserts the window fields are populated either
way, so the chart always has a range to fall back on.

---

## Session 2026-08-26 (later) — BUG: profile readings drifted after 19:15 IST

Reported as "tilt values keep changing". They were, and everything else with
them — POC, VAH/VAL, peaks — on every 45-second cache expiry.

### Cause: a clamped lookback stops anchoring and starts sliding

`fetch_minute_candles(token, minutes=N)` derives `from_date = now - N`, so N has
to track wall-clock for the window to stay anchored to the session open.
`gamma_density_history.minutes_since_session_open()` clamps N at **600**. That is
harmless while the session is young and silently wrong afterwards: once
elapsed-since-open passes 600 — 09:15 + 600 = **19:15 IST** — the clamp binds and
the window becomes a *rolling* 600 minutes, sliding forward a minute at a time
and dropping the open.

Measured 2026-08-26 at 20:25 IST, 4h45m after close:

| | 18:00 IST | 20:25 IST |
|---|---|---|
| bars | 385 | **314** |
| first candle | 09:15 | **10:26** |
| tilt | -19.16pp | -17.80pp |
| POC | 24278.13 | 24277.84 |

The first 71 minutes of the session had silently fallen out of the profile.

### Fix: `service.session_lookback_minutes()`, no upper clamp

Anchored to today's session open with **no ceiling**, so `from_date` always works
out to the open minus 15 minutes. That is also why it is safe on a weekend or
holiday: the window still lands on today, finds no candles, and the caller
reports `no_session_bars` rather than quietly profiling yesterday.

Verified: first candle back to 09:15, 385 bars, and three consecutive recomputes
byte-identical at tilt -19.16 / POC 24278.125 — matching the 18:00 reading, which
confirms that one was correct and the drift was pure bug.

### The store was not corrupted, by luck of an existing guard

`maybe_sample_tilt_history_periodic` skips a checkpoint bucket that already
exists, and today's buckets were all filled by backfill from full-day bars. Both
2026-08-26 rows are still `source: backfill` with 25 checkpoints. Had the day
been sampled live and the API restarted after 19:15, a truncated reading could
have been written into an empty bucket.

### Wider exposure — NOT fixed here

Two other desks call the same clamped helper for candle lookback and will
truncate the same way after 19:15 IST:

* `options/gamma_density.py:2302` — spot candle lookback
* `options/oi_movers.py:1073` — chart series lookback

Left alone deliberately: changing them alters live behaviour on desks that were
not the subject of the report. `tests/test_gamma_density_history.py` already
carries `test_minutes_since_session_open_mcx_not_capped_at_600`, so the clamp was
a known concern for MCX before this. Fixing it centrally would close all three at
once and is the better end state — it just needs to be a deliberate call.

---

## Session 2026-08-26 (later) — Volume Footprint: overlay toggles

The legend below the chart became a row of toggle switches above it. Eleven
overlays (7 GEX levels, POC, Peaks, Grid) each switch on and off, with a
show-all / hide-all control.

**The legend was already there doing nothing.** A colour swatch that only names a
line is wasted space; making it the control is the same pixels doing real work.
It is also the honest answer to label crowding on a busy session: rather than the
chart guessing which of eleven overlays matters today, the operator turns off what
they are not reading. That request came directly from a screenshot where a peak
tag rendered through the POC label.

Choice persists in `localStorage` under `3st.volume-footprint.hidden-overlays`,
wrapped in try/catch — a private window or blocked site data throws on access
rather than returning null, and losing a display preference is not worth
surfacing.

### A batching bug the first version shipped with

`toggleOverlay` was a `useCallback` closed over `hidden`. React batches state
updates, so three quick clicks all read the *same stale set* and overwrote each
other — toggling POC, Peaks and Grid together left only Grid off, and
`localStorage` held `["grid"]`. Found by clicking three switches in one tick
during verification, not by reading the code.

Fixed with a functional update (`setHidden(prev => ...)`) and persistence moved
into a `useEffect` on `hidden`, which also keeps a storage write out of a state
updater. Re-verified: three toggles in one tick now all stick, survive a reload,
and show-all clears them.

### Also this round

Peak tags flip inward when they would land in a label column's gutter
(`GUTTER_W`), which is what caused the reported `-15pp` / `POC 24,278` overlap.
The tag keeps its own price rather than being nudged off it.

---

## Session 2026-08-26 (later) — Volume Footprint: prominent peaks, tagged with how each level traded

Each side now reports its top peaks by prominence, and every peak carries two
readings of how its price band traded. Neither is the session tilt.

### A profile peak does not form at a time

The request was to tag "the tilt when the peak formed". Measured first, on the
live NIFTY session: the POC band was touched by **244 of 385 bars, spanning
10:21-15:27**, with the middle 50% of its volume landing between 12:13 and 14:28.

A volume profile is a distribution over **price**, not time. Volume under a peak
accumulates across many separate visits, so for most peaks there is no moment to
name — and printing one would be a fiction that looks authoritative.

So the chart names a time **only when the peak earns it**: `concentrated` is true
when the middle 50% of a band's volume fits inside 45 minutes. Today that flags
the 15:30 closing-auction spike (12 bars, 15:28-15:39) and the opening peak at
24,348, and correctly refuses for the POC. The full window is in the tooltip
either way.

### Two tilts, and they genuinely differ

* **`band_tilt_pp`** — the session tilt formula restricted to the peak's price
  band, read off the fitted mixture.
* **`flow_tilt_pp`** — the same formula over the *bars that actually traded
  through* the band, using each bar's own engine-assigned split.

One weights by where the model placed the mass, the other by raw bar volume.
Measured today they diverge by up to 4.6pp (sell peak 24,321: band -13.41 vs flow
-18.01), so keeping both is not redundancy.

**Both describe the band, not the side.** A buy peak and a sell peak at the same
price report identical numbers; the side only decides which peaks get detected.

And both differ from the session tilt, which is the point: NIFTY read **-19.16pp**
overall while its 15:30 peak read **-30.6pp** and its 24,321 peak **-13.4pp**.

### Prominence, not just local maxima

Peaks use topographic prominence — height above the higher of the two saddles
separating a peak from any taller neighbour. A raw local-max scan returns a dozen
bumps that are smoothing artefacts; a shoulder wobble on a taller peak scores near
zero and is dropped. Filters: prominence >= 8% of the side's tallest, minimum
separation of half a strike step, at most 4 per side. Walked on the
**full-resolution** arrays, never the sampled draw curve, which at this scale both
invents and hides bumps.

### Label discipline held

The tag is deliberately compact (`-31pp 15:31`) and sits at the peak's own tip
rather than joining the label columns — seven more entries in the left column
would have undone the stacking fix from earlier the same day. Only the tallest
peak keeps a stub back to the centre line; the rest are dots, or the chart becomes
a comb. Verified in the DOM: 7 peaks tagged, **zero label collisions**.

### Verified

`pytest tests/` — 961 passed, 5 new. They pin that prominence rejects a shoulder
bump, that selection keeps the taller of two close peaks, that band tilt is
unmeasurable rather than balanced on an empty band, and that a six-hour formation
window is never reported as concentrated.

---

## Session 2026-08-26 (later) — Volume Footprint: price grid and per-side peaks

Three additions to the profile chart: a dotted price grid, per-underlying
spacing, and each side's own density peak.

### The grid is the strike lattice, not round numbers

`grid_step` comes straight from `INDEX_OPTIONS[u]["strike_step"]` — NIFTY 50,
SENSEX 100, BANKNIFTY 100, CRUDEOIL/CRUDEOILM 50, NATURALGAS 5. Reading it from
config rather than inferring it from the price range means a gridline **is** a
strike, so the profile lines up with the OI ladder beside it. Nothing had to be
guessed for the other underlyings; the values were already there.

Coarsened when a session's range would draw too many: the chart steps the base
through 2x / 5x / 10x / 20x / 50x until at most `MAX_GRID_LINES` (14) fit, and
draws none if even that fails. A wide CRUDEOIL day at 50 points would otherwise
render a solid block, which is worse than no grid. Measured 2026-08-26: NIFTY 4
lines over 206 points, SENSEX 5 over 576, NATURALGAS 1 over 6 — all at 1x.

### Peaks are per-side, and are not the POC

`buy_peak` / `sell_peak` give the price at which each side's own density is
greatest. POC is the peak of the **combined** profile, so on a one-sided session
the three sit at different prices and that gap is the reading. Live NIFTY on
2026-08-26: buy peak 24,274, sell peak 24,278, POC 24,278.1 — sell carried the
POC while buying concentrated four points lower.

Both are read off the **full-resolution** `profile_buy` / `profile_sell` arrays,
never the sampled `curve`. The curve keeps ~1 point in N purely for drawing and
can miss a sharp peak by several ticks — the same rule POC and the value area
already follow. A side that carried no volume returns `null`, not the axis
minimum, which would draw a confident marker at the bottom of the chart.

They are drawn as a short dashed stub to the tip of their own curve with a dot,
deliberately not a full-width rule: a horizontal line across the chart reads as
another level, and these are properties of one side only.

### Verified

`pytest tests/` — 956 passed, 3 new. One pins that the peak equals the max of the
full array and not of the draw curve; the density assertion uses `abs=1e-4`
because the payload rounds to 4 dp for transport, while the peak *price* — what
the chart actually marks — is exact.

---

## Session 2026-08-26 (later) — Volume Footprint: session tilt history

`analysis/volume_profile/tilt_history.py` + `data/volume_tilt_history.json` +
`scripts/backfill_volume_tilt.py` + `GET /volume-footprint/tilt-history` +
`components/volume/TiltHistoryCard.tsx`. Sampled for **NIFTY and SENSEX only**.

### Sessions are stored as curves, not closing values

Tilt at 09:30 comes from ~15 bars; a finished session has ~385. Early-session
tilt is noisy and mean-reverts as volume accumulates, so ranking today-at-09:30
against thirty *closing* tilts yields a confident number that means nothing.

Every session is therefore a curve keyed by elapsed session minute (15-minute
checkpoints, ~25 per session), and `compare_current()` only ranks today against
prior sessions **at the same checkpoint**. The card states the basis in its
header — "compared at 15:30 · same point of every session" — so nobody assumes
closing-vs-closing.

This is also why the store shape was decided before anything was written:
retrofitting a curve onto a scalar store means discarding the accumulated
history.

### Backfill is a one-shot seed, and NIFTY could not be seeded at all

The profile needs **front-month futures** bars (cash indices carry no volume),
and Kite delists a contract about a day after expiry. Measured 2026-08-26:

* `NIFTY26AUGFUT` (expired 08-25) → `fetch_historical_by_token` raises
  **`invalid token`**, even though the locally cached instrument dump still
  listed it. The dump is not authority on what Kite will serve.
* `SENSEX26AUGFUT` expires 08-27, so it still resolves — for one more day.

So the reachable window shrinks with every expiry and the same command returns
fewer sessions next month, silently. **The durable answer is the live sampler**
(`maybe_sample_tilt_history_periodic`, hooked into `execution/scheduler.py`),
which records each checkpoint as the session runs — reusing the profile the desk
has already cached when one is fresh, so a page-open session costs nothing extra.

Seeded 2026-08-26: **SENSEX 18 sessions** (2026-08-03 → 08-26), **NIFTY 1**
(today only). July was skipped by instruction and is unreachable regardless.

### The volume guard was self-defeating; the calendar guard is not

The first backfill guard skipped a session whose volume was under 25% of the
window's **median** — and wrote 18 far-month NIFTY sessions anyway. The median is
computed over a window that is *mostly* far-month, so the far-month level becomes
the baseline and nothing looks anomalous: 2026-08-03 passed as "72% of median"
while being 10% of real front-month volume (287k against 2.72M).

Those rows were purged (`--purge`, `tilt_history.purge_underlying`) and the guard
replaced with a **calendar** test: a contract is front month only from the day
after its predecessor expired, read off the instrument dump's prior expiry. For
NIFTY that yields 2026-08-26 exactly — 17 sessions correctly rejected. The volume
test survives as a fallback for when the predecessor is already delisted, but now
compares against the **last** session rather than the median, because
`resolve_future` guarantees the resolved contract is front month today.

Substituting the far month is not a workaround either: `NIFTY26SEPFUT` traded
312k on 08-05 rising to 5.5M on roll day 08-25. A profile fitted to that measures
the roll, not the session.

### Null and label discipline

* A session is **excluded from its own comparison** — it cannot be part of the
  distribution it is ranked against.
* `available: False` with a named reason for `too_early` (before the first
  checkpoint), `no_history`, and `window_too_thin` (< 5 prior sessions). Ranking
  against four sessions is arithmetic, not evidence.
* **`n` always travels with the percentile.** "6th of 16" and "6th of 30" are
  different claims and must not render alike.
* `source` is `live` or `backfill`, counted in the payload and shown as a badge;
  a live point upgrades a backfilled session, never the reverse, because live
  points were observed as they happened.
* Percentile, not z-score: tilt is bounded [-100, +100] and its distribution is
  not normal, least of all with expiry-day outliers. Ties split at midrank so a
  flat window cannot read as 0 or 100.

### Clock note

Git Bash's `TZ=Asia/Kolkata date` double-applies the offset on this machine and
reported 09:23 IST when the real time was 14:53. Python's
`datetime.now(tz=ZoneInfo("Asia/Kolkata"))` is correct and matches the exchange
bar timestamps. Trust the Python path — the store's "today" depends on it.

### Verified

`pytest tests/` — 953 passed. 14 new tests in `tests/test_volume_tilt_history.py`,
including one that pins the partial-vs-complete refusal by seeding sessions that
drift from positive at 09:30 to negative at the close. The fixture patches the
module's own `data_dir` reference (not `settings.data_dir`) and the real store
was confirmed intact at 19 sessions afterwards. API restarted, `npm run build`
run with the API stopped, both underlyings confirmed on port 8001.

---

## Session 2026-08-26 — Volume Footprint: strike OI ladder (spot, session-open OI, ΔOI)

Added a per-strike OI ladder beside the session volume profile:
`/volume-footprint/oi-ladder` → `analysis/volume_profile.strike_oi_ladder()` →
`Pixel Perfect UI/src/components/volume/OiLadderCard.tsx`.

### It costs no extra Kite call, and that is the whole design

The gamma snapshot **already** computes everything this desk needed.
`attach_strike_oi_baselines()` (`options/gamma_density.py:1549`, called
unconditionally at `:1911`) attaches `ce/pe_oi_base` (session-open OI, prev-close
fallback), `ce/pe_doi` and `ce/pe_oi_base_source` to every strike row — and it is
**not** gated on `include_history`, so the history-free snapshot `gamma_levels()`
was already pulling carried all three, unread.

So `gamma_levels()` and `strike_oi_ladder()` now share one `_levels_and_ladder()`
build behind one cache entry. Opening the page is the same single option-chain
pull it always was. `include_history=False` still holds, for the original reason:
a second page calling the full snapshot would double-write the session trail and
record a page visit as a pin sample.

`ensure_session_open_oi()` *is* reached through that snapshot, but it is
idempotent — persists once per day, returns the stored map thereafter — so
serving this page cannot move a baseline the gamma desk already recorded.

### The ladder derives nothing

Every OI column is read straight off the snapshot. This module deliberately does
**not** re-derive a baseline: if it did, the ladder and the gamma desk could
disagree about what "session open" means on the same strike, and there would be
no way to tell which was right.

### Null discipline, and why the put side is full of dashes

Verified live on 2026-08-26 (NIFTY, spot 24,290): every strike ≥ 24,500 shows `—`
on the put side. That is correct, not a dropped column. Those deep-ITM puts were
never gamma-resolved by the chain build, so `pe_oi` keeps its `0` default and
`pe_oi_base` is `None`. Confirmed identical in the raw
`/gamma-density/snapshot` payload.

The rule, held in both the service and the card:

- `None` → **could not be measured**, renders as an em dash and an empty bar track.
- `0` → measured, and the strike genuinely did not move.

`net_doi` is null unless **both** sides are measured. An earlier draft returned
the measured side when only one had a baseline; that prints a confident net for a
strike half of which was never observed. `test_oi_ladder_keeps_an_uncaptured_baseline_null_never_zero`
pins the corrected behaviour.

### ΔOI % sits beside the absolute, and the bars ignore it

Each side shows ΔOI over a small percentage of its own baseline —
`doi / baseline * 100`, the same formula as
`oi_movers.build_session_change_boards`, so the two desks cannot print different
numbers for the same strike. Null on a zero baseline: a strike that opened empty
has no percentage, and one would turn the first contract written into an
infinite move. Clamped to `>999%` for display only.

**The bars stay on absolute contracts.** A 700% build on a thin far strike is
real, and worth reading, but it is not a larger trade than a 20% build on a
heavy ATM strike — scaling the bars by percentage would say it was. Verified
live on 2026-08-26: CE percentages spanned −15.7% to +708.3% across 41 strikes,
all of them legitimate.

### Display rules worth keeping

- **One bar scale across both sides** (`max_abs_doi`), so a call bar and a put bar
  of equal length mean equal contracts. Per-column scaling would make a quiet side
  look as busy as an active one.
- A **"mixed baseline" badge** appears when `oi_baseline_prev_close_count > 0` —
  the column header says "session open", and it must not imply a uniform 09:20
  capture when part of the ladder fell back to previous close.
- Strike step is the **modal** gap between adjacent strikes, not the mean: the
  snapshot carries no top-level `strike_step`, and one missing strike would widen
  every band if averaged.

### Route note

`/volume-footprint` is **not** in `_API_PREFIXES`, so an unmatched subpath under it
falls through to the SPA and returns the HTML shell with **200**, not a JSON 404.
While verifying, that made a missing route look like a working one — check the
body, not the status code. (The API here runs without `--reload`; the route only
appeared after a restart.)

### Verified

`pytest tests/` — 939 passed. Seven new tests in `tests/test_volume_profile.py`
cover the pass-through, the null discipline, thin-session degradation, the modal
strike step, the shared-snapshot contract, the percentage formula, and gamma failure not
taking the profile down. API restarted and `npm run build` run (API stopped first); ladder
confirmed on port 8001 including a direct URL load.

### Docs moved

`docs/volume-footprint/` → `volume Profile Gaucessian/` (commit `fbf66c1`). The
folder was the desk's original working directory, emptied on 2026-08-20 and left
behind; it is now the documentation home. Code stayed put — in particular
`vendor/volume_footprint/` can never move there, because a directory name with
spaces cannot be a Python package root.

---

## Session 2026-08-25 — Options Arbitrage desk (`/opt-arb`, API `/oarb`)

New analysis desk: `analysis/opt_arb/`. Scans option-to-option pricing violations
across NFO/BFO/MCX, prices them at bid/ask, nets them against the Indian charge
stack. **Scan and alert only** — no imports from `broker/` / `execution/` / `risk/`,
no order path. Built in the order the design called for: costs first, then the MCX
big/mini pairs, then the index butterfly sheet.

### The finding that changed the design

The obvious big-vs-mini trade (GOLD vs GOLDM, SILVER vs SILVERM — what the vendor
sheets show) **is not arbitrage today**. Read straight out of the instrument dump:

| Pair | Option expiry | Referenced future | |
|---|---|---|---|
| CRUDEOIL / CRUDEOILM | same | same | Tier A |
| NATURALGAS / NATGASMINI | same | same | Tier A |
| GOLD / GOLDM | 08-31 vs 08-28 | Oct-05 vs Sep-04 | Tier B |
| SILVER / SILVERM | 08-28 vs 09-24 | Sep-04 vs Nov-30 | Tier B |

Gold's two sides point at futures a month apart, so the "spread" on a vendor
big-vs-mini sheet is mostly carry — a four-figure number at ₹1,25,000/10 g that
is not edge. Crude and NatGas *do* share both, and are the only pairs where the
per-unit identity actually holds. `referenced_future()` resolves the reference as
the first future expiring on or after the option expiry, and the classification is
recomputed from the dump on every call, so a pair promotes itself when it becomes
comparable. `require_clean=true` (default) drops Tier B entirely.

### Costs are the gate, not a footnote

A NIFTY four-leg box round-trips at ~₹330/lot — 5 index points — before any edge
exists. The item most screens omit: **exercise STT is 0.125% of intrinsic**, and a
long box always finishes holding a leg struck at the *far* strike, so the levy is
unbounded in spot. A short box's long legs sit at the near strike and cost less;
`box_exercise_cost` prices both directions rather than assuming symmetry. MCX pays
CTT instead and has no intrinsic levy (ITM devolves into futures). NSE stock
options are physically settled, so their boxes are forced to Tier B.

Rate cards are operator-editable at runtime and persist to
`data/opt_arb_config.json` — **only fields that differ from the shipped card are
written**, so a future correction to the built-in schedule is not shadowed by a
stale full copy on disk.

### Bid/ask, never LTP — with negative controls to prove it

Every BUY leg is priced at the ask and every SELL leg at the bid. Each family has a
test asserting that a **parity-exact book with a real bid-ask produces zero rows**;
a mid-priced sheet would fire on roughly every strike pair at half the spread.
`require_depth` drops rows the top of book cannot fill — the binding constraint on
MCX, where offsetting one big lot needs `ratio` mini lots against a thin mini book.

### Performance: 48s → 1.4s

The first live sweep took 48 seconds. `universe.py` was rescanning the 113k-row
instrument dump five or six times per underlying, with a per-element
`pd.to_datetime` on top. Memoised the per-underlying frames against the dump's
mtime (the same guard `options/chain.py` uses) and vectorised the expiry column:
full sweep now ~1.4s, which makes the page's 15s poll viable.

**Trap for tests:** the cache key is the *real* dump's mtime, which does not move
when a fixture substitutes the frame — so a test swapping the dump must call
`universe.clear_caches()` or one fixture leaks into the next.
`test_clear_caches_lets_a_swapped_dump_take_effect` pins this.

### Live verification (2026-08-25, ~19:50 IST, MCX open, desk DISARMED)

Full sweep over NIFTY/BANKNIFTY/SENSEX + all four MCX pairs, 246 instruments
quoted, 1.4s. Index families returned **zero** rows (correct — cash closed, books
tight). Cross-contract returned Tier-A rows on NATURALGAS/NATGASMINI at
0.10–0.15 ₹/mmBtu, ₹12–42 net per lot after charges, 5–18 lots of book depth.
GOLD/SILVER correctly skipped with their carry reason. `implied_spot` — derived
from the option book itself rather than a separate index quote — returned
NIFTY 24,334.65 against a Gamma-Density spot of 24,334.6.

### Payoff chart per recommendation (added same session)

`analysis/opt_arb/payoff.py` attaches an expiry payoff curve to every scan row,
rendered on the page under the selected row. Dashed line = the structure's own
payoff, solid = after charges; for a real arbitrage the solid line never touches
zero. Computed backend-side for the same reason the delta-velocity aggregation is
— breakevens and the unbounded-tail flag are arithmetic worth testing rather than
reimplementing in TypeScript.

Two decisions worth keeping:

* **The sample grid always contains every strike.** The curve is piecewise linear
  with kinks only at strikes, so a breakeven interpolated across a missing kink
  would be wrong in a way that looks entirely plausible on a chart.
* **`risk_free` means "never touches zero", not "flat".** A butterfly bought
  below zero is a tent and is still free money; requiring flatness reported the
  whole butterfly family as risky. Caught by the smoke test before it shipped.

Cross-contract curves carry an explicit assumption line. A Tier A pair's two legs
settle against the same futures month so one price axis is exact; a Tier B pair's
flat line assumes a convergence that will not happen, and the note says so rather
than letting the picture imply otherwise.

Also fixed the reason the operator could not see any of this: the detail panel
required a click on a row with no affordance for it. The top row is now selected
automatically after a scan, rows have a hover state and a chevron, and the
selected row is highlighted.

### Correction: gold IS a Tier A pair, just not in the front month

The classification above was wrong, and the desk was hiding a real trade because
of it. `pair_status()` classified a **pair** by its **front** expiry. GOLD/GOLDM
is a carry spread in the front month (31 Aug vs 28 Aug, October vs September
futures) — but both sides also list a **25 Sep** option, and at that expiry they
share the date *and* the October future. That is a genuine Tier A trade, and
`require_clean=True` was skipping the whole pair before it was ever priced.

Fixed by splitting `expiry_status(pair, big_expiry, mini_expiry)` out of
`pair_status()`. The scan now targets the first *clean* shared expiry rather than
the first shared one, and tags tier from the expiry it actually traded.
`pair_status()` reports `clean_expiries` plus a separate `front_clean`, because
the front month being carry is still worth seeing on the page.

Live after the fix: gold produces 7 Tier-A rows at 25 Sep — all with
`max_lots: 0`, so the depth gate still keeps them out of the default ranking.
Previously invisible; now visible and correctly gated. Silver stays Tier B at
every shared expiry: the SILVER and SILVERM futures cycles never coincide.

### Big-vs-mini strike grid

`GET /oarb/xsheet` + `components/opt-arb/BigMiniSheet.tsx` reproduce the vendor
worksheet layout (strike rows, BUY/SELL columns, ATM highlight, threshold) with
the three things that make it mean something: cells net of charges, BUY and SELL
priced at the side of the book you would hit (so the gap between them is the
round trip, not zero), and the header basis explicitly labelled as carry when the
displayed expiry's legs reference different futures months.

Windowed ±12 strikes around the money — crude lists ~190 strikes and the far ITM
ones carry stale books whose cells run to six figures and swamp the readable rows.
`_priced_cell` deliberately keeps non-positive edges that `_direction` drops: a
worksheet needs every cell filled or the good ones have nothing to stand out
against.

### Deliberately not built

Ratio-spread credit screens (an unlimited-tail short is not arbitrage and must not
share a page with one), dispersion / index-vs-basket, and any `INDEX_OPTIONS`
entries for GOLD/SILVER — that constant is what
`ANALYTICS_HISTORY_SAMPLE_UNDERLYINGS` filters against, so adding names there would
silently switch on 30-second Gamma-Density and OI-VAR sampling for them.

See [docs/options-arbitrage/](options-arbitrage/) for the desk doc.

---

## Session 2026-08-20 (later) — Volume Footprint desk · volume × gamma

Vendored an existing footprint engine, wired it to Kite, and merged it into the Gamma
Concentration tab. Analysis only — nothing under `broker/` / `execution/` / `risk/`.

### The finding that justified the whole basis decision

Volume can only come from the front-month **future** (cash-index candles carry no volume);
gamma strikes are on the **index**. Measured live on 2026-08-20, the NIFTY basis was
**71.55 points — 1.4 strike steps**. Overlaying futures volume on the strike ladder unadjusted
would have put the POC a strike and a half from where business actually happened, and looked
entirely plausible. Each bar is now shifted by its own `fut_close − index_close`;
`basis.matched_bars` reports how many minutes had an index partner (364/374 live).

MCX is the easy case, not the hard one: options there are written on the future, flagged by the
**pre-existing** `spot_source: "future"` in `INDEX_OPTIONS`, so no correction is applied and
none is needed. It doubles as a control group.

### Shipped

1. **`vendor/volume_footprint/`** — the MPL-2.0 Pine port, moved verbatim with its LICENSE
   (fetched canonical, not reproduced from memory). New top-level `vendor/` with an `__init__`
   explaining the convention. Its 37 tests moved into `tests/` and now gate CI; the single
   divergence from upstream is an explicit import instead of a `sys.path` insert.
2. **`analysis/volume_profile/service.py`** — Kite fetch, per-bar basis alignment, 45s cache,
   normalised payload. Session-anchored with an explicit `minutes_since_session_open`: the
   default is 40 bars, which would have silently profiled the last 40 minutes.
3. **`/volume-footprint`** desk + sidebar entry — profile chart, POC/VA/tilt/OVL, and a
   "how to read this" panel carrying the caveats.
4. **Concentration tab** — `VolumeConfluencePanel` (POC vs γ peak vs pin, VA vs the containment
   band) and a per-strike **volume tint** on the Γ ladder via `band_mass()`, which is exact
   aggregation rather than resampling.
5. **One POC on the desk** — `session_poc` now prefers the footprint mixture POC when one is
   already cached, via a **peek-only** accessor. Paying the integration from there would push it
   onto the thin multi-index snapshots that deliberately avoid it; with no cache it falls back to
   the binned POC unchanged, so the Gamma chart never loses its level.
6. **`instruments._compact_row` now carries `tick_size`** — authoritative and it varies more than
   you would guess (NIFTY 0.10, SENSEX 0.05, CRUDEOIL 1.00), so the price lattice reads it
   rather than hardcoding a map.

### Three rules the code holds, each pinned by a test

- **No measurement is claimed that was not made.** Only the Geometric engine is reachable —
  no tick feed exists in this repo (confirmed: `KiteTicker` appears only in two execution
  modules and `.venv/autobahn`). Every payload carries `estimate: true`.
- **`null` is unmeasured, never zero.** Thin sessions return `available: false` with a bar
  count instead of a POC fitted to the opening minutes.
- **Off-frame mass is surfaced, not normalised away.** A ±20-strike ladder can miss a large
  share of a trending session; the ladder says so above 1%.

### Cost — measured, not assumed

Integration is ~O(bars²) and scales with the lattice, so tick size matters as much as bar count:
NIFTY 374 bars @ 0.1 tick = **430 ms**; CRUDEOIL 627 bars @ 1.0 tick = **106 ms**; synthetic 870
bars @ 0.1 = **920 ms**. `compute_ms` rides on every payload. The Concentration tab computes only
on the full desk poll, never on the multi-index strip.

### Test status

Full suite **818 passed, 0 failed** — 769 before, +37 vendored engine, +12 adapter.

## Session 2026-08-20 — Pin strength: `pin_source`, `daily_pin`, and the measure

Steps 1–2 of a plan to answer "is the pin holding?" on the Gamma Density desk. Analysis
only — nothing under `broker/` / `execution/` / `risk/`.

### The finding

`compute_gamma_concentration` picks `pin_strike` by three different rules — dominant strike
(`top1_share ≥ 0.18`), else the call/put **wall midpoint**, else the **ATM strike** — and all
three emitted a bare number. The ATM placeholder sits next to spot by construction, so
`pin_stable` reported it as rock-steady precisely when no pin existed: a false positive in the
only case that matters. A synthetic check makes it concrete — a peaked book, a flat book with
walls, and a flat book without walls all return `pin = 24600.0`, now separable as
`dominant` / `wall_mid` / `atm`.

### Shipped

1. **`pin_source`** on the concentration payload (`dominant` | `wall_mid` | `atm` | `fallback`)
   plus the TS type. Pin-strength logic must gate on it.
2. **`daily_pin` store** (`record_pin_sample`, `get_daily_pin_series`, `pin_hold_outcomes`) —
   bounded per-session checkpoints, default one per 30 min, 60 sessions retained, written from
   the snapshot under its own try/except so a store failure never costs the caller a snapshot.

**Why a new store rather than reusing `series`:** `append_history_point` prunes the intraday
trail to today on every write, so no multi-day intraday history exists. Without a surviving
record, "when the pin looked strong at midday, did it hold?" is unanswerable and every
threshold is a prior. Recording starts now so the measure can be calibrated rather than guessed.

**Stores inputs, not verdicts.** `held` is derived by `pin_hold_outcomes(hold_steps=…)` at read
time, so changing the tolerance re-reads history instead of invalidating it. Sessions with no
recorded close are skipped, never scored as failures.

### Shipped — the measure (`options/pin_lock.py`)

Pure functions, no I/O, no store imports. On the snapshot as `pin_lock`; window via
`?pin_window=15m|30m|60m|session` (default `30m`), selectable from the Concentration tab.

Two hard gates (`pin_source == "dominant"`, dealers long gamma for ≥80% of window ticks) then
five components — stability vs the **modal** pin, containment, crossings, flip room in σ, ΔOI on
the pin strike — plus a `breaker` level and plain-language `reasons`.

Three rules the implementation holds to, each worth keeping:

- **No blended score.** Weights are unjustifiable until `daily_pin` can fit them.
  `test_no_blended_score_is_emitted` pins that decision so it cannot drift in later.
- **`null` means unmeasured, never failed.** Gates return `None`, not `False`, when there is
  nothing to judge — otherwise a quiet desk reads as a broken pin.
- **Containment/crossings use `chart_series` minutes, not GEX ticks.** The tick trail is
  deliberately gappy, so scoring it would understate time spent away from the pin.

The window anchors on the newest sample present rather than wall clock, so a stale snapshot
yields an empty window instead of scoring old data as current.

**Placement:** full panel on the **Concentration** tab (next to HHI and the Γ ladder, where the
inputs live); on **Profile**, the `PIN CANDIDATE` tile now leads with `pin_source` — an `atm` pin
reads *"ATM placeholder — not a gamma pin"* instead of "stable". That tile was the live
falsehood: it claimed stability most confidently exactly when no pin existed.

### Superseded, not deleted

`_pin_stability` (`pin_stable` / `pin_stability_pct`) predates this and is **not** the measure:
it compares the last **12 ticks** to the *current* pin, so it is tick-counted rather than
time-boxed — a polling gap stretches the window silently — and a pin that just moved reads
unstable while the new one establishes. Left in place because the Profile tile and
`ConcentrationBoard` still read it; `pin_lock` is the one to trust.

**Still open:** thresholds (`PIN_LONG_GAMMA_SHARE` 0.8, `PIN_CONTAINMENT_STEPS` 1.0,
`PIN_FLIP_ROOM_SIGMA` 1.0) are provisional. Revisit once `daily_pin` has ~20 sessions and
`pin_hold_outcomes()` can say which readings actually preceded a pin that held.

### Test status

Full suite **769 passed, 0 failed** (`tests/test_pin_lock.py` adds 11) — the date-drifted fixtures that failed on 2026-08-10/11 are
now fixed, so there is no tolerated-failure list. New coverage: `pin_source` across all four
rules in `test_gamma_hhi.py`; sample throttling, close tracking, session gating, bucket
isolation and read-time outcome derivation in `test_gamma_density_history.py`.

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
