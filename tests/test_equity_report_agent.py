"""Report agent: continuation loop, refusal handling, parsing, allowlist.

The `pause_turn` cases are the important ones. The server-side tool loop stops
after 10 iterations and a full equity report needs far more, so if continuation
regresses the desk silently returns half-finished reports.
"""

from __future__ import annotations

import pytest

from analysis.equity_report import agent as ag
from analysis.equity_report import sources as src

# --------------------------------------------------------------------------- #
# Fake client
# --------------------------------------------------------------------------- #


class _FakeStream:
    def __init__(self, response, events):
        self._response = response
        self._events = events

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._response


class _FakeMessages:
    def __init__(self, responses, events_per_call=None):
        self._responses = list(responses)
        self._events = list(events_per_call or [])
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        response = self._responses.pop(0)
        events = self._events.pop(0) if self._events else []
        return _FakeStream(response, events)


class _FakeClient:
    def __init__(self, responses, events_per_call=None):
        self.messages = _FakeMessages(responses, events_per_call)
        # `beta` is unused because tests disable the fallbacks beta path; a
        # dedicated test below covers the degrade-to-non-beta behaviour.
        self.beta = None


def _cfg(stub: bool = False, provider: str = "anthropic"):
    return {
        "provider": provider,
        "model": "claude-opus-5",
        "effort": "high",
        "daily_usd_cap": 0.0,
        "stub": stub,
    }


@pytest.fixture(autouse=True)
def _no_stub_no_beta(monkeypatch):
    monkeypatch.setattr(ag, "_fallbacks_enabled", False)
    monkeypatch.setattr(ag, "equity_report_config", lambda: _cfg())


def _msg(text="", stop_reason="end_turn", usage=None, content=None, **extra):
    blocks = list(content or [])
    if text:
        blocks.append({"type": "text", "text": text})
    return {
        "content": blocks,
        "stop_reason": stop_reason,
        "usage": usage or {},
        "model": "claude-opus-5",
        **extra,
    }


# --------------------------------------------------------------------------- #
# Continuation
# --------------------------------------------------------------------------- #


def test_single_turn_report():
    client = _FakeClient([_msg("# RELIANCE\n\nBuy.", usage={"input_tokens": 100})])
    result = ag.generate_report("RELIANCE", client=client)

    assert result.markdown == "# RELIANCE\n\nBuy."
    assert result.iterations == 1
    assert len(client.messages.calls) == 1


def test_pause_turn_continues_until_the_report_lands():
    """Three server-tool passes before the model finishes writing."""
    client = _FakeClient(
        [
            _msg("gathering", stop_reason="pause_turn", usage={"input_tokens": 1000}),
            _msg("still gathering", stop_reason="pause_turn", usage={"input_tokens": 2000}),
            _msg("# RELIANCE\n\nFinal report.", usage={"input_tokens": 3000, "output_tokens": 500}),
        ]
    )
    result = ag.generate_report("RELIANCE", client=client)

    assert result.markdown == "# RELIANCE\n\nFinal report."
    assert result.iterations == 3
    assert len(client.messages.calls) == 3
    # Usage accumulates across every request, not just the last one.
    assert result.usage == {"input_tokens": 6000, "output_tokens": 500}


def test_pause_turn_echoes_the_paused_assistant_turn_back():
    """Continuation only works if the paused turn is replayed verbatim."""
    paused_blocks = [{"type": "server_tool_use", "name": "web_fetch"}]
    client = _FakeClient(
        [
            _msg(stop_reason="pause_turn", content=paused_blocks),
            _msg("# Done"),
        ]
    )
    ag.generate_report("INFY", client=client)

    second_messages = client.messages.calls[1]["messages"]
    assert len(second_messages) == 2
    assert second_messages[1]["role"] == "assistant"
    assert second_messages[1]["content"][0]["type"] == "server_tool_use"


def test_runaway_continuations_are_bounded(monkeypatch):
    monkeypatch.setattr(ag, "MAX_CONTINUATIONS", 3)
    client = _FakeClient([_msg("working", stop_reason="pause_turn") for _ in range(20)])

    with pytest.raises(ag.ReportError, match="did not finish"):
        ag.generate_report("RELIANCE", client=client)
    assert len(client.messages.calls) == 4  # MAX_CONTINUATIONS + the first pass


# --------------------------------------------------------------------------- #
# Failure modes
# --------------------------------------------------------------------------- #


def test_refusal_raises_before_reading_content():
    client = _FakeClient(
        [_msg(stop_reason="refusal", stop_details={"category": "cyber"}, content=[])]
    )
    with pytest.raises(ag.ReportError, match="declined"):
        ag.generate_report("RELIANCE", client=client)


def test_max_tokens_is_reported_not_returned_as_a_truncated_report():
    client = _FakeClient([_msg("# Half a rep", stop_reason="max_tokens")])
    with pytest.raises(ag.ReportError, match="token output limit"):
        ag.generate_report("RELIANCE", client=client)


def test_empty_response_raises():
    client = _FakeClient([_msg("")])
    with pytest.raises(ag.ReportError, match="no report text"):
        ag.generate_report("RELIANCE", client=client)


def test_progress_narration_is_never_returned_as_the_report():
    """Text from a paused pass is a status line, not a deliverable."""
    client = _FakeClient(
        [
            _msg("Fetching Screener data now...", stop_reason="pause_turn"),
            _msg(""),  # final turn produced nothing
        ]
    )
    with pytest.raises(ag.ReportError, match="no report text"):
        ag.generate_report("RELIANCE", client=client)


def test_cancellation_checked_between_passes():
    client = _FakeClient([_msg("a", stop_reason="pause_turn"), _msg("# Done")])
    calls = {"n": 0}

    def _cancel():
        calls["n"] += 1
        return calls["n"] > 1  # allow the first pass, cancel before the second

    with pytest.raises(ag.ReportError, match="Cancelled"):
        ag.generate_report("RELIANCE", should_cancel=_cancel, client=client)
    assert len(client.messages.calls) == 1


def test_missing_api_key_gives_an_actionable_error(monkeypatch):
    monkeypatch.setattr(ag, "anthropic_api_key", lambda: "")
    with pytest.raises(ag.ReportError, match="ANTHROPIC_API_KEY"):
        ag.generate_report("RELIANCE")


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def test_citations_collected_from_both_tool_result_shapes_and_deduped():
    client = _FakeClient(
        [
            _msg(
                stop_reason="pause_turn",
                content=[
                    {
                        "type": "web_search_tool_result",
                        "content": [
                            {"url": "https://screener.in/a", "title": "A"},
                            {"url": "https://trendlyne.com/b", "title": "B"},
                        ],
                    }
                ],
            ),
            _msg(
                "# Report",
                content=[
                    {
                        "type": "web_fetch_tool_result",
                        "content": {"url": "https://screener.in/a", "title": "A again"},
                    }
                ],
            ),
        ]
    )
    result = ag.generate_report("RELIANCE", client=client)

    urls = [c["url"] for c in result.citations]
    assert urls == ["https://screener.in/a", "https://trendlyne.com/b"]


def test_tool_call_progress_is_reported():
    events = [
        {"type": "content_block_start", "content_block": {"type": "server_tool_use", "name": "web_search"}},
        {"type": "content_block_delta"},
        {"type": "content_block_start", "content_block": {"type": "server_tool_use", "name": "web_fetch"}},
    ]
    client = _FakeClient([_msg("# Report")], events_per_call=[events])
    seen: list[dict] = []

    result = ag.generate_report("RELIANCE", on_progress=lambda **f: seen.append(f), client=client)

    assert result.tool_calls == 2
    assert [s["tool_calls"] for s in seen if "Fetching" in s["note"]] == [1, 2]


# --------------------------------------------------------------------------- #
# Request shape
# --------------------------------------------------------------------------- #


def test_request_uses_adaptive_thinking_effort_and_a_cache_breakpoint():
    client = _FakeClient([_msg("# Report")])
    ag.generate_report("RELIANCE", client=client)
    kwargs = client.messages.calls[0]

    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["output_config"] == {"effort": "high"}
    # Sampling params are rejected outright on Opus 5.
    assert "temperature" not in kwargs and "top_p" not in kwargs
    assert kwargs["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_web_tools_are_domain_restricted():
    client = _FakeClient([_msg("# Report")])
    ag.generate_report("RELIANCE", client=client)
    tools = {t["name"]: t for t in client.messages.calls[0]["tools"]}

    assert tools["web_search"]["type"] == "web_search_20260209"
    assert tools["web_fetch"]["type"] == "web_fetch_20260209"
    for tool in tools.values():
        assert "screener.in" in tool["allowed_domains"]
        assert not any("yahoo" in d for d in tool["allowed_domains"])


def test_kickoff_carries_concrete_urls_because_web_fetch_needs_them():
    text = ag.build_kickoff_message("RELIANCE", "Reliance Industries")
    assert "https://www.screener.in/company/RELIANCE/consolidated/" in text
    assert "https://www.tickertape.in/stocks/reliance-industries-RELIANCE" in text
    assert "[TICKER]" not in text


def test_system_prompt_inlines_all_three_reference_documents():
    prompt = ag.build_system_prompt()
    for heading in ("# DATA SOURCES", "# ANALYSIS FRAMEWORKS", "# REPORT TEMPLATE"):
        assert heading in prompt
    # The server copy must not tell the model to write files or call chat tools.
    assert "SendUserFile" not in prompt
    assert "/mnt/skills" not in prompt


def test_stub_mode_makes_no_api_call(monkeypatch):
    """Read from .env via settings, not os.getenv — the flag must not depend on
    which shell launched uvicorn."""
    monkeypatch.setattr(ag, "equity_report_config", lambda: _cfg(stub=True))
    client = _FakeClient([])
    result = ag.generate_report("RELIANCE", "Reliance Industries", client=client)

    assert ag.stub_mode() is True
    assert "STUB" in result.markdown
    assert client.messages.calls == []


def test_stub_flag_comes_from_dotenv(monkeypatch):
    from settings import equity_report_config

    monkeypatch.setattr("settings.env", lambda key, default="": "1" if key == "EQUITY_REPORT_STUB" else default)
    assert equity_report_config()["stub"] is True


# --------------------------------------------------------------------------- #
# Fallback beta degradation
# --------------------------------------------------------------------------- #


def test_missing_fallbacks_beta_degrades_instead_of_failing_every_report(monkeypatch):
    """An account without the beta must still be able to generate reports."""
    monkeypatch.setattr(ag, "_fallbacks_enabled", True)

    class _BetaMessages:
        def stream(self, **kwargs):
            raise RuntimeError("Error code: 400 - unsupported beta: server-side-fallback")

    class _Beta:
        messages = _BetaMessages()

    client = _FakeClient([_msg("# Report")])
    client.beta = _Beta()

    result = ag.generate_report("RELIANCE", client=client)
    assert result.markdown == "# Report"
    assert ag._fallbacks_enabled is False


def test_unrelated_beta_errors_are_not_swallowed(monkeypatch):
    monkeypatch.setattr(ag, "_fallbacks_enabled", True)

    class _BetaMessages:
        def stream(self, **kwargs):
            raise RuntimeError("Error code: 529 - overloaded")

    class _Beta:
        messages = _BetaMessages()

    client = _FakeClient([_msg("# Report")])
    client.beta = _Beta()

    with pytest.raises(RuntimeError, match="overloaded"):
        ag.generate_report("RELIANCE", client=client)


# --------------------------------------------------------------------------- #
# Source allowlist
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("https://www.screener.in/company/RELIANCE/consolidated/", True),
        ("https://trendlyne.com/equity/INFY/", True),
        ("https://economictimes.indiatimes.com/markets", True),
        ("https://finance.yahoo.com/quote/HDFCBANK.NS", False),
        ("https://www.reddit.com/r/IndiaInvestments", False),
        ("https://notscreener.in/x", False),
        ("not a url", False),
    ],
)
def test_is_allowed(url, allowed):
    assert src.is_allowed(url) is allowed


def test_blocked_domains_are_absent_from_the_allowlist():
    for blocked in src.BLOCKED_DOMAINS:
        assert blocked not in src.ALLOWED_DOMAINS
