"""Gemini backend for the Equity Report desk.

An interim provider for when the Anthropic API isn't available. Same
``generate_report`` contract, same prompt, same source allowlist — only the
transport differs.

**The important difference is that there is no search tool.** Gemini's
`google_search` grounding sits behind its own quota that the free tier does not
grant (it 429s while plain generation and `url_context` both succeed), so this
backend runs on `url_context` alone and is handed the canonical URLs from
``sources.seed_urls()``. That is workable because those URLs are templated per
ticker — search was only ever for *discovery* — but it does mean news, credit
ratings and anything else that needed a query cannot be reached. The prompt
addendum below tells the model to write "data unavailable" rather than fill the
gap from memory, and ``generate_report_gemini`` refuses to return a report when
nothing was fetched at all.

Uses the Interactions API (``client.interactions.create``), which needs
google-genai >= 2.3.0.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from settings import gemini_config

from .agent import ReportError, ReportResult, _log, build_system_prompt
from .sources import ALLOWED_DOMAINS, seed_urls

# url_context accepts at most 20 URLs per request.
MAX_URLS = 20
MAX_OUTPUT_TOKENS = 32000
# Each pass is a fresh model turn continuing the same interaction.
MAX_CONTINUATIONS = 4

TOOL_ADDENDUM = """

---

# TOOL CONSTRAINTS FOR THIS RUN

You have a `url_context` tool only. **There is no web search tool available.**

- Fetch every URL listed in the user message. Work only from what those pages contain.
- Never claim or imply that you searched the web.
- If a figure is not present in the fetched pages, write "data unavailable" for it.
  Do not estimate it, and do not fill it in from memory — an invented number is a
  worse outcome than a missing one.
- Some fetches will fail (several finance sites block automated retrieval). Report
  what you could not obtain in a short "Data limitations" note near the end rather
  than papering over it.
- Sections that normally depend on a search — recent news, credit ratings, analyst
  consensus — should be marked "not available in this run" unless the fetched pages
  happen to carry them.

Aim for 1,500-3,000 words excluding tables. Return only the report markdown.
"""


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _client(api_key: str) -> Any:
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
        raise ReportError(
            "The `google-genai` package is not installed. Run pip install -r requirements.txt."
        ) from exc
    return genai.Client(api_key=api_key)


def build_gemini_system_prompt() -> str:
    return build_system_prompt() + TOOL_ADDENDUM


def build_gemini_input(ticker: str, company: str, exchange: str, urls: list[str]) -> str:
    label = f"{company.strip()} ({ticker})" if company.strip() else ticker
    listed = "\n".join(f"- {u}" for u in urls)
    return (
        f"Produce the full equity research report for {label}, listed on {exchange}.\n\n"
        f"Today's date is {date.today().isoformat()}.\n\n"
        f"Fetch and read these URLs, then build the report from them:\n{listed}\n\n"
        f"Approved domains for this desk: {', '.join(ALLOWED_DOMAINS[:12])}…\n"
        f"Do not use any source outside that list."
    )


def _extract(interaction: Any) -> tuple[str, list[dict[str, str]], list[str]]:
    """Return (text, fetched_ok, fetch_failed) from one interaction."""
    text_parts: list[str] = []
    fetched: list[dict[str, str]] = []
    failed: list[str] = []

    for step in _attr(interaction, "steps", []) or []:
        stype = _attr(step, "type")

        if stype == "url_context_result":
            for item in _attr(step, "result", []) or []:
                url = str(_attr(item, "url", "") or "")
                if not url:
                    continue
                if str(_attr(item, "status", "")).lower() == "success":
                    fetched.append({"url": url, "title": ""})
                else:
                    failed.append(url)

        elif stype == "model_output":
            for block in _attr(step, "content", []) or []:
                if _attr(block, "type") == "text":
                    chunk = str(_attr(block, "text", "") or "")
                    if chunk.strip():
                        text_parts.append(chunk)

    return "\n".join(text_parts).strip(), fetched, failed


def _usage_of(interaction: Any) -> dict[str, int]:
    usage = _attr(interaction, "usage")
    if usage is None:
        return {}
    out: dict[str, int] = {}
    # Map onto the same keys the store already understands, plus Gemini's
    # tool-use tokens — fetched page content is billed there, and it dominates.
    mapping = {
        "input_tokens": "total_input_tokens",
        "output_tokens": "total_output_tokens",
        "cache_read_input_tokens": "total_cached_tokens",
        "thought_tokens": "total_thought_tokens",
        "tool_use_tokens": "total_tool_use_tokens",
    }
    for our_key, their_key in mapping.items():
        try:
            value = int(_attr(usage, their_key, 0) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            out[our_key] = value
    return out


def _merge(total: dict[str, int], addition: dict[str, int]) -> dict[str, int]:
    for key, value in addition.items():
        total[key] = total.get(key, 0) + value
    return total


def generate_report_gemini(
    ticker: str,
    company: str = "",
    exchange: str = "NSE",
    on_progress: Callable[..., None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    client: Any = None,
) -> ReportResult:
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise ValueError("ticker is required")

    cfg = gemini_config()
    if not cfg["api_key"] and client is None:
        raise ReportError(
            "GEMINI_API_KEY is not set. Add it to .env to use the Gemini provider."
        )
    client = client or _client(cfg["api_key"])

    urls = seed_urls(ticker, company)[:MAX_URLS]
    if not urls:
        raise ReportError(f"No source URLs could be built for {ticker}.")

    tools: list[dict[str, Any]] = [{"type": "url_context"}]
    if cfg["enable_search"]:
        # Off by default: free-tier keys 429 on google_search even though
        # url_context succeeds. Turn on only with a plan that includes grounding.
        tools.append({"type": "google_search"})

    generation_config = {
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "thinking_level": cfg["thinking_level"],
    }

    total_usage: dict[str, int] = {}
    fetched: list[dict[str, str]] = []
    failed: list[str] = []
    text = ""
    previous_id: str | None = None

    for iteration in range(1, MAX_CONTINUATIONS + 2):
        if should_cancel and should_cancel():
            raise ReportError("Cancelled before completion.")

        if on_progress:
            on_progress(
                iteration=iteration,
                tool_calls=len(fetched),
                note="Fetching sources" if iteration == 1 else f"Continuing (pass {iteration})",
            )

        kwargs: dict[str, Any] = {
            "model": cfg["model"],
            "system_instruction": build_gemini_system_prompt(),
            "tools": tools,
            "generation_config": generation_config,
        }
        if previous_id:
            kwargs["input"] = "Continue the report exactly where you stopped. Do not repeat content."
            kwargs["previous_interaction_id"] = previous_id
        else:
            kwargs["input"] = build_gemini_input(ticker, company, exchange, urls)

        try:
            interaction = client.interactions.create(**kwargs)
        except Exception as exc:
            message = str(exc)
            if "429" in message:
                raise ReportError(
                    "Gemini quota exceeded. The free tier does not include Google Search "
                    "grounding; if EQUITY_REPORT_GEMINI_SEARCH is on, turn it off."
                ) from exc
            raise ReportError(f"Gemini call failed: {type(exc).__name__}: {message[:400]}") from exc

        _merge(total_usage, _usage_of(interaction))
        chunk, ok, bad = _extract(interaction)
        fetched.extend(ok)
        failed.extend(bad)
        if chunk:
            text = f"{text}\n\n{chunk}".strip() if previous_id else chunk

        status = str(_attr(interaction, "status", "") or "")

        if status in {"failed", "cancelled", "budget_exceeded"}:
            raise ReportError(f"Gemini returned status '{status}'.")

        if status == "incomplete":
            previous_id = str(_attr(interaction, "id", "") or "") or None
            if not previous_id:
                break
            continue

        break

    # Anti-hallucination backstop: with no successful fetch every figure in the
    # report would have come from the model's memory.
    unique_fetched = {c["url"]: c for c in fetched}
    if not unique_fetched:
        raise ReportError(
            "No source page could be fetched"
            + (f" ({len(set(failed))} attempts failed)" if failed else "")
            + " — refusing to return a report built from memory."
        )

    if not text:
        raise ReportError("Gemini returned no report text.")

    _log(
        logging.INFO,
        "equity_report_gemini_sources",
        ticker=ticker,
        fetched=len(unique_fetched),
        failed=len(set(failed)),
    )

    if on_progress:
        on_progress(iteration=1, tool_calls=len(unique_fetched), note="Complete")

    return ReportResult(
        markdown=text,
        usage=total_usage,
        citations=list(unique_fetched.values()),
        model=str(_attr(interaction, "model", cfg["model"]) or cfg["model"]),
        iterations=iteration,
        tool_calls=len(unique_fetched),
    )
