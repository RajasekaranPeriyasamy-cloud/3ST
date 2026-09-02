# The live-order path

Everything that can move real money passes through the chain below. Read it
before adding any code that places, cancels, or sizes an order.

```
runner / API route
        │
        ▼
execution/order_executor.py     ← place_leg_order()  or  place_leg_to_target()
        │   acquire_symbol_lock(exchange, symbol, product)   ← concurrency gate
        │   risk_limits.check_order(...)                     ← risk gate
        ▼
broker/kite_broker.py           ← KiteBroker.place_order()
        │   require_armed_for_live()                         ← ARM gate
        ▼
        Kite Connect (static-IP egress)
```

Three gates, three different files. A code path that skips a layer silently
loses that layer's protection while still *looking* correct.

---

## The two entry points

Both live in `execution/order_executor.py` and are the **only** functions
permitted to reach a broker's `place_order`.

### `place_leg_order(broker, leg, *, transaction_type, order_type="MARKET", price=None, tag="3ST", product="MIS")`
`execution/order_executor.py:110` — fire-and-forget single leg. Quantity comes
from `leg["quantity"]`.

### `place_leg_to_target(broker, leg, *, target_qty, order_type="MARKET", price=None, tag="3ST", product="MIS")`
`execution/order_executor.py:170` — **smart order**. Reads the signed net
quantity from broker truth, computes the delta via `plan_order_to_target`, and
returns a `noop` result if already at target. Negative target = short, positive
= long, zero = flat.

Prefer `place_leg_to_target` for anything that should be idempotent-ish
(reconcile-driven resizing, exits to flat). It cannot double-fire when re-run
against a broker already at target.

**Asymmetry to know about:** `place_leg_to_target` calls
`invalidate_position_cache(broker)` after placing; `place_leg_order` does not.
If you place with `place_leg_order` and immediately read positions in the same
tick, you may see a stale cached snapshot.

`execution/order_router.py:submit_intent()` wraps these same two functions
rather than reimplementing the gates — it imports them directly at
`execution/order_router.py:27`. **Keep it that way.** If you extend the router,
extend it by calling the executor.

---

## Gate 1 — the symbol lock

`acquire_symbol_lock(exchange, tradingsymbol, product)`
(`broker/execution_support.py:23`) returns a `threading.Lock` keyed on the
instrument triple. Both executor functions take it before doing anything else
and hold it across the risk check *and* the broker call — so two runners cannot
interleave "check risk" and "place" on the same symbol.

The lock is process-local. It is correct only because 3ST runs one process; see
[stores-and-state.md](stores-and-state.md).

## Gate 2 — the risk check

`risk.limits.check_order(*, qty, product, exchange, open_positions, is_closing=False)`
(`risk/limits.py:85`) raises `RuntimeError` on breach. It checks, in order:

| Check | Default | Notes |
|---|---|---|
| orders/minute | 10 | rolling 60s window |
| `0 < qty <= max_qty` | 500 | |
| product allowed | `MIS`, `NRML` | case-insensitive |
| exchange allowed | `NSE, BSE, NFO, BFO, MCX, NCO` | case-insensitive |
| open positions | 6 | **skipped when `is_closing=True`** |
| daily loss | 10,000 | fed by `record_pnl()` |

Two subtleties:

- `is_closing` relaxes **only** the open-position cap — never the qty, product,
  exchange, rate, or loss checks. An exit can always be attempted even at the
  position cap, but cannot exceed size limits.
- The rate-limit timestamp is appended **at the end**, only on success. A
  rejected order does not consume rate budget.

`place_leg_order` derives `is_closing` from `_is_closing_leg` (broker net qty vs
transaction direction); `place_leg_to_target` derives it from
`_is_reducing_toward_target`. Don't hand-pass `is_closing=True` to get past the
cap.

Only `max_qty`, `max_open_positions`, `max_daily_loss`, and
`max_orders_per_minute` persist to `data/risk_limits.json` (`_PERSIST_KEYS`).
The allow-lists are code defaults — changing them at runtime does not survive a
restart.

## Gate 3 — the ARM gate

`require_armed_for_live()` (`execution/arming.py:106`) raises unless mode is not
`live`, or state is armed. Default is **DISARMED**, persisted to
`data/arm_state.json` so it survives an API restart.

It is called from exactly two places, both in `broker/kite_broker.py`:
`place_order` (`:89`) and `cancel_order` (`:121`).

Two things that surprise people:

- **Panic mode deliberately bypasses the ARM gate.** `require_armed_for_live()`
  returns early when `execution.panic.is_panic_active()`. This is intentional —
  the panic path must be able to cancel and flatten while disarmed. Do not
  "fix" it.
- **`PaperBroker.place_order` has no ARM gate**, by design. Paper mode is the
  safe rehearsal surface; that is what makes it usable for verification.

The `Broker` ABC (`broker/base.py`) declares only `place_order`, `cancel_order`,
`positions`, `orders`, `ltp`. There is no `modify_order` — so there is no third
mutating method to forget to gate. If you add one, gate it.

---

## Order tags

`order_tag(leg_key, kind)` (`execution/order_executor.py:258`):

```
3ST-{LEG_KEY}-{YYYYMMDD}-{kind}     truncated to 20 chars
```

Kite caps tags at 20 characters, and truncation happens **twice** — once in
`order_tag()` and again in `OrderRequest(tag=tag[:20])`. A long leg key
therefore loses the `kind` suffix silently; don't rely on `kind` surviving for
long keys.

Reconcile and orphan detection match on this prefix
(`open_orders_from_kite(..., only_3st=True)`). **Changing the format means
updating every matcher** — grep for `3ST-` before touching it.

---

## Reconcile

`reconcile_from_broker(broker, *, items, global_mode="live", apply_changes=True, adopt_orphans=False)`
(`execution/reconcile.py:55`) is broker-agnostic so it can be unit-tested with a
fake broker.

The safety property, from the comment at `execution/reconcile.py:96`: without a
trustworthy positions snapshot nothing can be decided, so a transient broker
error must never be read as "broker flat" — it aborts with no mutations, setting
`report["aborted"]` and returning immediately.

Preserve this. Any new reconcile-shaped logic needs the same fail-closed
behaviour. Note the asymmetry: a **positions** read failure aborts; an
**orders** read failure only records an error and continues with an empty order
list.

Orphans (broker positions with no local record) land in
`report["orphan_positions"]` and are surfaced to the UI. They are adopted only
when the operator asks — `adopt_orphans` defaults to `False`.

---

## The position ledger is not live yet

`execution/position_ledger.py`, `order_router.py`, and `signal_bus.py` were
added 2026-07-25 and are **additive and currently inert** — no runner calls
`submit_intent()`. `data/position_ledger.json` is not populated. The execution
queue still reads the `rolling_straddle` and `watchlist` stores directly
(`execution/execution_queue.py`).

Do not assume the ledger knows what is open. Migrating a runner onto the router
is a live-trading-behaviour change: paper-test it and get operator sign-off.

---

## Checklist for any order-path change

- [ ] Does it reach Kite through `KiteBroker`? (else: no ARM gate)
- [ ] Does it go through `place_leg_order` / `place_leg_to_target`? (else: no risk gate)
- [ ] Is placement inside `acquire_symbol_lock`?
- [ ] Does a failed broker read abort rather than assume flat?
- [ ] Does the tag still match what reconcile greps for?
- [ ] `pytest tests/` green — especially `test_order_executor.py`,
      `test_risk_limits.py`, `test_order_router.py`, `test_reconcile*.py`,
      `test_arm_persistence.py`, `test_panic.py`
- [ ] Verified in **paper mode**, and said so
