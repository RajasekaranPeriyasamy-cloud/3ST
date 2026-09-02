"""NSE corporate announcements — the "Corporate Actions" tab.

Structurally better than RSS for this desk: every row carries ``symbol``, the
actual NSE tradingsymbol, so these items need no fuzzy resolution at all. That
is why the tab exists separately rather than being folded into the news feed.

**This is the fragile source and it is isolated on purpose.** NSE's public API
requires a cookie obtained by hitting the site root first, rate-limits
aggressively, and changes shape without notice. Everything here is wrapped so a
failure produces an unhealthy source row in ``/newsfeed/sources`` and an empty
list — never an exception that reaches the poll loop and stops the RSS feed from
updating.

Observed on 2026-09-02: the warm-up request itself returns **403** while still
setting the cookie the API needs, so a non-200 warm-up is deliberately not
treated as failure. Check the API response, not the warm-up.

BSE is not wired up. Its ``AnnGetData`` endpoint returned zero rows for every
parameter combination tried, and NSE already supplies the symbol field that was
the point of using an exchange feed. The hook is ``fetch()``'s source list if
someone wants to revisit it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from . import normalize
from .net import direct_session

NSE_ROOT = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/corporate-announcements"
NSE_REFERER = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"

TIMEOUT_SEC = 20.0

# Exchange filings are stamped in IST with no offset.
_IST = timezone(timedelta(hours=5, minutes=30))


def _log(level: int, message: str, **fields: Any) -> None:
    try:
        from utils.logging import get_logger, log_event

        log_event(get_logger("news_desk_announcements"), level, message, **fields)
    except Exception:
        pass


def _parse_ist(value: str) -> str:
    """'02-Sep-2026 12:32:19' (IST) -> ISO-8601 UTC."""
    text = (value or "").strip()
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M"):
        try:
            naive = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=_IST).astimezone(UTC).isoformat(timespec="seconds")
    return datetime.now(UTC).isoformat(timespec="seconds")


def fetch(timeout: float = TIMEOUT_SEC) -> dict[str, Any]:
    """Fetch NSE corporate announcements.

    Returns ``{"key", "publisher", "ok", "items", "error", "checked_at"}`` —
    the same shape ``feeds.fetch_source`` returns, so both feed into one health
    table. Never raises.
    """
    result: dict[str, Any] = {
        "key": "nse_announcements",
        "publisher": "NSE",
        "url": NSE_API,
        "ok": False,
        "items": [],
        "error": "",
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }

    try:
        # Bypasses the process-wide Kite static-IP proxy — see net.py.
        session = direct_session(**{"Accept-Language": "en-US,en;q=0.9"})
        # Warm-up: this sets the cookie the API needs. It answers 403 in normal
        # operation — the cookie still lands, so the status is not checked.
        try:
            session.get(NSE_ROOT, timeout=timeout)
        except Exception:
            pass

        response = session.get(
            NSE_API,
            params={"index": "equities"},
            headers={"Accept": "application/json", "Referer": NSE_REFERER},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        _log(logging.WARNING, "nse_announcements_failed", error=result["error"])
        return result

    rows = payload if isinstance(payload, list) else payload.get("data", [])
    if not isinstance(rows, list):
        result["error"] = "unexpected payload shape"
        return result

    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        company = str(row.get("sm_name") or "").strip()
        subject = str(row.get("desc") or "").strip()
        body = str(row.get("attchmntText") or "").strip()

        title = f"{company or symbol}: {subject}" if subject else (company or symbol)
        if not title.strip(" :"):
            continue

        symbols: list[dict[str, Any]] = []
        if symbol:
            symbols.append({"exchange": "NSE", "tradingsymbol": symbol, "name": company})

        items.append(
            normalize.build_item(
                title=title,
                url=str(row.get("attchmntFile") or "").strip(),
                summary=body,
                # seq_id is NSE's own filing id — the most stable key available,
                # and it keeps re-polls from duplicating a filing whose PDF URL
                # changes.
                guid=f"nse-ann-{row.get('seq_id')}" if row.get("seq_id") else "",
                publisher="NSE",
                source_key="nse_announcements",
                kind="action",
                published=_parse_ist(str(row.get("an_dt") or "")),
                symbols=symbols,
                extra={"industry": row.get("smIndustry"), "subject": subject},
            )
        )

    result["ok"] = True
    result["items"] = items
    return result
