# Volume Footprint

**UI:** `/volume-footprint` · Session volume profile — POC, value area, balance —
reconstructed from OHLCV geometry.

Also feeds the **Gamma Density → Concentration** tab: a volume-confluence readout
and a per-strike volume tint on the cumulative-Γ ladder.

- **[DECISIONS.md](DECISIONS.md)** — every choice behind this desk and why, plus
  what is still provisional.
- **[FILES.md](FILES.md)** — where every related file lives, and why the code
  cannot be consolidated into this folder.
- **`reference/`** — the upstream write-up (PDF) and presentation.

## Where the code lives

| Piece | Path |
| --- | --- |
| Engine (third-party, MPL-2.0) | `vendor/volume_footprint/` |
| 3ST adapter — Kite, basis, cache | `analysis/volume_profile/service.py` |
| Engine tests (37, upstream) | `tests/test_volume_footprint.py` |
| Adapter tests | `tests/test_volume_profile.py` |
| Routes | `GET /volume-footprint/{config,snapshot}` |

`vendor/volume_footprint` is a verbatim port of a Pine v6 indicator by
`ata_sabanci`, kept unmodified so it stays diffable against upstream. Its LICENSE
travels with it. The **only** divergence is in the moved test file, whose import
is explicit (`from vendor.volume_footprint import …`) instead of a `sys.path`
insert, so it resolves the same way under pytest, uvicorn and the schedulers.

## Three things to know before trusting a reading

**1. The buy/sell split is a model, not a measurement.** Only the Geometric
engine is reachable here — it infers direction from where each candle closed in
its range (`buy = V·(C−L)/(H−L)`). The Intrabar engine needs sub-minute history
Kite does not serve; the Footprint engine needs a per-tick aggressor feed, and
nothing in this repo records one. Two very different order-flow paths produce the
same candle. Every payload carries `estimate: true`; `tilt_pp`, `overlap_pct` and
imbalance are **structure, not verified flow**.

**2. Volume is on the future; strikes are on the index.** Cash-index candles
carry no volume, so the profile is built from front-month futures bars. Those
trade at a basis — **measured at 71.55 points on NIFTY** on 2026-08-20, which is
1.4 strike steps. Each bar is therefore shifted by its own
`fut_close − index_close` before the profile is fitted, and `basis.matched_bars`
reports how many minutes actually had an index partner (the rest carry the last
known basis forward). MCX options are written on the future itself
(`spot_source == "future"` in `INDEX_OPTIONS`), so no shift is applied and none is
needed — that case is exact by construction rather than by correction.

**3. `RES = EXACT` certifies the arithmetic, not the market.** It says the drawn
shape faithfully represents the volume behind it. `DRIFT` means don't trade the
shape until it settles. Healthy readings sit far below 1 PPM — live runs come out
around 0.002–0.012.

## Behaviour

- **Session-anchored.** The profile covers 09:15 → now (MCX 09:00 → now), fetched
  with an explicit `minutes_since_session_open`, never the 40-bar default that
  would silently build the profile from the last 40 minutes and look plausible.
- **Thin sessions report unmeasured.** Below `MIN_PROFILE_BARS` (15) the payload
  is `available: false` with a reason and a bar count. `None` never means zero.
- **Cached 45s per underlying**, shared by the desk and the gamma poll, so one
  Kite pull and one integration serve both.
- **Fixed classical settings** for v1: 70% value area, 300% imbalance, 5pp tilt
  dead zone. Not exposed in the UI yet.

## Cost

Integration is roughly **O(bars²)** and scales with the price lattice, so tick
size matters as much as bar count:

| Underlying | Bars | mintick | `compute_ms` |
| --- | --- | --- | --- |
| NIFTY (full session) | 374 | 0.1 | ~430 |
| CRUDEOIL (full session) | 627 | 1.0 | ~106 |
| synthetic, 870 bars | 870 | 0.1 | ~920 |

`compute_ms` rides on every payload so a slow desk is diagnosable rather than
folklore. The Kite fetch dominates wall time on a cold cache (~3s for NIFTY: two
historical calls).

The Concentration tab only computes this on the **full desk poll**
(`build_session_chart`) — the multi-index strip runs thin snapshots and must not
trigger one integration per underlying.

## One POC on the desk

`options/session_poc.py` still computes a binned typical-price POC, but
`compute_session_poc_detail` now **prefers the footprint mixture POC whenever one
is already cached** (`source: "footprint"`). It is a *peek*, never a compute —
paying the integration from there would push it onto the thin snapshots that
deliberately avoid it. When nothing is cached, the binned POC answers exactly as
before, so the Gamma chart never loses its level.

## Per-strike volume

`strike_band_volume()` gives each strike the band `[K − step/2, K + step/2]` and
uses the engine's `band_mass()` — the **exact** truncated-normal mass of that
band, plus flat-bar atoms. Summing over a full lattice recovers total volume, so
this is aggregation, not resampling.

Mass outside the supplied strike window is returned as `off_frame` rather than
normalised away: a ±20-strike ladder can miss a large share of a trending
session, and silently rescaling would hide it. The ladder states it when it
exceeds 1%.
