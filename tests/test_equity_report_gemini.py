"""Gemini backend for the Equity Report desk.

The load-bearing case is the anti-hallucination backstop: Gemini has no search
tool on the free tier, several finance sites block automated fetches, and a
report built with zero successful fetches would be entirely invented.
"""

from __future__ import annotations

import pytest

from analysis.equity_report import gemini_backend as gb
from analysis.equity_report import store as st


class _FakeInteractions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        r = self._responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class _FakeClient:
    def __init__(self, responses):
        self.interactions = _FakeInteractions(responses)


def _interaction(text="", fetched=(), failed=(), status="completed", usage=None, iid="v1_x"):
    steps = []
    if fetched or failed:
        steps.append(
            {
                "type": "url_context_result",
                "result": [{"status": "success", "url": u} for u in fetched]
                + [{"status": "error", "url": u} for u in failed],
            }
        )
    if text:
        steps.append({"type": "model_output", "content": [{"type": "text", "text": text}]})
    return {
        "id": iid,
        "status": status,
        "model": "gemini-3.1-flash-lite",
        "steps": steps,
        "usage": usage or {},
    }


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    monkeypatch.setattr(
        gb,
        "gemini_config",
        lambda: {
            "api_key": "test-key",
            "model": "gemini-3.1-flash-lite",
            "thinking_level": "medium",
            "enable_search": False,
        },
    )


SCREENER = "https://www.screener.in/company/RELIANCE/consolidated/"
TRENDLYNE = "https://trendlyne.com/equity/RELIANCE/"


def test_report_returned_with_fetched_sources():
    client = _FakeClient(
        [
            _interaction(
                text="# RELIANCE\n\nBuy.",
                fetched=[SCREENER],
                failed=[TRENDLYNE],
                usage={"total_input_tokens": 18000, "total_output_tokens": 1600, "total_tool_use_tokens": 10000},
            )
        ]
    )
    result = gb.generate_report_gemini("RELIANCE", "Reliance Industries", client=client)

    assert result.markdown == "# RELIANCE\n\nBuy."
    assert [c["url"] for c in result.citations] == [SCREENER]
    assert result.usage["input_tokens"] == 18000
    # Fetched page content bills as tool-use tokens and dominates the total.
    assert result.usage["tool_use_tokens"] == 10000


def test_zero_successful_fetches_refuses_to_return_a_report():
    """Every figure would have come from the model's memory."""
    client = _FakeClient(
        [_interaction(text="# RELIANCE\n\nCMP is 1500.", fetched=[], failed=[SCREENER, TRENDLYNE])]
    )
    with pytest.raises(gb.ReportError, match="refusing to return a report"):
        gb.generate_report_gemini("RELIANCE", client=client)


def test_no_text_raises():
    client = _FakeClient([_interaction(text="", fetched=[SCREENER])])
    with pytest.raises(gb.ReportError, match="no report text"):
        gb.generate_report_gemini("RELIANCE", client=client)


def test_incomplete_status_continues_from_previous_interaction():
    client = _FakeClient(
        [
            _interaction(text="## Part one", fetched=[SCREENER], status="incomplete", iid="v1_a"),
            _interaction(text="## Part two", status="completed"),
        ]
    )
    result = gb.generate_report_gemini("RELIANCE", client=client)

    assert "Part one" in result.markdown and "Part two" in result.markdown
    assert client.interactions.calls[1]["previous_interaction_id"] == "v1_a"
    assert result.iterations == 2


def test_failed_status_raises():
    client = _FakeClient([_interaction(text="x", fetched=[SCREENER], status="failed")])
    with pytest.raises(gb.ReportError, match="status 'failed'"):
        gb.generate_report_gemini("RELIANCE", client=client)


def test_quota_error_names_the_search_flag():
    client = _FakeClient([RuntimeError("Error code: 429 - quota exceeded")])
    with pytest.raises(gb.ReportError, match="EQUITY_REPORT_GEMINI_SEARCH"):
        gb.generate_report_gemini("RELIANCE", client=client)


def test_search_tool_off_by_default_and_optional():
    client = _FakeClient([_interaction(text="# R", fetched=[SCREENER])])
    gb.generate_report_gemini("RELIANCE", client=client)
    assert client.interactions.calls[0]["tools"] == [{"type": "url_context"}]


def test_search_tool_added_when_enabled(monkeypatch):
    monkeypatch.setattr(
        gb,
        "gemini_config",
        lambda: {
            "api_key": "k",
            "model": "m",
            "thinking_level": "low",
            "enable_search": True,
        },
    )
    client = _FakeClient([_interaction(text="# R", fetched=[SCREENER])])
    gb.generate_report_gemini("RELIANCE", client=client)
    assert {"type": "google_search"} in client.interactions.calls[0]["tools"]


def test_input_carries_concrete_urls_and_prompt_forbids_inventing():
    client = _FakeClient([_interaction(text="# R", fetched=[SCREENER])])
    gb.generate_report_gemini("RELIANCE", "Reliance Industries", client=client)
    call = client.interactions.calls[0]

    assert SCREENER in call["input"]
    assert "[TICKER]" not in call["input"]
    system = call["system_instruction"]
    assert "no web search tool" in system.lower()
    assert "data unavailable" in system


def test_url_count_capped_at_the_api_limit(monkeypatch):
    monkeypatch.setattr(gb, "seed_urls", lambda t, c="": [f"https://screener.in/{i}" for i in range(50)])
    monkeypatch.setattr(gb, "MAX_URLS", 20)
    client = _FakeClient([_interaction(text="# R", fetched=[SCREENER])])
    gb.generate_report_gemini("RELIANCE", client=client)
    assert client.interactions.calls[0]["input"].count("https://screener.in/") == 20


def test_missing_key_is_actionable(monkeypatch):
    monkeypatch.setattr(
        gb,
        "gemini_config",
        lambda: {"api_key": "", "model": "m", "thinking_level": "low", "enable_search": False},
    )
    with pytest.raises(gb.ReportError, match="GEMINI_API_KEY"):
        gb.generate_report_gemini("RELIANCE")


def test_gemini_reports_are_not_priced_at_anthropic_rates():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000}
    assert st.estimate_cost_usd(usage, "gemini-3.1-flash-lite") == 0.0
    assert st.estimate_cost_usd(usage, "claude-opus-5") > 0


def test_agent_dispatches_to_gemini(monkeypatch):
    """The provider switch routes without the Anthropic client being built."""
    from analysis.equity_report import agent as ag

    monkeypatch.setattr(
        ag,
        "equity_report_config",
        lambda: {
            "provider": "gemini",
            "model": "gemini-3.1-flash-lite",
            "effort": "high",
            "daily_usd_cap": 0.0,
            "stub": False,
        },
    )

    def _boom():
        raise AssertionError("Anthropic client must not be constructed for the gemini provider")

    monkeypatch.setattr(ag, "_client", _boom)
    client = _FakeClient([_interaction(text="# Gemini report", fetched=[SCREENER])])

    result = ag.generate_report("RELIANCE", client=client)
    assert result.markdown == "# Gemini report"
