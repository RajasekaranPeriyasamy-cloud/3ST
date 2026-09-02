"""Read side of the news desk: filter, cluster, and attach live prices.

Prices are fetched **here**, at request time, and never written into the item
store. A stored price would be a lie the moment it was written — the item store
is an archive of headlines, not of quotes.

Clustering is what produces the "2 more in the last 2 days" affordance: items
sharing a primary symbol inside the configured window collapse under the newest
one, so a stock with five filings occupies one row rather than five.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from . import store

# Cap on how many distinct symbols we ask the broker to quote for one page of
# results. Kite accepts 500 per call; this is a page of feed, not a scan.
MAX_QUOTE_SYMBOLS = 200


def _log(level: int, message: str, **fields: Any) -> None:
    try:
        from utils.logging import get_logger, log_event

        log_event(get_logger("news_desk_feed"), level, message, **fields)
    except Exception:
        pass


def _primary_symbol(item: dict[str, Any]) -> str:
    symbols = item.get("symbols") or []
    if not symbols:
        return ""
    return str(symbols[0].get("tradingsymbol") or "")


def attach_prices(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``last_price`` / ``change`` / ``change_pct`` to each item's symbols.

    A broker read failure leaves the items untouched — the feed still renders,
    just without price chips. This desk is never a reason to fail a page.
    """
    keys: list[str] = []
    for item in items:
        for symbol in item.get("symbols") or []:
            key = f"{symbol.get('exchange', 'NSE')}:{symbol.get('tradingsymbol')}"
            if key not in keys:
                keys.append(key)
        if len(keys) >= MAX_QUOTE_SYMBOLS:
            break

    if not keys:
        return items

    try:
        from kite_client import fetch_quote_batch

        quotes = fetch_quote_batch(keys[:MAX_QUOTE_SYMBOLS])
    except Exception as exc:
        _log(logging.INFO, "news_feed_quotes_unavailable", error=f"{type(exc).__name__}: {exc}")
        return items

    for item in items:
        for symbol in item.get("symbols") or []:
            key = f"{symbol.get('exchange', 'NSE')}:{symbol.get('tradingsymbol')}"
            quote = quotes.get(key)
            if not quote:
                continue
            last = quote.get("last_price")
            close = (quote.get("ohlc") or {}).get("close")
            symbol["last_price"] = last
            if last is not None and close:
                change = last - close
                symbol["change"] = round(change, 2)
                symbol["change_pct"] = round(change / close * 100, 2)
    return items


def _cluster(items: list[dict[str, Any]], window_days: int) -> list[dict[str, Any]]:
    """Collapse same-symbol items inside the window under the newest one."""
    cutoff = (datetime.now(UTC) - timedelta(days=window_days)).isoformat(
        timespec="seconds"
    )
    head_by_symbol: dict[str, dict[str, Any]] = {}
    out: list[dict[str, Any]] = []

    for item in items:
        symbol = _primary_symbol(item)
        # Items with no resolved symbol are never clustered — they have nothing
        # reliable to cluster on, and merging them by topic would hide stories.
        if not symbol or item.get("published_at", "") < cutoff:
            item["related"] = []
            item["related_count"] = 0
            out.append(item)
            continue

        head = head_by_symbol.get(symbol)
        if head is None:
            item["related"] = []
            item["related_count"] = 0
            head_by_symbol[symbol] = item
            out.append(item)
            continue

        head["related"].append(
            {
                "id": item["id"],
                "title": item["title"],
                "publisher": item["publisher"],
                "published_at": item["published_at"],
                "url": item["url"],
                "sentiment": item.get("sentiment"),
            }
        )
        head["related_count"] = len(head["related"])

    return out


def build(
    tab: str = "all",
    limit: int = 60,
    sentiment_filter: str = "",
    symbol: str = "",
    since: str = "",
    watchlist_symbols: list[str] | None = None,
    with_prices: bool = True,
) -> dict[str, Any]:
    """Assemble one page of feed.

    ``tab`` is 'all' (news only), 'mine' (news for watchlist symbols) or
    'actions' (exchange filings).
    """
    config = store.get_config()
    items = store.all_items()

    if tab == "actions":
        items = [i for i in items if i.get("kind") == "action"]
    else:
        items = [i for i in items if i.get("kind") != "action"]

    if tab == "mine":
        wanted = {s.upper() for s in (watchlist_symbols or []) if s}
        if not wanted:
            items = []
        else:
            items = [
                i
                for i in items
                if any(
                    str(s.get("tradingsymbol", "")).upper() in wanted
                    for s in (i.get("symbols") or [])
                )
            ]

    if symbol:
        target = symbol.upper()
        items = [
            i
            for i in items
            if any(str(s.get("tradingsymbol", "")).upper() == target for s in (i.get("symbols") or []))
        ]

    if sentiment_filter:
        wanted_label = sentiment_filter.lower()
        items = [i for i in items if ((i.get("sentiment") or {}).get("label")) == wanted_label]

    if since:
        items = [i for i in items if i.get("published_at", "") > since]

    items.sort(key=lambda i: i.get("published_at", ""), reverse=True)
    clustered = _cluster(items, int(config.get("cluster_days") or 7))
    page = clustered[: max(1, limit)]

    if with_prices:
        page = attach_prices(page)

    return {
        "items": page,
        "tab": tab,
        "total": len(clustered),
        "returned": len(page),
        "last_poll": store.get_last_poll(),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
