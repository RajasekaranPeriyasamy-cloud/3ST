"""Offline sentiment + category engine for the news desk.

The default scorer. Zero dependencies, deterministic, instant — which is what
lets ``pytest`` score real fixture headlines with no network and no API key.

Method follows the reference notebook's shape (a label plus a VADER-like
compound in [-1, 1]) but the term weights are finance-specific rather than
general-purpose: "beat", "upgrade" and "order win" carry signal in a market feed
that a general lexicon scores as neutral, and "profit booking" is bearish here
despite containing "profit".

Deliberately headline-level. RSS gives a title and a short summary, and most
Indian publishers block full-article retrieval, so there is no body to score.
"""

from __future__ import annotations

import re
from typing import Any

# --- term weights -----------------------------------------------------------
# Magnitudes are on a rough 1-3 scale: 1 = leaning, 2 = clear, 3 = emphatic.
# Multi-word phrases are matched and consumed before single tokens, so
# "profit booking" wins over "profit" and does not double-count.

_POSITIVE: dict[str, float] = {
    "surge": 2.5, "surges": 2.5, "soar": 3.0, "soars": 3.0, "jump": 2.0, "jumps": 2.0,
    "rally": 2.0, "rallies": 2.0, "gain": 1.5, "gains": 1.5, "rise": 1.2, "rises": 1.2,
    "climb": 1.5, "climbs": 1.5, "advance": 1.2, "advances": 1.2, "spike": 2.0,
    "record high": 3.0, "all-time high": 3.0, "52-week high": 2.5, "lifetime high": 3.0,
    "multi-year high": 2.5, "upper circuit": 2.5,
    "beat": 2.0, "beats": 2.0, "outperform": 2.0, "outperforms": 2.0, "outperformer": 2.0,
    "upgrade": 2.5, "upgrades": 2.5, "upgraded": 2.5, "re-rating": 2.0,
    "buy rating": 2.5, "overweight": 2.0, "accumulate": 1.5,
    "profit rises": 2.5, "profit jumps": 3.0, "profit surges": 3.0,
    "strong": 1.8, "robust": 2.0, "healthy": 1.5, "solid": 1.5, "resilient": 1.5,
    "growth": 1.2, "expansion": 1.2, "recovery": 1.5, "rebound": 2.0, "turnaround": 2.0,
    "order win": 2.5, "bags order": 2.5, "wins order": 2.5, "new order": 2.0,
    "contract win": 2.5, "bags contract": 2.5, "wins contract": 2.5,
    "dividend": 1.2, "bonus issue": 1.5, "buyback": 1.8,
    "approval": 1.5, "approved": 1.5, "clearance": 1.5,
    "partnership": 1.5, "tie-up": 1.5, "milestone": 1.5, "breakthrough": 2.0,
    "upside": 1.8, "raises target": 2.5, "target price raised": 2.5,
    "bullish": 2.5, "optimistic": 2.0, "positive": 1.5, "favourable": 1.5, "favorable": 1.5,
    "demand picks up": 2.0, "demand revival": 2.0, "strong demand": 2.2,
    "cuts debt": 2.0, "debt reduction": 2.0, "deleveraging": 1.5,
    "beats estimates": 3.0, "above estimates": 2.5, "ahead of estimates": 2.5,
}

_NEGATIVE: dict[str, float] = {
    "fall": -1.5, "falls": -1.5, "drop": -1.8, "drops": -1.8, "decline": -1.5,
    "declines": -1.5, "slump": -2.5, "slumps": -2.5, "plunge": -3.0, "plunges": -3.0,
    "crash": -3.0, "crashes": -3.0, "tumble": -2.5, "tumbles": -2.5, "sink": -2.2,
    "sinks": -2.2, "slide": -2.0, "slides": -2.0, "skid": -2.0, "skids": -2.0,
    "record low": -3.0, "52-week low": -2.5, "lower circuit": -2.5, "multi-year low": -2.5,
    "miss": -2.0, "misses": -2.0, "underperform": -2.0, "underperforms": -2.0,
    "downgrade": -2.5, "downgrades": -2.5, "downgraded": -2.5, "de-rating": -2.0,
    "sell rating": -2.5, "underweight": -2.0,
    "profit falls": -2.5, "profit drops": -2.5, "loss widens": -3.0, "posts loss": -2.8,
    "net loss": -2.5, "loss-making": -2.5, "profit booking": -1.5,
    "weak": -2.0, "weakness": -2.0, "sluggish": -2.0, "subdued": -1.5,
    "muted": -1.2, "tepid": -1.5, "fragile": -1.8, "headwind": -1.8, "headwinds": -1.8,
    "concern": -1.5, "concerns": -1.8, "worry": -1.5, "worries": -1.5, "fear": -2.0,
    "fears": -2.0, "panic": -3.0, "uncertainty": -1.5, "caution": -1.2, "cautious": -1.2,
    "margin pressure": -2.2, "margin concerns": -2.2, "cost pressure": -1.8,
    "probe": -2.2, "investigation": -2.0, "raid": -2.5, "fraud": -3.0, "scam": -3.0,
    "penalty": -2.2, "show cause": -2.0, "lawsuit": -2.0,
    "default": -3.0, "insolvency": -3.0, "bankruptcy": -3.0,
    "resigns": -2.0, "resignation": -2.0, "steps down": -1.8, "quits": -1.8,
    "layoff": -2.2, "layoffs": -2.2, "job cuts": -2.2, "shutdown": -2.2,
    "recall": -2.0, "halted": -1.8, "suspended": -2.2, "ban": -2.5,
    "delay": -1.5, "delayed": -1.5, "deferred": -1.5, "cancelled": -2.0,
    "bearish": -2.5, "pessimistic": -2.0, "negative": -1.5, "unfavourable": -1.5,
    "stake sale": -1.2, "pledge": -1.8, "dilution": -1.8,
    "cuts target": -2.5, "target price cut": -2.5, "downside": -1.8,
    "below estimates": -2.5, "misses estimates": -3.0, "disappoints": -2.5,
    "glitch": -1.8, "outage": -2.0, "breach": -2.5,
    "discount to issue price": -2.0, "weak debut": -2.5, "weak market debut": -2.5,
}

# Flip the sign of a term appearing within _NEGATION_WINDOW tokens after one of
# these. "not weak" and "denies downgrade" are the cases that matter in headlines.
_NEGATORS = frozenset({
    "not", "no", "never", "none", "without", "despite", "denies", "denied",
    "dismisses", "unlikely", "refutes", "rules out", "fails to",
})
_NEGATION_WINDOW = 3

_INTENSIFIERS: dict[str, float] = {
    "sharply": 1.5, "steeply": 1.5, "significantly": 1.4, "substantially": 1.4,
    "massively": 1.6, "heavily": 1.4, "strongly": 1.3, "materially": 1.3,
    "slightly": 0.6, "marginally": 0.5, "mildly": 0.6, "modestly": 0.7,
}
_INTENSIFIER_WINDOW = 2

# Normalisation constant for the compound. Larger = a headline needs more loaded
# terms to saturate towards +/-1. 15.0 is VADER's alpha and behaves well on the
# 8-15 word headlines an RSS feed actually carries.
_ALPHA = 15.0

# Below this magnitude the label is "neutral". At 0.15 a single weak term
# ("rises", raw 1.2 -> compound 0.30) still reads as directional, which matches
# how the reference screenshots tag routine wire copy.
_NEUTRAL_BAND = 0.15

# --- category inference -----------------------------------------------------
# Ordered: first match wins, so specific beats generic. The LLM backend
# overrides these when enabled; this keeps the tag column populated for free in
# the default offline configuration.

_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("US Fed", (
        "fomc", "federal reserve", "powell", "rate hike", "treasury yield",
        "us cpi", "dollar index", "jackson hole", "fed rate", "fed policy",
    )),
    ("Corporate Shock", (
        "fraud", "scam", "raid", "probe", "resign", "steps down", "default",
        "insolvency", "bankruptcy", "recall", "layoff", "shutdown", "auditor quits",
    )),
    # Above Commodities on purpose: a broker note on "Vedanta Aluminium" is a
    # brokerage item, not a metals item, and metal words show up in company
    # names far more often than the reverse.
    ("Brokerage", (
        "brokerage", "initiate coverage", "target price", "upgrade", "downgrade",
        "buy rating", "sell rating", "overweight", "underweight", "analyst",
    )),
    ("Commodities", (
        "gold", "silver", "crude", "brent", "copper", "aluminium", "aluminum",
        "zinc", "nickel", "bullion", "opec", "natural gas", "palm oil", "commodit",
    )),
    ("Corporate Action", (
        "dividend", "record date", "bonus issue", "stock split", "buyback",
        "rights issue", " agm", " egm", "e-voting", "open offer", "demerger",
        "amalgamation", "scheme of arrangement",
    )),
    ("IPO", ("ipo", "lists at", "market debut", "grey market", "anchor book", "drhp")),
    ("Earnings", (
        " q1", " q2", " q3", " q4", "quarterly", "results", "profit", "revenue",
        "ebitda", " pat ", "margin", "earnings", "guidance",
    )),
    ("Regulatory", (
        "sebi", " rbi", "nclt", " cci ", "irdai", "trai", " cbi ", "income tax",
        " gst", "tribunal", "supreme court", "high court", "penalty",
    )),
    ("Macro", (
        " gdp", "inflation", " cpi", " wpi", " iip", " pmi", "fiscal deficit",
        "trade deficit", "monetary policy", "repo rate", "budget",
    )),
    ("Ownership", (
        "promoter", "stake", "pledge", "block deal", "bulk deal", "acquires",
        "open market purchase", " fpi", " fii", " dii",
    )),
    ("Deals", (
        "order win", "bags order", "wins contract", "contract win", "acquisition",
        "merger", "joint venture", "tie-up", "partnership", " mou",
    )),
)

_DEFAULT_CATEGORY = "Neutral/Markets"

# --- matching ---------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'\-\.]*")

_ALL_TERMS: dict[str, float] = {**_POSITIVE, **_NEGATIVE}

# Longest first so "profit booking" is consumed before "profit", and
# "misses estimates" before "misses".
_PHRASES: tuple[tuple[str, float], ...] = tuple(
    sorted(
        ((term, weight) for term, weight in _ALL_TERMS.items() if " " in term),
        key=lambda kv: -len(kv[0]),
    )
)


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _negated(tokens: list[str], index: int) -> bool:
    """True if a negator sits in the few tokens before ``index``."""
    lo = max(0, index - _NEGATION_WINDOW)
    window = tokens[lo:index]
    if any(tok in _NEGATORS for tok in window):
        return True
    joined = " ".join(window)
    return any(neg in joined for neg in _NEGATORS if " " in neg)


def _intensity(tokens: list[str], index: int) -> float:
    lo = max(0, index - _INTENSIFIER_WINDOW)
    factor = 1.0
    for tok in tokens[lo:index]:
        factor *= _INTENSIFIERS.get(tok, 1.0)
    return factor


def raw_score(text: str) -> float:
    """Sum of matched term weights, before compound normalisation."""
    lowered = (text or "").lower()
    total = 0.0
    consumed = lowered

    # Phrases first, removing each hit so its component words are not re-scored.
    for phrase, weight in _PHRASES:
        while phrase in consumed:
            head = consumed.split(phrase, 1)[0]
            consumed = consumed.replace(phrase, " ", 1)
            head_tokens = _tokenize(head)
            sign = -1.0 if _negated(head_tokens, len(head_tokens)) else 1.0
            total += weight * sign

    tokens = _tokenize(consumed)
    for i, tok in enumerate(tokens):
        weight = _ALL_TERMS.get(tok)
        if weight is None:
            continue
        sign = -1.0 if _negated(tokens, i) else 1.0
        total += weight * sign * _intensity(tokens, i)
    return total


def _compound_of(total: float) -> float:
    if total == 0.0:
        return 0.0
    return round(total / ((total * total + _ALPHA) ** 0.5), 4)


def compound(text: str) -> float:
    """Normalise one string's raw score into [-1, 1], VADER-style."""
    return _compound_of(raw_score(text))


def label_for(score_value: float) -> str:
    if score_value >= _NEUTRAL_BAND:
        return "positive"
    if score_value <= -_NEUTRAL_BAND:
        return "negative"
    return "neutral"


def category_for(text: str) -> str:
    lowered = f" {(text or '').lower()} "
    for name, needles in _CATEGORY_RULES:
        if any(needle in lowered for needle in needles):
            return name
    return _DEFAULT_CATEGORY


def score(title: str, summary: str = "") -> dict[str, Any]:
    """Score one headline.

    The summary is weighted at half the title, because RSS summaries are usually
    a truncated repeat of the title and double-counting saturates the compound.
    """
    title = title or ""
    summary = summary or ""
    comp = _compound_of(raw_score(title) + 0.5 * raw_score(summary))
    return {
        "label": label_for(comp),
        "score": comp,
        "confidence": round(min(abs(comp) * 1.4, 1.0), 4),
        "category": category_for(f"{title} {summary}"),
        "engine": "lexicon",
    }
