---
name: 3st-desk-engineering
description: Engineering playbook for the 3ST options-trading desk (FastAPI + Zerodha Kite + Pixel Perfect UI SPA). Use this skill for ANY code change inside C:\Dev\3ST — placing or routing orders, touching broker/ execution/ risk/, adding or modifying an analysis desk (backend module + API route + SPA page), adding a JSON-backed store, writing or repairing tests, or debugging a desk that looks stale, frozen, or unchanged after an edit. Triggers on "add a desk", "new runner", "place an order", "ARM / DISARM", "risk limit", "reconcile", "order tag", "symbol lock", "the UI didn't update", "tests are hitting Kite", "why is my route 404ing", "new store under data/". This system places real orders with real money — prefer this skill over improvising.
---

# 3ST Desk Engineering

A procedural playbook for changing 3ST safely. It is a **map, not a copy**: it
routes you to the authoritative source file and states the rule that file
enforces. When this skill and a source file disagree, the source file wins —
and the disagreement is a bug in this skill worth fixing.

3ST is single-user, single-process, single-Kite-session. There is no database,
no auth layer, and no second operator to catch a mistake. Every safety property
below is enforced by exactly one function in exactly one place, which is what
makes them easy to bypass by accident.

---

## 0. Pre-flight

Read [`CLAUDE.md`](../../CLAUDE.md) (repo root) for the desk inventory and
architecture, and the top of [`docs/CONVERSATION_SUMMARY.md`](../../docs/CONVERSATION_SUMMARY.md)
for what changed most recently. Treat `CONVERSATION_SUMMARY.md` as more current
than any dated review doc — in particular `docs/review/3ST_Project_Review_Gaps.md`
is stale and still claims there is no live order execution.

Then pick your lane:

| You are about to… | Read first |
|---|---|
| Place, route, cancel, or size an order; touch `broker/`, `execution/`, `risk/` | [references/order-path.md](references/order-path.md) |
| Add or change a desk — backend module, API route, SPA page | [references/adding-a-desk.md](references/adding-a-desk.md) |
| Add or change a `data/*.json` store, or any module-level state | [references/stores-and-state.md](references/stores-and-state.md) |
| Write, fix, or debug a test | [references/testing-offline.md](references/testing-offline.md) |

Do not load all four. Load the one that matches.

---

## 1. The four invariants

These are load-bearing. Breaking any one of them can place an unintended live
order or flatten real positions.

**1. The ARM gate lives in the broker, not in callers.**
`KiteBroker.place_order` and `.cancel_order` each call `require_armed_for_live()`
as their first statement ([`broker/kite_broker.py:89`](../../broker/kite_broker.py#L89),
[`:121`](../../broker/kite_broker.py#L121)). Any new code path that reaches Kite
without going through `KiteBroker` is an ungated path. Never construct a raw
`kiteconnect` client to place an order.

**2. Risk checks are reachable only through two functions.**
`risk.limits.check_order` is invoked from `place_leg_order` and
`place_leg_to_target` in [`execution/order_executor.py`](../../execution/order_executor.py)
— nowhere else. A runner that calls `broker.place_order()` directly is
unrisk-gated even though it is still ARM-gated. Runners call the executor.

**3. Every order placement is wrapped in a per-symbol lock.**
`acquire_symbol_lock(exchange, tradingsymbol, product)`
([`broker/execution_support.py:23`](../../broker/execution_support.py#L23)) prevents
two concurrent runners double-ordering the same instrument. The executor already
does this; if you add a placement path, you must.

**4. On reconcile, the broker is the source of truth — and an unreadable broker
is not a flat broker.** If `broker.positions()` raises, `reconcile_from_broker`
aborts with zero mutations ([`execution/reconcile.py:96`](../../execution/reconcile.py#L96)).
Never "fail open" here: interpreting a transient API error as "no positions"
would close every live leg. Orphans — broker positions with no local record —
are surfaced, never auto-adopted.

---

## 2. Working rules

- **Restart the API after any change under `execution/` or `analysis/equity_report/`.**
  `uvicorn --reload` does not reliably pick these up. Behaviour that looks stale
  usually is. `GET /health` confirms the restart took.
- **Read config through `settings.env()`**, never `os.getenv` directly —
  `settings.env()` loads `.env`, and a bare `os.getenv` silently inherits
  whichever shell launched uvicorn.
- **Two surfaces serve the UI and they are not equivalent.** Port 8080 is the
  Vite dev server (live source); port 8001 is FastAPI serving a *prebuilt*
  bundle. Verifying on 8080 alone proves nothing about the app as normally
  opened. See [references/adding-a-desk.md](references/adding-a-desk.md).
- **Stop the API before `npm run build`** — FastAPI holds a Windows directory
  lock on `.output/public/assets` and Vite fails with `EBUSY`, potentially
  leaving a half-written bundle. `.output/` is gitignored and not recoverable.
- **Don't add to the lint backlog.** ~90 pre-existing ruff findings predate the
  config, which is why CI's `lint` job is non-blocking. New code should be
  clean; skip drive-by cleanups of files you aren't already editing.

---

## 3. Definition of done

A change is not done until:

1. `pytest tests/` passes. The suite is fully green and runs offline in ~25s —
   **if you see failures, they are real.** There is no tolerated-failure list.
2. If the change touched `broker/`, `execution/`, or `risk/`: it has been
   exercised in **paper mode**, and you have said so explicitly. Prefer a test
   over a live-mode check for anything not already covered.
3. If the change touched the SPA: `npm run build` has been run (API stopped
   first) so port 8001 serves it, not just port 8000/8080.
4. If the change alters live-trading behaviour — migrating a runner to
   `order_router`, changing an exit rule, changing the order-tag format —
   **confirm with the operator before shipping.** That is a decision, not an
   implementation detail.
5. Any *why* worth keeping is recorded in `docs/CONVERSATION_SUMMARY.md`, not
   restated into a second doc.
