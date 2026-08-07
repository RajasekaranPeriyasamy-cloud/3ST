"""Tests for pluggable Gamma Density data providers."""

from __future__ import annotations

from options.gamma_density_provider import (
    CallableGammaDensityDataProvider,
    KiteGammaDensityDataProvider,
    StaticGammaDensityDataProvider,
    get_gamma_density_provider,
    set_gamma_density_provider,
)


def test_default_provider_is_kite():
    prov = get_gamma_density_provider()
    assert isinstance(prov, KiteGammaDensityDataProvider)
    assert prov.name == "kite"
    assert prov.requires_session() is True


def test_set_gamma_density_provider_swaps_global():
    original = get_gamma_density_provider()
    try:
        stub = StaticGammaDensityDataProvider(
            chain={"strikes": []},
            spot=100.0,
            quotes={},
            expiries=["2026-07-16"],
            name="test_stub",
        )
        set_gamma_density_provider(stub)
        assert get_gamma_density_provider().name == "test_stub"
    finally:
        set_gamma_density_provider(original)


def test_callable_provider_wraps_functions():
    prov = CallableGammaDensityDataProvider(
        name="demo",
        get_chain=lambda u, e: {"strikes": [], "exchange": "NFO"},
        get_spot=lambda u: 20000.0,
        fetch_quotes=lambda keys: {},
        list_expiries=lambda u: ["2026-07-16"],
        requires_session=False,
    )
    assert prov.name == "demo"
    assert prov.requires_session() is False
    assert prov.list_expiries("NIFTY") == ["2026-07-16"]
    assert prov.get_spot("NIFTY") == 20000.0


def test_static_provider_does_not_require_session():
    prov = StaticGammaDensityDataProvider(
        chain={"strikes": []},
        spot=100.0,
        quotes={"NFO:TEST": {"oi": 1, "last_price": 2.0}},
    )
    assert prov.requires_session() is False
    assert prov.fetch_quotes(["NFO:TEST", "NFO:MISSING"]) == {
        "NFO:TEST": {"oi": 1, "last_price": 2.0},
    }
