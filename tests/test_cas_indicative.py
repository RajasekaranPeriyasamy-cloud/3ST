"""Tests for Closing Auction Session (CAS) indicative helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from vollib.black_scholes import black_scholes as bs_price

from options import cas_indicative as cas
from options import gamma_density as gd
from options.gamma_density_provider import StaticGammaDensityDataProvider

IST = ZoneInfo("Asia/Kolkata")
R = 0.065
SPOT = 20000.0
STEP = 50
TTE = 5.0 / 365.0
IV = 0.14
EXPIRY = "2026-08-13"


@pytest.fixture(autouse=True)
def _clear_cas_last_ticks(monkeypatch: pytest.MonkeyPatch) -> None:
    cas.clear_last_ticks()
    # Keep CAS unit tests off Kite for Fut POC / synth / estimate (tested explicitly).
    monkeypatch.setattr(
        "options.session_poc.compute_session_poc",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "options.synthetic_future.compute_synthetic_future",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "options.cas_estimate.compute_cas_estimate",
        lambda *a, **k: {
            "estimate": None,
            "estimate_method": "proxy_v1",
            "estimate_components": None,
            "asof": "2026-08-06T15:20:00+05:30",
        },
    )


def _dt(hh: int, mm: int) -> datetime:
    return datetime(2026, 8, 6, hh, mm, tzinfo=IST)


# --- window boundaries -------------------------------------------------------


def test_cas_window_boundaries() -> None:
    assert cas.in_cas_window(_dt(15, 14)) is False
    assert cas.in_cas_window(_dt(15, 15)) is True
    assert cas.in_cas_window(_dt(15, 20)) is True
    assert cas.in_cas_window(_dt(15, 35)) is True
    assert cas.in_cas_window(_dt(15, 36)) is False
    assert cas.in_cas_window(_dt(9, 30)) is False


# --- quote parser ------------------------------------------------------------


def test_parse_indicative_prefers_close_price() -> None:
    quote = {
        "last_price": 24750.0,
        "indicative_close_price": 24772.0,
        "reference_limit_price": 24700.0,
        "upper_circuit_limit": 25441.0,
        "lower_circuit_limit": 23959.0,
        "total_imbalance": 1200,
    }
    fields = cas.parse_cas_fields(quote)
    assert fields["indicative"] == 24772.0
    assert fields["reference_limit_price"] == 24700.0
    assert fields["upper_circuit_limit"] == 25441.0
    assert fields["lower_circuit_limit"] == 23959.0
    assert fields["total_imbalance"] == 1200


def test_parse_indicative_missing_field() -> None:
    quote = {
        "last_price": 24750.0,
        "reference_limit_price": 24700.0,
    }
    assert cas.parse_indicative_from_quote(quote) is None
    fields = cas.parse_cas_fields(quote)
    assert fields["indicative"] is None
    assert fields["reference_limit_price"] == 24700.0


def test_parse_indicative_empty_quote() -> None:
    assert cas.parse_indicative_from_quote(None) is None
    assert cas.parse_cas_fields(None)["indicative"] is None


# --- fetch_cas_indicative shape ----------------------------------------------


def test_fetch_cas_indicative_in_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)
    quote = {
        "indicative_close_price": 24772.0,
        "reference_limit_price": 24700.0,
        "upper_circuit_limit": None,
        "lower_circuit_limit": None,
        "total_imbalance": None,
    }
    out = cas.fetch_cas_indicative("NIFTY", now=_dt(15, 20), quote=quote)
    assert out["underlying"] == "NIFTY"
    assert out["in_cas_window"] is True
    assert out["spot"] == 24750.0
    assert out["indicative"] == 24772.0
    assert out["official_indicative"] == 24772.0
    assert out["official_raw"] == 24772.0
    assert out["official_reject_reason"] is None
    assert out["reference_limit_price"] == 24700.0
    assert out["upper_circuit_limit"] is None
    assert out["lower_circuit_limit"] is None
    assert out["total_imbalance"] is None
    assert out["source"] == "kite_quote"
    assert out["asof"].startswith("2026-08-06T15:20:00")
    assert "+05:30" in out["asof"]
    assert out["session_poc"] is None
    assert out["synthetic_future"] is None
    assert out["estimate"] is None
    assert out["estimate_method"] == "proxy_v1"


def test_fetch_rejects_garbage_official_indicative(monkeypatch: pytest.MonkeyPatch) -> None:
    """Index-level Kite garbage (15 / 1866) must not poison indicative / last / basis."""
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24550.0)
    for garbage in (15.0, 1866.0):
        cas.clear_last_ticks()
        quote = {"indicative_close_price": garbage, "reference_limit_price": 24500.0}
        out = cas.fetch_cas_indicative("NIFTY", now=_dt(15, 20), quote=quote)
        assert out["indicative"] is None
        assert out["official_indicative"] is None
        assert out["reference_limit_price"] == 24500.0
        assert out["last"] is None  # sanitized null must not become last tick
        # The rejected value is still reported so the desk can see what Kite sent.
        assert out["official_raw"] == garbage
        assert out["official_reject_reason"] == "out_of_band"


def test_reject_reason_missing_field_vs_no_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """"Kite sent no indicative" and "no quote at all" must not look identical."""
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24550.0)
    out = cas.fetch_cas_indicative(
        "NIFTY", now=_dt(15, 20), quote={"reference_limit_price": 24500.0}
    )
    assert out["official_raw"] is None
    assert out["official_reject_reason"] == "missing_field"

    empty = cas._empty_payload("NIFTY", in_window=True, spot=24550.0, source="unavailable")
    assert empty["official_reject_reason"] == "no_quote"


def test_reject_reason_outside_window_keeps_raw(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)
    quote = {"indicative_close_price": 24772.0}
    out = cas.fetch_cas_indicative("NIFTY", now=_dt(14, 30), quote=quote)
    assert out["official_indicative"] is None
    assert out["official_raw"] == 24772.0
    assert out["official_reject_reason"] == "outside_window"


def test_fetch_cas_indicative_outside_window_nulls_indicative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)
    quote = {
        "indicative_close_price": 24772.0,
        "reference_limit_price": 24700.0,
    }
    out = cas.fetch_cas_indicative("NIFTY", now=_dt(15, 14), quote=quote)
    assert out["in_cas_window"] is False
    assert out["indicative"] is None
    assert out["reference_limit_price"] == 24700.0
    assert out["source"] == "kite_quote"
    assert out["last"] is None


def test_fetch_attaches_estimate_before_1515(monkeypatch: pytest.MonkeyPatch) -> None:
    """Objective 1: pre-close forecast is attached outside the CAS window."""
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)
    monkeypatch.setattr("options.session_poc.compute_session_poc", lambda *a, **k: None)
    monkeypatch.setattr("options.synthetic_future.compute_synthetic_future", lambda *a, **k: {
        "F": 24780.0,
        "atm_strike": 24750.0,
        "expiry": "2026-08-13",
        "basis_vs_spot": 30.0,
        "basis_vs_indicative": None,
        "asof": "2026-08-06T14:45:00+05:30",
    })
    monkeypatch.setattr(
        "options.cas_estimate.compute_cas_estimate",
        lambda *a, **k: {
            "estimate": 24762.0,
            "estimate_method": "proxy_v1",
            "estimate_components": {
                "synth_f": 24780.0,
                "fut_ltp": 24755.0,
                "ref_vwap": 24740.0,
                "ref_vwap_window": "session",
            },
            "asof": "2026-08-06T14:45:00+05:30",
        },
    )
    out = cas.fetch_cas_indicative("NIFTY", now=_dt(14, 45), quote={"last_price": 24750.0})
    assert out["in_cas_window"] is False
    assert out["indicative"] is None
    assert out["estimate"] == 24762.0
    assert out["estimate_method"] == "proxy_v1"
    assert out["estimate_components"]["ref_vwap_window"] == "session"


def test_last_tick_persists_outside_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)
    quote = {
        "indicative_close_price": 24772.0,
        "reference_limit_price": 24700.0,
        "total_imbalance": 1200,
    }
    inside = cas.fetch_cas_indicative("NIFTY", now=_dt(15, 20), quote=quote)
    assert inside["indicative"] == 24772.0
    assert inside["last"] is not None
    assert inside["last"]["indicative"] == 24772.0

    outside = cas.fetch_cas_indicative("NIFTY", now=_dt(15, 40), quote=quote)
    assert outside["in_cas_window"] is False
    assert outside["indicative"] is None
    assert outside["last"] is not None
    assert outside["last"]["indicative"] == 24772.0
    assert outside["last"]["reference_limit_price"] == 24700.0
    assert outside["last"]["total_imbalance"] == 1200


def test_fetch_cas_indicative_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)
    monkeypatch.setattr(cas, "resolve_index_quote_key", lambda u: "NSE:NIFTY 50")
    monkeypatch.setattr(cas, "fetch_quote_batch", lambda keys: (_ for _ in ()).throw(RuntimeError("down")))
    out = cas.fetch_cas_indicative("BANKNIFTY", now=_dt(15, 20))
    assert out["in_cas_window"] is True
    assert out["indicative"] is None
    assert out["source"] == "unavailable"
    assert out["spot"] == 24750.0


def test_fetch_batch_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    spots = {"NIFTY": 24750.0, "BANKNIFTY": 55000.0, "SENSEX": 81000.0}
    monkeypatch.setattr(cas, "get_index_spot", lambda u: spots[u])
    monkeypatch.setattr(
        cas,
        "resolve_index_quote_key",
        lambda u: {"NIFTY": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK", "SENSEX": "BSE:SENSEX"}[u],
    )

    def _batch(keys: list[str]) -> dict:
        return {
            "NSE:NIFTY 50": {"indicative_close_price": 24772.0, "reference_limit_price": 24700.0},
            "NSE:NIFTY BANK": {"indicative_close_price": 55100.0, "reference_limit_price": 54900.0},
            "BSE:SENSEX": {"reference_limit_price": 80900.0},
        }

    monkeypatch.setattr(cas, "fetch_quote_batch", _batch)
    out = cas.fetch_cas_indicative_batch(now=_dt(15, 25))
    assert set(out.keys()) == {"items"}
    assert len(out["items"]) == 3
    by_u = {i["underlying"]: i for i in out["items"]}
    assert by_u["NIFTY"]["indicative"] == 24772.0
    assert by_u["BANKNIFTY"]["indicative"] == 55100.0
    assert by_u["SENSEX"]["indicative"] is None
    assert by_u["SENSEX"]["reference_limit_price"] == 80900.0
    assert all(i["source"] == "kite_quote" for i in out["items"])


def test_unknown_underlying_raises() -> None:
    with pytest.raises(ValueError):
        cas.fetch_cas_indicative("CRUDEOILM")


def test_fetch_attaches_session_poc_and_synth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)
    poc = {
        "poc": 24700.0,
        "fut_symbol": "NIFTY25AUGFUT",
        "fut_token": 1,
        "bin_step": 50,
        "total_volume": 1000,
        "asof": "2026-08-06T15:20:00+05:30",
        "path": [],
    }
    synth = {
        "F": 24780.0,
        "atm_strike": 24750.0,
        "expiry": "2026-08-13",
        "ce_symbol": "NIFTY250CE",
        "pe_symbol": "NIFTY250PE",
        "ce_price": 100.0,
        "pe_price": 70.0,
        "ce_source": "mid",
        "pe_source": "mid",
        "price_source": "mid",
        "spot": 24750.0,
        "basis_vs_spot": 30.0,
        "basis_vs_indicative": 8.0,
        "asof": "2026-08-06T15:20:00+05:30",
    }
    monkeypatch.setattr("options.session_poc.compute_session_poc", lambda *a, **k: poc)
    monkeypatch.setattr("options.synthetic_future.compute_synthetic_future", lambda *a, **k: synth)
    monkeypatch.setattr(
        "options.cas_estimate.compute_cas_estimate",
        lambda *a, **k: {
            "estimate": 24760.0,
            "estimate_method": "proxy_v1",
            "estimate_components": {
                "synth_f": 24780.0,
                "fut_ltp": 24755.0,
                "ref_vwap": 24740.0,
                "fut_poc": 24700.0,
            },
            "asof": "2026-08-06T15:20:00+05:30",
        },
    )
    quote = {"indicative_close_price": 24772.0, "reference_limit_price": 24700.0}
    out = cas.fetch_cas_indicative("NIFTY", now=_dt(15, 20), quote=quote)
    assert out["session_poc"] == poc
    assert out["synthetic_future"] == synth
    assert out["indicative"] == 24772.0
    assert out["estimate"] == 24760.0
    assert out["estimate_method"] == "proxy_v1"
    assert out["estimate_components"]["synth_f"] == 24780.0


def test_reference_levels_failure_does_not_block_cas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)

    def _boom(*_a, **_k):
        raise RuntimeError("kite down")

    monkeypatch.setattr("options.session_poc.compute_session_poc", _boom)
    monkeypatch.setattr("options.synthetic_future.compute_synthetic_future", _boom)
    quote = {"indicative_close_price": 24772.0}
    out = cas.fetch_cas_indicative("NIFTY", now=_dt(15, 20), quote=quote)
    assert out["indicative"] == 24772.0
    assert out["session_poc"] is None
    assert out["synthetic_future"] is None


def test_cas_for_snapshot_skips_reference_levels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cas, "get_index_spot", lambda u: 24750.0)
    monkeypatch.setattr(cas, "in_cas_window", lambda when=None: True)
    monkeypatch.setattr(cas, "resolve_index_quote_key", lambda u: "NSE:NIFTY 50")
    monkeypatch.setattr(
        cas,
        "fetch_quote_batch",
        lambda keys: {"NSE:NIFTY 50": {"indicative_close_price": 24772.0}},
    )
    called: list[str] = []

    def _poc(*_a, **_k):
        called.append("poc")
        return {"poc": 1.0}

    def _synth(*_a, **_k):
        called.append("synth")
        return {"F": 1.0}

    monkeypatch.setattr("options.session_poc.compute_session_poc", _poc)
    monkeypatch.setattr("options.synthetic_future.compute_synthetic_future", _synth)
    out = cas.cas_for_snapshot("NIFTY")
    assert out is not None
    assert out.get("indicative") == 24772.0
    assert called == []
    assert out.get("session_poc") is None
    assert out.get("synthetic_future") is None


# --- API route shape (handlers, mocked) --------------------------------------


def test_api_cas_indicative_single(monkeypatch: pytest.MonkeyPatch) -> None:
    from api import main as api_main

    monkeypatch.setattr(api_main, "_require_kite_session", lambda: None)
    monkeypatch.setattr(
        api_main,
        "fetch_cas_indicative",
        lambda u: {
            "underlying": u,
            "in_cas_window": True,
            "spot": 24750.0,
            "indicative": 24772.0,
            "reference_limit_price": 24700.0,
            "upper_circuit_limit": None,
            "lower_circuit_limit": None,
            "total_imbalance": None,
            "source": "kite_quote",
            "asof": "2026-08-06T15:20:00+05:30",
        },
    )
    out = api_main.cas_indicative(underlying="NIFTY")
    assert out["indicative"] == 24772.0
    assert out["source"] == "kite_quote"


def test_api_cas_indicative_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    from api import main as api_main

    monkeypatch.setattr(api_main, "_require_kite_session", lambda: None)
    monkeypatch.setattr(
        api_main,
        "fetch_cas_indicative_batch",
        lambda: {"items": [{"underlying": "NIFTY"}, {"underlying": "BANKNIFTY"}, {"underlying": "SENSEX"}]},
    )
    out = api_main.cas_indicative(underlying=None)
    assert "items" in out
    assert len(out["items"]) == 3


# --- snapshot enrichment (no strike math change) -----------------------------


def _price(flag: str, strike: float) -> float:
    return float(bs_price(flag, SPOT, strike, TTE, R, IV))


def _fake_chain(underlying: str, expiry: str) -> dict:
    strikes = []
    quotes = {}
    for k in range(int(SPOT) - 5 * STEP, int(SPOT) + 5 * STEP + 1, STEP):
        ce_sym = f"{underlying}{k}CE"
        pe_sym = f"{underlying}{k}PE"
        strikes.append(
            {
                "strike": float(k),
                "ce": {
                    "tradingsymbol": ce_sym,
                    "instrument_token": k * 10 + 1,
                    "exchange": "NFO",
                    "lot_size": 65,
                },
                "pe": {
                    "tradingsymbol": pe_sym,
                    "instrument_token": k * 10 + 2,
                    "exchange": "NFO",
                    "lot_size": 65,
                },
            }
        )
        ce_px = _price("c", k)
        pe_px = _price("p", k)
        quotes[f"NFO:{ce_sym}"] = {
            "oi": 100000,
            "last_price": ce_px,
            "depth": {
                "buy": [{"price": ce_px * 0.995, "quantity": 100}],
                "sell": [{"price": ce_px * 1.005, "quantity": 100}],
            },
        }
        quotes[f"NFO:{pe_sym}"] = {
            "oi": 120000,
            "last_price": pe_px,
            "depth": {
                "buy": [{"price": pe_px * 0.995, "quantity": 100}],
                "sell": [{"price": pe_px * 1.005, "quantity": 100}],
            },
        }
    _fake_chain.quotes = quotes  # type: ignore[attr-defined]
    return {
        "underlying": underlying,
        "expiry": expiry,
        "exchange": "NFO",
        "strike_step": STEP,
        "lot_size": 65,
        "strikes": strikes,
    }


def test_gamma_snapshot_includes_cas_without_changing_math(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _fake_chain("NIFTY", EXPIRY)
    provider = StaticGammaDensityDataProvider(
        chain=chain,
        spot=SPOT,
        quotes=_fake_chain.quotes,  # type: ignore[attr-defined]
        expiries=[EXPIRY],
    )
    monkeypatch.setattr(gd, "time_to_expiry_years", lambda e: TTE)

    cas_payload = {
        "underlying": "NIFTY",
        "in_cas_window": True,
        "spot": SPOT,
        "indicative": 20010.0,
        "reference_limit_price": 19950.0,
        "upper_circuit_limit": None,
        "lower_circuit_limit": None,
        "total_imbalance": None,
        "source": "kite_quote",
        "asof": "2026-08-06T15:20:00+05:30",
    }
    monkeypatch.setattr(
        "options.cas_indicative.cas_for_snapshot",
        lambda u: cas_payload,
    )

    snap = gd.build_gamma_snapshot(
        "NIFTY",
        provider=provider,
        include_multi_expiry=False,
        include_history=False,
    )
    assert snap["cas"] == cas_payload
    assert snap["spot"] == SPOT
    assert snap["atm_strike"] == SPOT
    # indicative must not replace spot used for ATM / walls
    assert snap["atm_strike"] != cas_payload["indicative"]
    assert isinstance(snap["strikes"], list) and snap["strikes"]
    assert snap["total_gex"] is not None


def test_oi_movers_snapshot_includes_cas(monkeypatch: pytest.MonkeyPatch) -> None:
    from options import oi_movers as om

    fake_snap = {
        "underlying": "NIFTY",
        "expiry": EXPIRY,
        "spot": SPOT,
        "atm_strike": SPOT,
        "spot_warning": None,
        "updated_at": "2026-08-06T15:20:00+05:30",
        "options_count": 5,
        "pcr": {"chain_oi": 1.0},
        "calls": [{"key": "atm_ce", "strike": SPOT, "latest_oi": 1100}],
        "puts": [{"key": "atm_pe", "strike": SPOT, "latest_oi": 900}],
    }
    monkeypatch.setattr(om, "build_snapshot", lambda *a, **k: fake_snap)
    monkeypatch.setattr(
        om,
        "build_baselines",
        lambda *a, **k: {
            "atm_ce": {"oi": 1000, "source": "open", "open_oi": 1000, "prev_close_oi": 900},
            "atm_pe": {"oi": 850, "source": "open", "open_oi": 850, "prev_close_oi": 800},
        },
    )
    cas_payload = {
        "underlying": "NIFTY",
        "in_cas_window": False,
        "spot": SPOT,
        "indicative": None,
        "reference_limit_price": None,
        "upper_circuit_limit": None,
        "lower_circuit_limit": None,
        "total_imbalance": None,
        "source": "unavailable",
        "asof": "2026-08-06T10:00:00+05:30",
    }
    monkeypatch.setattr("options.cas_indicative.cas_for_snapshot", lambda u: cas_payload)

    out = om.build_movers_snapshot("NIFTY")
    assert out["cas"] == cas_payload
    assert out["spot"] == SPOT
    assert out["atm_strike"] == SPOT
    assert "session" in out["change_boards"]


def test_cas_for_snapshot_null_for_mcx() -> None:
    assert cas.cas_for_snapshot("CRUDEOILM") is None
