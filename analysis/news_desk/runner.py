"""Background poll loop for the news desk.

Its own daemon thread, for the same reason ``analysis/equity_report/runner.py``
has one: this work talks to ten third-party publishers over the network, and a
publisher that hangs must never be able to delay an order-placing tick. Nothing
here imports from ``broker/``, ``execution/`` or ``risk/``.

One pass is: fetch every source, store what is new, resolve tickers, score
what is unscored. Each stage is independently guarded — ticker resolution
failing (no instrument cache) must still leave you with a scored feed, and
scoring failing must still leave you with headlines.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from settings import news_desk_config

from . import announcements, feeds, sentiment, store, tickers

_thread: threading.Thread | None = None
_stop_event = threading.Event()
_wake_event = threading.Event()
_last_error = ""

# How many unscored items one pass will score. Bounds a first run against a cold
# store, where every one of ~500 items is unscored, from making 20 LLM calls
# back to back.
MAX_SCORE_PER_PASS = 200


def _log(level: int, message: str, **fields: Any) -> None:
    try:
        from utils.logging import get_logger, log_event

        log_event(get_logger("news_desk_runner"), level, message, **fields)
    except Exception:
        pass


def poll_once() -> dict[str, Any]:
    """Run one full ingest pass. Returns a summary dict; never raises."""
    global _last_error
    config = news_desk_config()
    health: list[dict[str, Any]] = []
    fetched: list[dict[str, Any]] = []

    try:
        items, feed_health = feeds.fetch_all()
        fetched.extend(items)
        health.extend(feed_health)
    except Exception as exc:
        _last_error = f"feeds: {type(exc).__name__}: {exc}"
        _log(logging.ERROR, "news_poll_feeds_failed", error=_last_error)

    if config["announcements"]:
        try:
            result = announcements.fetch()
            fetched.extend(result["items"])
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
        except Exception as exc:
            _log(logging.WARNING, "news_poll_announcements_failed", error=str(exc))

    added = store.upsert_many(fetched)
    store.set_health(health)

    # Resolve tickers for anything that arrived without them. Announcements come
    # with a symbol already; RSS never does.
    resolved_count = 0
    try:
        pending = [i for i in store.all_items() if not i.get("symbols")]
        if pending:
            resolved = {
                item["id"]: tickers.resolve(item.get("title", ""), item.get("summary", ""))
                for item in pending
            }
            resolved = {k: v for k, v in resolved.items() if v}
            resolved_count = store.apply_symbols(resolved)
    except Exception as exc:
        _log(logging.WARNING, "news_poll_ticker_resolve_failed", error=str(exc))

    scored_count = 0
    try:
        pending = store.unscored(limit=MAX_SCORE_PER_PASS)
        if pending:
            scored_count = store.apply_sentiment(sentiment.score_items(pending))
    except Exception as exc:
        _log(logging.WARNING, "news_poll_scoring_failed", error=str(exc))

    store.set_last_poll(added, _last_error)
    summary = {
        "added": added,
        "resolved": resolved_count,
        "scored": scored_count,
        "total": store.count(),
        "sources_ok": sum(1 for h in health if h["ok"]),
        "sources": len(health),
    }
    _log(logging.INFO, "news_poll_done", **summary)
    return summary


def _loop() -> None:
    # Poll immediately on start so a fresh API has a feed within seconds rather
    # than after a full interval.
    while not _stop_event.is_set():
        try:
            poll_once()
        except Exception as exc:  # a bug in poll_once itself
            _log(logging.ERROR, "news_poll_crashed", error=f"{type(exc).__name__}: {exc}")

        interval = float(store.get_config().get("poll_sec") or news_desk_config()["poll_sec"])
        _wake_event.wait(timeout=max(15.0, interval))
        _wake_event.clear()


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="news-desk-runner", daemon=True)
    _thread.start()
    _log(logging.INFO, "news_runner_started")


def stop() -> None:
    _stop_event.set()
    _wake_event.set()


def wake() -> None:
    """Ask the loop to poll now instead of waiting out the interval."""
    _wake_event.set()


def is_alive() -> bool:
    return _thread is not None and _thread.is_alive()


def status() -> dict[str, Any]:
    return {
        "alive": is_alive(),
        "last_poll": store.get_last_poll(),
        "items": store.count(),
        "engine": sentiment.engine_status(),
    }
