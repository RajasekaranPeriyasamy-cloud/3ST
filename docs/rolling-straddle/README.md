# Rolling Straddle

**UI:** `/rolling-straddle` · **Code:** `execution/rolling_straddle.py`

ATM CE/PE short book driven by **option-chart** 3ST signals (and related RS gates). Complementary to Premium Book — not replaced by it.

## Contrast with Premium Book

| | Rolling Straddle | Premium Book |
|--|------------------|--------------|
| Typical book | ATM short CE/PE (straddle-style) | Credit verticals (bull put / bear call) |
| Signal chart | Option legs (CE/PE candles) | Underlying index / futures |
| Flat / sideways | Dual open / re-entry rules | Sell book **sits out** |
| Use when | Want ATM short premium | Want defined-risk credit verticals |

## Operator pointers

- Orphan CE on Kite: **Adopt & manage exits**, or **ARM → Close all**, then confirm on Kite.
- Exit ladder UI: Entry (optional) · ATR · ST1 — see leg **Exit prices** card.
- Broader session notes: [`docs/CONVERSATION_SUMMARY.md`](../CONVERSATION_SUMMARY.md), [`docs/INSTRUCTION_MANUAL.md`](../INSTRUCTION_MANUAL.md).

Premium Book deep notes: [../premium-book/](../premium-book/).
