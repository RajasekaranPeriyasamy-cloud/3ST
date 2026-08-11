# Analysis Frameworks for India Equity Research

---

## A. FUNDAMENTAL ANALYSIS

### A1. Business Quality Assessment
Evaluate using the 5-factor quality lens:
- **Business model**: Revenue streams, pricing power, entry barriers (moat type: cost/switching/network/intangible)
- **Competitive position**: Market share trend, industry structure (fragmented vs consolidated), barriers to replication
- **Management quality**: See Section E (Management Deep-Dive) — this deserves its own section in the report
- **Capital allocation**: ROE vs ROCE trend, reinvestment rate, dividend history, buybacks, acquisition track record
- **ESG / Governance flags**: SEBI enforcement actions, audit qualifications, related-party transactions

### A2. Financial Health Scorecard
Compute from fetched data:

| Metric | Formula | Flag if... |
|---|---|---|
| Revenue growth (3yr CAGR) | (Rev_T / Rev_T-3)^(1/3) - 1 | < 8% for growth stock |
| EBITDA margin trend | EBITDA / Revenue | Declining 3 consecutive Qs |
| PAT margin | PAT / Revenue | < industry median |
| ROE | PAT / Avg Equity | < 15% |
| ROCE | EBIT / Capital Employed | < 15% |
| ROIC | NOPAT / Invested Capital | < WACC = value destruction |
| Debt/Equity | Total Debt / Equity | > 1.5x (non-BFSI) |
| Interest Coverage | EBIT / Interest expense | < 3x |
| CFO/PAT ratio | Operating Cash Flow / PAT | < 0.8 (earnings quality concern) |
| Accruals Ratio | (Net Income - CFO - CFI) / Avg Net Assets | > 5% = accruals-heavy; earnings may not persist |
| Working capital days | (Receivable + Inventory - Payable) days | Deteriorating YoY |

### A3. DuPont Decomposition (ROE Attribution)
**Always run a 3-factor DuPont.** It tells you *why* ROE is high/low — and whether it's sustainable.

```
ROE = Net Profit Margin × Asset Turnover × Equity Multiplier
    = (PAT / Revenue) × (Revenue / Total Assets) × (Total Assets / Equity)
```

| Component | FY[T-2] | FY[T-1] | FY[T] | Trend | Interpretation |
|---|---|---|---|---|---|
| Net Profit Margin (%) | | | | ↑/↓ | Pricing power / cost efficiency |
| Asset Turnover (x) | | | | ↑/↓ | Capital efficiency |
| Equity Multiplier (x) | | | | ↑/↓ | Leverage (higher = more risk) |
| **ROE (%)** | | | | | |

Interpretation guide:
- ROE rising due to higher margins → quality (sustainable)
- ROE rising due to higher leverage → risk (unsustainable if debt rises)
- ROE rising due to higher asset turnover → efficiency (positive, but watch for limits)

### A4. Capital Allocation Quality (ROIC vs WACC)
Investment bankers use the ROIC/WACC spread as the primary value creation metric.

- **ROIC** = NOPAT / (Total Equity + Total Debt - Cash)
  where NOPAT = EBIT × (1 - Tax Rate)
- **WACC** = Ke × (E/V) + Kd × (D/V) × (1-T)
  - Ke = Risk-free rate (10Y G-Sec, fetch from RBI) + Beta × ERP (5.5–6.5%)
  - Kd = Interest expense / Average Debt (from P&L and BS)
  - India ERP benchmark: 5.5–6.5% (use 6% as default)

| Metric | FY[T-2] | FY[T-1] | FY[T] |
|---|---|---|---|
| ROIC (%) | | | |
| WACC (%) | | | |
| Value Spread (ROIC − WACC) | | | |

If ROIC > WACC: Every rupee reinvested creates value → justify premium valuation
If ROIC < WACC: Growth is destroying value → discount warranted

### A5. Valuation
**Primary methods** (use at least 2; 3 for high-conviction reports):

#### P/E Re-rating
- Fetch TTM EPS and forward EPS estimate (Trendlyne / news / management guidance)
- Target Price = Forward EPS × Target P/E
- Target P/E = 5-year median P/E OR sector median P/E (justify)

#### EV/EBITDA
- EV = Market Cap + Net Debt (from balance sheet)
- Target Price = (Target EV/EBITDA × Forward EBITDA − Net Debt) / Shares outstanding
- Use sector-appropriate multiples; cite the source

#### DCF (for businesses with stable/predictable cash flows)
- 3-stage model: explicit forecast (3 yrs) → fade (3-4 yrs) → terminal
- WACC: Risk-free = current 10Y G-Sec (fetch from RBI), Beta from Trendlyne, ERP = 5.5–6.5%
- Terminal growth: 5–6% (domestic consumption), 4–5% (cyclicals/commodities)
- Always show sensitivity table (see Section G — Sensitivity Analysis)

#### P/B (for BFSI stocks)
- Target Price = Target P/B × Latest Book Value per Share
- Target P/B = derived from Gordon Growth Model: g / (RoE − g) where g = sustainable growth rate

#### EV/Sales (for high-growth, low-profit companies)
- Use when PAT is negative or < 3% margin; compare against 3-yr revenue CAGR
- Benchmark against listed peers; flag if premium to peers is not justified by higher growth

**Blended target**: Weight methods by relevance (e.g., 50% P/E + 30% EV/EBITDA + 20% DCF). Justify weights.
**Upside/Downside**: Always compute % upside from CMP to target.

---

## B. TECHNICAL ANALYSIS

### B1. Trend Analysis
- **Primary trend**: Above/below 200-DMA? (Bullish/Bearish structural)
- **Intermediate trend**: Above/below 50-DMA?
- **Short-term**: Price vs 20-DMA
- **Golden/Death Cross**: 50-DMA crossing above/below 200-DMA — flag if recently occurred

### B2. Momentum Indicators
- **RSI (14)**:
  - > 70: Overbought (caution for fresh entry; check divergence)
  - 40–70: Neutral to bullish
  - < 30: Oversold (potential reversal; look for bullish divergence)
  - **RSI Divergence**: Price making new high but RSI making lower high = bearish divergence (significant warning)
- **MACD (12,26,9)**: Bullish crossover (MACD > Signal) vs Bearish crossover; histogram expanding/contracting
- **ADX (14)**: > 25 = strong trend; < 20 = consolidation/ranging
- **Stochastic (9,6)**: Confirm overbought (>80) / oversold (<20) signals alongside RSI

### B3. Volume & Delivery Analysis
- Volume trend: rising/falling on up-days vs down-days (rising volume on up-days = accumulation)
- Delivery %: High delivery (>50%) on up-moves = conviction buying (not intraday speculation)
- **Bulk/Block deals** (last 90 days): Fetch from NSE/BSE. Institutional buying = bullish; promoter selling = alert
- **F&O data** (if applicable): Open interest trend, Put/Call ratio, max pain level

### B4. Support & Resistance
- Identify key levels: 52-week high, 52-week low, prior swing highs/lows, round numbers
- Fibonacci retracement levels from the most recent major swing: 23.6%, 38.2%, 50%, 61.8%
- State: "Strong support at ₹X (200-DMA + Fib 61.8%), resistance at ₹Y"

### B5. Chart Patterns (only if clearly identifiable from fetched data — never force)
- Continuation: Cup & Handle, Bull Flag, Ascending Triangle
- Reversal: Head & Shoulders (bearish), Inverse H&S (bullish), Double Bottom/Top

### B6. Relative Strength vs Index
- Compare stock's 3M, 6M, 12M return against: (a) Nifty 50, (b) Nifty 500 or Small/Mid-cap index, (c) sector index
- Outperformance vs index = positive alpha; underperformance = market is telling you something
- State: "Stock has returned +[X]% in last 12M vs Nifty 50's +[Y]% — [outperformed/underperformed] by [Z]%"
- Source: Screener.in price chart, Trendlyne

### B7. Technical Summary
Rate: **Bullish / Cautiously Bullish / Neutral / Cautiously Bearish / Bearish**
Provide: entry zone, stop-loss, and technical target (separately from fundamental target).

---

## C. RISK FRAMEWORK

### C1. Risk Categories (always address all applicable):
1. **Earnings quality risk**: Accruals, receivables growth > revenue growth, audit qualifications
2. **Balance sheet risk**: Debt trajectory, interest coverage trend, off-balance-sheet liabilities
3. **Regulatory / compliance risk**: SEBI actions, sectoral regulator, pending litigations (fetch from BSE announcements)
4. **Governance / promoter risk**: Pledging %, related party transactions, insider selling patterns
5. **Currency risk**: INR/USD or INR/CNY exposure (for importers like electronics distributors)
6. **Commodity risk**: Input cost exposure (crude, metals, agri commodities)
7. **Competition risk**: New entrants (especially Amazon/Flipkart for B2B), pricing pressure, private label threat
8. **Execution risk**: New business line ramp-up, capex delays, capacity utilization
9. **Macro risk**: RBI rate cycle, inflation, GDP slowdown, export market conditions
10. **Valuation risk**: Premium P/E leaving no margin of safety; any miss triggers sharp de-rating
11. **Concentration risk**: Customer/supplier/geography concentration

Rate each: **CRITICAL / HIGH / MEDIUM / LOW** with specific evidence and what to monitor.

### C2. Forensic Accounting Flags
Run these checks on every report (using Screener.in and BSE filings):

| Check | Flag if... | Source |
|---|---|---|
| Revenue recognition | Revenue growing much faster than cash receipts | OCF vs Revenue in Screener |
| Receivables quality | Debtor Days rising while revenue accelerating | Working capital ratios |
| Inventory build-up | Inventory days rising sharply + OCF negative | Balance sheet trend |
| Auditor change | New auditor appointed without explanation | BSE announcements |
| Audit qualification | Any "except for" or "emphasis of matter" | Annual report |
| Related party transactions | Growing RPT as % of revenue | Annual report notes |
| Contingent liabilities | Large undisclosed legal/tax claims | Annual report notes |
| Promoter pledging trend | Rising pledged % QoQ | BSE shareholding |

If 3+ flags are raised, add a ⚠️ FORENSIC ALERT callout box in the report.

---

## D. RECOMMENDATION FRAMEWORK

### Rating definitions:
| Rating | Condition |
|---|---|
| **BUY** | Expected total return > 15% over 12 months, fundamentals intact |
| **ADD / Accumulate** | Expected return 8–15% over 12 months |
| **HOLD** | Expected return 0–8%, no major negative catalysts |
| **REDUCE** | Expected return −5% to 0%, or fundamentals weakening |
| **SELL** | Expected return < −5% or significant fundamental deterioration |

### Target Price: Must be derived from valuation models. Never state without showing the model.
### Catalysts: List 3–5 near-term triggers with estimated timeline and probability.

---

## E. MANAGEMENT QUALITY DEEP-DIVE

This section is frequently the difference between a good and great research report. Cover:

### E1. Promoter Background
- Founder/promoter history: previous ventures, sector expertise, years in industry
- Track record at this company: how has the business grown under their stewardship?
- Source: company website, LinkedIn (via web search), annual report Board of Directors section

### E2. Corporate Governance Score
Check all of the following from BSE filings / annual report:
- **Board composition**: % independent directors (SEBI minimum: 1/3rd; ideal: 50%+)
- **Audit committee**: Chaired by independent director? Meets ≥4 times/year?
- **Auditor quality**: Big 4 / reputed mid-tier (Grant Thornton, BDO, S.R. Batliboi) vs unknown
- **Auditor tenure**: Long tenure (>10 yrs) can indicate insufficient scrutiny
- **Related party transactions**: Volume and nature — routine operational or questionable capital flows?
- **Promoter pledging**: Current % and trend (fetch from shareholding pattern)
- **SEBI enforcement history**: Any warning letters, orders, consent orders (search SEBI website)

Rate governance: **Strong / Adequate / Weak** with evidence.

### E3. Capital Allocation Track Record
Over the last 3-5 years, how has management deployed free cash flow?
- Reinvestment in core business (capacity, technology, sales)
- Acquisitions: Did they create or destroy value? (Check ROIC post-acquisition)
- Dividends / buybacks: Shareholder-friendly or cash-hoarding?
- Salary to promoter vs PAT: Flag if promoter remuneration > 5% of PAT in a small company

### E4. Management Guidance Track Record
Compare what management guided vs what they delivered over the last 4–6 quarters:
- Revenue guidance vs actual
- Margin guidance vs actual
- Capex guidance vs actual spend

Rate credibility: **High (consistently meets/beats) / Medium (mixed) / Low (consistently misses)**
Source: Screener.in concall transcripts, BSE earnings releases

### E5. Concall Tone Analysis
From the most recent 1–2 concall transcripts, flag:
- **Positive signals**: Specific guidance with numbers, candid discussion of challenges, new business wins
- **Caution signals**: Vague language ("demand environment uncertain"), excessive optimism without data, avoiding analyst questions
- **Red flags**: Changing accounting policies mid-year, blaming macro for company-specific problems, sudden CEO/CFO resignation

---

## F. TAM & MARKET SIZING

For every report, quantify the total addressable market to contextualize the company's growth runway.

### F1. TAM Calculation
- **Top-down**: Total India market size × addressable % for this company's product/service
- **Bottom-up**: Number of potential customers × average spend per customer
- Source: IBEF sector reports, MOSPI data, RBI sectoral credit data, industry associations

Present as:
```
India [Sector] TAM: ₹[X,000] Cr (FY[T]) growing at [Y]% CAGR
Company's current market share: [Z]% (₹[revenue] Cr / ₹[TAM] Cr)
Achievable market share in 5 years: [A]% → Revenue potential: ₹[B] Cr
```

### F2. Runway Assessment
- At current growth rates, how many years of runway does the company have?
- Is the market growing fast enough to support the company's growth without taking share from competitors?
- What % of TAM is already captured — is it still early-stage or approaching saturation?

---

## G. SENSITIVITY ANALYSIS

Every valuation model has assumptions. Show what happens when they change.

### G1. Target Price Sensitivity Table
Build a 2-variable sensitivity table for the primary valuation method:

**Example: P/E Re-rating sensitivity**

| | P/E: 30x | P/E: 35x | P/E: 40x | P/E: 45x | P/E: 50x |
|---|---|---|---|---|---|
| EPS: ₹18 | ₹540 | ₹630 | ₹720 | ₹810 | ₹900 |
| EPS: ₹20 | ₹600 | ₹700 | ₹800 | ₹900 | ₹1,000 |
| EPS: ₹22 | ₹660 | ₹770 | ₹880 | ₹990 | ₹1,100 |
| EPS: ₹24 | ₹720 | ₹840 | ₹960 | ₹1,080 | ₹1,200 |
| EPS: ₹26 | ₹780 | ₹910 | ₹1,040 | ₹1,170 | ₹1,300 |

Highlight the base case cell. This immediately shows the upside/downside range.

**For DCF: 2-variable sensitivity on revenue growth vs WACC:**

| | WACC: 10% | WACC: 11% | WACC: 12% | WACC: 13% |
|---|---|---|---|---|
| Rev. CAGR: 15% | | | | |
| Rev. CAGR: 20% | | | | |
| Rev. CAGR: 25% | | | | |
| Rev. CAGR: 30% | | | | |

### G2. Margin of Safety Assessment
- At the base case target, what margin of safety does the investor have?
- What is the "break-even" growth rate required to justify the CMP?
- Example: "At ₹999, the stock is pricing in 28% EPS CAGR. If growth falls to 20%, fair value is ₹750 — a 25% downside."

---

## H. INSTITUTIONAL FLOW TRACKER

Institutional money flows are one of the most powerful leading indicators for stock price. Build this from BSE/NSE data.

### H1. Bulk & Block Deal History (last 6 months)
Fetch from: `https://www.bseindia.com/markets/equity/EQReports/BulkDeals.aspx`
or `web_search: "[TICKER] bulk deal block deal 2025"`

| Date | Buyer/Seller | Qty (shares) | Price (₹) | Type | Significance |
|---|---|---|---|---|---|
| [Date] | [Institution name] | [X] | [₹Y] | Buy / Sell | [Accumulation / Distribution] |

### H2. Mutual Fund Holding Trend (last 4 quarters)
Fetch from Screener.in or Trendlyne MF holdings section.

| Fund Name | Jun-[T] | Sep-[T] | Dec-[T] | Mar-[T+1] | Trend |
|---|---|---|---|---|---|
| [Fund 1] | [X] Lakh shares | | | | ↑ Accumulating |
| [Fund 2] | | | | | ↓ Exiting |

### H3. Flow Signal Interpretation
- Large domestic MF entering / adding = strong conviction signal (they have done deeper due diligence)
- FII entering = global risk-on for India; exiting = risk-off or stock-specific concern
- Promoter pledging falling = confidence; rising = distress signal

---

## I. EVENT CALENDAR & CATALYST TRACKER

Forward-looking visibility is what separates a current research report from a stale one.

### Upcoming Catalysts (next 6 months):

| Date (est.) | Event | Expected Outcome | Bull Impact | Bear Impact | Probability |
|---|---|---|---|---|---|
| [Month] | Q[N] FY[T] Results | Revenue ~₹X Cr | Beat → re-rate higher | Miss → de-rate | [H/M/L] |
| [Month] | [Defense contract announcement] | PO formalization | Structural re-rating | Trial cancellation | [H/M/L] |
| [Month] | [AGM / Record Date] | Bonus shares | Liquidity premium | — | [H/M/L] |
| [Month] | [Budget / Policy event] | PLI expansion | Demand stimulus | Policy rollback | [H/M/L] |
| [Month] | [Capacity commissioning] | Revenue inflection | Margin expansion | Delay | [H/M/L] |

**Highest-conviction catalyst**: [Identify the single event most likely to move the stock materially and when it will happen]
