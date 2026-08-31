# Gamma Density — reading the desk

How to read every part of `/gamma-density`, and what each part does **not** tell
you.

- New to dealer gamma? Read **[PRIMER.md](PRIMER.md)** first — six ideas, ten minutes.
- Want the formulas and thresholds? **[README.md](README.md)** is the mechanism;
  this file is the interpretation. Where they overlap, README wins.

Every entry follows the same shape:

> **What it is** · **How to read it** · **In the market** · **Blind spot**

The blind-spot line is not filler. Each one exists because that reading has
misled someone — usually during this desk's own development.

Nothing here is trading advice. It describes what the instruments measure.

---

## Contents

**Profile tab** — [meta strip](#meta-strip) · [GEX × Vanna](#gex--vanna) ·
[the four tiles](#the-four-tiles) · [reference levels](#reference-levels) ·
[market read](#market-read) · [squeeze / momentum](#squeeze--momentum) ·
[session chart](#session-chart) · [strike chart](#strike-chart) ·
[per-strike detail](#per-strike-detail--convexity-zones) · [settings](#settings-that-change-the-numbers)

**Concentration tab** — [index strip & γ mass](#index-strip--γ-mass) ·
[HHI hero](#hhi-hero) · [Γ ladder](#γ-ladder) · [builders & side HHI](#hhi-builders--callput-γ-hhi) ·
[expiry magnet](#expiry-magnet) ·
[structural regime](#structural-regime) · [pin strength](#pin-strength) ·
[volume confluence](#volume-confluence) · [history](#30-session--intraday-hhi) ·
[OI movers](#oi-change--top-movers)

**[A worked session — 4 DTE to 1 DTE](#a-worked-session--4-dte-to-1-dte)**

**[When to distrust the screen](#when-to-distrust-the-screen)**

---

# Profile tab

## Meta strip

**What it is** — the header line: spot, Fut POC, ATM strike, ATM IV, dividend
yield, mid/ltp price-source counts, legs quoted, last update.

**How to read it** — `101/122 legs` means 101 of 122 chain legs passed the
spread and OI filters. `mid 101 / ltp 0` says all 101 came from the bid-ask
midpoint rather than last-traded price.

**In the market** — a sharp drop in quoted legs mid-session usually means
widening spreads, not vanishing interest. Every number downstream is computed
from those legs only.

**Blind spot** — it does not tell you *which* legs dropped. If they were all far
OTM the shape barely moves; if the ATM went, everything shifts.

## GEX × Vanna

**What it is** — a one-line joint read of gamma regime and vanna regime, plus
total VEX in ₹Cr.

**How to read it** — four states: *pin / fade extremes* (GEX>0, far from flip),
*trend / breakout risk* (GEX<0, near flip), *vol amplification* (GEX<0 + VEX<0),
*mean-revert bias* (positive γ).

**In the market** — positive vanna means a rise in IV pushes dealers to buy;
combined with negative gamma that is the setup where a vol spike accelerates a
move rather than calming it.

**Blind spot** — it collapses two continuous quantities into one label. Read the
tiles for magnitude.

## The four tiles

### Gamma Regime
**What it is** — sign of total GEX: *Positive γ* (dampening) or *Negative γ* (amplifying).
**How to read it** — this sets how every other level behaves. Read it first.
**In the market** — negative γ means support and resistance are weaker than they look; dealers push through them.
**Blind spot** — it is spot-dependent. A move of 100 points can flip it. Check *Distance to Flip* beside it, always.

### Total Net GEX
**What it is** — aggregate dealer gamma in ₹Cr per 1% move.
**How to read it** — sign matters more than magnitude; magnitude matters relative to this underlying's own recent range.
**In the market** — `−2,898,615 Cr` with +VE 5.75M and −VE 8.64M says the book is genuinely two-sided, with the short side winning — not a one-way position.
**Blind spot** — depends entirely on `sign_mode`. Switch from `naive` to `customer` and the sign inverts. It is a convention, not a measurement.

### Distance to Flip
**What it is** — points from spot to the gamma flip, plus the sticky-delta and sticky-strike variants.
**How to read it** — small distance = the regime is fragile. Convert to σ (the Concentration tab does this for you).
**In the market** — `+110` with 1σ = 174 is **0.63σ**: reachable within a normal day, so the current regime is not safe to assume.
**Blind spot** — the flip moves as the chain re-prices. It is not a fixed level; it is today's crossing point.

### Expected Move
**What it is** — 1σ to expiry from the ATM straddle, with the band.
**How to read it** — the ruler for every other distance on the page.
**In the market** — `±174 → 24,079–24,427` is the market's own 68% band to expiry.
**Blind spot** — straddle-derived, so it inherits any ATM mispricing, and it collapses as expiry nears — the same band means something different at 4 DTE and 1 DTE.

## Reference levels

**What it is** — previous day and previous week high / low / close, plus a Key
Levels badge row (Flip, Call wall, Put wall, Dom, Pin).

**How to read it** — structural gamma levels versus levels the market actually
made. When they coincide, that price has two independent reasons to matter.

**In the market** — prev-day close 24,231.85 sitting just under a 24,300 call
wall says yesterday's balance point is below today's ceiling.

**Blind spot** — prior-session levels have no mechanical force. They matter
because participants watch them, which is a different kind of claim.

## Market read

**What it is** — five generated sentences: regime, volatility, shape, change,
levels.

**How to read it** — a plain-language summary of the tiles. Fastest way to
orient after a break.

**Blind spot** — generated from thresholds, so it states things flatly that are
marginal. If a sentence surprises you, check the number behind it.

## Squeeze / momentum

**What it is** — a 0–100 score with five components (GEX, Squeeze, OI flow, IV,
Structure) and a driver list.

**How to read it** — `>60` up-bias, `40–60` neutral, `<40` fade. **Read the
components, not the headline.**

**In the market** — `67.9 bullish` looks directional, but with `gex 86,
squeeze 85.7, oi_flow 40.6` it is carried by structure while actual flow is
neutral. That is a structural tilt, not confirmed buying.

**Blind spot** — the label reads directionally but most inputs are magnitude,
not direction. A high score in negative gamma means "energy", not "up".

## Session chart

The densest thing on the page: **four y-axes** on one time base.

| Series | Axis | Reading |
| --- | --- | --- |
| **+VE GEX** (green) | GEX (Cr) | Long-gamma mass through the session |
| **−VE GEX** (red) | GEX (Cr) | Short-gamma mass |
| **Net Gamma** (amber) | GEX (Cr) | The difference — forward-filled after the first sample |
| **Sign flip** | GEX (Cr) | Marks only where Net Gamma crosses zero |
| **Spot** (blue) | Spot / Strike | Price, right axis |
| **ATM IV** (pink) | ATM IV % | Own axis, forward-filled after first sample |
| **Volume** | Volume | Lower pane; front-month futures when cash volume is empty |
| **Bull / Bear pivot** | Spot / Strike | Detected reversals — see below |

Level overlays as horizontal lines: **Fut POC** (violet dotted), **Call wall**,
**Put wall**, **Flip**, **Pin**. The **GEX zero line** is a dotted grey rule —
the boundary between the green and red regimes.

**Reversal markers have four grades**, and the distinction matters:
`Bull/Bear pivot (provisional)` = price pivot only, GEX gate not yet satisfied ·
`Bull/Bear pivot` = gated · `conf (pre-GEX)` = confirmed but before GEX
recording began · `conf` = fully confirmed. Muted markers are the ungated ones.

**How to read it** — watch Net Gamma against spot. Net Gamma falling while spot
rises means the rally is eroding dealer long gamma — the dampening is draining
away.

**Blind spot** — **gaps are real and deliberately not filled.** GEX history only
starts when the desk first sampled it; the chart says so in a note. Sparse GEX
does not mean flat GEX. Cash-index volume is usually zero, so the volume pane is
futures volume — a different instrument on the same clock.

## Strike chart

**What it is** — "Net GEX by strike · Γ×OI density" — the cross-section of the
book at this moment.

| Series | Colour | Reading |
| --- | --- | --- |
| **Net GEX** | dark green `#166534` / dark red `#991b1b` | Signed gamma per strike |
| **Γ×OI density** | — | Unsigned density — the magnitude behind the sign |
| **Call OI** | red `#ef4444` | Call open interest |
| **Put OI** | green `#22c55e` | Put open interest |
| **IV Curve** | magenta `#c026d3` | The smile across strikes |

Vertical lines mark **Spot**, **Flip**, **Dom**, **PIN**, **Fut POC**.

OI bars carry the session's change: **solid** = OI at session open, **striped**
stacked above = today's writing, **hollow** above the solid = today's unwind.
A shrinking wall is drawn as a ghost above what remains, never as a smaller
solid bar.

**How to read it** — find where Net GEX changes sign; that is the flip in
cross-section. Then look at whether OI bars near it are striped (building) or
hollow (dissolving).

**Blind spot** — a snapshot. It shows the book now, not how it got here. Two
identical cross-sections can have opposite histories.

## Per-strike detail & convexity zones

**What it is** — the numeric table behind the charts, and the top five strikes
by Γ×OI density.

**How to read it** — the audit trail. When a panel says something surprising,
this is where you check the strike that caused it.

**Blind spot** — convexity zones rank on density alone, with no distance
weighting. A high-density strike two expected-moves away appears here and is not
reachable.

## Settings that change the numbers

Six controls silently change every reading on the page:

| Setting | Effect |
| --- | --- |
| **Dealer sign** (`naive` / `customer` / `oi_delta`) | Inverts the sign of GEX. The single largest lever |
| **Strike window** (±10 … ±50) | Widens the chain. Mechanically **lowers HHI** — its floor is 1/N |
| **OI baseline** (session open / prev close) | Redefines ΔOI, so every "writing / unwind" label |
| **Multi-expiry** | Adds next expiry's GEX to the stack |
| **Reversal TF / GEX gate / OI gate** | Which pivots qualify as confirmed |
| **γ mass** (Concentration tab) | Gross vs net basis for HHI and contributors |

> Cross-session comparisons are only valid at a constant window and basis. The
> desk enforces this — `daily_hhi` rows are basis-tagged and mismatched days are
> excluded rather than silently mixed.

---

# Concentration tab

## Index strip & γ mass

**What it is** — HHI chips for NIFTY / BANKNIFTY / SENSEX, and the mass-basis
selector.

**How to read it** — chips let you spot which index is compressed without
switching desks. `γ mass` switches HHI between gross (|CE|+|PE|, default) and
net (|CE+PE|).

**Blind spot** — the strip runs a narrower strike window than the main board, so
its HHI is not directly comparable to the hero number below it.

## HHI hero

**What it is** — the headline concentration number, band label, day-over-day
change, a 0→1 gauge with the 5-session mean and band cuts marked, and six tiles.

**How to read it** — the gauge marks are the context: your value, the 5-session
mean tick, and the two band cuts. Band label is *compressed / balanced /
dispersed*.

The tile row carries net dealer γ, spot, **Gini**, **Shape**, the 5-session mean
and the percentile. **Gini is inequality, not concentration** — HHI asks "do a
few strikes dominate", Gini asks "is the distribution lopsided". They diverge,
and *Shape* (the Ávila quadrant) is the two read together.

**In the market** — `0.115 balanced` against a 5-session mean of `0.247` says
today is unusually **spread** — the book is less concentrated than it has been.
D/D of `+24.9%` off a low base is not the same as being concentrated.

That divergence, live: `HHI 0.11 balanced` with `Gini 0.76 unequal-balanced` —
no strike dominates, yet the distribution is very uneven. A few medium strikes
and a long tail of nothing.

**Blind spot** — percentile is inclusive of today and drawn from however many
sessions have been recorded on the current basis (9 at time of writing, not 30).
The tile says the count; believe the count, not the word "percentile".

## Γ ladder

**What it is** — "Cumulative Γ exposure": every strike in the window as a signed
net-γ bar around a zero axis, with a dashed cumulative gross-γ curve, session
volume as a row tint, and spot / pin / cliff / peak markers.

**How to read it** — bars show *direction* per strike; the curve shows how fast
gross gamma accumulates from the top down. A curve that jumps at one row is a
concentrated book. Hover any strike for its share, HHI contribution, distance
from spot and cumulative percentage.

**In the market** — the tint answers a different question from the bars: indigo
shading is where volume actually traded. Bars and tint disagreeing means dealers
must hedge where nobody is trading.

**Blind spot** — only covers the ±window. If volume traded outside it, the
caption says what share — read that before assuming the ladder is the whole
session.

## HHI builders / Call·Put γ HHI

**What it is** — top-N strikes by share of gamma, and HHI computed separately
for the call and put sides.

**How to read it** — the caption "top 5 strikes hold X% of dealer gamma" is the
concentration statement in plain terms. Side HHIs say whether one side is doing
the concentrating.

**In the market** — `call 0.107 / put 0.138` means puts are the more clustered
side; the put book has a tighter structure than the call book.

**Blind spot** — side HHIs are always gross by construction. If the headline is
on the net basis, they are not on the same footing.

## Expiry magnet

**What it is** — which strike is pulling settlement, ranked on **pressure**
(gamma × probability of settling there), with a four-state ladder, conviction
score, components, and a pressure ladder.

**Ladder depth** — Top 5 / 10 / 20 / All, default **Top 10**. Strikes are picked
by *pressure* and then laid out in *price* order, so the depth control keeps the
strongest magnets rather than the highest strikes. When rows are hidden the
ladder says how many of how many, and the spot divider stays on the board even
when every surviving row sits on one side of it.

**How to read it** — states run *No pin → Shifting → Stable → Locked*. **Time
boost** says how much harder the clock is squeezing than at the reference
horizon. The `%` column is pressure, not gamma share — both are on the payload.

**In the market** — at 1 DTE: pin `24,200`, **LOCKED**, conviction 78.4, time
boost **2.45×**, runner-up 24,150 with a 0.53 margin. Note the ranking inverts
against raw gamma: 24,150 carries *less* gamma than 24,250 and 24,300 but sits
closer to spot, so it is the second-strongest magnet, not the fourth.

**Blind spot** — conviction is marked **provisional** and it means it: the
weights are reasoned, not fitted against outcomes. Read the four component bars,
which are measurements, rather than the single number, which is a summary. Also:
the pin can sit on *short* net gamma — a magnet dealers hedge into rather than
against.

## Structural regime

**What it is** — pin-vs-POC confluence, every level in σ, and a state
classification with its evidence.

**How to read it** — the σ ladder is sorted **high to low with spot marked** —
it is a price axis. Dimmed rows sit beyond one expected move. `aligned` means
pin and POC are within one strike step.

**In the market** — `Coiled box` — *price contained between the walls while
dealers are short gamma*. Containment supplied by positioning rather than by
dealer hedging, which is a materially different thing from a pin.

**Blind spot** — `aligned` and `pin_in_value` answer different questions and can
disagree: the pin was within one step of POC (aligned) yet outside a 33-point
value area. Neither is wrong.

## Pin strength

**What it is** — two hard gates and five components over a selectable window
(15m / 30m / 60m / session).

**How to read it** — **both gates must pass.** Components: stability (ticks on
the modal pin), containment (minutes within one step), crossings (spot
oscillating *across* the pin), flip room in σ, and ΔOI on the pin strike.

**In the market** — the instructive case: stability 100%, containment 96.7%, and
**crossings 0.0/h**. Price stayed within a strike of the pin without ever
crossing it — that is a *lean*, not a pin. A real pin rotates through the level.
The gate failed anyway on `dealers long gamma 0%`.

**Blind spot** — deliberately **no blended score**. The components are the
reading. `null` means unmeasured, never zero — an empty window is not a broken pin.

## Volume confluence

**What it is** — POC, value area, pin-vs-POC gap, +γ-peak-vs-POC, tilt, overlap,
balance verdict.

**How to read it** — gap shown in points *and* strike steps. Value area is the
market's own containment band, against the pin measure's fixed ±1 step.

**In the market** — POC `24,252` against pin `24,300` is 48 points — one full
strike between where dealers must hedge and where business actually happened.

**Blind spot** — the buy/sell split is **inferred from candle geometry**, not
measured order flow (no tick feed exists). Tilt and overlap are structure, not
verified flow. Volume is futures volume shifted onto the index axis — the basis
was 75 points on the near month and 174 on the far.

## 30-session / intraday HHI

**What it is** — day-end HHI bars for up to 30 recorded sessions, and today's
tick-level HHI with mean-cross markers.

**How to read it** — bars are coloured by band, today highlighted, with the
5-session mean and both band cuts drawn.

**Blind spot** — only sessions on the **same basis and strike window** appear.
Change either and the sample shrinks; the caption says how many rows are
inferred. The intraday rank includes the current tick, so it can never read
below 100/n.

## OI change · top movers

**What it is** — ATM strike, put/call ratio, day ΔOI, and the largest OI changes
by strike and side.

**How to read it** — bars are coloured by **side** (call red, put green); the
sign and the action label carry writing versus unwind. Legend: `+ writing ·
− unwind`.

**In the market** — `+79.46M` net with **no unwinds in the top twelve** is a
two-sided premium-selling session. PCR 1.09 with writing stacked at
24,200–24,300 is the market expressing the same containment view the structure
implies.

**Blind spot** — ΔOI depends on the baseline setting. Under `session_open`,
positions opened and closed within the day are invisible.

---

# A worked session — 4 DTE to 1 DTE

The same NIFTY book, captured twice this session. This is what the panels look
like when they move together.

## At 4 DTE — a coiled box

```
spot 24,252.65   ATM IV 8.38%   1σ = 174 pts
regime NEGATIVE   net GEX −2.90M Cr   flip 24,362.66 (+110 pts = 0.63σ)
walls 24,200 / 24,300           HHI 0.115 balanced   eff strikes 8.72
pin 24,300 (dominant)           POC 24,252   VA 34.9 pts wide
day ΔOI +79.46M, zero unwinds   PCR 1.09
pin gates: dominant PASS · long gamma FAIL (0%)
containment 96.7%   crossings 0.0/h   flip room 0.63σ
```

**The read.** A compressed, heavily-written session. Gamma is *spread*
(eff 8.72, HHI below its 5-session mean), so this is not yet an expiry pin.
Price sits under the call wall without crossing it — leaning, not pinning.
Containment is being supplied by the +79M of two-sided writing, not by dealer
hedging, because dealers are short gamma throughout. The structural regime names
this exactly: **coiled box**.

**The asymmetry that matters.** The flip sits 0.63σ *above*. Rally into it and
dealers flip long gamma, which caps the move. Break below and they stay short,
which accelerates it. Upside self-limiting, downside self-reinforcing.

**The fragile point.** 24,200 is simultaneously the put wall, the −γ peak, and
the heaviest put writing. It is where the containment view is expressed and
where it would fail.

## At 1 DTE — locked

```
spot 24,173.9    σ to expiry 130.78 pts (was 174)
expiry magnet: pin 24,200 (+26.1 pts = +0.20σ)   LOCKED
runner-up 24,150   margin 0.53   leader share 0.36   stability 76.8%
time boost 2.45×
pressure: 24,200 (1.000) · 24,150 (0.474) · 24,250 (0.412) · 24,300 (0.306)
raw gamma order: 24,200 · 24,250 · 24,300 · 24,150 · 24,100
```

**What changed.** σ collapsed from 174 to 131 — the distribution tightened onto
the strike. The same book now exerts **2.45×** the pull it would six sessions
out. Price migrated down to the strike that was the put wall at 4 DTE, and the
leader is now clear of its runner-up by a wide margin with 76.8% session
stability. State: **Locked**.

**The inversion, live.** 24,150 carries less gamma than 24,250 or 24,300 but
sits at −0.18σ instead of +0.58σ / +0.96σ. Ranked on pressure it is second;
ranked on raw gamma it is fourth. This is why the desk ranks on pressure.

**What to still hold lightly.** The pin sits on **short** net gamma, and
conviction is `provisional`. Its strongest component is margin (1.00); its
weakest is time (0.29), meaning the clock has more squeezing left to do.

---

# When to distrust the screen

1. **`sign_mode` is a convention.** Every GEX sign on the page follows from it.
2. **The window changes HHI.** Its floor is 1/N. Widen the chain and
   concentration falls without the book changing.
3. **`null` is not zero.** Unmeasured components render as `—` or `n/a`, never
   as a bad reading. If a panel looks empty, check the bar count first.
4. **Volume tilt and overlap are inferred**, not measured. No tick feed exists.
5. **Provisional means provisional.** Conviction weights and several thresholds
   (`FLIP_NEAR_SIGMA`, `CONTAINMENT_HOLDS_PCT`, `PIN_CONTAINMENT_STEPS`) are
   reasoned, not fitted. `daily_pin` is accumulating the data that would fit them.
6. **An `atm` pin is not a pin.** It tracks spot, so it looks most stable when
   there is least structure.
7. **Charts have gaps on purpose.** Missing GEX history is drawn as missing.
8. **Cross-session comparisons need a constant basis.** The desk enforces it;
   changing the window mid-week shrinks your comparison sample rather than
   corrupting it.

---

Mechanism and formulas: **[README.md](README.md)** ·
Concepts: **[PRIMER.md](PRIMER.md)** ·
Volume engine: **[../../volume Profile Gaucessian/](../../volume%20Profile%20Gaucessian/)** ·
Development history: **[../CONVERSATION_SUMMARY.md](../CONVERSATION_SUMMARY.md)**
