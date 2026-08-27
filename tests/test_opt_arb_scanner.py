"""Sweep orchestration and the persisted config store.

The store tests redirect ``store.CONFIG_FILE`` at the *module's own* reference
and then assert the real ``data/`` file was never touched. ``store.py`` does
``from settings import data_dir`` at import time and resolves ``CONFIG_FILE``
once, so patching ``settings.data_dir`` here would do nothing and the test
would quietly write into live state — the failure mode that put 1,800
synthetic snapshots into the delta-velocity archive on 2026-08-13.
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta

import pandas as pd
import pytest

from analysis.opt_arb import costs, scanner, store, universe
from analysis.opt_arb.quotes import Quote, quote_key

SPOT = 24300.0
LOT = 65


def _expiry(days: int = 7) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _fair(strike: float, opt: str) -> float:
    intrinsic = max(SPOT - strike, 0.0) if opt == "CE" else max(strike - SPOT, 0.0)
    return round(intrinsic + 120.0 * math.exp(-(((strike - SPOT) / 700.0) ** 2)), 2)


@pytest.fixture(autouse=True)
def _pristine():
    saved = dict(store._CONFIG)
    costs.reset_rates()
    universe.clear_caches()
    yield
    store._CONFIG.clear()
    store._CONFIG.update(saved)
    costs.reset_rates()
    universe.clear_caches()


@pytest.fixture
def nifty(monkeypatch: pytest.MonkeyPatch):
    expiry = _expiry()
    rows = []
    token = 1
    for i in range(7):
        strike = 24000.0 + 100.0 * i
        for opt in ("CE", "PE"):
            rows.append(
                {
                    "instrument_token": token,
                    "tradingsymbol": f"NIFTY{strike:.0f}{opt}",
                    "name": "NIFTY",
                    "expiry": expiry,
                    "strike": strike,
                    "lot_size": LOT,
                    "instrument_type": opt,
                    "exchange": "NFO",
                }
            )
            token += 1
    frame = pd.DataFrame(rows)
    monkeypatch.setattr(universe, "load_instruments", lambda *a, **k: frame)
    universe.clear_caches()
    return expiry


def _book(expiry: str, price_of) -> dict[str, Quote]:
    quotes: dict[str, Quote] = {}
    for strike, legs in universe.strike_map("NIFTY", "NFO", expiry).items():
        for opt, contract in legs.items():
            mid = price_of(strike, opt)
            key = quote_key(contract["exchange"], contract["tradingsymbol"])
            quotes[key] = Quote(
                key=key, bid=round(mid - 0.5, 2), ask=round(mid + 0.5, 2),
                bid_qty=5000, ask_qty=5000, ltp=mid,
            )
    return quotes


# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------


def test_implied_spot_recovers_the_forward_from_the_book(nifty):
    quotes = _book(nifty, _fair)
    smap = universe.strike_map("NIFTY", "NFO", nifty)
    assert scanner.implied_spot(smap, quotes) == pytest.approx(SPOT, abs=1.0)


def test_implied_spot_is_none_without_quotes(nifty):
    smap = universe.strike_map("NIFTY", "NFO", nifty)
    assert scanner.implied_spot(smap, {}) is None


def test_scan_underlying_is_clean_on_a_fair_book(nifty):
    result = scanner.scan_underlying("NIFTY", "NFO", nifty, quotes=_book(nifty, _fair))
    assert result["rows"] == []
    assert result["expiry"] == nifty
    assert result["implied_spot"] == pytest.approx(SPOT, abs=1.0)


def test_scan_underlying_runs_only_the_requested_families(nifty):
    def priced(strike, opt):
        bump = 80.0 if (strike == 24300.0 and opt == "CE") else 0.0
        return _fair(strike, opt) + bump

    quotes = _book(nifty, priced)
    only_fly = scanner.scan_underlying(
        "NIFTY", "NFO", nifty, families=["butterfly"], quotes=quotes
    )
    assert only_fly["rows"]
    assert {r["family"] for r in only_fly["rows"]} == {"butterfly"}


def test_min_net_floor_drops_marginal_rows(nifty):
    def priced(strike, opt):
        bump = 80.0 if (strike == 24300.0 and opt == "CE") else 0.0
        return _fair(strike, opt) + bump

    quotes = _book(nifty, priced)
    loose = scanner.scan_underlying("NIFTY", "NFO", nifty, min_net=0.0, quotes=quotes)
    strict = scanner.scan_underlying("NIFTY", "NFO", nifty, min_net=1e9, quotes=quotes)
    assert loose["rows"]
    assert strict["rows"] == []


def test_unknown_underlying_reports_skipped_instead_of_raising(nifty):
    result = scanner.scan_underlying("NOSUCH", "NFO", None)
    assert result["rows"] == []
    assert "universe" in result["skipped"]


def test_scan_all_survives_a_broken_underlying(monkeypatch: pytest.MonkeyPatch, nifty):
    def boom(*_a, **_k):
        raise RuntimeError("instrument dump unreadable")

    monkeypatch.setattr(scanner, "scan_underlying", boom)
    result = scanner.scan_all(
        families=["butterfly"], underlyings=[{"name": "NIFTY", "exchange": "NFO"}]
    )
    assert result["rows"] == []
    assert "NFO:NIFTY" in result["skipped"]


def test_scan_all_counts_rows_by_tier(nifty):
    result = scanner.scan_all(families=[], underlyings=[])
    assert result["counts"] == {
        "rows": 0,
        "tier_a": 0,
        "tier_b": 0,
        "instruments_quoted": 0,
    }
    assert result["generated_at"]


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------


def test_save_config_round_trips_without_touching_live_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    real = store.CONFIG_FILE
    before = real.read_text(encoding="utf-8") if real.exists() else None

    monkeypatch.setattr(store, "CONFIG_FILE", tmp_path / "opt_arb_config.json")
    store.save_config({"min_net_rs": 250.0, "lots": 3, "require_clean": False})

    saved = json.loads((tmp_path / "opt_arb_config.json").read_text(encoding="utf-8"))
    assert saved["min_net_rs"] == 250.0
    assert saved["lots"] == 3
    assert saved["require_clean"] is False

    after = real.read_text(encoding="utf-8") if real.exists() else None
    assert after == before, "the live config file must not be written by a test"


def test_save_config_ignores_unknown_keys(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(store, "CONFIG_FILE", tmp_path / "cfg.json")
    result = store.save_config({"nonsense": 1, "lots": 2})
    assert "nonsense" not in result
    assert result["lots"] == 2


def test_lots_is_clamped_to_at_least_one(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(store, "CONFIG_FILE", tmp_path / "cfg.json")
    assert store.save_config({"lots": 0})["lots"] == 1


def test_families_fall_back_when_all_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(store, "CONFIG_FILE", tmp_path / "cfg.json")
    assert store.save_config({"families": ["nope"]})["families"] == store.DEFAULT_CONFIG["families"]


def test_only_changed_rate_fields_are_persisted(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A future correction to the shipped rate card must not be shadowed by a
    full stale copy on disk."""
    path = tmp_path / "cfg.json"
    monkeypatch.setattr(store, "CONFIG_FILE", path)
    store.save_config({"rates": {"NFO": {"txn_pct": 0.06}}})

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["rate_overrides"] == {"NFO": {"txn_pct": 0.06}}


def test_corrupt_config_file_leaves_defaults_in_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    path = tmp_path / "cfg.json"
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(store, "CONFIG_FILE", path)
    store._CONFIG.clear()
    store._CONFIG.update(store.DEFAULT_CONFIG)
    store.load_persisted_config()
    assert store.config()["lots"] == store.DEFAULT_CONFIG["lots"]


def test_persisted_rate_override_is_restored_on_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    path = tmp_path / "cfg.json"
    monkeypatch.setattr(store, "CONFIG_FILE", path)
    store.save_config({"rates": {"MCX": {"txn_pct": 0.09}}})
    costs.reset_rates()
    assert costs.rates_for("MCX").txn_pct != 0.09

    store.load_persisted_config()
    assert costs.rates_for("MCX").txn_pct == pytest.approx(0.09)


def _thin_book(expiry: str, price_of, depth: int) -> dict[str, Quote]:
    quotes = _book(expiry, price_of)
    return {
        k: Quote(
            key=q.key, bid=q.bid, ask=q.ask, bid_qty=depth, ask_qty=depth, ltp=q.ltp
        )
        for k, q in quotes.items()
    }


def _mispriced(strike: float, opt: str) -> float:
    bump = 80.0 if (strike == 24300.0 and opt == "CE") else 0.0
    return _fair(strike, opt) + bump


def test_depth_gate_drops_rows_the_book_cannot_fill(nifty):
    """65 units is one NIFTY lot; 60 is not even that."""
    fillable = scanner.scan_underlying(
        "NIFTY", "NFO", nifty, quotes=_thin_book(nifty, _mispriced, depth=65)
    )
    assert fillable["rows"]

    thin = scanner.scan_underlying(
        "NIFTY", "NFO", nifty, quotes=_thin_book(nifty, _mispriced, depth=60)
    )
    assert thin["rows"] == []


def test_depth_gate_annotates_instead_of_dropping_when_relaxed(nifty):
    relaxed = scanner.scan_underlying(
        "NIFTY",
        "NFO",
        nifty,
        require_depth=False,
        quotes=_thin_book(nifty, _mispriced, depth=60),
    )
    assert relaxed["rows"]
    assert any("top of book supports" in w for w in relaxed["rows"][0]["warnings"])


def test_max_lots_is_reported_in_lots_not_units(nifty):
    result = scanner.scan_underlying(
        "NIFTY", "NFO", nifty, quotes=_thin_book(nifty, _mispriced, depth=325)
    )
    assert result["rows"]
    # 325 units / 65 = 5 lots on a wing; the fly's body needs double, so 2.
    assert all(r["max_lots"] <= 5 for r in result["rows"])
    assert any(r["max_lots"] >= 2 for r in result["rows"])
