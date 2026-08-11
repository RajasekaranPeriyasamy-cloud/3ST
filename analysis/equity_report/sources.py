"""Approved data sources for equity research reports.

Single source of truth for two things:

* ``ALLOWED_DOMAINS`` — passed to the Anthropic ``web_search`` / ``web_fetch``
  server tools, which makes the skill's "only use sources listed here" rule an
  API-level constraint rather than an instruction the model can drift from.
* ``seed_urls()`` — concrete fetch URLs for a ticker. ``web_fetch`` only
  retrieves URLs already present in the conversation, so the kickoff message
  has to carry real URLs, not the ``[TICKER]`` templates the reference file uses.

Mirrors ``prompt/references/data-sources.md``. Edit both together.
"""

from __future__ import annotations

import re

# Registrable domains only — the API matches subdomains too, so "screener.in"
# also covers "www.screener.in".
PRIMARY_FINANCIAL = [
    "screener.in",
    "trendlyne.com",
    "tickertape.in",
    "moneycontrol.com",
    "5paisa.com",
]

EXCHANGE_REGULATORY = [
    "bseindia.com",
    "nseindia.com",
    "sebi.gov.in",
    "mca.gov.in",
]

MACRO_SECTOR = [
    "rbi.org.in",
    "dbie.rbi.org.in",
    "mospi.gov.in",
    "cmie.com",
    "ibef.org",
    "trai.gov.in",
    "irdai.gov.in",
    "amfiindia.com",
    "pngrb.gov.in",
    "cea.nic.in",
    "dpiit.gov.in",
]

NEWS = [
    "economictimes.indiatimes.com",
    "livemint.com",
    "business-standard.com",
    "thehindubusinessline.com",
    "cnbctv18.com",
    "bqprime.com",
]

CREDIT_RATINGS = [
    "crisil.com",
    "icra.in",
    "careratings.com",
    "indiaratings.co.in",
    "acuite.in",
]

TECHNICAL = [
    "tradingview.com",
    "chartink.com",
]

ALLOWED_DOMAINS: list[str] = [
    *PRIMARY_FINANCIAL,
    *EXCHANGE_REGULATORY,
    *MACRO_SECTOR,
    *NEWS,
    *CREDIT_RATINGS,
    *TECHNICAL,
]

# Documented in data-sources.md as explicitly blocked: Yahoo Finance quotes for
# Indian tickers are US-session delayed and have shown 10-15% gaps vs the NSE
# close. Absent from ALLOWED_DOMAINS, so the API blocks it — this list exists so
# a future edit doesn't quietly re-add it.
BLOCKED_DOMAINS: list[str] = [
    "finance.yahoo.com",
    "in.finance.yahoo.com",
]


def _slug(company: str) -> str:
    """Company name -> URL slug used by Tickertape / 5Paisa."""
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", company or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    return s.strip("-")


def seed_urls(ticker: str, company: str = "") -> list[str]:
    """Concrete URLs to put in front of the model so ``web_fetch`` can reach them."""
    t = (ticker or "").strip().upper()
    if not t:
        return []
    urls = [
        f"https://www.screener.in/company/{t}/consolidated/",
        f"https://www.screener.in/company/{t}/",
        f"https://trendlyne.com/equity/{t}/",
        f"https://trendlyne.com/equity/technical-analysis/{t}/",
        f"https://www.nseindia.com/get-quotes/equity?symbol={t}",
        f"https://chartink.com/stocks/{t}.html",
    ]
    slug = _slug(company)
    if slug:
        urls.append(f"https://www.tickertape.in/stocks/{slug}-{t}")
        urls.append(f"https://www.5paisa.com/stocks/{slug}-share-price")
    return urls


def is_allowed(url: str) -> bool:
    """True when ``url``'s host sits under an approved domain."""
    m = re.match(r"^https?://([^/?#]+)", (url or "").strip(), flags=re.IGNORECASE)
    if not m:
        return False
    host = m.group(1).lower().split(":")[0]
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)
