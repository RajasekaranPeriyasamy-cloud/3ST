"""Assemble both Noa v2 scripts from one shared engine block.

The point of generating them rather than hand-maintaining two files: the engine
has to be byte-identical in the monitor and the strategy, or the signals you
watch stop being the signals you tested. v1 drifted exactly that way -- the
indicator exited on the flip band, the strategy exited on anchored VWAP.
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
ENGINE = (HERE / "noa_engine_block.txt").read_text(encoding="utf-8").rstrip() + "\n"

MON_HEAD = '''//@version=6
indicator("Noa zVWAP Monitor v2",
     shorttitle        = "Noa.zBSH",
     overlay           = false,
     precision         = 2,
     max_labels_count  = 100)

// ===========================================================================
// MONITOR -- shares a byte-identical engine with the "Noa zVWAP AVWAP v2"
// strategy, so the signals you watch are the signals that were backtested.
// v1 used different exits in the two files: the indicator flattened when Z
// re-entered the flip band, the strategy exited on anchored VWAP. Alerts from
// one and a report from the other described different systems.
//
// The exit default is now "Session only", which measured best of the four.
// NIFTY 15m, 11.6 years, one bar of execution lag, gross bp per trade:
//   Session only 4.23 | AVWAP Cross 2.35 | AVWAP beyond 2.14 | Flip band 0.85
// ===========================================================================

grpV = "Display"
showMarkers = input.bool(true, "BUY / SELL / HOLD markers", group = grpV)
showAvwap   = input.bool(true, "Plot AVWAP on the price pane", group = grpV)
showTable   = input.bool(true, "State table", group = grpV)

'''

MON_TAIL = '''
// -- Levels -----------------------------------------------------------------
hTop    = hline(topUpper,  "Top upper",    color = color.gray, linewidth = 1)
hUpper  = hline(upperZ,    "Upper",        color = color.gray, linewidth = 1)
hLower  = hline(lowerZ,    "Lower",        color = color.gray, linewidth = 1)
hBottom = hline(botLower,  "Bottom lower", color = color.gray, linewidth = 1)
hFlipU  = hline(flipUpper, "Flip upper",   color = color.green, linestyle = hline.style_dotted)
hFlip   = hline(zFlip,     "Flip",         color = color.green, linewidth = 2)
hFlipL  = hline(flipLower, "Flip lower",   color = color.green, linestyle = hline.style_dotted)

fill(hTop, hUpper,    color = color.new(color.red, 90))
fill(hUpper, hFlipU,  color = color.new(color.white, 92))
fill(hBottom, hLower, color = color.new(color.green, 90))
fill(hFlipL, hLower,  color = color.new(color.white, 92))

bool extremeUp   = not na(zRaw) and zRaw > upperZ
bool normalUp    = not na(zRaw) and zRaw > flipUpper and zRaw <= upperZ
bool transition  = not na(zRaw) and zRaw >= flipLower and zRaw <= flipUpper
bool normalDown  = not na(zRaw) and zRaw < flipLower and zRaw >= lowerZ
bool extremeDown = not na(zRaw) and zRaw < lowerZ
color zColor = extremeUp ? color.aqua : normalUp ? color.blue : normalDown ? color.red : extremeDown ? color.maroon : color.silver

plot(zRaw, "Z-VWAP",   color = zColor,       linewidth = 2, style = plot.style_stepline)
plot(kf,   "Smoother", color = color.orange, linewidth = 1)

// force_overlay puts the anchored VWAP on the price pane even though this
// indicator lives in its own pane -- the exit level belongs next to price.
plot(showAvwap and state != 0 ? avwap : na, "Anchored VWAP",
     color = state == 1 ? color.new(#00897B, 0) : color.new(#EF6C00, 0),
     linewidth = 2, style = plot.style_linebr, force_overlay = true)

bgcolor(state == 1  ? color.new(#00897B, 88) : na, title = "BUY state")
bgcolor(state == -1 ? color.new(#EF6C00, 88) : na, title = "SELL state")

plotshape(showMarkers and toBuy,  "BUY",  style = shape.labelup,   location = location.bottom,
     color = #00897B, text = "BUY",  textcolor = color.white, size = size.tiny)
plotshape(showMarkers and toSell, "SELL", style = shape.labeldown, location = location.top,
     color = #EF6C00, text = "SELL", textcolor = color.white, size = size.tiny)
float holdMark = (showMarkers and toHold) ? zTrig : na
plotshape(holdMark, "HOLD", style = shape.xcross, location = location.absolute,
     color = color.gray, size = size.tiny)

plot(state,     "state",   display = display.data_window)
plot(barsInPos, "bars in", display = display.data_window)
plot(avwap,     "avwap",   display = display.data_window)

var table t = table.new(position.top_right, 2, 8, bgcolor = color.new(#111111, 15), border_width = 1)

f_cell(int r, string k, string v, color vc) =>
    table.cell(t, 0, r, k, text_color = color.gray, text_size = size.small)
    table.cell(t, 1, r, v, text_color = vc,         text_size = size.small)

if showTable and barstate.islast
    string stTxt  = state == 1 ? "BUY" : state == -1 ? "SELL" : "HOLD"
    color  stCol  = state == 1 ? #00897B : state == -1 ? #EF6C00 : color.gray
    string regime = extremeUp ? "EXTREME +" : normalUp ? "UP" : transition ? "FLIP zone" : normalDown ? "DOWN" : extremeDown ? "EXTREME -" : "-"
    table.cell(t, 0, 0, "State", text_color = color.gray, text_size = size.small)
    table.cell(t, 1, 0, stTxt,   text_color = stCol,      text_size = size.normal)
    f_cell(1, "Exit rule", exitRule, color.white)
    f_cell(2, "Regime",    regime,   zColor)
    f_cell(3, "Z",         str.tostring(zRaw, "#.##"), color.aqua)
    f_cell(4, "AVWAP",     na(avwap) ? "-" : str.tostring(avwap, format.mintick), color.yellow)
    f_cell(5, "Bars in",   str.tostring(barsInPos), color.white)
    f_cell(6, "Weighting", hasVol ? "volume" : "equal (no volume)", hasVol ? color.white : color.new(#C08A2E, 0))
    f_cell(7, "Policy",    policy, policy == "Mean Revert" ? color.new(#C08A2E, 0) : color.white)

alertcondition(toBuy,  "BUY",  "Noa zBSH: BUY")
alertcondition(toSell, "SELL", "Noa zBSH: SELL")
alertcondition(toHold, "HOLD", "Noa zBSH: flatten")
'''

STRAT_HEAD = '''//@version=6
strategy("Noa zVWAP AVWAP v2 (Nifty)",
     shorttitle              = "Noa.AVWAP",
     overlay                 = true,
     initial_capital         = 1000000,
     default_qty_type        = strategy.fixed,
     default_qty_value       = 1,
     pyramiding              = 0,
     commission_type         = strategy.commission.percent,
     commission_value        = 0.02,
     slippage                = 1,
     process_orders_on_close = false,
     calc_on_every_tick      = false,
     max_labels_count        = 100,
     max_lines_count         = 50)

// ===========================================================================
// Shares a byte-identical engine with "Noa zVWAP Monitor v2".
//
// FOUR CHANGES FROM v1, AND WHAT EACH IS WORTH
//
// 1. process_orders_on_close: true -> false.
//    v1 filled at the same close that produced the signal. You cannot compute a
//    signal from a close and also transact at it. Measured cost of one bar of
//    lag over 11.6 years: at 15m it was +0.5 bp per trade -- the delay actually
//    HELPED -- and at 30m -0.4 bp. Momentum entries tolerate lag; this is a much
//    smaller correction than it would be for a mean-reversion entry, where the
//    edge decays inside the bar.
//
// 2. Sizing: strategy.cash -> whole lots.
//    v1 used default_qty_value = 1,000,000 with strategy.cash, which at NIFTY
//    24,110 buys 41 units: 0.55 of a 75-unit lot. The equity curve belonged to
//    a position nobody can hold.
//
// 3. Zero-volume guard. NSE index volume is 0 on every bar (measured: 212,626
//    of 212,626 on NIFTY), so v1's src*volume/volume was 0/0 and the whole z
//    engine produced na on any spot index, silently and with no plot.
//
// 4. Default exit: AVWAP -> Session only, because it measured better.
//
// WHAT THE SWEEP FOUND -- NIFTY and RELIANCE, 15m and 30m, 11.6 years
//   Gross of costs the engine works: +7.3% to +11.1% a year, Sharpe 0.8-1.3.
//   Net of 0.02% a side it does not: -0.9% to -10.9% a year.
//   Cost drag is 9.6%/yr at 30m and 18.7%/yr at 15m against a 7-10%/yr gross
//   edge. Break-even needs about 1.1 bp a side at 15m, 1.5 bp at 30m.
//   Set commission_value to 0 and run it again: the gap between the two runs is
//   the entire question.
// ===========================================================================

grpQ = "Sizing"
lotSize = input.int(75, "Contract lot size", minval = 1, group = grpQ,
     tooltip = "NIFTY 75, BANKNIFTY 35, FINNIFTY 65 at the time of writing. NSE " +
               "revises these -- check the contract. Set 1 for cash equity.")
nLots   = input.int(1, "Lots per trade", minval = 1, group = grpQ)

grpR = "Risk (optional)"
useSlPts = input.bool(false, "Fixed stop (points)", group = grpR)
slPts    = input.float(50.0,  "Stop points",   minval = 1, group = grpR)
useTpPts = input.bool(false, "Fixed target (points)", group = grpR)
tpPts    = input.float(100.0, "Target points", minval = 1, group = grpR)

grpV = "Display"
showAvwap = input.bool(true, "Plot anchored VWAP", group = grpV)
showTable = input.bool(true, "Show table", group = grpV)

'''

STRAT_TAIL = '''
// -- Orders -----------------------------------------------------------------
int qty = lotSize * nLots

if toHold
    strategy.close_all(comment = "X " + exitWhy)

if toBuy
    strategy.entry("BUY", strategy.long, qty = qty, comment = "BUY")
if toSell
    strategy.entry("SELL", strategy.short, qty = qty, comment = "SELL")

if useSlPts or useTpPts
    float avg = strategy.position_avg_price
    if strategy.position_size > 0
        strategy.exit("BX", from_entry = "BUY", stop = useSlPts ? avg - slPts : na, limit = useTpPts ? avg + tpPts : na)
    if strategy.position_size < 0
        strategy.exit("SX", from_entry = "SELL", stop = useSlPts ? avg + slPts : na, limit = useTpPts ? avg - tpPts : na)

// -- Visuals ----------------------------------------------------------------
plot(showAvwap and state != 0 ? avwap : na, "Anchored VWAP (from signal)",
     color = state == 1 ? color.new(#00897B, 0) : color.new(#EF6C00, 0),
     linewidth = 2, style = plot.style_linebr)
plot(vwMean, "Rolling VW mean", color = color.new(color.blue, 70), linewidth = 1)

plotshape(toBuy,  "BUY",  style = shape.labelup,   location = location.belowbar,
     color = #00897B, text = "BUY",  textcolor = color.white, size = size.small)
plotshape(toSell, "SELL", style = shape.labeldown, location = location.abovebar,
     color = #EF6C00, text = "SELL", textcolor = color.white, size = size.small)
plotshape(toHold, "EXIT", style = shape.xcross, location = location.abovebar,
     color = color.gray, size = size.tiny)

bgcolor(state == 1  ? color.new(#00897B, 92) : na)
bgcolor(state == -1 ? color.new(#EF6C00, 92) : na)

var table t = table.new(position.top_right, 2, 8, bgcolor = color.new(#111111, 15), border_width = 1)

f_cell(int r, string k, string v, color vc) =>
    table.cell(t, 0, r, k, text_color = color.gray, text_size = size.small)
    table.cell(t, 1, r, v, text_color = vc,         text_size = size.small)

if showTable and barstate.islast
    string stTxt = state == 1 ? "BUY" : state == -1 ? "SELL" : "HOLD"
    color  stCol = state == 1 ? #00897B : state == -1 ? #EF6C00 : color.gray
    table.cell(t, 0, 0, "State", text_color = color.gray, text_size = size.small)
    table.cell(t, 1, 0, stTxt,   text_color = stCol,      text_size = size.normal)
    f_cell(1, "Exit rule", exitRule, color.white)
    f_cell(2, "Qty",       str.tostring(qty) + " (" + str.tostring(nLots) + " lot)", color.white)
    f_cell(3, "AVWAP",     na(avwap) ? "-" : str.tostring(avwap, format.mintick), color.yellow)
    f_cell(4, "Bars in",   str.tostring(barsInPos), color.white)
    f_cell(5, "Net P&L",   str.tostring(strategy.netprofit, "#.##"),
           strategy.netprofit >= 0 ? color.lime : color.red)
    f_cell(6, "Trades",    str.tostring(strategy.closedtrades), color.white)
    f_cell(7, "Weighting", hasVol ? "volume" : "equal (no volume)", hasVol ? color.white : color.new(#C08A2E, 0))
'''


def check(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").split("\n")
    depth, prev_open, bad = 0, False, []
    ops = ("?", ":", ",", "+", "-", "*", "/", "and", "or", "=", "(", "[")
    for i, ln in enumerate(lines, 1):
        st = ln.strip()
        is_c = st.startswith("//")
        code = "" if is_c else ln.split("//")[0].rstrip()
        if (depth > 0 or prev_open) and st and not is_c:
            ind = len(ln) - len(ln.lstrip(" "))
            if ind % 4 == 0:
                bad.append((i, ind, st[:45]))
        if not is_c:
            depth += code.count("(") - code.count(")") + code.count("[") - code.count("]")
            prev_open = bool(code) and code.endswith(ops)
        else:
            prev_open = False
    print(f"  {path.name}: indents={bad or 'ok'} parens={depth} lines={len(lines)}")


def main() -> int:
    mon = HERE / "Noa_zVWAP_Monitor_v2.pine"
    strat = HERE / "Noa_zVWAP_AVWAP_v2.pine"
    mon.write_text(MON_HEAD + ENGINE + MON_TAIL, encoding="utf-8")
    strat.write_text(STRAT_HEAD + ENGINE + STRAT_TAIL, encoding="utf-8")

    e = ENGINE.rstrip()
    print("engine byte-identical in monitor :", e in mon.read_text(encoding="utf-8"))
    print("engine byte-identical in strategy:", e in strat.read_text(encoding="utf-8"))
    print("syntax:")
    check(mon)
    check(strat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
