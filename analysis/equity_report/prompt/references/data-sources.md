# Approved Data Sources for India Equity Research

## ⚡ RULE: Only use sources listed here. Reject all others.

---

## PRIMARY FINANCIAL DATA

### 1. Screener.in ⭐ (Most important — use first)
- **What**: Financials (P&L, BS, CF), ratios, shareholding, concall transcripts, peer comparison
- **URL pattern**: `https://www.screener.in/company/[TICKER]/consolidated/`
- **Standalone**: `https://www.screener.in/company/[TICKER]/`
- **⚠️ CMP WARNING**: Screener.in's "Current Price" field is often a **stale cached value** that may be days old (it displays a date like "13 Feb - close price" even when fetched on Feb 27). **Do NOT use Screener.in for CMP.** Use Step 1.5 (web_search + Tickertape) for live price. Use Screener.in only for historical financials, ratios, and shareholding data.
- **Notes**: Free, excellent data quality for historical data. Use consolidated view for holding companies.

### 2. Trendlyne.com
- **What**: Forecasts, DVM score, promoter pledging, bulk deals, technical signals, analyst targets
- **URL pattern**: `https://trendlyne.com/equity/[TICKER]/`
- **Concalls**: `https://trendlyne.com/equity/concall/[TICKER]/`

### 3. Tickertape.in ⭐ (Best source for live CMP)
- **What**: Live/last-close price with date+time stamp, financials, ratios, peer comparison, quality checklist
- **URL**: `https://www.tickertape.in/stocks/[company-slug]-[TICKER]`
- **Why preferred for CMP**: Tickertape shows a clearly date-stamped last NSE closing price, making it easy to verify the price is current. Always use this to confirm the CMP from Step 1.5.

### 4. Moneycontrol.com
- **What**: Live price, historical data, news, analyst views
- **URL**: `https://www.moneycontrol.com/india/stockpricequote/[sector]/[company]/[CODE]`

### 4a. 5Paisa ⭐ (Confirmed CMP fallback)
- **What**: Confirmed NSE close price with date stamp, P/E, P/B, Market Cap
- **URL**: `https://www.5paisa.com/stocks/[company-name]-share-price`
- **Why useful**: Shows a verified NSE close price with explicit date — reliable secondary check for CMP after Tickertape.

### ❌ BLOCKED: Yahoo Finance (NEVER use for CMP)
- **Why blocked**: Yahoo Finance quotes for Indian stocks (e.g., HDFCBANK.NS) reflect **US-session delayed prices** that systematically differ from NSE close by 10–15%. In the 5 Mar 2026 benchmark, Yahoo Finance showed HDFCBANK at ₹1,001.60 while the actual NSE close (Tickertape) was ₹869.05 — a 15% discrepancy.
- **Use for**: Background company description, US-listed Indian ADRs only.
- **Never use for**: NSE/BSE CMP or any price that will appear in the report.

---

## EXCHANGE / REGULATORY FILINGS (Primary source for official disclosures)

### 5. BSE India ⭐
- **What**: Quarterly results, annual reports, shareholding pattern, corporate announcements, investor presentations
- **Quarterly results**: `https://www.bseindia.com/stock-share-price/[COMPANY]/[TICKER]/[BSE_CODE]/`
- **Filings search**: `https://www.bseindia.com/corporates/ann.html`
- **Notes**: Always cross-check management commentary here

### 6. NSE India
- **What**: Live data, F&O data, bulk/block deals, corporate actions
- **URL**: `https://www.nseindia.com/get-quotes/equity?symbol=[TICKER]`
- **Corporate filings**: `https://www.nseindia.com/companies-listing/corporate-filings-financial-results`

### 7. SEBI (Securities and Exchange Board of India)
- **What**: Regulatory orders, insider trading disclosures, substantial acquisition disclosures
- **URL**: `https://www.sebi.gov.in/`
- **SAST disclosures**: `https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognisedFpi=yes&intmId=13`

### 8. Ministry of Corporate Affairs (MCA)
- **What**: Director details, charges, annual filings
- **URL**: `https://www.mca.gov.in/`

---

## MACRO / SECTOR DATA

### 9. RBI (Reserve Bank of India)
- **What**: Monetary policy, credit data, sector-level banking stats
- **URL**: `https://www.rbi.org.in/`
- **Database**: `https://dbie.rbi.org.in/`

### 10. MOSPI (Ministry of Statistics)
- **What**: GDP, IIP, WPI, CPI data
- **URL**: `https://mospi.gov.in/`

### 11. CMIE (Centre for Monitoring Indian Economy)
- **What**: Economic indicators, corporate database
- **URL**: `https://www.cmie.com/` (via news search)

### 12. IBEF (India Brand Equity Foundation)
- **What**: Sector reports, industry overviews
- **URL**: `https://www.ibef.org/industry`

### 13. Sectoral regulators (as applicable)
- TRAI: `https://www.trai.gov.in/` (Telecom)
- IRDAI: `https://www.irdai.gov.in/` (Insurance)
- AMFI: `https://www.amfiiindia.com/` (Mutual Funds / AMCs)
- PNGRB: `https://www.pngrb.gov.in/` (Gas)
- CEA: `https://cea.nic.in/` (Power)
- DPIIT: `https://dpiit.gov.in/` (Manufacturing/FDI)

---

## NEWS & MEDIA (Use for qualitative context only — verify numbers against primary sources)

### Approved news sources (in order of preference):
1. **Economic Times Markets** — `https://economictimes.indiatimes.com/markets`
2. **Mint** — `https://www.livemint.com/`
3. **Business Standard** — `https://www.business-standard.com/`
4. **The Hindu BusinessLine** — `https://www.thehindubusinessline.com/`
5. **CNBCTV18** — `https://www.cnbctv18.com/`
6. **Moneycontrol News** — `https://www.moneycontrol.com/news/`
7. **BloombergQuint / BQ Prime** — `https://www.bqprime.com/`

### ❌ NOT approved for financial data:
- Reddit, Twitter/X, Telegram channels, Substack
- Any blog or personal website
- Wikipedia (for financial figures)
- Zerodha Varsity (educational only, not for specific company data)

---

## CREDIT RATINGS

### Approved rating agencies:
- **CRISIL**: `https://www.crisil.com/en/home/our-businesses/ratings.html`
- **ICRA**: `https://www.icra.in/`
- **CARE Ratings**: `https://www.careratings.com/`
- **India Ratings (Fitch)**: `https://www.indiaratings.co.in/`
- **Acuité Ratings**: `https://www.acuite.in/`

Search pattern: `web_search: "[COMPANY] credit rating CRISIL ICRA CARE 2025"`

---

## TECHNICAL ANALYSIS DATA

### Approved sources:
- **TradingView** (via web_search snippets): `web_search: "[TICKER] NSE technical analysis tradingview"`
- **Chartink**: `https://chartink.com/stocks/[TICKER].html`
- **Trendlyne technical**: `https://trendlyne.com/equity/technical-analysis/[TICKER]/`

### Technical indicators to fetch:
- RSI (14-day), MACD, 50-DMA, 200-DMA, Bollinger Bands
- 52-week high/low, volume trends
- Support/resistance levels
- Delivery percentage (from NSE)

---

## FETCH SEQUENCE (Recommended order to minimize tool calls)

0. **LIVE CMP FIRST** (before any other fetch):
   - Claude's fetch method: `web_search: "[TICKER] NSE share price [MONTH YEAR]"` — Google's search card pulls from Google Finance data, giving the current or last-close price with a date stamp. If unclear, follow up with `web_fetch: https://www.tickertape.in/stocks/[company-slug]-[TICKER]`
   - Record: `CMP = ₹[X] as of [DATE] [Source: Google Finance via web search]`
   - ⚠️ **Never write raw formulas in the report** — resolve to actual ₹ numbers before writing
   - ⚠️ **Never use Yahoo Finance** — it shows US-session delayed quotes, not NSE close prices
1. `web_fetch: https://www.screener.in/company/[TICKER]/consolidated/` → financials, ratios, shareholding (ignore the price shown here)
2. `web_search: "[TICKER] BSE quarterly results 2026"` → fetch BSE filing
3. `web_search: "[COMPANY] concall transcript Q[N] FY[YY]"` → fetch transcript
4. `web_search: "[TICKER] news last 3 months site:economictimes.indiatimes.com OR site:livemint.com"`
5. `web_search: "[TICKER] technical analysis 2026"`
6. **PEER COMPARISON** (mandatory for Valuation section):
   - `web_fetch: https://trendlyne.com/equity/[TICKER]/` → look for Peers section with P/E, P/B, ROE comparisons
   - Fallback: `web_search: "[COMPANY] vs peers [SECTOR] P/E P/B ROE comparison 2026"`
   - Minimum 3 peers required in the valuation section peer table
7. **ANALYST CONSENSUS TARGET** (mandatory if available):
   - `web_fetch: https://trendlyne.com/equity/[TICKER]/` → look for analyst target price and buy/sell/hold breakdown
   - Record as: `Analyst consensus: ₹[X] target, [N] analysts ([A] Buy / [B] Hold / [C] Sell) [Source: Trendlyne, DATE]`
   - If not found, note "Analyst consensus not available at time of report"
8. (If needed) Sector macro sources
