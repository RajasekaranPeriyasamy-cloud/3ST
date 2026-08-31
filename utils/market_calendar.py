"""Is a given date a trading day?

Exists because ``options/gamma_density_history.in_session`` gated only on
time-of-day. Any snapshot request between 09:15 and 15:40 on a Saturday therefore
passed the gate and wrote a day-end row — and with the market shut, Kite returns
the previous session's last-traded quotes, so the row recorded **Friday's book
under Saturday's date**. Observed live on 2026-08-22 (HHI 0.1029 against Friday's
0.1034), in both ``daily_hhi`` and ``daily_pin``.

Two independent gates, deliberately separated because their confidence differs:

**Weekend** is deterministic and complete. NSE and MCX both trade Mon-Fri, so
``weekday() >= 5`` is exact and needs no data. This is the gate that closes the
bug actually observed.

**Holidays** need a published calendar, which the repo did not have. The list
lives in ``data/market_holidays.json`` (committed, like ``fpi_sectors_seed.json``)
and declares which years it covers. That coverage field is the point: a calendar
that silently runs out is worse than none, because it turns into a stream of
false "trading day" answers with no signal that anything expired.

**Outside declared coverage this falls back to weekend-only and says so** via
:func:`trading_day_confidence`. Fail-open is deliberate: a missed holiday writes
one stale row, while a wrongly-rejected trading day loses a real session
permanently. The first is detectable and fixable, the second is not.

Populate the calendar from the NSE/BSE annual circular. Format::

    {
      "coverage_years": [2026],
      "holidays": {"NSE": ["2026-01-26", ...], "MCX": [...]},
      "source": "NSE circular ref / URL",
      "updated": "2026-08-31"
    }
"""

from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "HOLIDAY_FILE",
    "clear_cache",
    "holiday_coverage",
    "is_trading_day",
    "is_weekend",
    "trading_day_confidence",
]

HOLIDAY_FILE = Path(__file__).resolve().parent.parent / "data" / "market_holidays.json"

Confidence = Literal["weekend", "holiday", "trading", "trading_unverified"]


def is_weekend(day: date) -> bool:
    """Saturday or Sunday. Exact for both NSE and MCX."""
    return day.weekday() >= 5


@lru_cache(maxsize=1)
def _load() -> tuple[frozenset[str], dict[str, frozenset[date]]]:
    """``(coverage_years, {exchange: {holiday dates}})``. Never raises."""
    try:
        raw: dict[str, Any] = json.loads(HOLIDAY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset(), {}

    years = frozenset(str(y) for y in (raw.get("coverage_years") or []))
    out: dict[str, frozenset[date]] = {}
    for exch, days in (raw.get("holidays") or {}).items():
        parsed: set[date] = set()
        for d in days or []:
            try:
                parsed.add(datetime.strptime(str(d), "%Y-%m-%d").date())
            except ValueError:
                continue  # a malformed entry must not void the whole calendar
        out[str(exch).upper()] = frozenset(parsed)
    return years, out


def clear_cache() -> None:
    """Drop the memoised calendar — for tests, and after editing the file."""
    _load.cache_clear()


def holiday_coverage() -> frozenset[str]:
    """Years the calendar claims to cover, as strings. Empty when unpopulated."""
    return _load()[0]


def trading_day_confidence(day: date, exchange: str = "NSE") -> Confidence:
    """Why this date is (or is not) a trading day.

    ``trading_unverified`` means the weekend check passed but the calendar does
    not cover this year, so a holiday cannot be ruled out. Callers that record
    history may want to log that; callers that only need a boolean can use
    :func:`is_trading_day`, which treats it as a trading day.
    """
    if is_weekend(day):
        return "weekend"
    years, holidays = _load()
    if str(day.year) not in years:
        return "trading_unverified"
    if day in holidays.get(exchange.upper(), frozenset()):
        return "holiday"
    return "trading"


def is_trading_day(day: date, exchange: str = "NSE") -> bool:
    """False on weekends and on known holidays; True otherwise.

    Fails **open** outside calendar coverage — see the module docstring for why.
    """
    return trading_day_confidence(day, exchange) in ("trading", "trading_unverified")
