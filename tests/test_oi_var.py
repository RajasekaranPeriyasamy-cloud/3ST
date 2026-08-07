"""Unit tests for OI VAR ranking / flow tags (no Kite)."""

from __future__ import annotations

from options import oi_var as ov


def test_var_crores():
    assert ov.var_crores(1_000_000, 100) == 10.0
    assert ov.var_crores(None, 100) is None


def test_flow_tag():
    assert ov.flow_tag(100, 5.0) == "long_build"
    assert ov.flow_tag(100, -5.0) == "short_build"
    assert ov.flow_tag(-100, 5.0) == "short_cover"
    assert ov.flow_tag(-100, -5.0) == "long_unwind"
    assert ov.flow_tag(0, 1.0) == "flat"


def test_moneyness():
    assert ov.moneyness_label("call", 19900, 20000) == "ITMCE"
    assert ov.moneyness_label("call", 20100, 20000) == "OTMCE"
    assert ov.moneyness_label("put", 20100, 20000) == "ITMPE"
    assert ov.moneyness_label("put", 19900, 20000) == "OTMPE"


def test_rank_tables_names():
    legs = [
        {
            "side": "call",
            "strike": 20000,
            "symbol": "C1",
            "moneyness": "ATM",
            "oi": 1000,
            "ltp": 100,
            "var_cr": 50.0,
            "var_chg_cr": 5.0,
            "delta_oi": 100,
        },
        {
            "side": "call",
            "strike": 20100,
            "symbol": "C2",
            "moneyness": "OTMCE",
            "oi": 800,
            "ltp": 80,
            "var_cr": 40.0,
            "var_chg_cr": -3.0,
            "delta_oi": -50,
        },
        {
            "side": "put",
            "strike": 20000,
            "symbol": "P1",
            "moneyness": "ATM",
            "oi": 1200,
            "ltp": 110,
            "var_cr": 60.0,
            "var_chg_cr": 8.0,
            "delta_oi": 200,
        },
    ]
    ranked = ov.rank_tables(legs, top_n=10)
    assert ranked["calls"]["top_var"][0]["strike"] == 20000
    assert ranked["calls"]["top_dvar_up"][0]["var_chg_cr"] == 5.0
    assert ranked["calls"]["top_dvar_dn"][0]["var_chg_cr"] == -3.0
    assert ranked["puts"]["top_var"][0]["var_cr"] == 60.0
    assert ranked["ce_var_total"] == 90.0
    assert ranked["pe_var_total"] == 60.0
    # pct annotated
    assert ranked["calls"]["top_var"][0]["pct_side_var"] == 55.56


def test_quote_price_prefers_mid():
    q = {
        "last_price": 100.0,
        "depth": {"buy": [{"price": 99.0}], "sell": [{"price": 101.0}]},
    }
    px, src = ov._quote_price(q, 0.15)
    assert src == "mid"
    assert px == 100.0


def test_var_config_has_dvar_modes():
    cfg = ov.var_config()
    assert "oi_mark" in cfg["dvar_modes"]
    assert cfg["top_n"] > 0


def test_alerts_migration():
    history = [
        {"top_ce_strike": 20000, "top_pe_strike": 19900, "net_dvar": 10.0},
        {"top_ce_strike": 20100, "top_pe_strike": 19900, "net_dvar": 40.0},
    ]
    alerts = ov._build_alerts(
        history, top_ce=20100, top_pe=19900, net_dvar=40.0, burst_cr=25.0
    )
    types = {a["type"] for a in alerts}
    assert "top_ce_migration" in types
    assert "dvar_burst" in types
