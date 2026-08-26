# Volume Footprint — decision log

Every choice behind this desk, with the reasoning, so the *why* survives the
session it was decided in. Dated **2026-08-20**. Where a decision is still
provisional it says so.

Read alongside [README.md](README.md) (how the desk works) and the
`2026-08-20 (later)` entry in `docs/CONVERSATION_SUMMARY.md` (what shipped).

---

## The question that started it

> Combine the volume footprint with GEX.

Three sub-asks: put it on the Concentration tab, merge it with the cumulative-Γ
ladder if possible, and turn the existing footprint folder into a pinned desk.

## What made it non-trivial

Four constraints, established before any code was written:

**1. The buy/sell split is a model, not a measurement.** A real footprint needs
per-tick aggressor data. The engine's Geometric mode infers direction from where
each candle closed in its range — and two very different order-flow paths produce
the same candle. A repo-wide search confirmed **no tick recorder exists**:
`KiteTicker` appears only in `execution/survivor_runner.py`,
`execution/kite_strategy_adapter.py` and `.venv/autobahn`. The Intrabar engine
needs sub-minute history Kite does not serve. So Geometric is the ceiling, and
`tilt`, `overlap` and imbalance are structural estimates. Not a stylistic hedge —
a hard limit on what this feature can ever claim.

**2. Volume is on the future; strikes are on the index.** Cash-index candles
carry no volume. Measured live the same day, the NIFTY basis was **71.55 points —
1.4 strike steps**. An unadjusted overlay would have put the POC a strike and a
half from where business actually happened and looked entirely plausible.

**3. ~1000× granularity gap.** Footprint rows are one mintick (0.05–1.0); strikes
are 50–100.

**4. Cumulative vs instantaneous.** A volume profile accumulates; gamma is a
snapshot that re-prices every poll.

## Why merge at all

Not "two charts on one axis" — the merge answers something neither side can:

- **Gamma says where dealers *must* hedge; volume says where business *actually*
  happened.** γ peak ≈ POC is a level with mechanical *and* participatory
  support. γ peak far from POC means the pin is structural only.
- **Value area is the market's own containment band.** The pin-strength measure
  uses a fixed ±1 strike step; VAH/VAL is volume-derived. Comparing them is what
  can eventually calibrate `PIN_CONTAINMENT_STEPS` instead of leaving it a guess.
- **POC migration vs pin migration.** `daily_pin` already records the pin trail;
  POC beside it makes *"was price pinned, or did it just trade there?"* answerable.

---

## The eleven decisions

| # | Decision | Choice | Why |
| --- | --- | --- | --- |
| 1 | Basis alignment | **Per-bar shift onto the index axis** | A session constant is wrong early and late as the basis decays; leaving it on the futures axis makes the ladder merge impossible. Measured 71.55 pts — too large to ignore |
| 2 | Two POCs | **Footprint mixture POC wins** | One number on the desk. The mixture POC is the better estimate than binned typical price |
| 3 | Build order | **Standalone desk first** | Validates the port against live Kite data and forces the basis question into the open *before* anything is overlaid and starts quietly misleading |
| 4 | Concentration presentation | **Confluence readout + row tint** | Readout is what you act on; tint adds a dimension to the ladder without competing with the γ bars |
| 5 | Code location | **`vendor/volume_footprint/` + LICENSE** | Third-party MPL-2.0 code, kept diffable against upstream and clearly separated from first-party analysis code. The folder also had a space in its name, which Python cannot import from without `sys.path` hacks |
| 6 | The 37 engine tests | **Moved into `tests/`** | They join CI, so a refactor that breaks the port fails loudly. 0.15s against an 800-test suite is negligible |
| 7 | Profile period | **Session-anchored, 09:15 → now** | Matches how every other desk here thinks about a session, and makes POC comparable with `daily_pin` and the HHI trail |
| 8 | Engine tunables | **Fixed classical defaults for v1** | 70% VA, 300% imbalance, 5pp tilt. Fewer ways to produce a misleading picture before the readings are familiar |
| 9 | Underlying scope | **Cash indices + MCX** | MCX is the *easier* case — options are written on the future, so no basis correction. It doubles as a control group showing what exact alignment looks like |
| 10 | Compute placement | **Shared cached service, 45s TTL** | One Kite pull and one integration serve both the desk and the gamma poll |
| 11 | `session_poc.py` | **Kept as fallback** | The chart never loses its level when the engine cannot compute; existing reason codes stay meaningful |

### Decision 11 has a subtlety worth preserving

"Footprint POC wins" is implemented as a **peek**, never a compute
(`peek_volume_profile`). Paying the ~200–900 ms integration from inside
`session_poc` would push it onto the thin multi-index strip snapshots that
deliberately avoid it. "Available" therefore means *already cached*, not
*computable* — and with a cold cache the binned POC answers exactly as before.

### Decision 5 has a licensing tail

The package declares MPL-2.0 in its README and `__init__.py`, but shipped with
**no LICENSE file**, and the repo has none at root. The canonical text was
fetched from mozilla.org rather than reproduced from memory — a subtly wrong
licence is worse than an absent one. The single divergence from upstream is in
the moved test file, whose import is explicit instead of a `sys.path` insert.

---

## Three rules the implementation holds

Each is pinned by a test, so they cannot drift back in:

1. **No measurement is claimed that was not made.** Every payload carries
   `estimate: true`.
2. **`null` means unmeasured, never zero.** Thin sessions return
   `available: false` with a bar count, not a POC fitted to the opening minutes.
3. **Off-frame mass is surfaced, not normalised away.** A ±20-strike ladder can
   miss a large share of a trending session; the ladder says so above 1%.

---

## Measured, not assumed

Integration is roughly **O(bars²)** and scales with the price lattice, so tick
size matters as much as bar count:

| Case | Bars | mintick | `compute_ms` |
| --- | --- | --- | --- |
| Synthetic, ~1h | 75 | 0.1 | 23 |
| NIFTY full session (live) | 374 | 0.1 | 430 |
| CRUDEOIL full session (live) | 627 | 1.0 | 106 |
| Synthetic, MCX-length | 870 | 0.1 | 920 |

**Open item:** one live call after a rebuild returned **2477 ms** for NIFTY at the
same bar count. Most likely CPU contention from the build and restart that had
just run, but it is 5× the measured figure and unexplained. If it persists on a
quiet machine, the profile should move to a background thread like the GEX
recorder rather than being computed inline behind the cache.

## Still provisional

- The `compute_ms` variance above.
- `MIN_PROFILE_BARS = 15` is a reasoned floor, not a fitted one.
- Whether VA width is actually a better containment tolerance than the pin
  measure's fixed ±1 strike step — that is the whole point of the confluence
  readout, and it needs sessions of `daily_pin` data to answer.

## Considered and rejected

- **Session-constant basis** — simple, but wrong by a growing margin as expiry
  approaches.
- **Footprint on the futures axis, labelled** — honest, but forecloses the ladder
  merge entirely.
- **Replacing `session_poc` outright** — the Gamma chart would lose its Fut POC
  line whenever the engine could not compute.
- **Exposing the engine tunables in v1** — more ways to produce a misleading
  picture before anyone has a feel for the readings.
