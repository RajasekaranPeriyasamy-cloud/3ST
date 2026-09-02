# Volume Footprint — Python port

A faithful Python port of the Pine v6 indicator
**[Volume Footprint: Measuring Classical Indicators by Math & Geometry](https://www.tradingview.com/script/Tm1cGCPD-Volume-Footprint-Measuring-by-Math-Geometry/)**
by `ata_sabanci`. Licensed MPL-2.0, like the original.

The original Pine source is kept alongside at `Volume Foot print.txt` (1,876 lines).

---

## What the tool is for

> Volume tells you how much traded. A footprint tells you **where**.

A candle gives you four prices and one volume. It does not tell you whether the
volume happened on the way up or on the way down, or which prices inside the
range actually did business. A footprint answers that — and a real one needs a
per-tick feed most retail plans do not have.

This indicator's contribution is that it **reconstructs** the footprint from
OHLCV using geometry and a truncated-normal model, so the classical Market
Profile statistics (POC, Value Area, imbalance) can be computed on any data,
and then *checks its own arithmetic in parts per million* so you know when to
trust the picture.

---

## Install and run

Pure standard library. `pytest` for the tests, `matplotlib` only for the
optional plot.

```bash
python demo.py
```

```bash
python -m pytest tests/ -q
```

```bash
python demo.py --csv mydata.csv --mintick 0.05 --window 8 --ticks 12 --plot profile.png
```

CSV columns: `time,open,high,low,close,volume`.

---

## Library use

```python
from volume_footprint import BarSeries, Settings, VolumeEngine, apply_engine, compute
from volume_footprint import format_table, format_dashboard

series = BarSeries.from_ohlcv(rows, mintick=0.05, symbol="NIFTY")
series = apply_engine(series, VolumeEngine.GEOMETRIC)

res = compute(series, Settings(window_bars=6, view_ticks=9, profile_period=23))

print(format_table(res))
print(format_dashboard(res))

res.window_poc_price      # POC over the table's window of bars
res.chart_poc_price       # POC over the profile's period
res.chart_vah_price, res.chart_val_price
res.profile.overlap       # OVL, 0-100
res.profile.tilt          # percentage points, + = buy side
res.profile.residual_ppm  # RES self-check
res.balance_verdict()     # "BALANCED" / "OFF BALANCE TO BUY" / ...
```

**The one improvement over Pine:** `compute(series, end_offset=N)` evaluates the
whole indicator as of N bars back. Pine can only run this on `barstate.islast`,
so the readings cannot be backtested there. Here they can.

---

## The math, in order

### 1. Engine — where buy and sell volume come from

| Engine | Source | Availability |
| --- | --- | --- |
| **Geometric** | `buy = V·(C−L)/(H−L)`, `sell = V·(H−C)/(H−L)` | anywhere |
| **Intrabar** | directional volume aggregated from a lower timeframe | needs LTF history |
| **Footprint** | real per-tick buy/sell rows | needs a per-tick feed |

Geometric reads the candle as a story: a bar that closed on its high was lifted
all session, so most of its volume was buyer-initiated. It is an **estimator**,
not a measurement — it cannot know price ran up and got sold back into. The
other two engines exist to fix exactly that.

*Purity rule, preserved from the original:* a bar the engine cannot fill returns
`None`, never `0.0`. Missing data is missing, and a `None` bar is excluded from
the window rather than counted as a bar that traded nothing.

### 2. Row split — one bar onto a one-tick price ladder

Two truncated normal bells per bar:

```
buy bell    mu_b  = L + (C − L)/2          the lower half-journey
sell bell   mu_s  = C + (H − C)/2          the upper half-journey
both        sigma = (H − L) / concentration
```

Buyers did their work between the low and the close, so their volume centres
midway through that leg; sellers between the close and the high.

Row `k` is the absolute lattice index `round(price/tick)` and owns the band
`[(k−0.5)·tick, (k+0.5)·tick]`. Its value is the exact truncated-normal mass of
that band, clipped to the bar range:

```
row_k = w · [ Φ((top_k − mu)/σ) − Φ((bot_k − mu)/σ) ] / Z
Z     = Φ((H − mu)/σ) − Φ((L − mu)/σ)
```

Rows are never rescaled and never merged. Mass falling outside the visible frame
is returned separately rather than dropped, so **frame rows + off-frame = bar
volume**, per side. That invariant is what the residual check later measures.

Flat bars (zero range) are **point atoms**, not degenerate bells — the case that
breaks naive implementations on limit-locked or illiquid instruments.

### 3. Diagonal imbalance

```
buy_imb[k]   ⟺   buy[k]  > sell[k−1] · ratio
sell_imb[k]  ⟺   sell[k] > buy[k+1]  · ratio
```

The diagonal is the whole point. An aggressive buy lifting the ask pairs against
the resting bid one tick *under* it. Comparing `buy[k]` to `sell[k]` compares two
prints that never met; the diagonal compares the two sides of the same trade
decision. 300% — the classical figure — demands three times the opposing volume.

Untraded rows stay silent. Treating `None` as zero opposing volume would make
every edge row trivially imbalanced.

### 4. The profile — a truncated-normal mixture per side

Every bar in the period contributes one component per side:

```
coef = w / (σ · Z)
density(p) = Σ over components containing p of  coef · φ((p − mu)/σ)
```

Unit: **volume per unit price**. Divide by the side's total volume and it becomes
the mixture's probability density — which is what the overlap coefficient needs.

### 5. The three readings

| Reading | Formula | What it tells you |
| --- | --- | --- |
| **POC** | `argmax(buy + sell)` | where the market did the most business |
| **Value Area** | expand from POC, always taking the heavier neighbour, until 70% of volume is inside | where price was *accepted* |
| **OVL** | `∫ min(f_buy, f_sell) dp` + matched atoms, 0→1 | how much both sides worked the same prices |
| **Tilt** | `100·(B−S)/(B+S)` | which side carried the period, in pp |
| **RES** | `\|(∫f + atoms)/total − 1\|` × 1e6 | the tool's own arithmetic health, in PPM |

**OVL ≥ 0.75 = BALANCED.** Both sides worked the same levels: rotation, and the
profile edges tend to hold. Below that, each side kept its own price territory —
what a directional market looks like. The label only *names* a side when tilt
clears its dead zone (default 5pp); below that it says `OFF BALANCE` and refuses
to dress a coin flip up as a direction.

**RES is a health reading of the tool, not a market signal.** `EXACT` means the
drawing faithfully represents the volume behind it. `DRIFT` means don't trade the
shape until it settles. Healthy readings sit far below 1 PPM — the demo run
comes out at **0.02 PPM**.

### 6. Why the integration is careful

Composite Simpson, on pieces cut at every bar boundary (where the active
component set jumps and the integrand kinks) and, for the overlap term,
additionally at every crossing of the two densities, located by bisection to
machine tolerance. Each piece's active set is fixed by its midpoint, so every
integrand Simpson sees is smooth. Step size is bounded by the narrowest active
sigma.

This is why RES lands below 1 PPM instead of at the percent level a naive
uniform grid would give — and it is why the residual check is worth having at
all: it is the only thing standing between you and a plausible-looking curve
that quietly lost 3% of its volume.

Two normal CDFs are used, exactly as in the original:

| | algorithm | error | used for |
| --- | --- | --- | --- |
| `norm_cdf_fast` | Abramowitz & Stegun 26.2.17 | < 7.5e-8 | table rows |
| `norm_cdf_precise` | Cody `erfc` (via `math.erfc`) | < 2e-16 | the profile |

The profile needs the precise one because it self-reports in PPM: a 7.5e-8 CDF
would light up DRIFT on its own.

---

## Module layout

```
volume_footprint/
  mathkit.py    the two normal CDFs, and why there are two
  bars.py       Bar / BarSeries, the price lattice
  engines.py    Geometric / Intrabar / Footprint volume splits
  rows.py       one bar -> one-tick rows, plus diagonal imbalance
  profile.py    the truncated-normal mixture, OVL, RES self-check
  metrics.py    POC, value area, balance tilt
  indicator.py  window search, and the assembled result
  render.py     terminal table, dashboard, ASCII and matplotlib profile
demo.py         runnable demo, synthetic or CSV
tests/          37 tests pinning the invariants above
```

---

## Reading the table

```
  P        104.15     -51.7 ^+321.4     -32.1 ^+368.9     ...
  ^        103.85                 .     -17.7 ^+236.1     ...
```

Cells read `-sell +buy`. Column headers are bars back from the anchor (`0` is
the newest). Headers can jump — candles that never traded inside the frame take
no column, so every column you see is a candle that genuinely left volume at
these prices. Left-margin marks: `P` window POC, `V` inside the value area,
`^`/`v` imbalance, `>` the axis row (where price is now). `.` means the bar
never traded at that price.

---

## What this port does *not* do

- **No Footprint-engine data source.** `FootprintRow` and `footprint_view_rows`
  are implemented and tested, but you must supply real per-tick rows; there is
  no offline substitute, and inventing one would defeat the purpose.
- **No chart drawing objects.** Labels, merge priorities, colour ladders and
  table anchoring are TradingView presentation concerns. The numbers are all
  here; `render.py` gives you a terminal view and a matplotlib profile.
- **No alerts.** The control levels (`window_poc_price`, `chart_poc_price`) are
  exposed as plain values — wire them into whatever you already use.

---

## Caveats worth stating plainly

The Geometric engine is a **model**, not a measurement. Its buy/sell split is a
geometric assumption about how price traversed its range, and two very different
order-flow paths produce the same candle. Treat POC, Value Area and OVL from
this engine as *structure*, not as verified flow. Where a decision hinges on
real aggressor direction, use the Intrabar engine at a meaningfully lower
timeframe, or a genuine per-tick footprint feed.

RES being `EXACT` says the integration is faithful **to the model**. It says
nothing about whether the model matches what actually traded.
