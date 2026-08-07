# 3ST Improvement Roadmap — lessons from NautilusTrader

**Source:** [nautechsystems/nautilus_trader](https://github.com/nautechsystems/nautilus_trader) — an open-source, production-grade, Rust-native, event-driven trading engine with Python as the control plane.

This doc distills the architectural ideas from NautilusTrader that are worth adopting in **3ST** (FastAPI + Kite Connect + React), tailored to our scope. It is a living roadmap: each item has a **status** and, where started, a **concrete design**.

> Scope note: 3ST is a Python + React retail platform for Indian markets (Kite). We deliberately do **not** adopt Nautilus's Rust core, multi-venue/DEX/crypto machinery, 128-bit precision, or "no UI" stance — our React UI is a core strength.

---

## 1. Architecture comparison

| Concern | NautilusTrader | 3ST today | Target |
|---|---|---|---|
| Core loop | Event-driven (tick → bus → handlers) | Polling scheduler (~60s ticks, 15–20s exit scans) | Event-driven exits off the WS feed |
| Backtest vs live | Identical strategy code, deterministic | Separate paths (`backtest_engine.py` vs `execution/*`) | Shared signal/exit module |
| Domain model | Typed `Price`/`Quantity`/`Money`, fail-fast on NaN | `dict[str, Any]` everywhere | Typed `domain/` models + fail-fast |
| Order/position truth | `ExecutionEngine` reconciles venue state | In-memory; restart loses tracking | Broker-as-source-of-truth reconciliation |
| Risk | `RiskEngine` (limits, notional, order rate) | `risk/limits.py` + `arming.py` | + rate limiting + reconciliation |
| Lifecycle | Explicit FSM (READY/RUNNING/DEGRADED/FAULTED…) | Implicit RUNNING/STOPPED | Explicit runner state incl. DEGRADED |
| Recovery | Crash-only: startup == recovery, idempotent | JSON files; stale-state bugs on restart | Idempotent + reconcile on boot |
| Order types | IOC/FOK/GTD, OCO/OTO brackets, reduce-only | MARKET/LIMIT + manual exit-priority scan | First-class bracket/OCO abstraction |

---

## 2. Prioritized backlog

Status: ☐ todo · ◐ in progress · ☑ done

### High impact — reliability & correctness
- ◐ **#2 Typed domain model + fail-fast on bad market data** — new `domain/` package; reject NaN/None/≤0 prices before they drive exits. *(phase 1 done — see §4)*
- ☑ **#1 State reconciliation on startup + periodic** — broker (Kite) as source of truth for `3ST*`-tagged orders/positions. *(implemented — see §3)*
- ◐ **#3 Idempotent order submission** — deterministic client tag/ID per intended entry to prevent double-submit on retry/restart. *(partial: `order_tag()` exists + reconciliation dedupes by symbol/tag; full client-order-ID dedupe pending.)*

### High impact — architecture
- ☐ **#4 Internal event bus (pub/sub)** — WS tick → exit evaluator, replacing the polling scan (build on `execution/ltp_cache.py`).
- ☐ **#5 Research-to-live parity** — extract 3ST signal/exit logic into one module used by both `backtest_engine.py` and live runners.

### Medium impact
- ☐ **#6 Explicit component state machine** — runner states incl. DEGRADED/FAULTED surfaced to UI.
- ☐ **#7 Bracket / OCO exit abstraction** — model TSL → target → ST-zone → force-exit as a first-class reduce-only bracket.
- ☐ **#8 Order-rate throttler** around Kite calls.
- ☐ **#9 Ports & adapters split** — separate `DataClient` (candles/OI/quotes) from `ExecutionClient` (orders) behind `broker/base.py`.

### Lower impact / longer term
- ☐ **#10 Local data catalog (Parquet)** for candles + OI snapshots.
- ☐ **#11 CI + test rigor** — GitHub Actions running pytest + ruff + type-check; property tests for indicator/exit math.
- ☐ **#12 Realistic backtest fills** — slippage/fees; unify backtest fill model with `PaperBroker`.

### Explicitly out of scope
- Rust rewrite · removing the UI · multi-venue/DEX/crypto · 128-bit precision · Redis message bus.

---

## 3. Design — #1 State reconciliation (crash-only recovery) — *implemented*

**Problem (documented in `CONVERSATION_SUMMARY.md`):** active orders are tracked in-memory, so stopping/restarting the runner loses tracking while broker orders/positions still exist; separate `PaperBroker` instances produced empty/stale positions.

**Principle (from Nautilus):** *startup path == recovery path*. The broker is the source of truth; local JSON is a cache we rebuild from the venue.

### Implementation (`execution/reconcile.py`)
- `reconcile_from_broker(broker, *, items, global_mode, apply_changes, adopt_orphans)` — broker-agnostic core (unit-tested with a fake broker). Uses `domain.open_positions_from_kite` / `open_orders_from_kite`.
- `reconcile_live_desk(*, apply_changes, adopt_orphans)` — resolves `KiteBroker`, gathers active watchlist items; **skips cleanly** when no Kite session.
- `maybe_reconcile_periodic(min_interval_sec=60)` — throttled scheduler hook (live mode only).
- **Rules:** local-active + broker-flat + no working order → close locally (`exit_reason="reconcile: broker flat"`); local-active + broker-open → refresh `entry_qty`/`entry_price` (broker wins); local-active + working order → leave active; broker-open + no local record → orphan (adopt only when `adopt_orphans=True`).
- **Safety:** never places/cancels at the venue; only live trades (`trade_mode="live"`) are eligible for closing so paper trades are never touched; idempotent. `reconcile_live_desk` skips entirely in **paper mode** and when there is no Kite session. **Fail-safe reads:** if the broker positions read fails it **aborts with no mutations** (a transient API/token error must never be read as "broker flat" and close live trades); if the orders read fails, flat items go to `close_skipped` instead of being closed.
- **Hooks:** API lifespan startup (`api/main.py`), scheduler loop (`execution/scheduler.py`), and endpoints `GET /live/reconcile` (dry-run) + `POST /live/reconcile` (apply, `{adopt_orphans}`).
- **Tests:** `tests/test_reconcile.py` (close-stale, matched-refresh, pending-order, paper-never-closed, orphan report/adopt, dry-run).

### Approach
1. **Reconciliation service** `execution/reconcile.py`:
   - `reconcile_from_broker(broker) -> ReconcileReport`
   - Pull `broker.orders()` and `broker.positions()`, filter by `3ST*` tag / known instruments.
   - Parse into typed `Order`/`Position` (from §4).
   - Diff against local state (`watchlist.json`, `rolling_straddle_state.json`, `paper_broker.json`):
     - **Venue-open, local-missing** → adopt (rebuild local record).
     - **Local-open, venue-missing/closed** → mark closed locally (broker wins).
     - **Quantity/price mismatch** → broker value wins; log discrepancy.
2. **Hooks:**
   - API lifespan startup (`api/main.py`) → run reconciliation after Kite session is available.
   - Periodic task (every ~30–60s while `mode=live`) → drift correction.
   - After Kite login callback → immediate reconcile.
3. **Idempotency (#3):** entries carry a deterministic tag (`order_tag()` already exists in `order_executor.py`); reconciliation dedupes by tag+day so retries/restarts never double-count.
4. **Report** surfaced via a new `GET /live/reconcile` endpoint and shown on the Live Desk.

### Risks / notes
- Requires a live Kite session to test end-to-end; add unit tests with a fake broker first.
- Must be read-only w.r.t. the venue (never places/cancels during reconcile except explicit user action).

---

## 4. Design — #2 Typed domain model + fail-fast *(implemented, phase 1)*

**Problem:** order/position/quote data flows as `dict[str, Any]`; a missing/NaN price silently propagated (the `spot × 0.01` wrong-fill bug). Nautilus rejects invalid data at the boundary (fail-fast) and uses typed value objects.

### New package: `domain/`
| Module | Responsibility |
|---|---|
| `domain/errors.py` | `DomainError`, `InvalidMarketData`, `InvalidOrder` |
| `domain/validation.py` | Fail-fast helpers: `validate_price`, `validate_quantity`, `safe_price` (reject-and-skip), side/product coercion |
| `domain/models.py` | Typed `Instrument`, `Quote`, `Order`, `Fill`, `Position` (dataclasses) + `from_kite_*` parsers |
| `domain/__init__.py` | Public exports |

### Fail-fast policy (mirrors Nautilus)
- **Reject (raise `InvalidMarketData`)** for prices used in decisions: `None`, `NaN`, `±inf`, `<= 0`.
- **Skip (return `None` via `safe_price`)** at ingestion boundaries (WS/REST feed) so one bad tick never crashes the feed but also never poisons the cache.
- Quantities: reject non-integer / negative where only positive is valid.

### Domain types (phase 1)
- `OrderSide` = BUY | SELL, `Product` = MIS | NRML | CNC, `OrderType` = MARKET | LIMIT | SL | SL-M, `OrderStatus` enum.
- `Instrument(exchange, tradingsymbol, instrument_token, lot_size?)`.
- `Quote(instrument, last_price, ts)` — validated, always finite & positive.
- `Order(order_id, instrument, side, quantity, product, order_type, status, price?, average_price?, tag?, filled_quantity)` + `Order.from_kite(dict)`.
- `Position(instrument, quantity, average_price, last_price?, pnl?, product?)` + `Position.from_kite(dict)`; `is_open`, `direction`, `net_value`.

### Adoption (phase 1, this change)
- `execution/ltp_cache.py`: ingestion now uses `safe_price()` so non-finite/≤0 ticks are dropped from both the WS (`ingest_ws_ticks`) and REST (`_rest_fetch`) paths — the exact class of bug that caused wrong fills.

### Adoption (later phases)
- `execution/watchlist_exit_runner.py`: wrap LTP used in TSL/ST-zone/force-exit decisions in `validate_price` (fail-fast — better to skip a scan than exit at a garbage price).
- `#1 reconciliation`: parse `broker.orders()/positions()` via `Order.from_kite`/`Position.from_kite`.
- Gradually replace dict access in `order_executor.py` / `positions_view.py` / `desk_trades.py`.

### Tests
- `tests/test_domain.py`: validation rejects NaN/inf/None/≤0; `safe_price` skips; Kite parsers map real field names; `Position` helpers.

---

## 5. Changelog
- **2026-07-12** — Roadmap created. #2 phase 1 implemented: `domain/` package (errors, validation, models) + fail-fast LTP ingestion in `execution/ltp_cache.py` + `tests/test_domain.py`.
- **2026-07-12** — #1 reconciliation implemented: `execution/reconcile.py` (broker-as-source-of-truth), `GET/POST /live/reconcile`, startup + periodic scheduler hooks, `tests/test_reconcile.py`.
