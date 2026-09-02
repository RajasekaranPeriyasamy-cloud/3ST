"""One canonical item shape for everything the news desk ingests.

RSS entries and NSE/BSE announcements arrive with wildly different field names;
everything downstream (sentiment, ticker resolution, the store, the API) sees
only the dict ``build_item`` returns.

Two properties matter and are tested:

* ``id`` is **stable** — derived from the entry's guid, falling back to its URL,
  falling back to publisher+title. Re-polling the same feed must not create a
  second copy of an item, or the store fills with duplicates and every headline
  gets re-scored (which costs money on the LLM backend).
* Dedup is **cross-publisher**. The same PTI/Reuters copy runs on four sites
  under near-identical headlines; the feed should show it once.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from collections.abc import Iterable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]")

# Words dropped when building a dedup key. Publishers rewrite headlines with
# these swapped ("shares fall" / "stock falls"), so keying on them splits what
# is really one story.
_DEDUP_STOPWORDS = frozenset({
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "as", "by", "with",
    "and", "or", "is", "are", "was", "were", "be", "its", "it", "from", "amid",
    "after", "over", "up", "down", "says", "said", "shares", "stock", "stocks",
    "rs", "crore", "cr", "per", "cent", "pc",
})

# Enough shared significant words to call two headlines the same story. Tuned on
# wire copy: 0.75 merges "Tata Motors Q1 profit falls 12%" across four sites but
# keeps "Tata Motors Q1 profit falls" and "Tata Motors Q1 revenue rises" apart.
_DEDUP_THRESHOLD = 0.75


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def item_id(guid: str = "", url: str = "", publisher: str = "", title: str = "") -> str:
    """Stable id for an item, in descending order of reliability."""
    for candidate in (guid, url):
        if candidate and candidate.strip():
            return _sha1(candidate.strip())
    return _sha1(f"{publisher.strip().lower()}|{normalize_title(title)}")


def clean_text(raw: str) -> str:
    """Strip markup and collapse whitespace out of an RSS summary.

    Unescaped **twice** on purpose. Several publishers double-encode — Livemint
    ships literal ``&amp;nbsp;`` — and a single pass leaves a visible ``&nbsp;``
    in the rendered feed. A second pass is safe because after the first the
    text holds no markup: tags were stripped before unescaping, so a decoded
    ``&lt;b&gt;`` cannot turn back into an executable tag.
    """
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", str(raw))
    text = html.unescape(html.unescape(text))
    # NBSP and friends survive unescaping as real characters; NFKC folds them
    # to plain spaces so _WS_RE can collapse them.
    text = unicodedata.normalize("NFKC", text).replace("\xa0", " ")
    return _WS_RE.sub(" ", text).strip()


def normalize_title(title: str) -> str:
    """Lowercased, punctuation-free form used for comparison, not display."""
    text = unicodedata.normalize("NFKC", (title or "").lower())
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def _dedup_tokens(title: str) -> frozenset[str]:
    words = normalize_title(title).split()
    return frozenset(w for w in words if w not in _DEDUP_STOPWORDS and len(w) > 2)


def parse_published(value: Any) -> str:
    """Best-effort parse of a feed's date field into an ISO-8601 UTC string.

    Feeds lie about dates constantly — missing fields, local time with no offset,
    RFC-822 with a bogus zone name. An unparseable date becomes 'now' rather than
    dropping the item, because an item with a slightly wrong timestamp is far
    more useful than no item.
    """
    if not value:
        return _now_iso()

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (tuple, list)) and len(value) >= 6:
        # feedparser's struct_time, already UTC.
        try:
            dt = datetime(*[int(p) for p in value[:6]], tzinfo=UTC)
        except (TypeError, ValueError):
            return _now_iso()
    else:
        text = str(value).strip()
        dt = None
        try:
            dt = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            dt = None
        if dt is None:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return _now_iso()

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat(timespec="seconds")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def build_item(
    *,
    title: str,
    url: str = "",
    summary: str = "",
    guid: str = "",
    publisher: str = "",
    source_key: str = "",
    kind: str = "news",
    published: Any = None,
    symbols: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one canonical item. ``kind`` is 'news' or 'action'."""
    clean_title = clean_text(title)
    item: dict[str, Any] = {
        "id": item_id(guid=guid, url=url, publisher=publisher, title=clean_title),
        "title": clean_title,
        "summary": clean_text(summary),
        "url": (url or "").strip(),
        "publisher": publisher,
        "source_key": source_key,
        "kind": kind,
        "published_at": parse_published(published),
        "fetched_at": _now_iso(),
        "symbols": symbols or [],
        # Filled in later by sentiment.score_items(); absent means "not yet
        # scored", which is what the runner looks for.
        "sentiment": None,
    }
    if extra:
        item["extra"] = extra
    return item


def dedupe(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the same story reported by several publishers into one item.

    Input order decides the survivor, so callers sort newest-first (or by
    preferred publisher) before calling. The dropped publishers are recorded on
    the survivor as ``also_reported_by`` so the UI can show "also on Mint".
    """
    kept: list[dict[str, Any]] = []
    kept_tokens: list[frozenset[str]] = []
    seen_ids: set[str] = set()

    for item in items:
        if item.get("id") in seen_ids:
            continue
        tokens = _dedup_tokens(item.get("title", ""))

        duplicate_of = None
        if tokens:
            for index, prior in enumerate(kept_tokens):
                if not prior:
                    continue
                overlap = len(tokens & prior) / max(len(tokens), len(prior))
                if overlap >= _DEDUP_THRESHOLD:
                    duplicate_of = index
                    break

        if duplicate_of is not None:
            survivor = kept[duplicate_of]
            publisher = item.get("publisher")
            if publisher and publisher != survivor.get("publisher"):
                also = survivor.setdefault("also_reported_by", [])
                if publisher not in also:
                    also.append(publisher)
            continue

        seen_ids.add(item.get("id", ""))
        kept.append(item)
        kept_tokens.append(tokens)

    return kept


def sort_newest_first(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda i: i.get("published_at", ""), reverse=True)
