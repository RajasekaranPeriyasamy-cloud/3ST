"""Pinned tickers for the Equity Report desk."""

from __future__ import annotations

import pytest

from analysis.equity_report import pins as ep


@pytest.fixture
def clean_pins(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "PINS_FILE", tmp_path / "equity_pins.json")
    monkeypatch.setattr(ep, "_PINS", [])
    return ep


def test_add_normalises_symbol_and_persists(clean_pins, monkeypatch):
    ep.add_pin("  reliance ", "Reliance Industries")
    assert [p["symbol"] for p in ep.list_pins()] == ["RELIANCE"]

    monkeypatch.setattr(ep, "_PINS", [])
    ep.load_persisted_pins()
    assert ep.list_pins()[0]["company"] == "Reliance Industries"


def test_repin_updates_name_without_duplicating(clean_pins):
    ep.add_pin("INFY")
    ep.add_pin("infy", "Infosys Ltd")
    pins = ep.list_pins()
    assert len(pins) == 1
    assert pins[0]["company"] == "Infosys Ltd"


def test_remove_reports_whether_it_did_anything(clean_pins):
    ep.add_pin("TCS")
    assert ep.remove_pin("tcs") is True
    assert ep.remove_pin("TCS") is False
    assert ep.list_pins() == []


def test_empty_symbol_rejected(clean_pins):
    with pytest.raises(ValueError):
        ep.add_pin("   ")


def test_pin_limit_enforced(clean_pins, monkeypatch):
    monkeypatch.setattr(ep, "MAX_PINS", 2)
    ep.add_pin("A")
    ep.add_pin("B")
    with pytest.raises(RuntimeError, match="Pin limit"):
        ep.add_pin("C")


def _fake_watchlist(monkeypatch, names, equities):
    """Stub the watchlist and the instrument search the importer leans on."""
    import instruments
    import watchlist_store

    monkeypatch.setattr(watchlist_store, "list_items", lambda *a, **k: [{"name": n} for n in names])

    def _search(q, segment="equity", limit=25, force_refresh=False):
        if segment != "equity":
            return []
        return [
            {"tradingsymbol": q, "exchange": "NSE", "name": equities[q]}
            for _ in [0]
            if q in equities
        ]

    monkeypatch.setattr(instruments, "search_instruments", _search)


def test_import_skips_index_and_commodity_underlyings(clean_pins, monkeypatch):
    """The real watchlist holds NIFTY/BANKNIFTY/CRUDEOIL option legs, none of
    which are cash equities — the importer must report that, not pin them."""
    _fake_watchlist(monkeypatch, ["NIFTY", "BANKNIFTY", "CRUDEOIL"], equities={})

    result = ep.import_from_watchlist()
    assert result["added"] == []
    assert set(result["skipped"]) == {"NIFTY", "BANKNIFTY", "CRUDEOIL"}
    assert result["scanned"] == 3
    assert ep.list_pins() == []


def test_import_pins_real_equities_and_dedupes_names(clean_pins, monkeypatch):
    _fake_watchlist(
        monkeypatch,
        ["RELIANCE", "NIFTY", "RELIANCE", "INFY"],
        equities={"RELIANCE": "Reliance Industries", "INFY": "Infosys"},
    )

    result = ep.import_from_watchlist()
    assert result["scanned"] == 3  # RELIANCE listed twice, counted once
    assert set(result["added"]) == {"RELIANCE", "INFY"}
    assert result["skipped"] == ["NIFTY"]
    assert {p["symbol"] for p in ep.list_pins()} == {"RELIANCE", "INFY"}


def test_import_survives_a_failing_instrument_search(clean_pins, monkeypatch):
    import instruments
    import watchlist_store

    monkeypatch.setattr(watchlist_store, "list_items", lambda *a, **k: [{"name": "RELIANCE"}])

    def _boom(*a, **k):
        raise RuntimeError("instrument cache empty — log in to Kite")

    monkeypatch.setattr(instruments, "search_instruments", _boom)

    result = ep.import_from_watchlist()
    assert result["added"] == []
    assert result["skipped"] == ["RELIANCE"]
