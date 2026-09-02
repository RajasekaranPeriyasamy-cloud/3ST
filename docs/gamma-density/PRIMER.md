# Gamma Density — primer

The six ideas the whole desk rests on. If you already trade dealer gamma, skip
straight to [MANUAL.md](MANUAL.md); this exists so the desk is readable by
someone who has not met GEX before, and so the manual can stay dense.

Nothing here is advice. It is a description of a mechanism.

---

## 1. Dealers are on the other side, and they hedge

When you buy an option, a market maker sells it. They do not want a directional
bet, so they hedge the delta — buying or selling the underlying to stay flat.

Delta changes as price moves. **Gamma is the rate at which it changes.** So the
dealer must keep re-hedging, and the *sign* of their gamma decides which way
they trade as price moves:

| Dealer position | As price rises they… | As price falls they… | Effect on the market |
| --- | --- | --- | --- |
| **Long gamma** | sell | buy | **Dampens** — moves get faded, ranges hold |
| **Short gamma** | buy | sell | **Amplifies** — moves get chased, ranges break |

This is the single most important idea on the page. Everything else refines it.

**GEX** (gamma exposure) is that gamma aggregated across the option chain and
expressed in rupees per 1% move. Positive GEX means dealers are net long gamma
and hedging dampens; negative means they amplify.

> One caveat that never goes away: nobody publishes dealer positioning. GEX is
> *inferred* from open interest plus a sign convention — the desk's `sign_mode`
> setting. `naive` assumes customers buy calls and sell puts. It is a model, and
> a different convention gives a different number from the same chain.

## 2. The flip is where the sign changes

GEX is not one number for all prices — it depends on where spot is. The **gamma
flip** is the price at which total GEX crosses zero.

Above it dealers are long gamma and dampen; below it they are short and amplify
(or the reverse, depending on the book). So the flip is not support or
resistance — it is a **behaviour boundary**. The same news lands differently
either side of it.

Distance to the flip therefore matters more than most levels, and matters *in
proportion to how far price normally travels* — which is idea 5.

## 3. Gross gamma and net gamma answer different questions

At one strike there is call gamma and put gamma. You can combine them two ways:

- **Gross** = |CE γ| + |PE γ| — *how much hedging must happen here*
- **Net** = CE γ + PE γ — *which way it pushes*

They are not interchangeable. Under the `naive` sign convention calls are
dealer-long and puts dealer-short, so a strike with balanced call and put gamma
has enormous **gross** gamma and nearly zero **net**. Rank strikes by net and
that strike vanishes; rank by gross and it is the biggest thing on the board.

The desk uses **gross** for "where is gamma concentrated" and keeps **net** as a
separate signed column for "which direction does it lean". Both appear
side-by-side in several panels for exactly this reason.

## 4. Concentration: HHI

Gamma spread thinly across thirty strikes behaves nothing like gamma stacked on
two, even if the total is identical. **HHI** measures that.

Take each strike's share of total gamma, square it, add them up:

```
HHI = Σ (share_i)²        Σ share_i = 1
```

Squaring is what makes it a concentration measure — one strike with 50% adds
0.25, while ten strikes with 5% each add only 0.025 between them. High HHI means
a few strikes own the book.

`1 / HHI` gives **effective strikes** — roughly how many strikes actually matter.
An HHI of 0.11 is about 9 effective strikes.

Cut points depend on the basis and are documented in
[README.md](README.md#concentration-tab--hhi-measurement-basis).

## 5. The expected move is the ruler

"110 points away" means nothing on its own. On a quiet day it is far; before a
result it is nothing.

The ATM straddle price is the market's own estimate of how far price travels by
expiry — **1σ**. Divide any distance by it and you get a number that means the
same thing on every day and every underlying:

```
flip is +110 pts  →  +0.63σ  →  reachable, but not routine
```

The desk expresses key levels in σ for this reason, and dims anything beyond 1σ.

**σ shrinks as expiry approaches**, roughly with √t. That single fact drives the
Expiry Magnet's time boost (idea 6).

## 6. Pressure: gamma weighted by where price can actually get

A huge gamma stack four expected-moves away is not a magnet — price will not
reach it. **Pressure** fuses "how much hedging" with "how likely is settlement
here":

```
P(K) ∝ Γ(K) · exp( −(K − S)² / 2σ² )
```

That second term is a normal density centred on spot, with the market's own
expected move as its width. Normalise so the strongest strike is 1.0.

**Pressure and raw gamma genuinely invert.** A nearer strike with less gamma
routinely outranks a farther one with more — that is the entire point, and the
desk shows both columns so you can see when it happens.

Because peak pressure scales as 1/σ, and σ shrinks as √t:

```
time boost = √( t_reference / t_now )
```

At 1 DTE against a 6-session reference that is √6 ≈ **2.45×**. Pins tighten into
expiry not because positioning changed, but because the distribution collapsed
onto them.

---

## Vocabulary

| Term | Meaning |
| --- | --- |
| **GEX** | Gamma exposure, ₹ per 1% move. Sign = dealer gamma direction |
| **Flip** | Price where total GEX crosses zero — a behaviour boundary |
| **Call / Put wall** | Strike with the strongest magnet pull above / below spot |
| **Pin** | Strike gamma is concentrated at, which price tends toward |
| **Dominant strike** | The single largest gamma strike (not always the pin) |
| **HHI** | Concentration of gamma across strikes, 0→1 |
| **Effective strikes** | 1/HHI — how many strikes actually matter |
| **Pressure** | Gamma × probability of settling there, leader = 1.0 |
| **POC** | Volume point of control — price that did the most business |
| **Value area** | Range containing 70% of session volume |
| **σ** | 1 standard-deviation expected move to expiry, from the ATM straddle |
| **ΔOI** | Change in open interest since the session-open baseline |
| **Writing / unwind** | OI rising = new shorts written; OI falling = positions closed |

## Three habits worth forming

1. **Read the sign before the size.** A big GEX number means opposite things
   above and below the flip.
2. **Convert distances to σ.** The desk does it for you in several places; the
   habit stops you treating 100 points as a constant.
3. **Check what a number is *not* saying.** Every panel in the manual has a
   *blind spot* line. They are there because each one has caught something real.

---

Next: **[MANUAL.md](MANUAL.md)** — how to read each part of the screen.
Mechanism and formulas: **[README.md](README.md)**.
