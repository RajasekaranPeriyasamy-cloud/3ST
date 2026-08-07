"""Tests for the gamma density engine (no Kite; synthetic chain + BS-priced quotes)."""

from __future__ import annotations

import pytest
from vollib.black_scholes import black_scholes as bs_price

from options import gamma_density as gd
from options.gamma_density_provider import StaticGammaDensityDataProvider

R = 0.065
SPOT = 20000.0
STEP = 50
TTE = 5.0 / 365.0  # ~5 calendar days
IV = 0.14
EXPIRY = "2026-07-16"


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
        # Include depth so mid-path is exercised
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


@pytest.fixture
def static_provider():
    chain = _fake_chain("NIFTY", EXPIRY)
    return StaticGammaDensityDataProvider(
        chain=chain,
        spot=SPOT,
        quotes=_fake_chain.quotes,  # type: ignore[attr-defined]
        expiries=[EXPIRY, "2026-07-23"],
    )


@pytest.fixture
def patched(monkeypatch, static_provider):
    monkeypatch.setattr(gd, "time_to_expiry_years", lambda e: TTE)
    monkeypatch.setattr(gd, "build_reference_levels", lambda *_a, **_k: gd._empty_reference_levels())
    # Soft-empty OI baselines so snapshot tests never hit Kite historical OI.
    monkeypatch.setattr(
        "options.oi_movers.ensure_session_open_oi",
        lambda *_a, **_k: {},
    )
    monkeypatch.setattr(
        "options.oi_movers.get_prev_day_oi_map",
        lambda *_a, **_k: {},
    )
    return static_provider


def _snap(provider, **kwargs):
    return gd.build_gamma_snapshot(
        "NIFTY",
        provider=provider,
        include_multi_expiry=False,
        include_history=False,
        **kwargs,
    )


def test_gamma_config():
    cfg = gd.gamma_config()
    assert "NIFTY" in cfg["underlyings"]
    assert "CRUDEOIL" in cfg["underlyings"]
    assert "NATURALGAS" in cfg["underlyings"]
    assert cfg["refresh_seconds"] > 0
    assert cfg["strike_window"] > 0
    assert cfg["provider"] == "kite"
    assert cfg["requires_session"] is True
    assert "dividend_yield" in cfg
    assert "naive" in cfg["sign_modes"]
    assert list(cfg["hedge_moves_pts"]) == [50, 100]
    assert gd._hedge_moves_for_underlying("NIFTY") == [50, 100]
    assert gd._hedge_moves_for_underlying("CRUDEOIL") == [50, 100]


def test_hedge_moves_for_index_and_mcx():
    assert gd._hedge_moves_for_underlying("NIFTY") == [50, 100]
    assert gd._hedge_moves_for_underlying("CRUDEOIL") == [50, 100]


def test_mcx_energy_are_index_options():
    from config import INDEX_OPTIONS, MCX_OPTION_UNDERLYINGS, is_mcx_underlying

    for u in ("CRUDEOIL", "CRUDEOILM", "NATURALGAS"):
        assert u in INDEX_OPTIONS
        assert u in MCX_OPTION_UNDERLYINGS
        assert is_mcx_underlying(u)
        meta = INDEX_OPTIONS[u]
        assert meta["exchange"] == "MCX"
        assert meta["spot_source"] == "future"


def test_unknown_underlying_raises():
    with pytest.raises(ValueError):
        gd.build_gamma_snapshot("FOO")


def test_snapshot_shape(patched):
    snap = _snap(patched)
    assert snap["underlying"] == "NIFTY"
    assert snap["spot"] == SPOT
    assert snap["strikes"], "expected resolvable strikes"
    assert snap["atm_strike"] == SPOT
    assert snap["chain_legs_quoted"] > 0
    for key in (
        "total_gex",
        "pos_gex",
        "neg_gex",
        "gamma_regime",
        "call_wall",
        "put_wall",
        "expected_move",
        "gex_profile",
        "hedge_flow",
        "distance_to_flip",
        "reference_levels",
    ):
        assert key in snap
    assert snap["reference_levels"] is not None
    assert "prev_day_close" in snap["reference_levels"]
    assert snap["expected_move"]["source"] in ("straddle", "atm_iv")
    assert len(snap["gex_profile"]) > 10
    assert len(snap["hedge_flow"]) >= 2
    assert snap["pos_gex"] >= 0
    assert snap["neg_gex"] >= 0


def test_gex_sign_convention(patched):
    snap = _snap(patched, sign_mode="naive")
    for row in snap["strikes"]:
        assert row["ce_gex"] >= 0, "call GEX must be non-negative (naive)"
        assert row["pe_gex"] <= 0, "put GEX must be non-positive (naive)"
        assert row["ce_density"] >= 0 and row["pe_density"] >= 0


def test_customer_sign_flips(patched):
    naive = _snap(patched, sign_mode="naive")
    cust = _snap(patched, sign_mode="customer")
    assert naive["total_gex"] == pytest.approx(-cust["total_gex"], rel=1e-6)


def test_mid_price_stats(patched):
    snap = _snap(patched)
    assert snap["price_source_stats"]["mid"] > 0


def test_expected_move_bands(patched):
    snap = _snap(patched)
    bands = snap["expected_move"]
    assert bands is not None
    assert bands["sigma1_up"] > SPOT > bands["sigma1_dn"]
    assert bands["sigma2_up"] > bands["sigma1_up"]


def test_strike_window_limits(patched):
    snap = _snap(patched, strike_window=2)
    assert len(snap["strikes"]) <= 5


def test_total_gex_sign_flips_across_spot():
    legs = [
        (19500.0, 100000, 65, "PE", 0.14),
        (20000.0, 100000, 65, "CE", 0.14),
        (20000.0, 100000, 65, "PE", 0.14),
        (20500.0, 100000, 65, "CE", 0.14),
    ]
    low = gd.total_gex_at_spot(legs, 19000.0, TTE)
    high = gd.total_gex_at_spot(legs, 21000.0, TTE)
    assert low < 0 < high or high < 0 < low


def test_gamma_flip_level_scan():
    legs = [
        (19800.0, 150000, 65, "PE", 0.14),
        (20000.0, 120000, 65, "CE", 0.14),
        (20000.0, 120000, 65, "PE", 0.14),
        (20200.0, 150000, 65, "CE", 0.14),
    ]
    flip = gd.gamma_flip_level(legs, SPOT, TTE, 19000.0, 21000.0)
    if flip is not None:
        assert 19000.0 <= flip <= 21000.0


def test_snapshot_flip_positive(patched):
    snap = _snap(patched)
    if snap["flip_level"] is not None:
        assert snap["flip_level"] > 0
    assert "flip_sticky_delta" in snap


def test_magnet_walls_present(patched):
    snap = _snap(patched)
    assert snap["call_wall"] is not None
    assert snap["put_wall"] is not None
    assert snap["call_wall_magnet"] is not None


def test_vanna_strip(patched):
    snap = gd.build_gamma_snapshot(
        "NIFTY",
        provider=patched,
        include_multi_expiry=False,
        include_history=False,
        include_vanna_strip=True,
    )
    assert snap["vanna_strip"] is not None
    assert "joint_read" in snap["vanna_strip"]


def test_quote_price_prefers_mid():
    q = {
        "last_price": 100.0,
        "depth": {"buy": [{"price": 99.0}], "sell": [{"price": 101.0}]},
    }
    px, src = gd._quote_price(q, 0.12)
    assert src == "mid"
    assert px == pytest.approx(100.0)


def test_pick_strike_oi_baseline_modes():
    oi, src = gd._pick_strike_oi_baseline(1000, 800, "session_open")
    assert (oi, src) == (1000, "open")
    oi, src = gd._pick_strike_oi_baseline(None, 800, "session_open")
    assert (oi, src) == (800, "prev_close")
    oi, src = gd._pick_strike_oi_baseline(1000, 800, "prev_close")
    assert (oi, src) == (800, "prev_close")
    oi, src = gd._pick_strike_oi_baseline(1000, None, "prev_close")
    assert (oi, src) == (None, None)


def test_attach_strike_oi_baselines_doi_and_sources():
    strikes = [
        {
            "strike": 20000.0,
            "ce_oi": 120000,
            "pe_oi": 90000,
            "_ce_token": 111,
            "_pe_token": 222,
        },
        {
            "strike": 20050.0,
            "ce_oi": 50000,
            "pe_oi": 40000,
            "_ce_token": 333,
            "_pe_token": 444,
        },
    ]
    open_map = {"111": 100000, "222": 85000}  # CE open; PE open
    prev_map = {"111": 95000, "222": 80000, "333": 45000, "444": 38000}

    meta = gd.attach_strike_oi_baselines(
        strikes,
        "NIFTY",
        EXPIRY,
        oi_baseline_mode="session_open",
        open_map=open_map,
        prev_map=prev_map,
    )
    assert meta["oi_baseline_mode"] == "session_open"
    assert meta["oi_baseline_open_count"] == 2
    assert meta["oi_baseline_prev_close_count"] == 2
    assert "prev_close fallback" in (meta["oi_baseline_note"] or "")

    atm = strikes[0]
    assert atm["ce_oi_base"] == 100000
    assert atm["ce_oi_base_source"] == "open"
    assert atm["ce_doi"] == 20000
    assert atm["pe_oi_base"] == 85000
    assert atm["pe_oi_base_source"] == "open"
    assert atm["pe_doi"] == 5000
    assert "_ce_token" not in atm

    wing = strikes[1]
    assert wing["ce_oi_base"] == 45000
    assert wing["ce_oi_base_source"] == "prev_close"
    assert wing["ce_doi"] == 5000
    assert wing["pe_oi_base"] == 38000
    assert wing["pe_doi"] == 2000


def test_attach_strike_oi_baselines_prev_close_mode():
    strikes = [
        {
            "strike": 20000.0,
            "ce_oi": 110000,
            "pe_oi": 95000,
            "_ce_token": 111,
            "_pe_token": 222,
        }
    ]
    meta = gd.attach_strike_oi_baselines(
        strikes,
        "NIFTY",
        EXPIRY,
        oi_baseline_mode="prev_close",
        open_map={"111": 100000, "222": 90000},
        prev_map={"111": 105000, "222": 92000},
    )
    assert meta["oi_baseline_mode"] == "prev_close"
    assert strikes[0]["ce_oi_base"] == 105000
    assert strikes[0]["ce_oi_base_source"] == "prev_close"
    assert strikes[0]["ce_doi"] == 5000
    assert strikes[0]["pe_doi"] == 3000


def test_attach_strike_oi_baselines_soft_fail_missing(patched):
    snap = _snap(patched)
    assert snap["oi_baseline_mode"] == "session_open"
    assert snap["oi_baseline_note"]
    for row in snap["strikes"]:
        assert row["ce_oi_base"] is None
        assert row["pe_oi_base"] is None
        assert row["ce_doi"] is None
        assert row["pe_doi"] is None
        assert row["ce_oi_base_source"] is None
        assert row["pe_oi_base_source"] is None


def test_snapshot_oi_baseline_with_injected_maps(patched, monkeypatch):
    real_attach = gd.attach_strike_oi_baselines

    def _attach(strikes, underlying, expiry, **kwargs):
        om: dict[str, int] = {}
        pm: dict[str, int] = {}
        for row in strikes:
            for side in ("ce", "pe"):
                tok = row.get(f"_{side}_token")
                if tok is None:
                    continue
                curr = int(row.get(f"{side}_oi") or 0)
                om[str(tok)] = max(0, curr - 10000)
                pm[str(tok)] = max(0, curr - 5000)
        return real_attach(
            strikes,
            underlying,
            expiry,
            oi_baseline_mode=kwargs.get("oi_baseline_mode", "session_open"),
            open_map=om,
            prev_map=pm,
            session_capture_rows=kwargs.get("session_capture_rows"),
        )

    monkeypatch.setattr(gd, "attach_strike_oi_baselines", _attach)
    snap = _snap(patched, oi_baseline_mode="session_open")
    assert snap["oi_baseline_open_count"] > 0
    row = next(r for r in snap["strikes"] if r["strike"] == SPOT)
    assert row["ce_oi_base"] == row["ce_oi"] - 10000
    assert row["ce_doi"] == 10000
    assert row["ce_oi_base_source"] == "open"


def _conc_strikes() -> list[dict]:
    """Synthetic strike rows with known |GEX| shares for concentration unit tests."""
    return [
        {"strike": 19900.0, "net_gex": 10.0, "ce_gex": 10.0, "pe_gex": 0.0, "total_density": 0.0},
        {"strike": 20000.0, "net_gex": 50.0, "ce_gex": 40.0, "pe_gex": 10.0, "total_density": 0.0},
        {"strike": 20100.0, "net_gex": 30.0, "ce_gex": 5.0, "pe_gex": -25.0, "total_density": 0.0},
        {"strike": 20200.0, "net_gex": 10.0, "ce_gex": 0.0, "pe_gex": -10.0, "total_density": 0.0},
    ]


def test_gex_components_from_strikes():
    """+VE / −VE masses are absolute component sums; net = pos − neg."""
    pos, neg, net = gd.gex_components_from_strikes(_conc_strikes())
    # +VE: 10+40+10+5 = 65; −VE abs: 25+10 = 35; net = 30
    assert pos == pytest.approx(65.0)
    assert neg == pytest.approx(35.0)
    assert net == pytest.approx(30.0)


def test_side_hhi_falls_back_when_ce_gex_missing():
    """Call/Put HHI must populate whenever net mass exists (same gate as net HHI)."""
    strikes = [
        {"strike": 7700.0, "net_gex": 1e6, "total_density": 10.0, "ce_density": 8.0, "pe_density": 2.0},
        {"strike": 7800.0, "net_gex": 2e6, "total_density": 20.0, "ce_density": 5.0, "pe_density": 15.0},
        {"strike": 7900.0, "net_gex": -0.5e6, "total_density": 5.0, "ce_density": 1.0, "pe_density": 4.0},
    ]
    out = gd.compute_gamma_concentration(
        strikes,
        spot=7762.0,
        atm_strike=7750.0,
        call_wall=8000.0,
        put_wall=7700.0,
        strike_step=50.0,
        flip_level=None,
    )
    assert out["hhi"] is not None and out["hhi"] > 0
    assert out["call_hhi"] is not None and out["call_hhi"] > 0
    assert out["put_hhi"] is not None and out["put_hhi"] > 0
    contribs = out["top_contributors"]
    assert 0 < len(contribs) <= 25
    assert len(contribs) == 3  # fixture has 3 strikes
    shares = [c["share"] for c in contribs]
    assert shares == sorted(shares, reverse=True)
    assert out["cliff_strike"] == 8000.0
    # Density-based call HHI: masses 8,5,1 total 14
    expected_call = (8 / 14) ** 2 + (5 / 14) ** 2 + (1 / 14) ** 2
    assert out["call_hhi"] == pytest.approx(round(expected_call, 4), abs=1e-4)


def test_compute_gamma_concentration_call_put_hhi_and_contributors():
    strikes = _conc_strikes()
    # |net| masses: 10+50+30+10 = 100 → shares 0.1, 0.5, 0.3, 0.1 → HHI = 0.01+0.25+0.09+0.01 = 0.36
    # |ce|: 10+40+5+0 = 55 → HHI = (10/55)^2+(40/55)^2+(5/55)^2
    # |pe|: 0+10+25+10 = 45 → HHI = (10/45)^2+(25/45)^2+(10/45)^2
    out = gd.compute_gamma_concentration(
        strikes,
        spot=20000.0,
        atm_strike=20000.0,
        call_wall=20100.0,
        put_wall=19900.0,
        strike_step=100.0,
        flip_level=20050.0,
    )
    assert out["hhi"] == pytest.approx(0.36, abs=1e-4)
    assert out["band"] == "concentrated"
    assert out["call_hhi"] is not None
    assert out["put_hhi"] is not None
    ce_total = 55.0
    expected_call = (10 / ce_total) ** 2 + (40 / ce_total) ** 2 + (5 / ce_total) ** 2
    pe_total = 45.0
    expected_put = (10 / pe_total) ** 2 + (25 / pe_total) ** 2 + (10 / pe_total) ** 2
    assert out["call_hhi"] == pytest.approx(round(expected_call, 4), abs=1e-4)
    assert out["put_hhi"] == pytest.approx(round(expected_put, 4), abs=1e-4)

    contribs = out["top_contributors"]
    assert 0 < len(contribs) <= 25
    assert len(contribs) == 4  # only 4 strikes
    shares = [c["share"] for c in contribs]
    assert shares == sorted(shares, reverse=True)
    assert contribs[0]["strike"] == 20000.0
    assert contribs[0]["share"] == pytest.approx(0.5, abs=1e-4)
    assert contribs[0]["net_gex"] == 50.0
    assert contribs[0]["side_bias"] == "call"
    assert contribs[1]["strike"] == 20100.0
    assert contribs[1]["side_bias"] == "put"


def test_top_contributors_returns_up_to_25():
    """API returns up to 25 contributors so the UI can slice without refetch."""
    strikes = [
        {
            "strike": float(20000 + i * 100),
            "net_gex": float(30 - i),
            "ce_gex": float(30 - i),
            "pe_gex": 0.0,
            "total_density": 0.0,
        }
        for i in range(30)
    ]
    out = gd.compute_gamma_concentration(
        strikes,
        spot=20000.0,
        atm_strike=20000.0,
        call_wall=20500.0,
        put_wall=19500.0,
        strike_step=100.0,
        flip_level=None,
    )
    contribs = out["top_contributors"]
    assert len(contribs) == 25
    shares = [c["share"] for c in contribs]
    assert shares == sorted(shares, reverse=True)
    assert contribs[0]["strike"] == 20000.0


def test_compute_gamma_concentration_cliff_prefers_flip_in_window():
    strikes = _conc_strikes()
    out = gd.compute_gamma_concentration(
        strikes,
        spot=20000.0,
        atm_strike=20000.0,
        call_wall=20100.0,
        put_wall=19900.0,
        strike_step=100.0,
        flip_level=20050.0,
    )
    assert out["cliff_strike"] == 20050.0


def test_compute_gamma_concentration_cliff_falls_back_to_breakout_wall():
    strikes = _conc_strikes()
    # Flip outside window → farther breakout-side wall from spot
    out = gd.compute_gamma_concentration(
        strikes,
        spot=20000.0,
        atm_strike=20000.0,
        call_wall=20200.0,
        put_wall=19950.0,
        strike_step=100.0,
        flip_level=21000.0,
    )
    # call wall 200 pts above, put wall 50 pts below → farther = call wall
    assert out["cliff_strike"] == 20200.0


def test_compute_gamma_concentration_intraday_percentile():
    strikes = _conc_strikes()
    history = [
        {"hhi": 0.20, "pin_strike": 20000.0},
        {"hhi": 0.30, "pin_strike": 20000.0},
        {"hhi": 0.40, "pin_strike": 20000.0},
    ]
    out = gd.compute_gamma_concentration(
        strikes,
        spot=20000.0,
        atm_strike=20000.0,
        call_wall=20100.0,
        put_wall=19900.0,
        strike_step=100.0,
        history=history,
        flip_level=20050.0,
    )
    # current hhi ~0.36; vals [0.20, 0.30, 0.40, 0.36] → 3 of 4 ≤ 0.36 → 75th
    assert out["hhi_session_mean"] is not None
    assert out["hhi_percentile_intraday"] == pytest.approx(75.0, abs=0.1)


def test_compute_gamma_concentration_30d_percentile():
    strikes = _conc_strikes()
    # Build daily series where current (~0.36 from _conc_strikes) ranks high
    daily = [{"date": f"2026-07-{d:02d}", "hhi": h} for d, h in zip(
        range(1, 11),
        [0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22, 0.24, 0.26, 0.36],
    )]
    out = gd.compute_gamma_concentration(
        strikes,
        spot=20000.0,
        atm_strike=20000.0,
        call_wall=20100.0,
        put_wall=19900.0,
        strike_step=100.0,
        daily_hhi_history=daily,
        flip_level=20050.0,
    )
    assert out["hhi"] is not None
    assert out["hhi_session_count"] == 10
    # current ≈0.36; all 10 days ≤ ~0.36 → 100th
    assert out["hhi_percentile_30d"] == pytest.approx(100.0, abs=0.1)
    # Intraday fields still present / independent
    assert "hhi_percentile_intraday" in out


def test_snapshot_includes_concentration_extensions(patched):
    snap = _snap(patched)
    conc = snap["concentration"]
    assert conc is not None
    assert "call_hhi" in conc
    assert "put_hhi" in conc
    assert "gini" in conc
    assert "call_gini" in conc
    assert "put_gini" in conc
    assert "shape_quadrant" in conc
    assert "top_contributors" in conc
    assert "cliff_strike" in conc
    assert isinstance(conc["top_contributors"], list)
    if conc["top_contributors"]:
        row = conc["top_contributors"][0]
        assert {"strike", "share", "net_gex", "side_bias"} <= set(row.keys())
    if snap["flip_level"] is not None and conc["cliff_strike"] is not None:
        strikes = [r["strike"] for r in snap["strikes"]]
        lo, hi = min(strikes), max(strikes)
        if lo <= snap["flip_level"] <= hi:
            assert conc["cliff_strike"] == snap["flip_level"]


def _mass_strikes(masses: list[float], *, start: float = 20000.0, step: float = 100.0) -> list[dict]:
    """Build synthetic strike rows from net |GEX| masses (ce=pe=0 unless set)."""
    return [
        {
            "strike": start + i * step,
            "net_gex": float(m),
            "ce_gex": 0.0,
            "pe_gex": 0.0,
            "total_density": 0.0,
        }
        for i, m in enumerate(masses)
    ]


def _conc_kwargs(**overrides):
    base = dict(
        spot=20000.0,
        atm_strike=20000.0,
        call_wall=20200.0,
        put_wall=19800.0,
        strike_step=100.0,
        flip_level=None,
    )
    base.update(overrides)
    return base


def test_gini_case_a_equal_masses():
    """Case A — equal masses → Gini = 0; HHI band drives equal-* quadrant."""
    # 4 equal → HHI = 0.25 (concentrated edge) → equal-concentrated
    out4 = gd.compute_gamma_concentration(
        _mass_strikes([100, 100, 100, 100]), **_conc_kwargs()
    )
    assert out4["hhi"] == pytest.approx(0.25, abs=1e-3)
    assert out4["gini"] == pytest.approx(0.0, abs=1e-3)
    assert out4["band"] == "concentrated"
    assert out4["shape_quadrant"] == "equal-concentrated"

    # 5 equal → HHI = 0.2 (mixed) → equal-balanced
    out5 = gd.compute_gamma_concentration(
        _mass_strikes([1, 1, 1, 1, 1]), **_conc_kwargs()
    )
    assert out5["hhi"] == pytest.approx(0.2, abs=1e-3)
    assert out5["gini"] == pytest.approx(0.0, abs=1e-3)
    assert out5["band"] == "mixed"
    assert out5["shape_quadrant"] == "equal-balanced"


def test_gini_case_b_one_dominates():
    """Case B — one dominates → high HHI + high Gini → unequal-concentrated."""
    out = gd.compute_gamma_concentration(
        _mass_strikes([900, 50, 30, 20]), **_conc_kwargs()
    )
    # Shares 0.9/0.05/0.03/0.02 → HHI ≈ 0.8138; Gini = 0.665 (sorted 20,30,50,900)
    assert out["hhi"] == pytest.approx(0.8138, abs=1e-3)
    assert out["gini"] == pytest.approx(0.665, abs=1e-3)
    assert out["band"] == "concentrated"
    assert out["shape_quadrant"] == "unequal-concentrated"


def test_gini_case_c_unequal_balanced():
    """Case C — mixed HHI + high Gini (Ávila group 1) → unequal-balanced."""
    # Two fat + eight thin: HHI alone looks "balanced"; Gini flags inequality.
    masses = [450, 350, 50, 50, 50, 50, 50, 50, 50, 50]
    out = gd.compute_gamma_concentration(_mass_strikes(masses), **_conc_kwargs())
    assert out["hhi"] == pytest.approx(0.240, abs=1e-3)
    assert out["gini"] is not None and out["gini"] >= 0.40
    assert out["gini"] == pytest.approx(0.475, abs=1e-3)
    assert out["band"] == "mixed"
    assert out["shape_quadrant"] == "unequal-balanced"


def test_gini_case_d_call_put_diverge():
    """Case D — CE concentrated, PE even → call_gini high, put_gini ~0."""
    strikes = [
        {"strike": 19900.0, "net_gex": 925.0, "ce_gex": 900.0, "pe_gex": -25.0, "total_density": 0.0},
        {"strike": 20000.0, "net_gex": 75.0, "ce_gex": 50.0, "pe_gex": -25.0, "total_density": 0.0},
        {"strike": 20100.0, "net_gex": 55.0, "ce_gex": 30.0, "pe_gex": -25.0, "total_density": 0.0},
        {"strike": 20200.0, "net_gex": 45.0, "ce_gex": 20.0, "pe_gex": -25.0, "total_density": 0.0},
    ]
    out = gd.compute_gamma_concentration(strikes, **_conc_kwargs())
    assert out["call_gini"] == pytest.approx(0.665, abs=1e-3)
    assert out["put_gini"] == pytest.approx(0.0, abs=1e-3)
    assert out["gini"] is not None
    assert out["put_gini"] < out["gini"] < out["call_gini"]


def test_gini_case_e_degenerate():
    """Case E — empty / all-zero mass → gini is None."""
    empty = gd.compute_gamma_concentration([], **_conc_kwargs())
    assert empty["gini"] is None
    assert empty["call_gini"] is None
    assert empty["put_gini"] is None
    assert empty["shape_quadrant"] is None
    assert empty["hhi"] is None

    zeros = gd.compute_gamma_concentration(
        _mass_strikes([0.0, 0.0, 0.0]), **_conc_kwargs()
    )
    assert zeros["gini"] is None
    assert zeros["shape_quadrant"] is None
    assert zeros["hhi"] is None


def test_gini_from_masses_direct():
    """Unit-check the Gini helper on the worked Case B numbers."""
    assert gd._gini_from_masses([100, 100, 100, 100]) == pytest.approx(0.0, abs=1e-9)
    assert gd._gini_from_masses([900, 50, 30, 20]) == pytest.approx(0.665, abs=1e-3)
    assert gd._gini_from_masses([1.0]) is None
    assert gd._gini_from_masses([]) is None
    assert gd._gini_from_masses([0.0, 0.0]) is None


def test_default_concentration_underlyings_cash_only():
    from config import INDEX_OPTIONS, is_mcx_underlying

    names = gd.default_concentration_underlyings()
    assert names == ["NIFTY", "BANKNIFTY", "SENSEX"] or (
        "FINNIFTY" in INDEX_OPTIONS and "FINNIFTY" in names
    )
    assert "NIFTY" in names and "BANKNIFTY" in names and "SENSEX" in names
    for u in names:
        assert u in INDEX_OPTIONS
        assert not is_mcx_underlying(u)


def test_concentration_summary_shape_and_hhi(patched):
    out = gd.build_concentration_summary(
        underlyings=["NIFTY"],
        provider=patched,
        strike_window=8,
        parallel=False,
    )
    assert out["strike_window"] == 8
    assert out["underlyings"] == ["NIFTY"]
    assert out["provider"] == "static"
    assert "updated_at" in out
    assert len(out["items"]) == 1
    row = out["items"][0]
    for key in (
        "underlying",
        "expiry",
        "spot",
        "hhi",
        "band",
        "pin_strike",
        "cliff_strike",
        "gini",
        "shape_quadrant",
        "hhi_percentile_30d",
        "hhi_session_count",
        "source",
        "error",
    ):
        assert key in row
    assert row["underlying"] == "NIFTY"
    assert row["source"] == "live"
    assert row["error"] is None
    assert row["hhi"] is not None
    assert row["band"] in ("concentrated", "mixed", "diffuse")
    assert row["spot"] == SPOT
    assert row["expiry"] == EXPIRY


def test_concentration_summary_isolates_failures(patched, monkeypatch):
    real = gd.build_gamma_snapshot

    def flaky(underlying, *args, **kwargs):
        if str(underlying).upper() == "BANKNIFTY":
            raise RuntimeError("injected BANKNIFTY failure")
        return real(underlying, *args, **kwargs)

    monkeypatch.setattr(gd, "build_gamma_snapshot", flaky)
    # History fallback stays empty — BANKNIFTY row should still be returned.
    monkeypatch.setattr(
        "options.gamma_density_history.get_daily_hhi_series",
        lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        "options.gamma_density_history.get_history",
        lambda *_a, **_k: [],
    )

    out = gd.build_concentration_summary(
        underlyings=["NIFTY", "BANKNIFTY"],
        provider=patched,
        strike_window=8,
        parallel=False,
    )
    assert len(out["items"]) == 2
    by_u = {r["underlying"]: r for r in out["items"]}
    assert by_u["NIFTY"]["hhi"] is not None
    assert by_u["NIFTY"]["band"] is not None
    assert by_u["NIFTY"]["source"] == "live"
    assert by_u["BANKNIFTY"]["hhi"] is None
    assert by_u["BANKNIFTY"]["error"]
    assert "injected BANKNIFTY failure" in by_u["BANKNIFTY"]["error"]


def test_concentration_summary_history_fallback(patched, monkeypatch):
    monkeypatch.setattr(
        gd,
        "build_gamma_snapshot",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("live down")),
    )
    monkeypatch.setattr(
        "options.gamma_density_history.get_daily_hhi_series",
        lambda *_a, **_k: [{"date": "2026-07-30", "hhi": 0.31}],
    )
    monkeypatch.setattr(
        "options.gamma_density_history.get_history",
        lambda *_a, **_k: [
            {"t": "2026-07-30T15:00:00+05:30", "pin_strike": 20100.0, "flip_level": 19950.0}
        ],
    )

    out = gd.build_concentration_summary(
        underlyings=["NIFTY"],
        provider=patched,
        strike_window=8,
        parallel=False,
    )
    row = out["items"][0]
    assert row["source"] == "history"
    assert row["hhi"] == pytest.approx(0.31)
    assert row["band"] == "concentrated"
    assert row["pin_strike"] == 20100.0
    assert row["cliff_strike"] == 19950.0
    assert "live down" in (row["error"] or "")


def test_gamma_config_includes_concentration_summary():
    cfg = gd.gamma_config()
    assert cfg["concentration_summary_window"] == 8
    assert cfg["concentration_summary_refresh_seconds"] >= 60
    assert "NIFTY" in cfg["concentration_summary_underlyings"]
    assert "CRUDEOIL" not in cfg["concentration_summary_underlyings"]


def test_live_sparse_waiting_keeps_persisted_and_allows_provisional(
    patched, monkeypatch, tmp_path
):
    """Live + sparse GEX: waiting=true; prior chips kept; detect may add provisional."""
    from datetime import date as date_cls
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import options.gamma_density_history as gdh

    IST = ZoneInfo("Asia/Kolkata")
    monkeypatch.setattr(gdh, "HISTORY_FILE", tmp_path / "gamma_density_history.json")
    monkeypatch.setattr(gdh, "in_session", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "kite_client.fetch_index_minute_spot",
        lambda *_a, **_k: [],
    )
    detect_kwargs: dict = {}

    def _fake_detect(*_a, **k):
        detect_kwargs.update(k)
        return [
            {
                "t": "2099-01-01T12:00:00+05:30",
                "ts_ms": 9_999_999_999_000,
                "confirmed_at": "2099-01-01T12:05:00+05:30",
                "confirmed_ts_ms": 9_999_999_999_000 + 300_000,
                "side": "bearish",
                "spot": 1.0,
                "move_pts": 999.0,
                "gex_confirm": False,
                "provisional": True,
                "oi_align": False,
                "oi_gate_pass": None,
                "tf": "5m",
                "label": "5m Bearish · (-999 pts)",
            }
        ]

    monkeypatch.setattr(gdh, "detect_spot_reversals", _fake_detect)

    today = date_cls.today().isoformat()
    # Near cash open so gex_history_partial stays False — Live wait only applies
    # when recording started on time; mid-session starts use the hybrid/relax path.
    during = datetime.now(tz=IST).replace(hour=9, minute=16, second=0, microsecond=0)
    series_key = f"NIFTY|{EXPIRY}|{today}"
    rev_key = f"NIFTY|{EXPIRY}|{today}|5m"
    sparse = [
        {
            "t": during.isoformat(timespec="seconds"),
            "spot": SPOT,
            "total_gex": 1.0e9,
            "flip_level": SPOT - 50,
            "gamma_regime": "positive",
        },
        {
            "t": (during.replace(minute=18)).isoformat(timespec="seconds"),
            "spot": SPOT + 10,
            "total_gex": -0.5e9,
            "flip_level": SPOT - 40,
            "gamma_regime": "negative",
        },
    ]
    frozen = [
        {
            "t": during.isoformat(timespec="seconds"),
            "ts_ms": int(during.timestamp() * 1000),
            "side": "bullish",
            "spot": SPOT,
            "move_pts": 49.0,
            "gex_confirm": True,
            "oi_align": True,
            "oi_gate_pass": True,
            "tf": "5m",
            "label": "5m Bullish · (+49 pts) · GEX · OI align",
        }
    ]
    gdh._save(
        {
            "series": {series_key: sparse},
            "reversals": {rev_key: frozen},
            "daily_hhi": {},
        }
    )

    snap = gd.build_gamma_snapshot(
        "NIFTY",
        expiry=EXPIRY,
        provider=patched,
        include_multi_expiry=False,
        include_history=True,
        include_vanna_strip=False,
        reversal_tf="5m",
        reversal_gex_gate=True,
        reversal_gex_mode="live",
        reversal_oi_gate=True,
    )

    assert snap["reversals_gex_waiting"] is True
    assert snap["reversals_gex_relaxed"] is False
    assert snap["reversal_gex_mode"] == "live"
    assert snap["reversals_gex_samples"] < snap["reversals_gex_min_samples"]
    assert detect_kwargs.get("provisional_ungated") is True
    assert detect_kwargs.get("gex_gate") is True
    # Prior gated chip + new provisional (opposite side) both visible.
    assert len(snap["reversals"]) == 2
    by_side = {r["side"]: r for r in snap["reversals"]}
    assert by_side["bullish"]["move_pts"] == 49.0
    assert by_side["bearish"].get("provisional") is True
    assert by_side["bearish"]["gex_confirm"] is False


def test_default_gex_history_sample_underlyings_includes_crude():
    names = gd.default_gex_history_sample_underlyings()
    assert "NIFTY" in names
    assert "CRUDEOIL" in names


def test_maybe_sample_gex_history_periodic_records_without_chart(monkeypatch, patched):
    """Scheduler path must persist ticks with build_session_chart=False for all due names."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import options.gamma_density_history as gdh

    IST = ZoneInfo("Asia/Kolkata")
    calls: list[dict] = []

    def _fake_snap(underlying, **kwargs):
        calls.append({"underlying": underlying, **kwargs})
        return {"underlying": underlying, "history": [{"total_gex": 1.0}]}

    monkeypatch.setattr(gd, "build_gamma_snapshot", _fake_snap)
    monkeypatch.setattr(gdh, "in_session", lambda *_a, **_k: True)
    monkeypatch.setattr(gd, "_gex_sample_last_ok", {})
    monkeypatch.setattr(gd, "_gex_last_desk_underlying", "CRUDEOIL")
    # Force a weekday mid-session clock so weekend short-circuit does not apply.
    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 3, 11, 0, tzinfo=tz or IST)

    monkeypatch.setattr(gd, "datetime", _FixedDateTime)

    assert gd.maybe_sample_gex_history_periodic() is True
    names = gd.default_gex_history_sample_underlyings()
    assert len(calls) == len(names)
    assert all(c["include_history"] is True for c in calls)
    assert all(c["build_session_chart"] is False for c in calls)
    assert all(c["include_multi_expiry"] is False for c in calls)
    assert {c["underlying"] for c in calls} == set(names)
    # Last-viewed desk underlying is sampled first.
    assert calls[0]["underlying"] == "CRUDEOIL"


def test_maybe_sample_gex_history_periodic_logs_failure_short_backoff(monkeypatch, patched):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    import options.gamma_density_history as gdh

    IST = ZoneInfo("Asia/Kolkata")

    def _boom(underlying, **kwargs):
        raise RuntimeError(f"no chain for {underlying}")

    monkeypatch.setattr(gd, "build_gamma_snapshot", _boom)
    monkeypatch.setattr(gdh, "in_session", lambda u, *_a, **_k: u == "CRUDEOIL")
    monkeypatch.setattr(gd, "_gex_sample_last_ok", {})
    monkeypatch.setattr(gd, "_gex_last_desk_underlying", None)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 3, 20, 0, tzinfo=tz or IST)

    monkeypatch.setattr(gd, "datetime", _FixedDateTime)

    assert gd.maybe_sample_gex_history_periodic() is False
    last = gd._gex_sample_last_ok.get("CRUDEOIL")
    assert last is not None
    # Failure must not impose a full success interval of silence.
    age = __import__("time").time() - last
    assert age >= gd.GEX_HISTORY_SAMPLE_INTERVAL_SEC - gd.GEX_HISTORY_SAMPLE_FAIL_BACKOFF_SEC - 2
