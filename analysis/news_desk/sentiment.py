"""Sentiment provider dispatch.

One entry point — ``score_items`` — so callers never care which engine is
configured. The contract both engines satisfy:

    {"label": "positive"|"negative"|"neutral",
     "score": float in [-1, 1],
     "confidence": float in [0, 1],
     "category": str,
     "engine": "lexicon"|"anthropic"}

The LLM path always falls back to the lexicon rather than leaving an item
unscored. An unscored item would be retried on every poll forever, which on a
paid backend is the expensive failure mode.
"""

from __future__ import annotations

from typing import Any

from settings import news_desk_config

from . import lexicon


def score_items(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Score items that have no sentiment yet. Returns ``{item_id: sentiment}``."""
    if not items:
        return {}

    config = news_desk_config()
    scored: dict[str, dict[str, Any]] = {}

    if config["provider"] == "anthropic":
        from . import llm

        batch_size = config["batch"]
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            scored.update(llm.score_batch(batch))
            # A batch that came back empty means the backend is unavailable or
            # capped; stop asking and let the rest fall through to the lexicon.
            if not scored:
                break

    for item in items:
        if item["id"] in scored:
            continue
        scored[item["id"]] = lexicon.score(item.get("title", ""), item.get("summary", ""))

    return scored


def engine_status() -> dict[str, Any]:
    """What the desk will actually use, for /newsfeed/sources and /health."""
    config = news_desk_config()
    status: dict[str, Any] = {
        "provider": config["provider"],
        "model": config["model"] if config["provider"] == "anthropic" else None,
    }
    if config["provider"] == "anthropic":
        from settings import anthropic_ready

        from . import llm

        status["key_present"] = anthropic_ready()
        status.update(llm.cap_status())
    return status
