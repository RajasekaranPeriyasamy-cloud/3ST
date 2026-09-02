# Options Arbitrage desk

**Route:** `/opt-arb` · **API prefix:** `/oarb` · **Backend:** `analysis/opt_arb/`

Scans for **model-free** option-to-option pricing violations across NFO, BFO and
MCX, prices them at executable bid/ask, and nets them against the full Indian
charge stack before showing anything.

**Scan and alert only. This desk never places an order.** It imports nothing
from `broker/`, `execution/` or `risk/`. Wiring a row to
`execution/order_router.submit_intent()` would be a live-trading-behaviour
change and needs the operator's sign-off first.

---

## What counts as arbitrage here

`tier: "A"` — model-free. No volatility model, no rate assumption, no view.
A violation is arbitrage before costs by construction.

| Family | Bound | Violation |
|---|---|---|
| `xcontract` | Big and mini options on the same underlying future, same expiry, same strike are the same claim per unit | any per-unit premium gap |
| `butterfly` | `C(K−w) − 2C(K) + C(K+w) ≥ 0`, and the fly is never worth more than `w` | buy below zero · sell above width |
| `vertical` | `0 ≤ C(K₁) − C(K₂) ≤ K₂ − K₁` | debit below zero · credit above width |
| `box` | `(C(K₁)−C(K₂)) + (P(K₂)−P(K₁)) = (K₂−K₁)·e^(−rT)` | long box below PV · short box above it |

Strike monotonicity needs no detector of its own — a call cheaper than a
higher-struck call *is* a vertical debit below zero.

`tier: "B"` — the spread has a real driver the scanner does not model (futures
carry, an expiry gap, physical settlement). Surfaced with a reason, never as
free money. `require_clean=true` (the default) drops these entirely.

## The MCX finding that shapes the whole desk

Big-vs-mini is only an identity when the two sides share an option expiry **and**
that expiry references the same futures month.

**This is a property of an expiry, not of a pair** — and getting that wrong is
the single most expensive mistake available here. Classified per expiry from the
live dump on 2026-08-25:

| Pair | Front month | Tradable expiries | |
|---|---|---|---|
| **CRUDEOIL / CRUDEOILM** | aligned | 09-17, 10-15 | ✅ Tier A throughout |
| **NATURALGAS / NATGASMINI** | aligned | 09-23, 10-23 | ✅ Tier A throughout |
| **GOLD / GOLDM** | 08-31 vs 08-28 → Oct-05 vs Sep-04 futures | **09-25** | ⚠️ carry in front, Tier A at 09-25 |
| SILVER / SILVERM | 08-28 vs 09-24 → Sep-04 vs Nov-30 | none | ❌ Tier B always |

Gold in the *front* month is a carry spread: two option expiries three days apart
pointing at futures a month apart. At ₹1,60,000/10 g that gap is four figures and
is not edge — which is exactly what a vendor big-vs-mini sheet prints at the top
of its gold panel without saying so. But at the **25 Sep** expiry both sides
share the date *and* the October future, so gold there is a genuine Tier A trade.

Silver is the opposite: the SILVER and SILVERM futures cycles never coincide, so
no shared expiry is ever clean.

`referenced_future()` resolves the reference as *the first future expiring on or
after the option expiry*. `expiry_status()` classifies one expiry;
`pair_status()` reports `clean_expiries` across all of them and keeps a separate
`front_clean` for the contract an operator looks at first. The scan targets the
first **clean** expiry rather than the first shared one — classifying by front
month alone silently excluded gold from the desk entirely.

## Why every price is a bid or an ask

A screen built on LTP is a fiction generator: LTP on an untraded wing can be
minutes old and nowhere near the book. A BUY leg is priced at the **ask**, a
SELL leg at the **bid**, and a row is dropped when either side is missing.
`require_depth` additionally drops rows the top of book cannot fill at the
requested size — the binding constraint on the MCX pairs, where offsetting one
big lot needs `ratio` mini lots and the mini book is thin.

`tests/test_opt_arb_detectors.py` gives every family a **negative control**: a
parity-exact book with a real bid-ask must produce zero rows. A mid-priced sheet
would light up on roughly every strike pair at half the spread.

## Costs are the gate, not a footnote

`analysis/opt_arb/costs.py`. A NIFTY four-leg box round-trips at **₹2–12 per
index point of lot** before any edge exists, and that is before the item most
screens omit:

> **Exercise STT is 0.125% of *intrinsic*** on cash-settled index options. A
> long box always finishes holding a leg struck at the *far* strike, whose
> intrinsic is unbounded in spot. A short box's long legs sit at the *near*
> strike and cost materially less. `box_exercise_cost` prices both.

MCX pays CTT (0.05% on option sell) and has no intrinsic exercise levy — ITM
contracts devolve into futures. NSE **stock** options are physically settled, so
a box carried to expiry is a delivery obligation; those rows are forced to
tier B with a warning.

Rate cards are overridable at runtime (`POST /oarb/config` with `rates`) and
persist to `data/opt_arb_config.json` — only fields that differ from the shipped
card are written, so a future correction to the built-in schedule is not
shadowed by a stale copy on disk. Verify against Zerodha's current charge list
before sizing anything real; `rates_asof` says what they were checked against.

## Quote budget

Everything is REST — there is no `KiteTicker` anywhere in this repo.
`fetch_quotes` batches at 500 instruments (Kite's per-call cap) and one fetch
per underlying/expiry feeds all three single-underlying detectors.

`universe.py` memoises its instrument frames against the dump's mtime, the same
guard `options/chain.py` uses. Without it a full sweep took **~48s** (the 113k-row
dump was rescanned five or six times per underlying, with a per-element
`pd.to_datetime` on top); with it, ~1.4s. Tests that swap the dump must call
`universe.clear_caches()` — the mtime does not move when a fixture substitutes
the frame.

## Big-vs-mini grid

`GET /oarb/xsheet` renders the same strike-by-strike worksheet a vendor sheet
does — STRIKE / BUY / SELL per strike, ATM highlighted, a threshold that
highlights cells — with three differences that change what it means:

* **Cells are net of charges.** A raw spread that prints green at ₹300 is a loss
  once a round trip on both legs is paid for.
* **BUY and SELL are not mirror images.** Each is priced at the side of the book
  you would actually hit, so the gap between them *is* the round-trip cost. On a
  mid-priced sheet they are exact negatives, which hides it.
* **The header basis is named.** A vendor panel prints one number at the top and
  leaves you to assume it is opportunity. Here it is measured as the gap between
  the two contracts' own implied forwards, and labelled as carry whenever the
  expiry being shown references different futures months.

The grid defaults to a **clean** expiry when one exists, so the gold panel opens
on 25 Sep rather than on the front month where the number is carry. It windows
±12 strikes around the money: crude lists ~190 strikes and the far ITM ones carry
stale books whose cells run to six figures.

No depth gate here — a worksheet wants every cell, and `max_lots` is in the cell
tooltip. The ranked Opportunities tab is the gated view.

## Payoff chart

Every scan row carries a `payoff` block ([payoff.py](../../analysis/opt_arb/payoff.py)),
and the page opens the top-ranked row automatically so the curve is visible
without hunting for a click target.

Two lines: the dashed one is the structure's own expiry payoff, the solid one is
after charges. For a genuine arbitrage the solid line sits **above zero across
the whole range** — flat for a cross-contract or box row, a tent for a butterfly
bought below zero. A curve that dips below zero anywhere is the fastest way to
see that a row is not the risk-free trade its ranking implied.

The curve is piecewise linear with kinks only at the strikes, so every strike is
forced into the sample grid; that is what makes the interpolated breakevens
exact rather than approximate. `risk_free` means the net curve never touches
zero — **not** that it is flat, or the whole butterfly family would report as
risky.

`assumptions` states what you have to accept for the picture to be true. The one
that matters: a Tier B cross-contract row's flat line assumes a convergence
between two different futures months that **will not happen**, and says so.

`POST /oarb/payoff` prices an arbitrary leg set the same way, for a structure
assembled by hand.

## API

| Route | Purpose |
|---|---|
| `GET /oarb/config` · `POST /oarb/config` · `POST /oarb/config/reset` | scan knobs + charge rates |
| `GET /oarb/pairs` | big/mini registry with live clean-vs-carry classification (no quotes needed) |
| `GET /oarb/scan` | full sweep, ranked by net edge |
| `GET /oarb/underlying` | butterfly/vertical/box for one underlying+expiry |
| `GET /oarb/xcontract` | big-vs-mini only, optionally pinned to a pair |
| `GET /oarb/xsheet` | big-vs-mini strike grid (the vendor-sheet view), net of charges |
| `GET /oarb/sheet` | combo-butterfly worksheet (body strikes × wing widths) |
| `GET /oarb/expiries` | listed expiries + settlement type |
| `GET /oarb/costs` | single-leg charge preview |
| `POST /oarb/payoff` | expiry payoff curve for an arbitrary leg set |

The prefix is `/oarb`, not `/options-arbitrage`: `api.ui_static.is_api_path`
matches on `startswith` and `/options` is already an API prefix, so the SPA page
lives at `/opt-arb` and a hard browser load of it resolves to the app.

## What this desk deliberately does not do

- **No orders.** See above.
- **No ratio-spread credit screen.** A 1:2 or 1:4 ratio entered for a credit is
  not arbitrage — it is an unlimited-tail short that happens to be free on one
  side. Putting it on an arbitrage page trains you to read it as risk-free.
- **No dispersion / index-vs-basket.** Index implied vol being structurally rich
  to the Nifty-50 basket is real, but it is correlation trading, not a bound
  violation, and it belongs on its own page.
- **No `INDEX_OPTIONS` entries for GOLD/SILVER.** Adding them there would switch
  on 30-second Gamma-Density and OI-VAR background sampling for those names as a
  side effect (`config.ANALYTICS_HISTORY_SAMPLE_UNDERLYINGS` is filtered against
  `INDEX_OPTIONS` at call time). This desk keeps its own universe instead.
