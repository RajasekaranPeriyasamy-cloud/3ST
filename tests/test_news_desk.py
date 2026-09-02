"""Tests for the Live Market News desk.

Fully offline. Feeds are served from fixture bytes, the instrument master is
stubbed, and no test reaches a publisher or the broker.

The ticker tests carry the most weight: resolution is the least exact part of
the desk, and the false-positive guards are the thing most likely to be
loosened by accident later.
"""

from __future__ import annotations

import json
from datetime import UTC

import pytest

from analysis.news_desk import feed, lexicon, normalize, sentiment, store, tickers

# --- lexicon ----------------------------------------------------------------


@pytest.mark.parametrize(
    "headline, expected",
    [
        ("Tata Steel profit jumps 40%, beats estimates", "positive"),
        ("Infosys shares plunge 8% after guidance cut", "negative"),
        ("Company to hold board meeting on Tuesday", "neutral"),
        ("Reliance bags order worth Rs 5,000 crore", "positive"),
        ("Auditor resigns; shares hit lower circuit", "negative"),
    ],
)
def test_lexicon_labels(headline, expected):
    assert lexicon.score(headline)["label"] == expected


def test_lexicon_compound_is_bounded():
    """Even a headline stuffed with loaded terms must stay inside [-1, 1]."""
    piled_on = "plunge crash slump tumble fraud scam default bankruptcy panic"
    assert -1.0 <= lexicon.compound(piled_on) <= 0.0
    assert lexicon.compound("surge soar rally jump beats estimates upgrade") <= 1.0


def test_lexicon_negation_flips_polarity():
    plain = lexicon.compound("results weak")
    negated = lexicon.compound("results not weak")
    assert plain < 0 < negated, f"expected a sign flip, got {plain} -> {negated}"


def test_lexicon_phrase_beats_component_word():
    """'profit booking' is bearish despite containing the bullish 'profit'."""
    assert lexicon.compound("profit booking drags the index") < 0


def test_lexicon_neutral_band_is_symmetric():
    assert lexicon.label_for(0.0) == "neutral"
    assert lexicon.label_for(0.149) == "neutral"
    assert lexicon.label_for(-0.149) == "neutral"
    assert lexicon.label_for(0.15) == "positive"
    assert lexicon.label_for(-0.15) == "negative"


def test_lexicon_category_specific_beats_generic():
    # "Vedanta Aluminium" is a brokerage note, not a metals story — Brokerage is
    # deliberately ordered above Commodities.
    text = "Brokerages initiate coverage on Vedanta Aluminium with 36% upside"
    assert lexicon.category_for(text) == "Brokerage"
    assert lexicon.category_for("Gold may rise 15-20% as festive demand picks up") == "Commodities"
    assert lexicon.category_for("Prince Pipes sets record date for dividend") == "Corporate Action"


# --- normalize --------------------------------------------------------------


def test_item_id_is_stable_across_calls():
    first = normalize.item_id(guid="abc-123", url="https://x.test/a")
    second = normalize.item_id(guid="abc-123", url="https://x.test/DIFFERENT")
    assert first == second, "guid must win over url, so a changed url is still one item"


def test_item_id_falls_back_to_url_then_title():
    by_url = normalize.item_id(url="https://x.test/a")
    assert by_url == normalize.item_id(url="https://x.test/a")
    by_title = normalize.item_id(publisher="ET", title="Some Headline")
    assert by_title == normalize.item_id(publisher="et", title="some headline")


def test_clean_text_strips_markup_and_entities():
    assert normalize.clean_text("<p>Profit &amp; loss</p>") == "Profit & loss"


def test_clean_text_handles_double_encoded_entities():
    """Livemint ships literal '&amp;nbsp;' — one unescape pass leaves it visible."""
    assert "&nbsp;" not in normalize.clean_text("Growth was uneven.&amp;nbsp;")
    assert normalize.clean_text("Growth was uneven.&amp;nbsp;") == "Growth was uneven."


def test_dedupe_collapses_same_story_across_publishers():
    items = [
        normalize.build_item(
            title="Tata Motors Q1 profit falls 12% on weak JLR sales",
            url="https://a.test/1",
            publisher="Economic Times",
        ),
        normalize.build_item(
            title="Tata Motors Q1 profit falls 12% on weak JLR sales",
            url="https://b.test/2",
            publisher="Livemint",
        ),
    ]
    deduped = normalize.dedupe(items)
    assert len(deduped) == 1
    assert deduped[0]["publisher"] == "Economic Times"
    assert "Livemint" in deduped[0]["also_reported_by"]


def test_dedupe_keeps_genuinely_different_stories():
    items = [
        normalize.build_item(title="Tata Motors Q1 profit falls 12%", url="https://a.test/1"),
        normalize.build_item(title="Infosys wins $500 million cloud deal", url="https://b.test/2"),
    ]
    assert len(normalize.dedupe(items)) == 2


def test_parse_published_handles_junk_without_dropping_the_item():
    assert normalize.parse_published("not a date").endswith("+00:00")
    assert normalize.parse_published("Tue, 02 Sep 2026 06:54:15 +0000").startswith("2026-09-02")


# --- tickers ----------------------------------------------------------------


@pytest.fixture
def stub_index(monkeypatch):
    """A small deterministic index, standing in for the Kite instrument master."""
    rows = [
        ("HEROMOTOCO", "HERO MOTOCORP", 1),
        ("ICICIBANK", "ICICI BANK", 2),
        ("BEML", "BEML", 3),
        ("INDIGO", "INTERGLOBE AVIATION", 4),
        ("TMPV", "TATA MOTORS PASSENGER VEHICLES", 5),
        ("SWIGGY", "SWIGGY", 6),
        ("GLOBAL", "GLOBAL", 7),          # a real NSE name that is a common word
        ("ACC", "ACC", 8),                # short symbol, also an ordinary token
        ("NIFTYBEES", "NIPPON INDIA ETF NIFTY BEES", 9),  # fund row, must be dropped
    ]

    def fake_build_index(force=False):
        keys = {}
        for symbol, name, token in rows:
            if tickers._FUND_RE.search(symbol) or tickers._FUND_RE.search(name):
                continue
            meta = {
                "exchange": "NSE",
                "tradingsymbol": symbol,
                "name": name,
                "instrument_token": token,
            }
            for key in {symbol, tickers._clean_name(name)}:
                if not key or key in tickers._BLOCKED_KEYS or len(key) < 3:
                    continue
                if " " not in key and key in tickers._COMMON_WORDS:
                    continue
                keys.setdefault(key, meta)
        for phrase, symbol in tickers.load_aliases().items():
            meta = next((m for m in keys.values() if m["tradingsymbol"] == symbol.upper()), None)
            if meta is not None:
                keys[tickers._clean_name(phrase)] = meta
        return sorted(keys.items(), key=lambda kv: -len(kv[0]))

    monkeypatch.setattr(tickers, "build_index", fake_build_index)
    return fake_build_index


def test_resolve_matches_multiword_company_name(stub_index):
    hits = tickers.resolve("Hero MotoCorp shares slide 5% on weak retail data")
    assert [h["tradingsymbol"] for h in hits] == ["HEROMOTOCO"]


def test_resolve_uses_alias_when_registered_name_differs(stub_index):
    """Nobody writes 'InterGlobe Aviation' in a headline."""
    hits = tickers.resolve("IndiGo shares dive 4% as oil spikes")
    assert [h["tradingsymbol"] for h in hits] == ["INDIGO"]


def test_resolve_ignores_generic_single_word_names(stub_index):
    """'Global' is a real NSE name; it must not fire on ordinary market copy."""
    hits = tickers.resolve("Global bond selloff deepens as inflation risks mount")
    assert hits == []


def test_resolve_requires_caps_for_short_symbols(stub_index):
    """A short symbol only counts when the headline wrote it as a ticker."""
    assert tickers.resolve("BEML shares rise 2% after Vande Bharat order")
    assert tickers.resolve("the beml plant reopened next week") == []


def test_resolve_excludes_etf_rows(stub_index):
    assert tickers.resolve("NIFTYBEES sees record inflows") == []


def test_resolve_prefers_the_longer_more_specific_match(stub_index):
    hits = tickers.resolve("Tata Motors Passenger Vehicles reports weak JLR volumes")
    assert hits[0]["tradingsymbol"] == "TMPV"


def test_resolve_is_capped(stub_index):
    hits = tickers.resolve(
        "Hero MotoCorp, ICICI Bank, BEML and Swiggy all move on results", limit=2
    )
    assert len(hits) <= 2


def test_blocked_keys_cover_the_exchanges():
    """'listed on BSE' is not a story about BSE Ltd."""
    for key in ("BSE", "NSE", "NIFTY", "SENSEX"):
        assert key in tickers._BLOCKED_KEYS


# --- store ------------------------------------------------------------------


@pytest.fixture
def clean_store(tmp_path, monkeypatch):
    """Point the store at a tmp file.

    Patches the store module's own constants — patching ``settings.data_dir``
    would not work, because the module binds the path at import time.
    """
    monkeypatch.setattr(store, "ITEMS_FILE", tmp_path / "news_items.json")
    monkeypatch.setattr(store, "CONFIG_FILE", tmp_path / "news_desk_config.json")
    monkeypatch.setattr(store, "_ITEMS", {})
    monkeypatch.setattr(store, "_CONFIG", dict(store._DEFAULT_CONFIG))
    return tmp_path


def _item(title: str, published: str, **kwargs):
    return normalize.build_item(title=title, url=f"https://x.test/{title}", published=published, **kwargs)


def test_store_upsert_is_idempotent(clean_store):
    items = [_item("Reliance gains", "2026-09-01T10:00:00+00:00")]
    assert store.upsert_many(items) == 1
    assert store.upsert_many(items) == 0, "re-polling the same feed must not duplicate"
    assert store.count() == 1


def test_store_does_not_overwrite_a_paid_for_score(clean_store):
    items = [_item("Reliance gains", "2026-09-01T10:00:00+00:00")]
    store.upsert_many(items)
    item_id = items[0]["id"]
    store.apply_sentiment({item_id: {"label": "positive", "score": 0.5, "engine": "anthropic"}})

    store.upsert_many([_item("Reliance gains", "2026-09-01T10:00:00+00:00")])
    kept = store.all_items()[0]
    assert kept["sentiment"]["engine"] == "anthropic"


def test_store_round_trips_through_disk(clean_store):
    store.upsert_many([_item("Infosys wins deal", "2026-09-01T10:00:00+00:00")])
    store.load_persisted_items()
    assert store.count() == 1


def test_store_unscored_only_returns_unscored(clean_store):
    store.upsert_many(
        [
            _item("A rises", "2026-09-01T10:00:00+00:00"),
            _item("B falls", "2026-09-01T11:00:00+00:00"),
        ]
    )
    first = store.all_items()[0]["id"]
    store.apply_sentiment({first: {"label": "positive", "score": 0.4}})
    assert [i["id"] for i in store.unscored()] != [first]
    assert len(store.unscored()) == 1


def test_store_config_clamps_poll_interval(clean_store):
    assert store.save_config({"poll_sec": 2})["poll_sec"] == 15
    assert store.save_config({"poll_sec": 300})["poll_sec"] == 300


# --- sentiment dispatch -----------------------------------------------------


def test_sentiment_falls_back_to_lexicon_when_llm_returns_nothing(monkeypatch):
    """A capped or broken LLM backend must never leave an item unscored.

    An unscored item is retried on every poll forever, which on a paid backend
    is the expensive failure mode.
    """
    monkeypatch.setattr(
        "settings.news_desk_config",
        lambda: {
            "provider": "anthropic", "model": "claude-haiku-4-5", "poll_sec": 60,
            "daily_usd_cap": 2.0, "batch": 25, "announcements": False,
        },
    )
    monkeypatch.setattr("analysis.news_desk.llm.score_batch", lambda items: {})

    items = [_item("Tata Steel profit jumps 40%", "2026-09-01T10:00:00+00:00")]
    scored = sentiment.score_items(items)
    assert scored[items[0]["id"]]["engine"] == "lexicon"
    assert scored[items[0]["id"]]["label"] == "positive"


def test_sentiment_scores_every_item_it_is_given():
    items = [
        _item("A surges on strong demand", "2026-09-01T10:00:00+00:00"),
        _item("B plunges after fraud probe", "2026-09-01T11:00:00+00:00"),
    ]
    scored = sentiment.score_items(items)
    assert set(scored) == {i["id"] for i in items}


# --- feed assembly ----------------------------------------------------------


def test_feed_clusters_same_symbol_items(clean_store):
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    symbols = [{"exchange": "NSE", "tradingsymbol": "HEROMOTOCO", "name": "HERO MOTOCORP"}]
    store.upsert_many(
        [
            _item("Hero MotoCorp falls 5%", (now - timedelta(hours=1)).isoformat(), symbols=symbols),
            _item("Hero MotoCorp exports drop", (now - timedelta(hours=2)).isoformat(), symbols=symbols),
            _item("Hero MotoCorp wholesales weak", (now - timedelta(hours=3)).isoformat(), symbols=symbols),
        ]
    )
    snapshot = feed.build(limit=20, with_prices=False)
    assert snapshot["returned"] == 1, "three items on one symbol collapse to one row"
    assert snapshot["items"][0]["related_count"] == 2


def test_feed_never_clusters_unresolved_items(clean_store):
    """Items with no symbol have nothing reliable to cluster on."""
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    store.upsert_many(
        [
            _item("Market wrap one", (now - timedelta(hours=1)).isoformat()),
            _item("Market wrap two", (now - timedelta(hours=2)).isoformat()),
        ]
    )
    assert feed.build(limit=20, with_prices=False)["returned"] == 2


def test_feed_separates_news_from_actions(clean_store):
    store.upsert_many(
        [
            _item("Reliance gains", "2026-09-01T10:00:00+00:00"),
            _item("ACME Ltd: Shareholders meeting", "2026-09-01T11:00:00+00:00", kind="action"),
        ]
    )
    assert feed.build(tab="all", with_prices=False)["returned"] == 1
    assert feed.build(tab="actions", with_prices=False)["returned"] == 1


def test_feed_mine_tab_is_empty_without_a_watchlist(clean_store):
    store.upsert_many([_item("Reliance gains", "2026-09-01T10:00:00+00:00")])
    snapshot = feed.build(tab="mine", watchlist_symbols=[], with_prices=False)
    assert snapshot["returned"] == 0


def test_feed_mine_tab_filters_to_watchlist(clean_store):
    symbols = [{"exchange": "NSE", "tradingsymbol": "RELIANCE", "name": "RELIANCE"}]
    store.upsert_many(
        [
            _item("Reliance gains", "2026-09-01T10:00:00+00:00", symbols=symbols),
            _item("Some other stock moves", "2026-09-01T11:00:00+00:00"),
        ]
    )
    snapshot = feed.build(tab="mine", watchlist_symbols=["RELIANCE"], with_prices=False)
    assert snapshot["returned"] == 1
    assert snapshot["items"][0]["title"] == "Reliance gains"


def test_feed_survives_a_broker_read_failure(clean_store, monkeypatch):
    """No quotes must mean no price chips, not a failed page."""
    symbols = [{"exchange": "NSE", "tradingsymbol": "RELIANCE", "name": "RELIANCE"}]
    store.upsert_many([_item("Reliance gains", "2026-09-01T10:00:00+00:00", symbols=symbols)])

    def boom(_keys):
        raise RuntimeError("kite unavailable")

    monkeypatch.setattr("kite_client.fetch_quote_batch", boom)
    snapshot = feed.build(limit=5, with_prices=True)
    assert snapshot["returned"] == 1
    assert "last_price" not in snapshot["items"][0]["symbols"][0]


def test_feed_sentiment_filter(clean_store):
    store.upsert_many(
        [
            _item("A surges", "2026-09-01T10:00:00+00:00"),
            _item("B plunges", "2026-09-01T11:00:00+00:00"),
        ]
    )
    store.apply_sentiment(sentiment.score_items(store.all_items()))
    assert feed.build(sentiment_filter="negative", with_prices=False)["returned"] == 1


# --- ingestion guards -------------------------------------------------------


def test_feed_fetch_reports_a_dead_publisher_as_data(monkeypatch):
    """One publisher failing reduces the feed; it must not raise."""
    from analysis.news_desk import feeds

    class DeadSession:
        def get(self, *_a, **_k):
            raise OSError("connection reset")

    monkeypatch.setattr(feeds, "direct_session", lambda **_k: DeadSession())
    result = feeds.fetch_source(feeds.SOURCES[0])
    assert result["ok"] is False
    assert result["items"] == []
    assert "OSError" in result["error"]


def test_announcements_failure_is_contained(monkeypatch):
    from analysis.news_desk import announcements

    class DeadSession:
        def get(self, *_a, **_k):
            raise OSError("nse unreachable")

    monkeypatch.setattr(announcements, "direct_session", lambda **_k: DeadSession())
    result = announcements.fetch()
    assert result["ok"] is False
    assert result["items"] == []


# --- egress ------------------------------------------------------------------


def test_news_sessions_bypass_the_kite_static_ip_proxy(monkeypatch):
    """News traffic must never go through the static-IP order proxy.

    settings.apply_kite_proxy_env() pins HTTP(S)_PROXY process-wide AND deletes
    NO_PROXY, so a session that trusts the environment silently routes publisher
    traffic through metered order egress. On 2026-09-02 that took all eleven
    sources from healthy to ProxyError the moment the desk ran under uvicorn —
    a failure invisible to any test that runs outside the API process.
    """
    from analysis.news_desk.net import direct_session

    monkeypatch.setenv("HTTPS_PROXY", "http://should-not-be-used.test:3128")
    monkeypatch.setenv("HTTP_PROXY", "http://should-not-be-used.test:3128")
    monkeypatch.delenv("NO_PROXY", raising=False)

    session = direct_session()
    assert session.trust_env is False
    assert not session.proxies
    # The real check: requests merges env proxies in unless trust_env is False.
    merged = session.merge_environment_settings("https://feeds.test/rss", {}, None, None, None)
    assert not merged["proxies"], f"news session would use proxy {merged['proxies']}"


def test_announcements_parses_ist_into_utc():
    from analysis.news_desk import announcements

    # 12:32:19 IST is 07:02:19 UTC.
    assert announcements._parse_ist("02-Sep-2026 12:32:19").startswith("2026-09-02T07:02:19")


def test_alias_file_is_optional(tmp_path, monkeypatch):
    """A missing or corrupt alias file must not break resolution."""
    monkeypatch.setattr(tickers, "ALIAS_FILE", tmp_path / "missing.json")
    assert tickers.load_aliases()

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(tickers, "ALIAS_FILE", bad)
    assert tickers.load_aliases(), "seed aliases survive a corrupt user file"


def test_alias_file_overrides_seed(tmp_path, monkeypatch):
    path = tmp_path / "news_aliases.json"
    path.write_text(json.dumps({"aliases": {"my company": "RELIANCE"}}), encoding="utf-8")
    monkeypatch.setattr(tickers, "ALIAS_FILE", path)
    assert tickers.load_aliases()["my company"] == "RELIANCE"


# --- LLM daily spend cap -----------------------------------------------------


@pytest.fixture
def spend_store(tmp_path, monkeypatch):
    """Point the spend ledger at a tmp file and start from empty."""
    from analysis.news_desk import llm

    monkeypatch.setattr(llm, "SPEND_FILE", tmp_path / "news_llm_spend.json")
    monkeypatch.setattr(llm, "_SPEND", {})
    return llm


def test_spend_survives_a_restart(spend_store):
    """The cap is per DAY, not per process.

    This was the bug: _SPEND was in-memory, so every API restart handed the desk
    a fresh budget. The desk restarts several times on a working day, so a "$2
    daily cap" could be spent repeatedly.
    """
    llm = spend_store
    llm._record_spend(1.25)
    assert llm.spend_today_usd() == 1.25

    # Simulate a process restart: drop the in-memory tally, reload from disk.
    llm._SPEND.clear()
    assert llm.spend_today_usd() == 0.0, "sanity — the tally really was cleared"
    llm.load_persisted_spend()

    assert llm.spend_today_usd() == 1.25, "spend must be restored from disk"


def test_spend_accumulates_across_restarts(spend_store):
    llm = spend_store
    llm._record_spend(0.80)
    llm._SPEND.clear()
    llm.load_persisted_spend()
    llm._record_spend(0.75)
    assert llm.spend_today_usd() == pytest.approx(1.55)


def test_cap_blocks_scoring_after_a_restart(spend_store, monkeypatch):
    """Over-cap must stay over-cap across a restart, and fall back to lexicon."""
    llm = spend_store
    monkeypatch.setattr(
        "settings.news_desk_config",
        lambda: {
            "provider": "anthropic", "model": "claude-haiku-4-5", "poll_sec": 60,
            "daily_usd_cap": 2.0, "batch": 25, "announcements": False,
        },
    )
    monkeypatch.setattr("settings.anthropic_api_key", lambda: "sk-test")

    llm._record_spend(2.5)
    assert llm.cap_status()["capped"] is True

    llm._SPEND.clear()
    llm.load_persisted_spend()
    assert llm.cap_status()["capped"] is True, "restart must not clear the cap"

    # No anthropic stub needed, and that is the point: the cap is checked before
    # the SDK is imported, so an over-cap call cannot reach the network even in
    # an environment where the package is not installed.
    item = _item("Tata Steel profit jumps 40%", "2026-09-01T10:00:00+00:00")
    assert llm.score_batch([item]) == {}


def test_spend_ledger_prunes_old_days(spend_store):
    from datetime import date, timedelta

    llm = spend_store
    stale = (date.today() - timedelta(days=llm.SPEND_RETENTION_DAYS + 5)).isoformat()
    llm._SPEND[stale] = 9.99
    llm._record_spend(0.10)
    assert stale not in llm._SPEND
    llm._SPEND.clear()
    llm.load_persisted_spend()
    assert stale not in llm._SPEND


def test_corrupt_spend_ledger_does_not_crash(spend_store):
    """A bad ledger loses the tally but must not take scoring down."""
    llm = spend_store
    llm.SPEND_FILE.write_text("{not json", encoding="utf-8")
    llm.load_persisted_spend()
    assert llm.spend_today_usd() == 0.0
    llm._record_spend(0.5)
    assert llm.spend_today_usd() == 0.5


def test_unreadable_spend_file_does_not_break_a_poll(spend_store, monkeypatch):
    """An unwritable ledger degrades to per-process, it does not raise."""
    llm = spend_store

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(type(llm.SPEND_FILE), "write_text", boom)
    llm._record_spend(0.25)
    assert llm.spend_today_usd() == 0.25, "in-memory tally still works"


# --- equity search -----------------------------------------------------------


def test_search_terms_include_symbol_name_and_aliases(stub_index):
    terms = tickers.search_terms("INDIGO")
    assert "INDIGO" in terms
    assert "INTERGLOBE AVIATION" in terms, "registered name must be searchable"
    assert any("INTERGLOBE" in t for t in terms)
    # Longest first, so a caller matching greedily hits the specific form first.
    assert terms == sorted(terms, key=len, reverse=True)


def test_search_terms_empty_for_blank_symbol(stub_index):
    assert tickers.search_terms("") == []
    assert tickers.search_terms("   ") == []


def test_search_finds_untagged_text_mentions(clean_store, stub_index):
    """The whole point: search is looser than resolution.

    An item the resolver passed over must still be findable by name — otherwise
    the search box is silently weaker than the ticker chips suggest.
    """
    tagged = _item(
        "Hero MotoCorp shares slide 5%",
        "2026-09-01T10:00:00+00:00",
        symbols=[{"exchange": "NSE", "tradingsymbol": "HEROMOTOCO", "name": "HERO MOTOCORP"}],
    )
    untagged = _item("Analysts remain split on Hero MotoCorp after weak retails", "2026-09-01T09:00:00+00:00")
    store.upsert_many([tagged, untagged])

    found = feed.build(symbol="HEROMOTOCO", with_prices=False)
    assert found["returned"] == 2, "must find the untagged mention too"


def test_search_matches_on_word_boundary(clean_store, stub_index):
    """RELIANCE must not drag in RELIANCEPOWER."""
    store.upsert_many(
        [
            _item("Swiggy posts wider loss", "2026-09-01T10:00:00+00:00"),
            _item("Swiggyverse launches something unrelated", "2026-09-01T09:00:00+00:00"),
        ]
    )
    found = feed.build(symbol="SWIGGY", with_prices=False)
    titles = [i["title"] for i in found["items"]]
    assert "Swiggy posts wider loss" in titles
    assert "Swiggyverse launches something unrelated" not in titles


def test_search_does_not_cluster_results(clean_store, stub_index):
    """Clustering keys on primary symbol, so a symbol search would collapse to
    one row and look broken. Search results are flat."""
    symbols = [{"exchange": "NSE", "tradingsymbol": "HEROMOTOCO", "name": "HERO MOTOCORP"}]
    from datetime import datetime, timedelta

    now = datetime.now(UTC)
    store.upsert_many(
        [
            _item("Hero MotoCorp falls 5%", (now - timedelta(hours=1)).isoformat(), symbols=symbols),
            _item("Hero MotoCorp exports drop", (now - timedelta(hours=2)).isoformat(), symbols=symbols),
            _item("Hero MotoCorp wholesales weak", (now - timedelta(hours=3)).isoformat(), symbols=symbols),
        ]
    )
    # Unfiltered: clustered into one row.
    assert feed.build(with_prices=False)["returned"] == 1
    # Searched: flat.
    searched = feed.build(symbol="HEROMOTOCO", with_prices=False)
    assert searched["returned"] == 3
    assert searched["clustered"] is False
    assert all(i["related_count"] == 0 for i in searched["items"])


def test_free_text_search_matches_title_and_summary(clean_store):
    store.upsert_many(
        [
            normalize.build_item(
                title="Board approves dividend",
                url="https://x.test/1",
                summary="The company will pay a special payout.",
                published="2026-09-01T10:00:00+00:00",
            ),
            normalize.build_item(
                title="Quarterly numbers disappoint",
                url="https://x.test/2",
                published="2026-09-01T09:00:00+00:00",
            ),
        ]
    )
    assert feed.build(q="dividend", with_prices=False)["returned"] == 1
    assert feed.build(q="special payout", with_prices=False)["returned"] == 1, "summary is searched"
    assert feed.build(q="DIVIDEND", with_prices=False)["returned"] == 1, "case-insensitive"
    assert feed.build(q="nothing matches this", with_prices=False)["returned"] == 0


def test_search_echoes_the_active_query(clean_store, stub_index):
    snap = feed.build(symbol="heromotoco", q=" dividend ", with_prices=False)
    assert snap["symbol"] == "HEROMOTOCO"
    assert snap["q"] == "dividend"


def test_search_survives_a_missing_instrument_cache(clean_store, monkeypatch):
    """No instrument master: fall back to the plain symbol, don't return nothing."""
    def boom(_symbol):
        raise RuntimeError("no instrument cache")

    monkeypatch.setattr(tickers, "search_terms", boom)
    store.upsert_many(
        [
            _item(
                "HEROMOTOCO slides",
                "2026-09-01T10:00:00+00:00",
                symbols=[{"exchange": "NSE", "tradingsymbol": "HEROMOTOCO", "name": ""}],
            )
        ]
    )
    assert feed.build(symbol="HEROMOTOCO", with_prices=False)["returned"] == 1
