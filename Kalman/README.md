# Kalman pairs trading on NSE indices — a timeframe study

Two things live here.

1. **A corrected implementation.** The article's framing is right — a static
   hedge ratio is the weak point of a pairs trade, and a Kalman filter is the
   standard fix. Its *code* has bugs that change what the strategy is, and this
   package is a working version with the maths written out and tests that fail
   against the original.
2. **The actual question you asked**: across 5m / 15m / 30m / 60m / 2h / 4h, on
   Nifty indices with Kite data, which timeframe fits best — and best by which
   definition.

```
kpairs/
  indices.py    the index universe, split by whether a listed future exists
  bars.py       one 5-minute Kite pull, session-anchored resampling to all six
  kalman.py     the [alpha, beta] random-walk filter, plain numpy, MLE tuning
  stats.py      cointegration, half-life, Hurst, Benjamini-Hochberg FDR
  signals.py    the entry / exit / stop state machine over a causal z-score
  backtest.py   two-leg P&L, NSE cost model, frequency-aware metrics
  tfstudy.py    the walk-forward sweep and the model-fit scan
scripts/
  fetch_index_bars.py   pull and cache 5-minute history
  timeframe_study.py    run the sweep, write results/
  verdict.py            consolidate a finished sweep into one answer
tests/                  42 tests, including an end-to-end causality check:
                        change prices after a cut date, and nothing before it
                        may move. The plumbing is pinned before any result is
                        read.
```

---

## Part 1 — what the article's code actually does

The prose is fine. The code has six problems, and three of them change the
strategy into a different strategy. Each has a test in `tests/`.

### 1. The spread it trades is the wrong series

`KalmanPairsTrader.spread` is a stub — the author's own comment says
`# Placeholder — corrected below`, and it returns `state_means[:, 0]`, the
intercept. The working version is `get_spread`:

```python
spread = y_prices.values - (self.alpha + self.beta * x_prices.values)
```

That is the **posterior** residual: `alpha_t` and `beta_t` have already been
pulled toward `y_t` by the update step that just consumed it. The algebra is
exact —

```
theta_post = theta_prior + K e,   K = P H'/Q
y - H theta_post = e - (H P H'/Q) e = e · (R / Q_t)
```

— so what you get is the innovation multiplied by a hidden, time-varying factor
`R/Q_t < 1`. The trap tightens exactly where the article points you: its pitch
is "raise `transition_covariance` to be more adaptive", which raises the Kalman
gain, which drives `R/Q` down, which collapses the series it told you to trade.
`test_faster_filters_shrink_the_posterior_residual_harder` shows the ratio going
0.97 → 0.79 → 0.28 as the state variance rises three decades.

The tradable series is the **one-step-ahead forecast error**
`e_t = y_t − [1, x_t]·θ_{t−1}`, and its variance `Q_t = H P⁻ H' + R` comes out
of the same recursion. So `z_t = e_t / √Q_t` needs **no rolling window, no
burn-in choice and no lookahead** — that is the actual reason to prefer a
Kalman filter over rolling OLS, and pykalman throws both quantities away.
(`KalmanFilter.filter()` returns only `state_means` and `state_covs`.)

### 2. The book can never go flat

```python
signal[(z_score.abs() < exit_z) & (signal.shift(1) != 0)] = 0
signal = signal.replace(0, np.nan).ffill().fillna(0)
```

The second line overwrites every zero — including the exits the first line just
wrote — with the previous non-zero position. The strategy holds full size from
its first entry to the end of the sample, flipping only when the *opposite*
entry threshold trips. A second bug is stacked on it: `signal.shift(1)` is
evaluated on the pre-fill series, which is zero almost everywhere, so the exit
condition barely fires even before the fill erases it.

Entry/exit hysteresis is a state machine — the action at bar *t* depends on the
position at *t−1*, which depends on the action at *t−1*. Vectorised boolean
assignment cannot express one. Write the loop (`signals.positions_from_z`).

### 3. The P&L identity has a unit error

```python
spread_returns = signal.shift(1) * (y.pct_change() - self.beta * x.pct_change())
```

`beta` came from regressing **raw price on raw price**, so it is a price ratio —
about 2.3 for BANKNIFTY against NIFTY. Multiplying a *percentage* return by 2.3
claims the short leg carries 2.3× the notional of the long, when the hedge
actually calls for 2.3 index *units*, i.e. roughly equal rupee exposure. The
hedge ratio only doubles as a value weight when it comes from a **log-price**
regression — which is why everything here runs on log prices.

Two more in the same line: `self.beta` is indexed at `t`, the same bar as the
return being earned (the position was sized with a hedge ratio the filter only
produces after seeing that bar's price — use `beta_{t−1}`), and there is no
capital base, so pairs with hedge ratios of 0.3 and 3.0 are compared on
different denominators.

### 4. Costs are charged per event, not per unit traded

`trades = signal.diff().abs() > 0` is a boolean, so `trades * transaction_cost`
charges the same amount whether the position moved by 0.05 or 2.0, and ignores
that there are two legs. It also charges nothing for **hedge rebalancing** —
if beta drifts daily and you re-hedge, that is real turnover on real spreads.
This package charges cost on `|Δw_y| + |Δw_x|`, and `freeze_hedge` lets you
measure what daily re-hedging costs against what it buys.

### 5. Pair selection is in-sample, and untested for multiplicity

`find_pairs` runs `coint()` over the whole `prices_df` and then trades that same
`prices_df`. Every pair in the book was chosen because it mean-reverted over
exactly the period being backtested. That one line is worth more Sharpe than the
Kalman filter is.

Then `if pvalue < 0.05` with no multiplicity control. Over a Nifty-50 universe
that is 1,225 simultaneous tests and ~61 expected false positives before a
single real relationship is found; rank by p-value, take the top 10, and the
portfolio is built from the most extreme sampling noise in the sample. And the
half-life screen is run on `prices_df[t1] - prices_df[t2]` — a raw 1:1
difference, meaningful only if the true hedge ratio happens to be 1.0.
`test_naive_price_difference_gives_a_misleading_half_life` builds a pair with a
hedge ratio of 3.0 where the hedged residual reverts in under 15 bars and the
raw difference does not revert at all.

### 6. Tuning on validation Sharpe

`tune_kalman_parameters` grid-searches `transition_covariance` on validation
Sharpe — selecting the filter on the same statistic it will then report, over
six candidates, with no penalty for the search. `fit_kalman_mle` uses maximum
likelihood instead: a property of the state-space model, computable on the
formation window alone, that never sees a P&L number. It answers "how much does
beta actually drift in this pair" rather than "which drift rate paid best last
year". (That function also crashes as written — it fits on train-length arrays
and then indexes them against validation-length ones.)

### And the results table

Sharpe 1.26 → 2.12 with a −4.9% max drawdown, no cost assumption stated, no
borrow cost, no methodology. The internal arithmetic is at least consistent
(1.50/0.89 = 1.68 ✓). Treat it as illustrative.

### Two things it gets right

The half-life formula `-ln(2)/ln(1+ρ)` is the correct **discrete** expression;
plenty of implementations use the continuous shortcut `-ln(2)/ρ`, which drifts
as ρ grows. And `expanding()` is genuinely causal, so the z-score itself is not
lookahead-biased — the lookahead is in the pair selection, not there.

---

## Part 2 — the timeframe study

### Universe

Nifty indices only, split by whether the spread can be an order ticket:

| tier | members | tradable? |
|---|---|---|
| 1 | NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKEX | yes — listed index futures |
| 2 | IT, PHARMA, AUTO, FMCG, METAL, ENERGY, REALTY, PSUBANK, PVTBANK, … | **no derivative** — research only |

A NIFTY IT / NIFTY PHARMA spread is a good research object and a bad order.
It can only be approximated with thin sector ETFs or a 10–15 leg replicating
basket per side, which at intraday frequency destroys the cost budget. Tier 2 is
reported separately and read as an upper bound.

Kite's intraday history for indices starts **2015-01-09**, which bounds the
sample:

| index | first 5m bar |
|---|---|
| NIFTY, BANKNIFTY, FINNIFTY | 2015-01-09 |
| SENSEX | 2018-03-08 |
| BANKEX | 2021 |
| MIDCPNIFTY | 2022-01-21 |

So there is a real trade-off between depth and breadth, and the study runs both:
a **deep** three-index / 11.6-year pass and a **wide** six-index / recent pass.

### One pull, six timeframes

Everything is fetched once at 5 minutes and every coarser bar is built by
grouping whole 5-minute bars. Not a shortcut — the only way to make the
comparison fair. If 5m came from Kite's `5minute` endpoint and 15m from its
`15minute` endpoint, any difference in how the two handle the 15:15–15:30 stub,
a halted session or a muhurat session would surface as a "timeframe effect".

The NSE session is 09:15–15:30 = 375 minutes = 75 five-minute bars, and 375 does
not divide by 30, 60, 120 or 240:

| timeframe | bars/session | bars/year | stub fraction |
|---|---|---|---|
| 5m | 74.7 | 18,750 | 0.000 |
| 15m | 24.9 | 6,250 | 0.000 |
| 30m | 12.95 | 3,250 | 0.077 |
| 60m | 6.97 | 1,750 | 0.143 |
| 2h | 3.99 | 1,000 | 0.251 |
| 4h | 2.00 | 500 | 0.501 |

Half of every 4h "bar" is a 2h15m stub. Grouping is anchored to each session's
first bar, not the wall clock — `pandas.resample('2h')` anchors to midnight and
produces an 08:00–10:00 bin that never existed and that straddles the overnight
gap (`test_grouping_is_session_anchored_not_wall_clock`).

### Annualisation is the trap

Sharpe scales with √(periods per year), so comparing 5m against 4h means the
annualisation factor moves by 37×, or 6.1× in Sharpe. Get it wrong and it
decides the winner by itself. Every factor here is measured from the data
(`bars.bars_per_year`) rather than assumed, because NSE has half-days and
muhurat sessions and index feeds have gaps.

**This is the bug in OpenAlgo's reference backtest.**
`_review/openalgo/examples/python/backtesting_vectorbt.py` sets `INTERVAL = "15m"`
at line 25 and passes `freq="5min"` to `vbt.Portfolio.from_signals` at line 128.
vectorbt derives its annualisation factor from `freq`, so every Sharpe that
example prints is inflated by √3 ≈ 1.73×. (Its docstring also says RELIANCE
while `SYMBOL = "SBIN"`.) Worth knowing before adopting it as a template —
`test_sharpe_annualisation_is_frequency_sensitive` pins the arithmetic.

More broadly: OpenAlgo's engine is `Portfolio.from_signals`, which is long-only
and single-asset. It cannot express a two-leg spread with a drifting hedge
ratio. This harness keeps its conventions — flat-bps fees, explicit `freq`, a
tearsheet — but computes P&L from explicit weight arrays.

### Method

Rolling walk-forward, everything decided from data that existed at the time:

```
formation (250 sessions)              trading (60 sessions)
|---------------------------|         |--------------------|
 cointegration screen + FDR            filter runs causally
 half-life / Hurst / corr filter       positions taken
 pair ranking                          P&L booked
 Kalman variance MLE
 initial (alpha, beta) seed
```

then step forward one trading window and re-select from scratch. Windows are
measured in **sessions**, not bars, so a 250-session formation window is the
same calendar span at 5m as at 4h — splitting on a fixed bar count would give 5m
a 13-day window and 4h a 500-day one and prove nothing.

Selection and hedge fitting run **once per pair-window and are shared across
every variant**, so the costed, zero-cost, overnight, no-open-entry and OLS runs
trade exactly the same pairs in exactly the same windows. Any difference between
them is attributable to the thing the variant changed and nothing else.

### Costs

Per unit of notional turned over, both legs, so a round trip on one leg costs
`2 × bps_per_turnover`. For index futures:

| component | bp |
|---|---|
| STT, sell side only | 2.0 |
| exchange txn + SEBI + stamp + GST | ~0.4 |
| brokerage (₹20 flat on a ~₹19 lakh lot) | ~0.01 |
| half-spread, both sides (0.05 tick on ~25,000) | ~0.4 |
| **round trip** | **~2.8** → 1.4 bp per turnover unit |

Default 1.5 bp. Presets for `stock_futures()` (6 bp) and `sector_proxy()`
(20 bp) are in `backtest.CostModel`. The sweep also runs at **0 bp**, because
"no edge" and "edge eaten by costs" are different diagnoses with different fixes.

### "Best fit" is two questions

**Statistical.** A correctly specified Kalman filter leaves innovations that are
white, unit-variance and unautocorrelated. `fit_scan` measures that with no
trading rules, no thresholds and no costs attached — worth having separately,
because a P&L answer confounds model fit with the entry threshold, the holding
limit, the session rule and the cost model. Move entry z from 2.0 to 2.5 and the
"best" timeframe can move with it; the likelihood does not care.

**Economic.** Gross edge per trade shrinks roughly with √(bar interval) while
cost per trade is flat, so there is a frequency floor below which a real signal
still loses money. Reported as gross bps per trade against cost bps per trade.

They can disagree, and where they disagree is the interesting part.

---

## Part 3 — results

### Run A: the deep pass — NIFTY / BANKNIFTY / FINNIFTY, 2015-01 to 2026-08

The only three indices with full intraday history, so three candidate pairs, 43
walk-forward windows, 250-session formation / 60-session trading, entry z 2.0,
1.5 bp per turnover unit.

**Model fit** (`fit_scan`: no trading rules, no thresholds, no costs)

| tf | z_std | kurt | ac1 | P(\|z\|>4) | half-life | fit score |
|---|---|---|---|---|---|---|
| 5m | **1.15** | 166.5 | +0.022 | 0.84% | 19.8 sess | 0.509 |
| 15m | 1.19 | 41.1 | +0.015 | 1.15% | 17.8 | 0.560 |
| 30m | 1.23 | 44.1 | +0.017 | 1.31% | 18.2 | 0.566 |
| 60m | 1.27 | 39.8 | −0.007 | 1.35% | 17.5 | 0.577 |
| 2h | 1.28 | 38.1 | +0.031 | 1.39% | 17.9 | 0.582 |
| 4h | 1.30 | **35.2** | +0.017 | 1.43% | 17.2 | **0.621** |

Three things fall out.

**The two halves of "fit" point in opposite directions.** Variance calibration
is best at 5m (z_std 1.15) and degrades monotonically as bars coarsen. Tails go
the other way — kurtosis 166 at 5m down to 35 at 4h. No timeframe wins both.
The composite favours 4h, but only because the tail and gap terms outweigh
calibration; if you weight calibration more heavily the ranking inverts. That
sensitivity is why the components are printed alongside the score.

**The model form is fine everywhere; the variance is not.** `ac1` never exceeds
0.031 in absolute value, so the random-walk-β state equation leaves essentially
white innovations at every frequency. But |z|>4 occurs 0.8–1.4% of the time
against a Gaussian expectation of 0.006% — 130× to 230× too often. That is a
mis-specified observation *variance*, not a mis-specified model, and it is
fixable without touching the timeframe.

**Half-life is 17–20 sessions at every timeframe.** Sampling finer does not
create faster reversion; it samples the same three-to-four-week clock more
often. This is the single most important number in the study, and it means an
intraday-flat rule is structurally mismatched: median holding period came out at
0.03–0.5 sessions against a ~17-session half-life.

**Economics** (walk-forward, out of sample, net of 1.5 bp/turnover)

| tf | trades | trades/sess | gross bps/trade | cost bps/trade | gross Sharpe | net Sharpe |
|---|---|---|---|---|---|---|
| 5m | 3,381 | 1.19 | −0.07 | 3.0 | −0.40 | −15.83 |
| 15m | 1,043 | 0.37 | −0.40 | 3.0 | −0.88 | −7.12 |
| 30m | 670 | 0.24 | −0.08 | 3.0 | −0.10 | −3.85 |
| 60m | 437 | 0.15 | −0.41 | 3.0 | −0.33 | −2.68 |
| 2h | 290 | 0.10 | −1.55 | 3.0 | −0.73 | −2.11 |
| 4h | 27 | 0.01 | −9.18 | 3.0 | −0.66 | −0.86 |

**No timeframe clears zero, and gross is negative everywhere.** This is an
absence of signal, not a cost problem — the distinction the zero-cost variant
exists to make. Costs then turn a flat result into a catastrophic one at the
fine end: 1.19 trades per session against a 3 bp round trip is −15.8 net Sharpe
at 5m purely from turnover.

A 17–21% hit rate looks at first like a sign error, so the sweep tests it:

| | 30m | 60m |
|---|---|---|
| reversion, 1-bar lag | −0.08 bps/trade | −0.41 |
| reversion, no lag | −0.60 | **+0.93** |
| momentum, 1-bar lag | +0.08 | +0.41 |
| momentum, no lag | +0.60 | −0.93 |

Momentum is the exact mirror — gross P&L negates term for term, which confirms
the P&L identity has no sign asymmetry but adds no information. The real
finding is in the lag rows: the per-trade edge is **under 1 bp in magnitude and
flips sign between adjacent timeframes and across a single bar of execution
lag**. That is noise. An edge would not reverse when you wait one bar at 60m and
reverse the other way at 30m.

**What the Kalman filter does buy.** Compare the innovation diagnostics of the
two hedge estimators on identical pairs and windows:

| | Kalman | rolling OLS |
|---|---|---|
| lag-1 autocorrelation of z | ~0.02 | 0.75 – 0.98 |
| excess kurtosis of z | 14 – 290 | 0.25 – 3.2 |

The Kalman z is genuinely white; the rolling-OLS z is almost perfectly
autocorrelated, because consecutive windows overlap by all but one bar. So the
filter delivers exactly what it promises statistically — a clean, causally
standardised signal — and it still does not make money here, because the
underlying spread has no exploitable intraday reversion to find. Worth being
precise about: the filter was never the binding constraint.

**Session-open entries are the one lever that moved gross.** Blocking them lifted
gross Sharpe from −0.40 to +0.30 at 5m, −0.88 to +0.25 at 15m and −0.10 to +0.49
at 30m. The diagnostic predicted it: the first bar of the session has an
innovation the filter prices with its ordinary intraday variance, so its z is
inflated by

| 5m | 15m | 30m | 60m | 2h | 4h |
|---|---|---|---|---|---|
| 6.29× | 4.05× | 3.16× | 2.77× | 2.32× | 1.81× |

A mean-reversion rule fires on large |z|, so without the block the book was
systematically entering on the one bar the model understands least. Even so,
+0.49 gross at 30m becomes −1.62 net. It is a real improvement to a losing
strategy.

### Run B: the wide pass — 15 indices, 105 candidate pairs, 2015-01 to 2026-08

Every index with full intraday history: NIFTY, BANKNIFTY, FINNIFTY plus IT,
PHARMA, AUTO, FMCG, METAL, ENERGY, REALTY, PSUBANK, MEDIA, INFRA, PSE, SERVICES.
Same 43 windows, up to 8 concurrent pairs, BH-FDR on the screen.

| tf | trades | gross bps/trade | gross Sharpe | net Sharpe | edge/cost |
|---|---|---|---|---|---|
| 5m | 7,097 | +0.03 | +0.09 | −9.01 | 0.01 |
| 15m | 3,440 | −0.24 | −0.31 | −4.24 | — |
| **30m** | 2,130 | **+0.86** | **+0.66** | −1.63 | **0.29** |
| 60m | 1,019 | −0.90 | −0.43 | −1.84 | — |
| 2h | 608 | +0.02 | +0.01 | −1.02 | 0.01 |
| 4h | 39 | +3.73 | +0.26 | +0.05 | 1.24 |

**30 minutes is the answer**, and it is the same answer in both universes — 30m
had the best gross Sharpe in the deep pass too. It is the only timeframe with a
gross edge that is both positive and large enough to be worth discussing, and
the shape either side of it is the expected one: microstructure noise below,
too few observations above.

Ignore the 4h row. Two bars a session and one of them must be flat under an
intraday rule, so it produced **39 round trips in ten years**; its Sharpe has a
standard error much wider than the number. `verdict.py` applies a 150-trade
floor for exactly this reason — without one, a naive argmax crowns 4h about half
the time by luck.

**The best configuration found, and what it would take to trade it**

| | gross bps/trade | trades | net Sharpe | break-even cost |
|---|---|---|---|---|
| 30m, block open entries | **+1.70** | 992 | −0.71 | 0.85 bp/turnover |
| 2h, overnight | +1.52 | 642 | −0.34 | 0.76 |
| 30m, overnight | +0.91 | 2,222 | −1.37 | 0.46 |
| 30m, baseline | +0.86 | 2,130 | −1.63 | 0.43 |

against a modelled **1.50 bp per turnover unit** (≈3.0 bp round trip). So the
best variant needs execution roughly **twice as cheap as modelled** — a ~1.7 bp
round trip — merely to break even, before any margin of safety. That is the
whole finding stated as a number, and it is a far more useful output than a
Sharpe: it tells you exactly how much the cost assumption would have to move.

**Two caveats that matter more than the table**

*The edge is in the pairs you cannot trade.* Of the 992 trades in the best
configuration, only **81 had listed futures on both legs**. The rest are
sectoral spreads — NIFTY/FMCG, REALTY/MEDIA, SERVICES/REALTY — with no
derivative, implementable only through thin ETFs or a 10-15 leg basket per
side, at costs an order of magnitude above the 1.5 bp assumed here. Run with
`--tradable-only` and the universe collapses to the four index futures, which is
Run A, which has no positive gross anywhere. **The universe with an edge and the
universe you can trade do not overlap.**

*Per-pair dispersion swamps the mean.* NIFTY/FMCG returned +5.57 bp per trade
over 48 trades; PHARMA/AUTO returned −8.30 over 21. A +0.86 bp average across
2,130 trades sitting inside that spread is not a stable estimate of anything.

---

## Two bugs this harness found in itself

Both were caught by tests, and both would have produced a confident wrong answer.

**The intraday square-off did not square off.** `force_flat` marks bars the
*position* must be flat on, but the state machine emits *decisions*, which
`exec_lag` then shifts forward. Constraining the decision at bar *b* leaves the
position at *b+lag* still holding — so an "intraday only" book was carrying every
overnight gap in the sample. Fixing it moved 60m gross Sharpe from −0.74 to
+0.35. (`test_intraday_flat_rule_survives_the_execution_lag`)

**The stop was decorative.** Flatten at z = −4.2 and the next bar, with z still
at −4.1, the entry rule fires again and you are back in the same broken trade at
a worse level. A stop needs a re-arm latch: no new entry until |z| comes back
inside the entry band. (`test_stop_does_not_immediately_re_enter`)

---

## What the study concludes

**Which timeframe fits best?** Two answers, and they are both real.

*Statistically*, the composite favours **4h** — but only because tail behaviour
and overnight-gap inflation improve as bars coarsen, while variance calibration
gets steadily worse. No timeframe wins both halves. The one thing that is
uniform across the sweep is that the random-walk-β **model form is right
everywhere** (lag-1 autocorrelation of the innovations never exceeds 0.03) while
the **observation variance is wrong everywhere** (|z|>4 occurs 130–230× more
often than Gaussian).

*Economically*, **30 minutes**, in both universes independently. Below it the
signal is microstructure and the turnover is ruinous — 2.5 trades per session at
5m against a 3 bp round trip is −9 net Sharpe. Above it there is not enough
data: 4h yields 39 trades in a decade.

**Is it tradable? No, and the reason is specific rather than vague.** The best
configuration found — 30m, session-open entries blocked, Kalman hedge — earns
**+1.70 bp per round trip gross** against a **3.0 bp** modelled cost. Break-even
needs 0.85 bp per turnover unit, about half of what NSE index futures actually
cost. And 92% of those trades are in sectoral spreads with no listed derivative,
so the real implementation cost is higher still, not lower.

**The three things worth keeping regardless of the P&L**

1. **Half-life is 17–20 sessions at every timeframe.** These index spreads mean
   revert on a three-to-four-week clock. Sampling at 5 minutes does not make
   them revert faster, it just samples the same slow process 75 times a day.
   Any intraday-flat implementation is mismatched to the horizon by two orders
   of magnitude — that is a property of the spreads, not of the filter, and no
   parameter change fixes it.
2. **Session-open bars are systematically mispriced by the filter**, by 6.3× at
   5m falling to 1.8× at 4h. The filter has no notion of a session, so it prices
   the overnight gap with its ordinary intraday variance; a rule that fires on
   large |z| then enters disproportionately on the one bar the model understands
   least. Blocking those entries was the single largest gross improvement found
   anywhere in the study (−0.10 → +0.49 at 30m in the deep pass, +0.86 → +1.70
   in the wide one). The principled version is a session-aware observation
   variance rather than a blanket block.
3. **The Kalman filter did exactly what it promises and it was never the binding
   constraint.** Its innovations are white (ac1 ≈ 0.01–0.03) where rolling-OLS z
   is 0.70–0.98 autocorrelated, and it needs no window length and no rolling
   standard deviation. The article's central claim — that the adaptive hedge
   beats the static one — is supported on the diagnostics. It just does not
   convert into money here, because the underlying spread has no intraday
   reversion to harvest.

**If the goal is a working strategy, the evidence points away from this design
in three specific directions**: trade these spreads at the daily horizon the
17-session half-life actually implies rather than intraday; use single-stock
pairs, where dispersion and borrow-driven mispricing are much larger than
between two capitalisation-weighted indices of overlapping constituents; or keep
the index-pair frame and trade it in options, where a 1.7 bp edge is not
immediately erased by the spread.

**What would change the answer.** Costs below ~0.85 bp per turnover unit. A
session-aware observation variance (the diagnostic says this is the largest
single mis-specification). Or a universe where the tradable set and the
cointegrated set overlap, which the six Indian index futures do not provide.

---

## Running it

```bash
cd C:\Dev\3ST\Kalman
python scripts/fetch_index_bars.py --tier all --start 2015-01-09
```

```bash
python scripts/timeframe_study.py --indices NIFTY,BANKNIFTY,FINNIFTY --start 2015-01-09 --max-pairs 3 --tag deep3
```

```bash
python scripts/timeframe_study.py --indices NIFTY,BANKNIFTY,FINNIFTY,IT,PHARMA,AUTO,FMCG,METAL,ENERGY,REALTY,PSUBANK,MEDIA,INFRA,PSE,SERVICES --start 2015-01-09 --max-pairs 8 --skip-fitscan --tag wide15
```

```bash
python scripts/verdict.py --tag wide15
```

```bash
python -m pytest tests -q
```

Runtimes on one machine: the 28-index fetch is ~55 min (resumable, cached per
index); the deep pass ~18 min; the wide pass ~50 min. The `--skip-fitscan` flag
matters on wide universes — the fit scan is one MLE per pair per window per
timeframe, so 105 pairs would be ~15 hours. It samples 20 pairs by default;
`--fitscan-pairs` controls it.

Requires a live Kite session (`data/kite_session.json` in the parent repo) for
the fetch only; everything after that runs off the parquet cache. Market-data
reads are unrestricted by the Kite IP whitelist, and nothing in this package can
place an order.
