# Premium Book — Operator Guide

**UI:** `/premium-book` · **Code:** `execution/premium_book_runner.py`, `execution/premium_book_store.py`  
**Role:** Sell premium (credit verticals) or optional Buy & Hold — driven by **underlying** 3ST (ST1+ST2 entry, ST1 exit).

Use **Rolling Straddle** for ATM short straddles / per-leg option-chart signals. Premium Book does **not** replace RS.

---

## 1. Auto structure (sell premium)

With **Auto structure from ST1+ST2** ON (default) and trade bias **Sell premium**:

| Underlying ST1+ST2 | Structure | Action |
|--------------------|-----------|--------|
| **Above** (bull zone) | `bull_put` | Sell put credit vertical |
| **Below** (bear zone) | `bear_call` | Sell call credit vertical |
| **Flat / no signal / whipsaw** | — | **Sit out** — no new entry |

Reasons you may see in status / logs:

| Reason | Meaning |
|--------|---------|
| `auto_above_st1_st2` | Live pick = bull put |
| `auto_below_st1_st2` | Live pick = bear call |
| `auto_flat_no_entry` | No directional lock → sit out |
| `skip_whipsaw` | Conflicting ST1/ST2 → sit out |

The Structure dropdown is a **manual override / fallback** only when auto is OFF. With auto ON, trust **Active now** / `active_structure` + `auto_structure_reason`, not the dropdown label.

### Buy & Hold (optional, default OFF)

| Direction | Structure |
|-----------|-----------|
| Above | `bull_call` |
| Below | `bear_put` |
| Flat | `long_strangle` |

Use **Revoke Buy & Hold** to return to sell premium and flatten buy-book packages.

---

## 2. Short straddle / strangle — retired for new entries

Sell book **no longer opens** new `short_straddle` / `short_strangle` packages.

- Flat / whipsaw → **sit out** (not sideways short vol).
- Legacy open CE/PE short legs can still be managed / exited / SL-converted to a vertical.
- Log: `entry_skipped_legacy_structure` if a legacy template is still selected.
- Config migration maps retired structures → default `bull_put`.

**ATM straddles:** use [Rolling Straddle](../rolling-straddle/).

---

## 3. Signals vs risk charts

| What | Source | Used for |
|------|--------|----------|
| **Entry / ST1 structure exit** | Underlying **index or futures** candles (MCX crude → front-month future) | ST1/ST2/ADX zones |
| **ATR / SL / force** | Option **LTP** (package mid or leg LTP) | Tick-responsive risk |

Do not expect option-premium SuperTrend to match the Premium Book signal — signals are on the **underlying** chart.

---

## 4. Entry & exit gates (candle-close)

Defaults that matter for whipsaw control:

| Flag | Default | Behavior |
|------|---------|----------|
| `entry_require_st1_st2` | ON | Entry needs ST1 zone (+ADX) **and** ST1+ST2 same direction |
| `exit_on_bar_close_only` | ON | ST1 structure entry/exit: **one decision per TF bar** |
| `entry_exit_enabled` | OFF | TF close vs entry (shorts) — leave off unless you want it |
| `adx_enabled` | ON | ADX filter on **entry** |
| ADX period / threshold | **14 / 20** | Same as desk defaults (`DEFAULT_ADX`) |

**Still live every tick (not deferred to next bar):** Force exit, ATR trail, fixed SL.

ST3 does **not** gate Premium Book entry even if enabled in config.

### `entry_skipped_same_bar`

Logged when the desk already entered / exited / converted on this candle timestamp. Means: **wait for the next bar close** — not “signal broken.”

Related:

| Event | Meaning |
|-------|---------|
| `entry_skipped_same_bar` | Entry deferred — already acted this bar |
| `{ce\|pe}_skipped_same_bar` | ST1 exit deferred same bar |
| `entry_reentry_cooldown` / `reentry_cooldown` | After exit, no re-entry until a **newer** bar timestamp |
| `skipped_same_bar` (exit reason) | Internal: ST1 skip; ATR/force can still fire |

Desk-wide bar stamps merge package + CE + PE so package↔leg paths cannot churn on the same candle.

---

## 5. ST1 exit mapping (naked shorts & verticals)

### Credit verticals (packages)

| Structure | ST1 exit when |
|-----------|----------------|
| `bull_put` | Short zone / short ready (price **above** ST1 adverse for put credit) |
| `bear_call` | Long zone / long ready (price **below** ST1 adverse for call credit) |

### Naked short legs (legacy strangle/straddle CE/PE)

Underlying zone that **hurts** the short option:

| Short leg | Adverse ST1 zone |
|-----------|------------------|
| **CE** | `short_zone_exit` (price **above** ST1) |
| **PE** | `long_zone_exit` (price **below** ST1) |

Using the **opposite** flags was a bug: short CE would ST1-exit while price was still **below** ST1, then re-enter on flat/whipsaw → **CE churn**.

Regression coverage: `tests/test_premium_book_bar_churn.py` (`test_naked_short_ce_st1_uses_adverse_zone_not_below_st1`).

Converted verticals on a leg use the package-style mapping for that structure.

---

## 6. Log / status glossary

### `ce_exit_blocked_disarm` / `package_exit_blocked_disarm`

Live mode + **DISARMED**: algo will **not** place exit orders on Kite.

- Local state may still show the package/leg **open**.
- If Kite is already flat, runner may **reconcile** local state to flat (`*_exit_reconciled_broker_flat`) without ARM.
- If Kite still has size → you must **ARM** (or flatten on Kite), then Force close / Close.

**Local state ≠ Kite.** Always confirm positions on Kite when logs say `*_exit_blocked_disarm`.

### Signal vs “Bull Ready”

| UI / state | Meaning |
|------------|---------|
| **Signal: long (bull zone)** | Underlying `long_ready` / bull zone for auto pick |
| **Signal: short (bear zone)** | Underlying `short_ready` / bear zone |
| **Active structure** | Live auto pick (`bull_put` / `bear_call` / null) |
| Structure dropdown “Bull put” | Config preference only — **not** “ready to trade” |

Earlier UI confusion: dropdown default looked like “Bull ready.” Fixed by showing live `active_structure` / reason and not falling back to the dropdown when auto is ON.

### Package vs CE/PE status

| Field | Meaning |
|-------|---------|
| **Package** | Multi-leg vertical (or buy book) — primary sell path |
| **CE / PE** | Naked short legs (legacy) or converted wing bookkeeping |

A flat Package with open CE/PE (or the reverse) usually means **legacy short legs** or a partial flatten — check Activity log + Kite.

---

## 7. Dual-open toggle (historical UI bug)

**Symptom:** “Allow dual open” unchecked itself when changing other controls.

**Cause:** Status/config poll overwrote the checkbox with a stale default.

**Note:** Dual-open mainly mattered for short strangle/straddle. Those templates are **retired** for new sell entries; verticals use the Package path. Leave dual-open concerns for any residual legacy legs / older UI builds.

---

## 8. Orphan / stuck CE — clear steps

When local CE looks open but exits are blocked, or Kite and UI disagree:

1. Confirm on **Kite** whether the CE (and any wing) is still open.
2. On Premium Book (or Algo Execution / Rolling Straddle if the orphan is RS-owned): ensure mode **Live**.
3. **ARM** live orders.
4. **Force close** (Premium Book) or **Close all** (Rolling Straddle) to flatten managed legs.
5. If still open on Kite only: close manually on Kite, then refresh — runner should reconcile local state when broker is flat (even if disarmed, when symbols are confirmed flat).
6. Re-check Activity log for `*_exit_blocked_disarm` vs `*_exit_reconciled_broker_flat`.

Orphan badges on **Algo Execution** count unlinked broker positions — clear those from the desk that owns them (RS vs Premium Book).

---

## 9. Exit ladder (order of evaluation)

For open packages / short legs (simplified):

1. **Force** (session force time / Force close) — always
2. **ATR** (if `tsl_mode = ATR`) — tick / LTP
3. **Fixed SL** (if enabled) — tick / LTP  
4. **ST1 structure** — bar-gated when `exit_on_bar_close_only`
5. Optional **entry-exit** — only if enabled

Optional **SL → credit vertical convert** (`convert_sl_to_spread`) applies to legacy naked shorts on SL reasons, not as the primary vertical path.

---

## 10. Quick operator checklist

1. Kite logged in · Paper first, then Live + **ARM** only when ready  
2. Underlying + expiry + timeframe set · **Save**  
3. Sell premium · Auto structure ON · ADX 14/20 if matching desk defaults  
4. **Start** runner · watch Signal + Active structure + Package  
5. Flat/whipsaw → expect **sit out**, not strangle  
6. Same-bar skips in the log → wait for next candle  
7. `*_exit_blocked_disarm` → ARM or flatten on Kite  
8. Want ATM straddle → **Rolling Straddle**, not Premium Book  

---

## Code & tests

| Path | Role |
|------|------|
| `execution/premium_book_runner.py` | Tick loop, auto structure, exits |
| `execution/premium_book_store.py` | Config / state / log persistence |
| `Pixel Perfect UI/src/routes/premium-book.tsx` | Desk UI |
| `tests/test_premium_book.py` | Structure / store behavior |
| `tests/test_premium_book_bar_churn.py` | Same-bar gates + CE ST1 mapping |

Implementation plan (Cursor, not in `_plans/`): `~/.cursor/plans/premium_book_engine_*.plan.md` — durable home for operators is **this folder**.

---

## Session sign-off — 2026-07-20

Full bullet list + reminders: **[SESSION-2026-07-20.md](SESSION-2026-07-20.md)**.

**Shipped (this guide):** auto structure · strangle/straddle retired for new sells · candle-close / same-bar gates · CE ST1 adverse-zone fix · disarm orphan CE + broker-flat reconcile · ADX 14/20 · Signal vs dropdown · underlying futures vs option LTP · dual-open poll note.

**Reminders:** restart API after pull · orphan CE → Live → **ARM** → Force close / Close all · flat = sit out · new sells = verticals only.

**Excluded from this save:** Mumbai VPS Latency Plan (not documented / not claimed shipped here).
