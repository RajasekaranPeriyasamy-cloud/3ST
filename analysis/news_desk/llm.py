"""Optional Anthropic backend for news sentiment + category tagging.

Off by default (``NEWS_SENTIMENT_PROVIDER=lexicon``). Enable with
``NEWS_SENTIMENT_PROVIDER=anthropic`` and an ``ANTHROPIC_API_KEY``.

Why it exists when the lexicon already works: the *category* tag is where a word
list is genuinely weak. "TMPV shares fall as JLR weakness and margin concerns
weigh" is a Corporate Shock story that keyword rules tag as Earnings, because
"margin" appears. A model reads the sentence.

Cost control, in order:

1. Only ever called with items that have **no** sentiment yet — a headline is
   scored once, ever, because ``store`` keys items by a stable id.
2. Batched (``NEWS_LLM_BATCH``, default 25 headlines per call).
3. Hard daily USD cap (``NEWS_LLM_DAILY_USD_CAP``, default $2). Over the cap it
   returns nothing and the caller falls back to the lexicon, so the desk keeps
   working rather than going blank. The tally is persisted to
   ``data/news_llm_spend.json``, so the cap is genuinely per *day* and not per
   process — a restart no longer hands the desk a fresh budget.

Nothing here raises on failure — a scoring backend that is down must degrade to
the lexicon, not take the feed offline.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import date, timedelta
from typing import Any

from settings import anthropic_api_key, data_dir, news_desk_config

# USD per million tokens (input, output), mirroring the table in
# analysis/equity_report/store.py. Unknown models bill at the Haiku rate rather
# than silently costing nothing.
_RATES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-5": (5.0, 25.0),
}
_DEFAULT_RATE = (1.0, 5.0)

_VALID_LABELS = {"positive", "negative", "neutral"}

# The tag vocabulary. Constrained so the UI has a bounded set to colour, and so
# two runs of the same headline do not produce "US Fed" and "Fed policy".
CATEGORIES = (
    "Commodities", "US Fed", "Corporate Shock", "Corporate Action", "IPO",
    "Earnings", "Regulatory", "Macro", "Brokerage", "Ownership", "Deals",
    "Neutral/Markets",
)

_SYSTEM = (
    "You classify Indian stock-market headlines for a trading desk. "
    "For each numbered headline return sentiment from the perspective of a holder "
    "of the affected stock, and one category tag.\n"
    f"sentiment must be one of: positive, negative, neutral.\n"
    f"category must be exactly one of: {', '.join(CATEGORIES)}.\n"
    "score is a number from -1.0 (very bearish) to 1.0 (very bullish).\n"
    "Respond with a JSON array only, no prose, one object per headline: "
    '[{"n": 1, "sentiment": "negative", "score": -0.6, "category": "Earnings"}]'
)

SPEND_FILE = data_dir() / "news_llm_spend.json"

# How many days of history to keep. Only today's figure gates the cap; the rest
# is there so an operator can see what the desk has been costing.
SPEND_RETENTION_DAYS = 30

_LOCK = threading.RLock()
# {iso date: usd spent}, persisted. This was in-memory until 2026-09-02, which
# meant a restart reset the day's tally and the "daily" cap could be enforced
# once per process rather than once per day — the desk restarts several times on
# a normal working day, so the cap did not do what its name said.
_SPEND: dict[str, float] = {}


def _log(level: int, message: str, **fields: Any) -> None:
    try:
        from utils.logging import get_logger, log_event

        log_event(get_logger("news_desk_llm"), level, message, **fields)
    except Exception:
        pass


def _prune_locked() -> None:
    cutoff = (date.today() - timedelta(days=SPEND_RETENTION_DAYS)).isoformat()
    for day in [d for d in _SPEND if d < cutoff]:
        del _SPEND[day]


def _save_spend_locked() -> None:
    try:
        SPEND_FILE.write_text(json.dumps({"spend": _SPEND}, indent=2), encoding="utf-8")
    except OSError as exc:
        # A cap that cannot persist is still better than a crashed poll. Log it
        # loudly — this is the one failure that silently restores the old
        # per-process behaviour.
        _log(logging.ERROR, "news_llm_spend_save_failed", error=str(exc))


def load_persisted_spend() -> None:
    """Restore the spend ledger from disk (survives API restart)."""
    global _SPEND
    with _LOCK:
        _SPEND = {}
        if not SPEND_FILE.exists():
            return
        try:
            raw = json.loads(SPEND_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            # A corrupt ledger must not disable scoring, but it does mean today's
            # tally is lost — say so rather than starting silently from zero.
            _log(logging.WARNING, "news_llm_spend_unreadable", error=str(exc))
            return
        spend = raw.get("spend") if isinstance(raw, dict) else None
        if isinstance(spend, dict):
            for day, usd in spend.items():
                try:
                    _SPEND[str(day)] = float(usd)
                except (TypeError, ValueError):
                    continue
        _prune_locked()


load_persisted_spend()


def estimate_cost_usd(usage: Any, model: str) -> float:
    if usage is None:
        return 0.0
    in_rate, out_rate = _RATES.get(model, _DEFAULT_RATE)
    tokens_in = float(getattr(usage, "input_tokens", 0) or 0)
    tokens_out = float(getattr(usage, "output_tokens", 0) or 0)
    return (tokens_in * in_rate + tokens_out * out_rate) / 1_000_000.0


def spend_today_usd() -> float:
    with _LOCK:
        return round(_SPEND.get(date.today().isoformat(), 0.0), 6)


def _record_spend(usd: float) -> None:
    """Add to today's tally and persist immediately.

    Written on every call rather than batched: the window between spending money
    and recording it is exactly where a crash loses the tally, and the file is
    thirty small floats.
    """
    with _LOCK:
        key = date.today().isoformat()
        _SPEND[key] = _SPEND.get(key, 0.0) + usd
        _prune_locked()
        _save_spend_locked()


def cap_status() -> dict[str, Any]:
    config = news_desk_config()
    spent = spend_today_usd()
    cap = config["daily_usd_cap"]
    return {
        "spent_today_usd": spent,
        "daily_usd_cap": cap,
        "capped": cap > 0 and spent >= cap,
    }


def _extract_json(text: str) -> list[dict[str, Any]]:
    """Pull the JSON array out of a response that may be fenced or prefaced."""
    if not text:
        return []
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        candidate = text[start : end + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def score_batch(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Score a batch of items. Returns ``{item_id: sentiment}``.

    An empty dict means "could not score" for any reason — no key, over cap,
    API error, unparseable response — and the caller must fall back.
    """
    if not items:
        return {}

    config = news_desk_config()
    if config["provider"] != "anthropic":
        return {}

    key = anthropic_api_key()
    if not key:
        _log(logging.WARNING, "news_llm_no_key")
        return {}

    status = cap_status()
    if status["capped"]:
        _log(
            logging.WARNING,
            "news_llm_daily_cap_reached",
            spent=status["spent_today_usd"],
            cap=status["daily_usd_cap"],
        )
        return {}

    numbered = []
    for index, item in enumerate(items, start=1):
        headline = item.get("title", "")
        summary = (item.get("summary") or "")[:200]
        numbered.append(f"{index}. {headline}\n   {summary}".rstrip())
    prompt = "\n".join(numbered)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=key)
        response = client.messages.create(
            model=config["model"],
            max_tokens=2048,
            system=_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        _log(logging.WARNING, "news_llm_call_failed", error=f"{type(exc).__name__}: {exc}")
        return {}

    _record_spend(estimate_cost_usd(getattr(response, "usage", None), config["model"]))

    text = "".join(
        getattr(block, "text", "") for block in (getattr(response, "content", None) or [])
    )
    rows = _extract_json(text)
    if not rows:
        _log(logging.WARNING, "news_llm_unparseable_response", chars=len(text))
        return {}

    scored: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            position = int(row.get("n", 0))
        except (TypeError, ValueError):
            continue
        if not 1 <= position <= len(items):
            continue

        label = str(row.get("sentiment", "")).strip().lower()
        if label not in _VALID_LABELS:
            continue
        try:
            value = float(row.get("score", 0.0))
        except (TypeError, ValueError):
            value = 0.0
        value = max(-1.0, min(1.0, value))

        category = str(row.get("category", "")).strip()
        if category not in CATEGORIES:
            category = "Neutral/Markets"

        scored[items[position - 1]["id"]] = {
            "label": label,
            "score": round(value, 4),
            "confidence": round(min(abs(value) * 1.2, 1.0), 4),
            "category": category,
            "engine": "anthropic",
        }

    _log(logging.INFO, "news_llm_scored", requested=len(items), scored=len(scored))
    return scored
