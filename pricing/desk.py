"""Live complementary pricing desk — BS IV / fair value vs LTP.

Read-only: quotes market data only. Never arms, orders, or mutates 3ST state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import INDEX_OPTIONS, PRICING_ENGINE_DEFAULTS
from options.chain import atm_strike, get_chain, list_expiries, require_index_spot
from options.iv import time_to_expiry_years
from options.oi_var import _quote_batches
from pricing.bs_engine import price_black_scholes, solve_iv_and_greeks
from pricing.heston_cos import HestonParams, heston_cos_price
from pricing.recommendations import build_recommendations


def pricing_config() -> dict[str, Any]:
    d = PRICING_ENGINE_DEFAULTS
    reco = dict(d.get("recommendations") or {})
    return {
        "underlyings": list(d.get("index_underlyings") or ["NIFTY", "BANKNIFTY", "SENSEX"]),
        "strike_count": d["strike_count"],
        "refresh_seconds": d["refresh_seconds"],
        "risk_free_rate": d["risk_free_rate"],
        "heston_defaults": dict(d.get("heston") or {}),
        "recommendations": {
            "atm_window_steps": reco.get("atm_window_steps", 2),
            "min_ltp": reco.get("min_ltp", 5.0),
            "max_ideas": reco.get("max_ideas", 3),
        },
        "note": "Complementary analytics only — does not drive 3ST execution.",
    }


def _strike_window(spot: float, step: int, strike_count: int) -> list[float]:
    atm = atm_strike(spot, step)
    half = max(1, strike_count // 2)
    return [float(atm + i * step) for i in range(-half, half + 1)]


def _leg_row(
    *,
    otype: str,
    spot: float,
    strike: float,
    tte: float,
    ltp: float | None,
    r: float,
    ref_iv: float | None,
    heston: HestonParams | None,
    include_heston: bool,
) -> dict[str, Any]:
    """Price one leg. BS fair/edge uses ``ref_iv`` (ATM) so cross-strike mispricing shows."""
    solved = solve_iv_and_greeks(
        market_price=ltp,
        spot=spot,
        strike=strike,
        tte_years=tte,
        option_type=otype,
        risk_free_rate=r,
        model_iv=ref_iv,
    )
    heston_px: float | None = None
    heston_edge: float | None = None
    if include_heston and heston is not None:
        hp = heston_cos_price(
            spot=spot,
            strike=strike,
            tte_years=tte,
            option_type=otype,
            params=heston,
        )
        heston_px = hp.get("price")
        if ltp and heston_px is not None:
            heston_edge = round(float(ltp) - float(heston_px), 4)

    return {
        "ltp": solved["market_price"],
        "iv": solved["iv"],
        "bs_fair": solved["bs_fair_value"],
        "edge": solved["edge"],
        "edge_pct": solved["edge_pct"],
        "delta": solved["delta"],
        "gamma": solved["gamma"],
        "theta": solved["theta"],
        "vega": solved["vega"],
        "heston_fair": heston_px,
        "heston_edge": heston_edge,
    }


def build_pricing_desk(
    underlying: str,
    expiry: str,
    *,
    strike_count: int | None = None,
    include_heston: bool = False,
    heston_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS.keys())}")

    available = list_expiries(underlying)
    if expiry not in available:
        raise RuntimeError(f"Expiry {expiry} not available for {underlying}")

    defaults = PRICING_ENGINE_DEFAULTS
    n = int(strike_count if strike_count is not None else defaults["strike_count"])
    n = max(5, min(n, 41))
    step = int(INDEX_OPTIONS[underlying]["strike_step"])
    r = float(defaults.get("risk_free_rate", 0.065))

    spot = require_index_spot(underlying)
    atm = atm_strike(spot, step)
    strikes = _strike_window(spot, step, n)

    chain_data = get_chain(underlying, expiry)
    smap = {float(row["strike"]): row for row in chain_data.get("strikes", [])}
    exch_default = chain_data.get("exchange", INDEX_OPTIONS[underlying]["exchange"])

    quote_keys: list[str] = []
    leg_by_strike: dict[float, dict[str, str]] = {}
    for k in strikes:
        row = smap.get(k)
        if not row:
            continue
        leg_by_strike[k] = {}
        for otype in ("ce", "pe"):
            leg = row.get(otype)
            if leg and leg.get("tradingsymbol"):
                key = f"{leg.get('exchange', exch_default)}:{leg['tradingsymbol']}"
                leg_by_strike[k][otype] = key
                quote_keys.append(key)

    quotes = _quote_batches(quote_keys)
    tte = time_to_expiry_years(expiry)
    if tte is None:
        raise RuntimeError(f"Expiry {expiry} has no time to expiry")

    heston: HestonParams | None = None
    if include_heston:
        hd = dict(defaults.get("heston") or {})
        if heston_overrides:
            hd.update({k: float(v) for k, v in heston_overrides.items() if v is not None})
        heston = HestonParams(
            v0=float(hd.get("v0", 0.04)),
            kappa=float(hd.get("kappa", 2.0)),
            theta=float(hd.get("theta", 0.04)),
            sigma=float(hd.get("sigma", 0.5)),
            rho=float(hd.get("rho", -0.7)),
            r=r,
            q=float(hd.get("q", 0.0)),
        )

    # Pass 1: solve ATM IV to use as flat BS reference for the smile edge.
    from options.iv import implied_volatility

    atm_ce_iv_dec: float | None = None
    atm_pe_iv_dec: float | None = None
    atm_legs = leg_by_strike.get(atm, {})
    if "ce" in atm_legs:
        q = quotes.get(atm_legs["ce"])
        if q:
            px = float(q.get("last_price") or 0)
            if px > 0:
                atm_ce_iv_dec = implied_volatility(px, spot, atm, tte, "CE", r)
    if "pe" in atm_legs:
        q = quotes.get(atm_legs["pe"])
        if q:
            px = float(q.get("last_price") or 0)
            if px > 0:
                atm_pe_iv_dec = implied_volatility(px, spot, atm, tte, "PE", r)

    ref_iv: float | None = None
    if atm_ce_iv_dec is not None and atm_pe_iv_dec is not None:
        ref_iv = (atm_ce_iv_dec + atm_pe_iv_dec) / 2.0
    elif atm_ce_iv_dec is not None:
        ref_iv = atm_ce_iv_dec
    elif atm_pe_iv_dec is not None:
        ref_iv = atm_pe_iv_dec

    rows: list[dict[str, Any]] = []
    for k in strikes:
        legs = leg_by_strike.get(k, {})
        ce_ltp = pe_ltp = None
        if "ce" in legs:
            q = quotes.get(legs["ce"])
            if q:
                px = float(q.get("last_price") or 0)
                ce_ltp = px if px > 0 else None
        if "pe" in legs:
            q = quotes.get(legs["pe"])
            if q:
                px = float(q.get("last_price") or 0)
                pe_ltp = px if px > 0 else None

        ce = _leg_row(
            otype="CE",
            spot=spot,
            strike=k,
            tte=tte,
            ltp=ce_ltp,
            r=r,
            ref_iv=ref_iv,
            heston=heston,
            include_heston=include_heston,
        )
        pe = _leg_row(
            otype="PE",
            spot=spot,
            strike=k,
            tte=tte,
            ltp=pe_ltp,
            r=r,
            ref_iv=ref_iv,
            heston=heston,
            include_heston=include_heston,
        )

        straddle_ltp = None
        if ce_ltp is not None and pe_ltp is not None:
            straddle_ltp = round(ce_ltp + pe_ltp, 4)
        straddle_bs = None
        if ce.get("bs_fair") is not None and pe.get("bs_fair") is not None:
            straddle_bs = round(float(ce["bs_fair"]) + float(pe["bs_fair"]), 4)

        rows.append(
            {
                "strike": k,
                "ce": ce,
                "pe": pe,
                "straddle_ltp": straddle_ltp,
                "straddle_bs": straddle_bs,
                "straddle_edge": (
                    round(straddle_ltp - straddle_bs, 4)
                    if straddle_ltp is not None and straddle_bs is not None
                    else None
                ),
            }
        )

    atm_iv = round(ref_iv * 100, 2) if ref_iv is not None else None

    recommendations = build_recommendations(
        rows,
        underlying=underlying,
        spot=float(spot),
        atm_strike=float(atm),
    )

    return {
        "underlying": underlying,
        "expiry": expiry,
        "spot": float(spot),
        "atm_strike": float(atm),
        "atm_iv": atm_iv,
        "tte_years": round(tte, 8),
        "risk_free_rate": r,
        "include_heston": include_heston,
        "heston_params": heston.as_dict() if heston else None,
        "rows": rows,
        "recommendations": recommendations,
        "updated_at": datetime.now().astimezone().isoformat(),
        "engine": "complementary_pricing",
        "note": "Does not affect Rolling Straddle / 3ST execution.",
    }


def price_single(
    *,
    spot: float,
    strike: float,
    tte_years: float | None = None,
    expiry: str | None = None,
    option_type: str,
    market_price: float | None = None,
    iv: float | None = None,
    risk_free_rate: float = 0.065,
    include_heston: bool = False,
    heston_overrides: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Manual calculator — no broker calls."""
    tte = tte_years
    if tte is None:
        if not expiry:
            raise ValueError("Provide tte_years or expiry")
        tte = time_to_expiry_years(expiry)
        if tte is None:
            raise ValueError(f"Expiry {expiry} has no remaining time")

    model_iv = (iv / 100.0) if iv is not None and iv > 1.0 else iv  # accept % or decimal
    if model_iv is not None and model_iv > 5:
        model_iv = model_iv / 100.0

    bs = solve_iv_and_greeks(
        market_price=market_price,
        spot=spot,
        strike=strike,
        tte_years=float(tte),
        option_type=option_type,
        risk_free_rate=risk_free_rate,
        model_iv=model_iv,
    )

    # If user supplied IV but no market price, still show BS premium
    if market_price is None and model_iv:
        fv = price_black_scholes(
            spot=spot,
            strike=strike,
            tte_years=float(tte),
            iv=model_iv,
            option_type=option_type,
            risk_free_rate=risk_free_rate,
        )
        bs["bs_fair_value"] = fv
        bs["model_iv"] = round(model_iv * 100, 4)

    out: dict[str, Any] = {"bs": bs, "heston": None}
    if include_heston:
        hd = dict(PRICING_ENGINE_DEFAULTS.get("heston") or {})
        if heston_overrides:
            hd.update({k: float(v) for k, v in heston_overrides.items() if v is not None})
        params = HestonParams(
            v0=float(hd.get("v0", 0.04)),
            kappa=float(hd.get("kappa", 2.0)),
            theta=float(hd.get("theta", 0.04)),
            sigma=float(hd.get("sigma", 0.5)),
            rho=float(hd.get("rho", -0.7)),
            r=risk_free_rate,
            q=float(hd.get("q", 0.0)),
        )
        out["heston"] = heston_cos_price(
            spot=spot,
            strike=strike,
            tte_years=float(tte),
            option_type=option_type,
            params=params,
        )
    return out
