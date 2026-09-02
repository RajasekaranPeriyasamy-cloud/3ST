"""Public RSS sources for the news desk.

Every source here is a publicly advertised RSS/Atom endpoint — no reverse
engineering, no private API, no key. That is a deliberate trade against the
reference notebook's approach (which scraped a private Groww endpoint captured
from browser devtools): a documented feed keeps working, and an undocumented one
breaks silently on the vendor's schedule.

What we give up is structured ticker metadata — RSS carries a headline, a link
and a short summary, nothing else — which is why ``tickers.py`` has to resolve
symbols out of the text.

Every fetch is independently guarded. One publisher timing out must reduce the
feed, never empty it, and the failure is reported through ``/newsfeed/sources``
rather than being swallowed.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import normalize
from .net import direct_session

FETCH_TIMEOUT_SEC = 12.0
MAX_PARALLEL_FETCHES = 6


@dataclass(frozen=True)
class Source:
    key: str
    publisher: str
    url: str
    # Used only when the lexicon/LLM cannot infer one from the text — a
    # markets-wide feed should not tag everything with its section name.
    default_category: str = ""


# Ordered by how much we trust the copy; ``dedupe`` keeps the first survivor, so
# the earlier a publisher sits here the more likely its wording is the one shown.
SOURCES: tuple[Source, ...] = (
    Source(
        "et_markets",
        "Economic Times",
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    ),
    Source(
        "et_stocks",
        "Economic Times",
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    ),
    Source(
        "bl_markets",
        "Hindu Business Line",
        "https://www.thehindubusinessline.com/markets/feeder/default.rss",
    ),
    Source(
        "bl_companies",
        "Hindu Business Line",
        "https://www.thehindubusinessline.com/companies/feeder/default.rss",
    ),
    Source(
        "moneycontrol_markets",
        "Moneycontrol",
        "https://www.moneycontrol.com/rss/marketreports.xml",
    ),
    Source(
        "moneycontrol_business",
        "Moneycontrol",
        "https://www.moneycontrol.com/rss/business.xml",
    ),
    Source(
        "business_standard_markets",
        "Business Standard",
        "https://www.business-standard.com/rss/markets-106.rss",
    ),
    Source(
        "livemint_markets",
        "Livemint",
        "https://www.livemint.com/rss/markets",
    ),
    Source(
        "livemint_companies",
        "Livemint",
        "https://www.livemint.com/rss/companies",
    ),
    Source(
        "cnbctv18_market",
        "CNBC-TV18",
        "https://www.cnbctv18.com/commonfeeds/v1/cne/rss/market.xml",
    ),
)

SOURCES_BY_KEY: dict[str, Source] = {s.key: s for s in SOURCES}


def _log(level: int, message: str, **fields: Any) -> None:
    try:
        from utils.logging import get_logger, log_event

        log_event(get_logger("news_desk_feeds"), level, message, **fields)
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fetch_source(source: Source, timeout: float = FETCH_TIMEOUT_SEC) -> dict[str, Any]:
    """Fetch and parse one feed.

    Returns ``{"key", "publisher", "ok", "items", "error", "checked_at"}``.
    Never raises — a dead publisher is data, not an exception.
    """
    import feedparser

    result: dict[str, Any] = {
        "key": source.key,
        "publisher": source.publisher,
        "url": source.url,
        "ok": False,
        "items": [],
        "error": "",
        "checked_at": _now_iso(),
    }

    try:
        # Fetched with requests rather than letting feedparser do it, so the
        # timeout is actually enforced — feedparser's urllib path has no usable
        # timeout and a hung publisher would stall the whole poll.
        # direct_session, not requests.get: a bare call inherits the process-wide
        # Kite static-IP proxy and every source dies with ProxyError. See net.py.
        session = direct_session(
            Accept="application/rss+xml, application/xml, text/xml, */*"
        )
        response = session.get(source.url, timeout=timeout)
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        _log(logging.WARNING, "news_feed_fetch_failed", source=source.key, error=result["error"])
        return result

    entries = getattr(parsed, "entries", None) or []
    if not entries and getattr(parsed, "bozo", 0):
        result["error"] = f"unparseable feed: {getattr(parsed, 'bozo_exception', '')}"
        return result

    items: list[dict[str, Any]] = []
    for entry in entries:
        title = entry.get("title") or ""
        if not title.strip():
            continue
        published = (
            entry.get("published_parsed")
            or entry.get("updated_parsed")
            or entry.get("published")
            or entry.get("updated")
        )
        items.append(
            normalize.build_item(
                title=title,
                url=entry.get("link") or "",
                summary=entry.get("summary") or entry.get("description") or "",
                guid=entry.get("id") or entry.get("guid") or "",
                publisher=source.publisher,
                source_key=source.key,
                kind="news",
                published=published,
            )
        )

    result["ok"] = True
    result["items"] = items
    return result


def fetch_all(
    sources: tuple[Source, ...] = SOURCES,
    timeout: float = FETCH_TIMEOUT_SEC,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch every source in parallel.

    Returns ``(items, health)``. Items are deduped and sorted newest-first;
    health is one row per source for ``/newsfeed/sources``.
    """
    health: list[dict[str, Any]] = []
    collected: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_FETCHES) as pool:
        futures = {pool.submit(fetch_source, s, timeout): s for s in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # a bug in fetch_source itself
                result = {
                    "key": source.key,
                    "publisher": source.publisher,
                    "url": source.url,
                    "ok": False,
                    "items": [],
                    "error": f"{type(exc).__name__}: {exc}",
                    "checked_at": _now_iso(),
                }
            collected.extend(result["items"])
            health.append(
                {
                    "key": result["key"],
                    "publisher": result["publisher"],
                    "ok": result["ok"],
                    "count": len(result["items"]),
                    "error": result["error"],
                    "checked_at": result["checked_at"],
                }
            )

    health.sort(key=lambda h: (not h["ok"], h["key"]))
    ordered = normalize.sort_newest_first(collected)
    return normalize.dedupe(ordered), health
