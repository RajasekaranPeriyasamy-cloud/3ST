"""Resolve NSE tradingsymbols out of free-text headlines.

RSS carries no ticker metadata, so this is where a headline becomes actionable.
It is also, by some distance, the least exact part of the desk — read the guards
below before loosening anything.

Three things make naive matching wrong:

* **Registered names are not the names journalists use.** The instrument master
  says ``INTERGLOBE AVIATION``; every headline says "IndiGo". That gap is what
  ``data/news_aliases.json`` exists to close, and it is expected to grow.
* **Short symbols are ordinary words.** ``BSE``, ``ACC``, ``MRF``, ``REC``,
  ``ITI``, ``TIL`` and ``EMS`` are all real NSE symbols. Matched
  case-insensitively they fire on nearly every markets headline — "listed on
  BSE" is not a story about BSE Ltd. Short keys therefore only match when the
  token appears in caps in the original text, the way a headline actually writes
  a ticker.
* **Index rows look like equities.** ``NIFTY 50`` and friends carry
  ``instrument_type == "EQ"`` in the Kite master, so they survive
  ``_filter_segment(df, "equity")`` and would otherwise match the word "Nifty"
  in every market-wrap headline.

The index is built once and cached; ``load_instruments()`` reads a local JSON
cache, so this costs nothing per poll after the first call.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from settings import data_dir

ALIAS_FILE = data_dir() / "news_aliases.json"

# Below this length a key must appear in caps in the source text to count.
_CAPS_ONLY_MAX_LEN = 5

# Never resolved, whatever the instrument master says. Exchanges, regulators,
# indices and words that are symbols only by coincidence.
_BLOCKED_KEYS = frozenset({
    "BSE", "NSE", "MCX", "NCDEX", "SEBI", "RBI", "IRDAI", "NCLT", "CCI", "CBI",
    "NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY", "INDIA", "BHARAT",
    "IT", "PSU", "FMCG", "AUTO", "METAL", "PHARMA", "REALTY", "ENERGY", "INFRA",
    "BANK", "MEDIA", "POWER", "OIL", "GAS", "GOLD", "SILVER", "STEEL", "CEMENT",
    "ETF", "IPO", "GDP", "CPI", "WPI", "GST", "PAT", "EPS", "AGM", "EGM",
    "USA", "US", "UK", "EU", "UAE", "CEO", "CFO", "MD", "AI", "EV", "ESG",
    "ACE", "ACC", "EMS", "TIL", "SIS", "MPS", "GRP", "BPL", "IVP", "TRF",
    "CMI", "GTL", "PDS", "BSL", "GKW", "LTM", "DCW",
})

# Company-name suffixes stripped when building a match key. "HERO MOTOCORP LTD"
# and "HERO MOTOCORP" must produce the same key.
_SUFFIX_RE = re.compile(
    r"\b(limited|ltd|private|pvt|public|company|co|corporation|corp|industries|"
    r"enterprises|holdings|ventures|projects|technologies|technology|services|"
    r"solutions|systems|international|india|of india|group|and|&)\b\.?",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^A-Za-z0-9\s]")
_WS_RE = re.compile(r"\s+")

# ETFs, index funds and fund-house paper. ~7k rows of the NSE equity segment,
# none of them newsworthy, and their names ("... MOMENTUM QUALITY 100 ETF") are
# built from exactly the generic words that generate false positives.
_FUND_RE = re.compile(
    r"\b(ETF|BEES|EXCHANGE\s+TRADED|MUTUAL\s+FUND|INDEX\s+FUND|LIQUID|GILT|OVERNIGHT|"
    r"NIFTY|SENSEX|MIDCAP|SMALLCAP|LARGECAP|MULTICAP|FOF|SDL|GSEC|"
    r"MOMENTUM\s+QUALITY|TARGET\s+MATURITY)\b",
    re.IGNORECASE,
)

# A single-word company name that is also an ordinary English or business word.
# "Swiggy" is safe to match in running text; "Global", "Retail" and "Momentum"
# are not — each is a real NSE name that fires on most market copy. Multi-word
# names are exempt: "GLOBAL HEALTH" is unambiguous where "GLOBAL" is not.
_COMMON_WORDS = frozenset({
    "GLOBAL", "RETAIL", "MOMENTUM", "CONSUMER", "DOLLAR", "DEFENCE", "DEFENSE",
    "RELIABLE", "CAPITAL", "FINANCE", "FINANCIAL", "INVESTMENT", "TRUST", "FUND",
    "GROWTH", "VALUE", "QUALITY", "ALPHA", "PRIME", "SUPREME", "UNIVERSAL",
    "NATIONAL", "INTERNATIONAL", "MODERN", "ADVANCE", "ADVANCED", "PIONEER",
    "STANDARD", "GENERAL", "SPECIAL", "SUPER", "MEGA", "ULTRA", "MAX", "MINI",
    "FIRST", "NEXT", "ONE", "TOP", "BEST", "SMART", "UNION", "CENTRAL",
    "EASTERN", "WESTERN", "NORTHERN", "SOUTHERN", "ORIENT", "ORIENTAL",
    "MOTORS", "AUTO", "MOTOR", "ENERGY", "SOLAR", "GREEN", "TECH", "DIGITAL",
    "NETWORK", "MEDIA", "TIMES", "TODAY", "FUTURE", "VISION", "MISSION",
    "SAFE", "SECURE", "RAPID", "SWIFT", "UNITED", "ALLIED", "PREMIER",
    "EXPRESS", "TRAVEL", "HOTEL", "RESORT", "HOSPITAL", "HEALTH", "PHARMA",
    "CHEMICAL", "PLASTIC", "PAPER", "SUGAR", "TEA", "COFFEE", "SPICE",
    "MARINE", "SHIPPING", "PORT", "AIRLINE", "AVIATION", "RAIL", "METRO",
    "HOUSING", "INFRA", "CEMENT", "STEEL", "METAL", "MINING", "COAL",
    "TEXTILE", "APPAREL", "FASHION", "JEWEL", "GEMS", "CRAFT", "DESIGN",
    "ELECTRIC", "ELECTRONICS", "POWER", "GRID", "SOLUTION", "SERVICE",
    "TRADING", "EXPORT", "IMPORT", "AGRO", "FOODS", "DAIRY", "BEVERAGE",
    "CONTROL", "SYSTEM", "MACHINE", "TOOLS", "ENGINEER", "PROJECT", "BUILD",
    "REALTY", "ESTATE", "LAND", "CITY", "TOWN", "PARK", "GARDEN",
})

# Headline aliases shipped as the seed. Everything here is a name the press uses
# that the instrument master does not carry. Users extend this file; it is read
# fresh whenever the index is rebuilt.
_SEED_ALIASES: dict[str, str] = {
    # Registered name differs from the trading name
    "indigo": "INDIGO",
    "interglobe": "INDIGO",
    "ultratech": "ULTRACEMCO",
    "hero motocorp": "HEROMOTOCO",
    "hero moto": "HEROMOTOCO",
    "bajaj auto": "BAJAJ-AUTO",
    "m&m": "M&M",
    "mahindra & mahindra": "M&M",
    "maruti": "MARUTI",
    "maruti suzuki": "MARUTI",
    "l&t": "LT",
    "larsen": "LT",
    "larsen & toubro": "LT",
    "hdfc bank": "HDFCBANK",
    "icici bank": "ICICIBANK",
    "kotak bank": "KOTAKBANK",
    "kotak mahindra bank": "KOTAKBANK",
    "axis bank": "AXISBANK",
    "sbi": "SBIN",
    "state bank": "SBIN",
    "state bank of india": "SBIN",
    "bank of baroda": "BANKBARODA",
    "punjab national bank": "PNB",
    "reliance": "RELIANCE",
    "ril": "RELIANCE",
    "reliance industries": "RELIANCE",
    "tcs": "TCS",
    "tata consultancy": "TCS",
    "infosys": "INFY",
    "wipro": "WIPRO",
    "hcl tech": "HCLTECH",
    "tech mahindra": "TECHM",
    "bharti airtel": "BHARTIARTL",
    "airtel": "BHARTIARTL",
    "vodafone idea": "IDEA",
    "vi ": "IDEA",
    "adani ports": "ADANIPORTS",
    "adani enterprises": "ADANIENT",
    "adani green": "ADANIGREEN",
    "adani power": "ADANIPOWER",
    "asian paints": "ASIANPAINT",
    "hindustan unilever": "HINDUNILVR",
    "hul": "HINDUNILVR",
    "itc": "ITC",
    "nestle": "NESTLEIND",
    "britannia": "BRITANNIA",
    "titan": "TITAN",
    "sun pharma": "SUNPHARMA",
    "dr reddy": "DRREDDY",
    "dr reddys": "DRREDDY",
    "cipla": "CIPLA",
    "divis": "DIVISLAB",
    "power grid": "POWERGRID",
    "ntpc": "NTPC",
    "ongc": "ONGC",
    "coal india": "COALINDIA",
    "jsw steel": "JSWSTEEL",
    "tata steel": "TATASTEEL",
    "hindalco": "HINDALCO",
    "vedanta": "VEDL",
    "grasim": "GRASIM",
    "shree cement": "SHREECEM",
    "ambuja": "AMBUJACEM",
    "bajaj finance": "BAJFINANCE",
    "bajaj finserv": "BAJAJFINSV",
    "sbi life": "SBILIFE",
    "hdfc life": "HDFCLIFE",
    "max financial": "MFSL",
    "max fin": "MFSL",
    "zomato": "ETERNAL",
    "eternal": "ETERNAL",
    "paytm": "PAYTM",
    "one 97": "PAYTM",
    "nykaa": "NYKAA",
    "irctc": "IRCTC",
    "lic": "LICI",
    # Tata Motors demerged: JLR sits with the PV entity.
    "tata motors pv": "TMPV",
    "tata motors passenger": "TMPV",
    "jlr": "TMPV",
    "jaguar land rover": "TMPV",
    "tata motors cv": "TMCV",
    "tata motors commercial": "TMCV",
}

_LOCK = threading.RLock()
_INDEX: list[tuple[str, dict[str, Any]]] = []  # (match key, symbol meta), longest first
_INDEX_BUILT = False


def _log(level: int, message: str, **fields: Any) -> None:
    try:
        from utils.logging import get_logger, log_event

        log_event(get_logger("news_desk_tickers"), level, message, **fields)
    except Exception:
        pass


def _clean_name(name: str) -> str:
    text = _NON_WORD_RE.sub(" ", (name or "").upper())
    text = _SUFFIX_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def load_aliases() -> dict[str, str]:
    """Seed aliases merged with any user additions in ``data/news_aliases.json``."""
    aliases = dict(_SEED_ALIASES)
    try:
        if ALIAS_FILE.exists():
            raw = json.loads(ALIAS_FILE.read_text(encoding="utf-8"))
            user = raw.get("aliases") if isinstance(raw, dict) else None
            if isinstance(user, dict):
                aliases.update({str(k).lower(): str(v).upper() for k, v in user.items()})
    except (json.JSONDecodeError, OSError) as exc:
        _log(logging.WARNING, "news_alias_file_unreadable", error=str(exc))
    return aliases


def build_index(force: bool = False) -> list[tuple[str, dict[str, Any]]]:
    """Build the (match key -> symbol) index from the Kite instrument master."""
    global _INDEX, _INDEX_BUILT
    with _LOCK:
        if _INDEX_BUILT and not force:
            return _INDEX


        import instruments

        try:
            df = instruments.load_instruments()
            equity = instruments._filter_segment(df, "equity")
        except Exception as exc:
            # No instrument cache yet (fresh clone, no Kite session). The desk
            # still works, it just shows no ticker chips.
            _log(logging.WARNING, "news_ticker_index_unavailable", error=str(exc))
            _INDEX, _INDEX_BUILT = [], True
            return _INDEX

        if equity.empty:
            _INDEX, _INDEX_BUILT = [], True
            return _INDEX

        # Indices carry instrument_type EQ in the Kite master and would match
        # "Nifty" in every market wrap. Drop them.
        segment = equity["segment"].astype(str).str.upper()
        equity = equity[segment != "INDICES"]
        nse = equity[equity["exchange"].astype(str).str.upper() == "NSE"]

        keys: dict[str, dict[str, Any]] = {}
        for tsym, name, token in zip(
            nse["tradingsymbol"].astype(str),
            nse["name"].astype(str),
            nse["instrument_token"].astype(int),
            strict=False,
        ):
            symbol = tsym.strip().upper()
            if not symbol or symbol in _BLOCKED_KEYS:
                continue
            if _FUND_RE.search(symbol) or _FUND_RE.search(name):
                continue
            meta = {
                "exchange": "NSE",
                "tradingsymbol": symbol,
                "name": name.strip(),
                "instrument_token": int(token),
            }
            # The symbol itself, and the cleaned registered name.
            for key in {symbol, _clean_name(name)}:
                if not key or key in _BLOCKED_KEYS or len(key) < 3:
                    continue
                # Single generic word: too ambiguous to match in running text.
                if " " not in key and key in _COMMON_WORDS:
                    continue
                keys.setdefault(key, meta)

        # Aliases last so they override an accidental name collision.
        alias_map = load_aliases()
        by_symbol = {m["tradingsymbol"]: m for m in keys.values()}
        for phrase, symbol in alias_map.items():
            meta = by_symbol.get(symbol.upper())
            if meta is None:
                continue
            key = _clean_name(phrase)
            if key and key not in _BLOCKED_KEYS:
                keys[key] = meta

        # Longest key first so "TATA MOTORS PV" beats "TATA MOTORS".
        _INDEX = sorted(keys.items(), key=lambda kv: -len(kv[0]))
        _INDEX_BUILT = True
        _log(logging.INFO, "news_ticker_index_built", keys=len(_INDEX))
        return _INDEX


def _caps_tokens(text: str) -> set[str]:
    return set(re.findall(r"\b[A-Z][A-Z0-9&\-]{1,}\b", text or ""))


def resolve(title: str, summary: str = "", limit: int = 3) -> list[dict[str, Any]]:
    """Resolve up to ``limit`` NSE symbols mentioned in a headline.

    The first entry is the primary symbol — the longest, most specific match.
    """
    index = build_index()
    if not index:
        return []

    text = f"{title or ''} {summary or ''}"
    haystack = _WS_RE.sub(" ", _NON_WORD_RE.sub(" ", text.upper())).strip()
    if not haystack:
        return []
    padded = f" {haystack} "
    caps = _caps_tokens(text)

    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    claimed: list[tuple[int, int]] = []  # spans already consumed by a longer key

    for key, meta in index:
        if meta["tradingsymbol"] in seen:
            continue
        needle = f" {key} "
        position = padded.find(needle)
        if position < 0:
            continue

        # Short keys are ordinary words unless the headline wrote them as a
        # ticker, in caps.
        if len(key) <= _CAPS_ONLY_MAX_LEN and key not in caps:
            continue

        start, end = position, position + len(needle)
        if any(start < c_end and c_start < end for c_start, c_end in claimed):
            continue

        claimed.append((start, end))
        seen.add(meta["tradingsymbol"])
        hits.append(dict(meta))
        if len(hits) >= limit:
            break

    return hits


def search_terms(tradingsymbol: str) -> list[str]:
    """Text forms that mean ``tradingsymbol``, for searching raw headline text.

    Search has to be looser than resolution. ``resolve()`` is deliberately
    conservative — it would rather miss a mention than tag the wrong stock, and
    it only ran against the text a headline had *when it was ingested*. Someone
    typing "RELIANCE" into the search box wants every headline that talks about
    Reliance, including ones the resolver passed over and ones that arrived
    before an alias existed.

    So: the symbol, its cleaned registered name, and every alias pointing at it.
    A false positive here costs a stray row in a search someone asked for, which
    is a far cheaper mistake than a wrong ticker chip on the main feed.
    """
    target = (tradingsymbol or "").strip().upper()
    if not target:
        return []

    terms = {target}
    for key, meta in build_index():
        if meta["tradingsymbol"] == target and len(key) >= 3:
            terms.add(key)

    # Aliases are keyed phrase -> symbol, so they are not all in the index under
    # this symbol (only the cleaned form is).
    for phrase, symbol in load_aliases().items():
        if symbol.upper() == target:
            cleaned = _clean_name(phrase)
            if len(cleaned) >= 3:
                terms.add(cleaned)

    return sorted(terms, key=len, reverse=True)


def reset_index() -> None:
    """Drop the cached index — used by tests and after an alias-file edit."""
    global _INDEX, _INDEX_BUILT
    with _LOCK:
        _INDEX, _INDEX_BUILT = [], False
