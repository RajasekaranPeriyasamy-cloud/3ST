"""Anthropic-backed report generator for the Equity Report desk.

Runs the vendored `india-equity-report` prompt as a server-side agent using the
`web_search` / `web_fetch` server tools. Places no orders and imports nothing
from ``broker/``, ``execution/``, or ``risk/``.

The one non-obvious mechanic here is **pause_turn continuation**. The server-side
tool loop caps at 10 iterations per request and returns
``stop_reason == "pause_turn"`` when it hits that ceiling. A full equity report
needs far more than 10 fetches (Screener, BSE, Trendlyne, Tickertape, concalls,
news, technicals, peers), so without the continuation loop below the desk would
return half-finished reports with no error at all.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from settings import anthropic_api_key, equity_report_config

from .sources import ALLOWED_DOMAINS, seed_urls

PROMPT_DIR = Path(__file__).resolve().parent / "prompt"

# Ceiling on pause_turn continuations. Each one is a fresh request carrying the
# whole conversation, so this bounds worst-case spend on a runaway report.
MAX_CONTINUATIONS = 10

MAX_TOKENS = 32000
WEB_SEARCH_MAX_USES = 30
WEB_FETCH_MAX_USES = 25

# Opt into server-side refusal fallbacks. Disabled for the rest of the process if
# the account has not been granted the beta (see _stream_once).
_FALLBACKS_BETA = "server-side-fallback-2026-07-01"
_fallbacks_enabled = True


class ReportError(RuntimeError):
    """Report generation failed in a way worth showing the operator verbatim."""


@dataclass
class ReportResult:
    markdown: str
    usage: dict[str, int] = field(default_factory=dict)
    citations: list[dict[str, str]] = field(default_factory=list)
    model: str = ""
    iterations: int = 1
    tool_calls: int = 0


def _log(level: int, message: str, **fields: Any) -> None:
    try:
        from utils.logging import get_logger, log_event

        log_event(get_logger("equity_report"), level, message, **fields)
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Prompt assembly
# --------------------------------------------------------------------------- #

_REFERENCE_SECTIONS = [
    ("DATA SOURCES", "data-sources.md"),
    ("ANALYSIS FRAMEWORKS", "analysis-frameworks.md"),
    ("REPORT TEMPLATE", "report-template.md"),
]


@lru_cache(maxsize=1)
def build_system_prompt() -> str:
    """SKILL.md plus the three reference files, concatenated.

    Cached because it is byte-identical on every request — that stability is what
    lets the prompt-cache breakpoint in ``_request_kwargs`` actually hit.
    """
    parts = [(PROMPT_DIR / "SKILL.md").read_text(encoding="utf-8")]
    for title, filename in _REFERENCE_SECTIONS:
        body = (PROMPT_DIR / "references" / filename).read_text(encoding="utf-8")
        parts.append(f"\n\n---\n\n# {title}\n\n{body}")
    return "".join(parts)


def build_kickoff_message(ticker: str, company: str = "", exchange: str = "NSE") -> str:
    """The user turn.

    Carries concrete URLs because ``web_fetch`` only retrieves URLs already
    present in the conversation — the reference file holds ``[TICKER]``
    templates, which it cannot resolve on its own.
    """
    ticker = (ticker or "").strip().upper()
    label = f"{company.strip()} ({ticker})" if company.strip() else ticker
    urls = "\n".join(f"- {u}" for u in seed_urls(ticker, company))
    today = date.today().isoformat()
    return (
        f"Produce the full equity research report for {label}, listed on {exchange}.\n\n"
        f"Today's date is {today}. Treat any figure older than the most recent "
        f"trading day as stale and say so.\n\n"
        f"Candidate source URLs for this ticker (verify each resolves before "
        f"citing it; the company slug in the Tickertape and 5Paisa URLs is a "
        f"guess and may need correcting via web_search):\n{urls}\n\n"
        f"Begin with Step 1.5 — fetch the live CMP — before anything else. "
        f"Return only the report markdown."
    )


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "allowed_domains": ALLOWED_DOMAINS,
            "max_uses": WEB_SEARCH_MAX_USES,
        },
        {
            "type": "web_fetch_20260209",
            "name": "web_fetch",
            "allowed_domains": ALLOWED_DOMAINS,
            "max_uses": WEB_FETCH_MAX_USES,
            "citations": {"enabled": True},
        },
    ]


# --------------------------------------------------------------------------- #
# Response parsing — tolerant of both SDK objects and plain dicts so tests can
# stub the client without importing the SDK's model classes.
# --------------------------------------------------------------------------- #


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _blocks(response: Any) -> list[Any]:
    return list(_attr(response, "content", []) or [])


def _text_of(response: Any) -> str:
    chunks = [
        str(_attr(b, "text", "") or "") for b in _blocks(response) if _attr(b, "type") == "text"
    ]
    return "\n".join(c for c in chunks if c.strip()).strip()


def _usage_of(response: Any) -> dict[str, int]:
    usage = _attr(response, "usage")
    if usage is None:
        return {}
    keys = (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
    )
    out: dict[str, int] = {}
    for key in keys:
        try:
            value = int(_attr(usage, key, 0) or 0)
        except (TypeError, ValueError):
            value = 0
        if value:
            out[key] = value
    return out


def _merge_usage(total: dict[str, int], addition: dict[str, int]) -> dict[str, int]:
    for key, value in addition.items():
        total[key] = total.get(key, 0) + value
    return total


def _citations_of(response: Any) -> list[dict[str, str]]:
    """Pull source URLs out of web_search / web_fetch result blocks."""
    found: list[dict[str, str]] = []

    def _add(url: Any, title: Any = "") -> None:
        url = str(url or "").strip()
        if url:
            found.append({"url": url, "title": str(title or "").strip()})

    for block in _blocks(response):
        btype = _attr(block, "type")
        if btype not in {"web_search_tool_result", "web_fetch_tool_result"}:
            continue
        content = _attr(block, "content")
        if isinstance(content, list):
            for item in content:
                _add(_attr(item, "url"), _attr(item, "title"))
        elif content is not None:
            # web_fetch returns a single result object wrapping a document.
            _add(_attr(content, "url"), _attr(content, "title"))
    return found


def _count_tool_uses(response: Any) -> int:
    return sum(1 for b in _blocks(response) if _attr(b, "type") == "server_tool_use")


def _dedupe_citations(citations: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for c in citations:
        url = c.get("url", "")
        if url and url not in seen:
            seen.add(url)
            out.append(c)
    return out


# --------------------------------------------------------------------------- #
# The call
# --------------------------------------------------------------------------- #


def _client() -> Any:
    key = anthropic_api_key()
    if not key:
        raise ReportError(
            "ANTHROPIC_API_KEY is not set. Add it to .env to enable the Equity Report desk."
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is in requirements.txt
        raise ReportError("The `anthropic` package is not installed. Run pip install -r requirements.txt.") from exc
    return anthropic.Anthropic(api_key=key)


def _request_kwargs(messages: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": cfg["model"],
        "max_tokens": MAX_TOKENS,
        # Breakpoint on the system prompt: it is ~15k identical tokens re-sent on
        # every continuation, so caching it is what keeps a report affordable.
        "system": [
            {
                "type": "text",
                "text": build_system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": cfg["effort"]},
        "tools": _tools(),
        "messages": messages,
    }


def _stream_once(client: Any, messages: list[dict[str, Any]], cfg: dict[str, Any], on_event) -> Any:
    """One request. Returns the final message.

    Tries the beta path with server-side refusal fallbacks first; if the account
    does not have that beta, degrades to the plain endpoint for the rest of the
    process rather than failing every report.
    """
    global _fallbacks_enabled
    kwargs = _request_kwargs(messages, cfg)

    if _fallbacks_enabled:
        try:
            with client.beta.messages.stream(
                betas=[_FALLBACKS_BETA], fallbacks="default", **kwargs
            ) as stream:
                for event in stream:
                    on_event(event)
                return stream.get_final_message()
        except Exception as exc:
            text = str(exc).lower()
            is_beta_rejection = "400" in text and ("fallback" in text or "beta" in text)
            if not is_beta_rejection:
                raise
            _fallbacks_enabled = False
            _log(
                logging.WARNING,
                "equity_report_fallbacks_unavailable",
                detail="server-side refusal fallbacks rejected; continuing without them",
                error=str(exc)[:500],
            )

    with client.messages.stream(**kwargs) as stream:
        for event in stream:
            on_event(event)
        return stream.get_final_message()


def _stub_result(ticker: str, company: str) -> ReportResult:
    label = f"{company} ({ticker})" if company else ticker
    markdown = (
        f"# {label} — Equity Research Report (STUB)\n\n"
        f"> **This is stub output.** `EQUITY_REPORT_STUB=1` is set, so no Anthropic "
        f"API call was made and no money was spent. Unset it to generate a real report.\n\n"
        "## Price Snapshot\n\n"
        "| Field | Value |\n| --- | --- |\n| CMP | ₹0.00 |\n| 52W High | ₹0.00 |\n"
        "| 52W Low | ₹0.00 |\n| Market Cap | ₹0 Cr |\n\n"
        "## Investment Verdict\n\nNot a real recommendation — stub mode.\n"
    )
    return ReportResult(markdown=markdown, model="stub", iterations=0, tool_calls=0)


def stub_mode() -> bool:
    """Canned reports, no API call, no spend — set EQUITY_REPORT_STUB=1 in .env."""
    return bool(equity_report_config()["stub"])


def generate_report(
    ticker: str,
    company: str = "",
    exchange: str = "NSE",
    on_progress: Callable[..., None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    client: Any = None,
) -> ReportResult:
    """Generate one report. Blocking; call it from the runner thread, never a request handler."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise ValueError("ticker is required")

    if stub_mode():
        return _stub_result(ticker, company)

    cfg = equity_report_config()
    client = client or _client()

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": build_kickoff_message(ticker, company, exchange)}
    ]

    total_usage: dict[str, int] = {}
    citations: list[dict[str, str]] = []
    tool_calls = 0

    def _report(note: str, iteration: int) -> None:
        if on_progress:
            on_progress(iteration=iteration, tool_calls=tool_calls, note=note)

    for iteration in range(1, MAX_CONTINUATIONS + 2):
        if should_cancel and should_cancel():
            raise ReportError("Cancelled before completion.")

        live_tool_calls = tool_calls

        def _on_event(event: Any, _pass: int = iteration) -> None:
            nonlocal live_tool_calls
            if _attr(event, "type") != "content_block_start":
                return
            block = _attr(event, "content_block")
            if _attr(block, "type") == "server_tool_use":
                live_tool_calls += 1
                name = str(_attr(block, "name", "") or "source")
                if on_progress:
                    on_progress(
                        iteration=_pass,
                        tool_calls=live_tool_calls,
                        note=f"Fetching sources ({name})",
                    )

        response = _stream_once(client, messages, cfg, _on_event)

        _merge_usage(total_usage, _usage_of(response))
        citations.extend(_citations_of(response))
        tool_calls = max(live_tool_calls, tool_calls + _count_tool_uses(response))

        stop_reason = _attr(response, "stop_reason")

        if stop_reason == "refusal":
            details = _attr(response, "stop_details")
            category = _attr(details, "category") if details is not None else None
            raise ReportError(
                "The model declined this request"
                + (f" (category: {category})" if category else "")
                + ". Nothing was generated."
            )

        if stop_reason == "pause_turn":
            # Server-tool iteration ceiling, not an error. Echo the paused turn
            # back and let the model carry on from exactly where it stopped.
            messages.append({"role": "assistant", "content": _attr(response, "content")})
            _report(f"Still gathering data (pass {iteration + 1})", iteration + 1)
            continue

        if stop_reason == "max_tokens":
            raise ReportError(
                f"Report hit the {MAX_TOKENS}-token output limit before finishing. "
                "Retry, or lower EQUITY_REPORT_EFFORT."
            )

        # The report is the final turn's text, never an earlier one's — text from
        # a paused pass is progress narration, not a deliverable.
        text = _text_of(response)
        if not text:
            raise ReportError(f"Model returned no report text (stop_reason={stop_reason}).")

        _report("Complete", iteration)
        return ReportResult(
            markdown=text,
            usage=total_usage,
            citations=_dedupe_citations(citations),
            model=str(_attr(response, "model", cfg["model"]) or cfg["model"]),
            iterations=iteration,
            tool_calls=tool_calls,
        )

    raise ReportError(
        f"Report did not finish within {MAX_CONTINUATIONS} continuations. "
        "The ticker may be sparsely covered, or the source sites may be unreachable."
    )
