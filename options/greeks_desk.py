"""Higher-order Greeks desk — chain exposures + GEX/VEX refinement.

Builds per-strike dealer exposures for Vanna / Vomma / Speed / Charm / Color /
Zomma and refines Gamma Density flip levels using Speed (dΓ/dS). Cross-maps
VEX with Vanna/Vomma for IV crush vs spike dealer-flow context.

Market data reuses :mod:`options.gamma_density_provider`. Read-only analytics.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from config import GREEKS_ENGINE_DEFAULTS, INDEX_OPTIONS
from options.gamma_density import gamma_flip_level, total_gex_at_spot
from options.gamma_density_provider import (
    GammaDensityDataProvider,
    get_gamma_density_provider,
)
from options.greeks_engine import compute_greeks, greeks_engine_config
from options.iv import implied_volatility, time_to_expiry_years
from options.oi_var import _flatten_chain_legs
from options.vanna_exposure import vanna_line_level

CRORE = 1e7


def _cfg() -> dict[str, Any]:
    return dict(GREEKS_ENGINE_DEFAULTS)


def _unit_greeks(
    spot: float,
    strike: float,
    tte: float,
    iv: float | None,
    option_type: str,
    *,
    theta_mode: str | None = None,
) -> dict[str, float | None] | None:
    if iv is None or iv <= 0 or tte <= 0 or spot <= 0 or strike <= 0:
        return None
    cfg = _cfg()
    return compute_greeks(
        spot=spot,
        strike=strike,
        tte_years=tte,
        iv=iv,
        option_type=option_type,
        risk_free_rate=float(cfg["risk_free_rate"]),
        dividend_yield=float(cfg["dividend_yield"]),
        theta_mode=str(theta_mode or cfg.get("theta_mode") or "calendar"),
    )


def _leg_exposure(
    leg: dict[str, Any],
    quote: dict[str, Any] | None,
    spot: float,
    tte: float,
    *,
    theta_mode: str | None = None,
) -> dict[str, Any] | None:
    if not quote:
        return None
    oi_raw = quote.get("oi")
    if oi_raw is None:
        oi_raw = quote.get("open_interest")
    if oi_raw is None:
        return None
    oi = int(oi_raw)
    ltp = float(quote.get("last_price") or 0)
    if ltp <= 0 or oi <= 0:
        return None

    strike = float(leg["strike"])
    option_type = "CE" if leg["side"] == "call" else "PE"
    lot = int(
        leg.get("lot_size")
        or INDEX_OPTIONS.get(leg.get("_underlying", ""), {}).get("lot_size")
        or 1
    )
    iv = implied_volatility(
        ltp,
        spot,
        strike,
        tte,
        option_type,
        risk_free_rate=float(_cfg()["risk_free_rate"]),
    )
    g = _unit_greeks(spot, strike, tte, iv, option_type, theta_mode=theta_mode)
    if g is None or g.get("gamma") is None:
        return None

    sign = 1.0 if option_type == "CE" else -1.0
    scale = oi * lot
    gamma = float(g["gamma"] or 0)
    vanna = float(g["vanna"] or 0)
    vomma = float(g["vomma"] or 0)
    speed = float(g["speed"] or 0)
    charm = float(g["charm_day"] or 0)
    color = float(g["color_day"] or 0)
    zomma = float(g["zomma"] or 0)
    delta = float(g["delta"] or 0)
    vega = float(g["vega"] or 0)
    theta = float(g["theta"] or 0)

    gex = sign * gamma * scale * spot * spot * 0.01
    vex_inr = sign * vanna * scale * spot * 0.01
    speed_x = sign * speed * scale
    vomma_x = sign * vomma * scale
    charm_x = sign * charm * scale
    color_x = sign * color * scale
    zomma_x = sign * zomma * scale

    return {
        "strike": strike,
        "option_type": option_type,
        "oi": oi,
        "ltp": round(ltp, 2),
        "iv": round(iv * 100, 2) if iv is not None else None,
        "iv_dec": iv,
        "lot": lot,
        "delta": delta,
        "gamma": gamma,
        "vega": vega,
        "theta": theta,
        "vanna": vanna,
        "vomma": vomma,
        "speed": speed,
        "charm_day": charm,
        "color_day": color,
        "zomma": zomma,
        "gex": gex,
        "vex_inr": vex_inr,
        "speed_x": speed_x,
        "vomma_x": vomma_x,
        "charm_x": charm_x,
        "color_x": color_x,
        "zomma_x": zomma_x,
        "vanna_gamma_score": abs(vanna) * abs(gamma) * scale,
    }


def _total_speed_at_spot(
    legs: list[tuple[float, int, int, str, float]],
    spot: float,
    tte: float,
) -> float:
    total = 0.0
    for strike, oi, lot, otype, iv in legs:
        g = _unit_greeks(spot, strike, tte, iv, otype)
        if not g or g.get("speed") is None:
            continue
        signed = float(g["speed"]) * oi * lot
        total += signed if otype == "CE" else -signed
    return total


def dynamic_gamma_flip(
    legs: list[tuple[float, int, int, str, float]],
    spot: float,
    tte: float,
    s_min: float,
    s_max: float,
    *,
    steps: int | None = None,
    bump_pct: float = 0.0025,
) -> float | None:
    """Speed-refined flip: zero of GEX(S) + Speed(S)×(bump·S).

    Captures where dealer gamma flips after a small spot move (acceleration).
    """
    if not legs or s_max <= s_min:
        return None
    # Adaptive grid — SENSEX/BANKNIFTY chains are wide; keep latency bounded.
    if steps is None:
        steps = 120 if len(legs) > 80 else 220
    step = (s_max - s_min) / steps

    def _dyn(s: float) -> float:
        gex = total_gex_at_spot(legs, s, tte)
        spd = _total_speed_at_spot(legs, s, tte)
        # Speed is ∂Γ/∂S; convert to GEX-space approx via Γ term × S²×0.01
        # Use local Γ-bump: ΔGEX ≈ Speed × dS × OI×lot × S²×0.01 (sign in spd)
        d_s = bump_pct * s
        return gex + spd * d_s * s * s * 0.01

    prev_s = s_min
    prev_v = _dyn(s_min)
    crossings: list[float] = []
    for i in range(1, steps + 1):
        s = s_min + i * step
        v = _dyn(s)
        if prev_v == 0.0:
            crossings.append(prev_s)
        elif (prev_v < 0 < v) or (prev_v > 0 > v):
            frac = -prev_v / (v - prev_v)
            crossings.append(prev_s + frac * (s - prev_s))
        prev_s, prev_v = s, v
    if not crossings:
        return None
    return round(min(crossings, key=lambda x: abs(x - spot)), 2)


def build_greeks_snapshot(
    underlying: str,
    expiry: str | None = None,
    *,
    strike_window: int | None = None,
    theta_mode: str | None = None,
    provider: GammaDensityDataProvider | None = None,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    cfg = _cfg()
    mode = str(theta_mode or cfg.get("theta_mode") or "calendar")

    prov = provider or get_gamma_density_provider()
    window = int(strike_window) if strike_window is not None else int(cfg["strike_window"])

    exp = expiry or prov.nearest_expiry(underlying)
    if not exp:
        raise RuntimeError(f"No expiries found for {underlying}")

    tte = time_to_expiry_years(exp)
    if tte is None:
        raise RuntimeError(f"Expiry {exp} is in the past for {underlying}")

    spot = prov.get_spot(underlying)
    if spot is None:
        raise RuntimeError(f"Index spot unavailable for {underlying}")
    spot = float(spot)

    chain = prov.get_chain(underlying, exp)
    raw_legs = _flatten_chain_legs(chain)
    if not raw_legs:
        raise RuntimeError(f"No option legs for {underlying} expiry {exp}")
    for leg in raw_legs:
        leg["_underlying"] = underlying

    quote_keys = [
        f"{leg.get('exchange', chain.get('exchange', 'NFO'))}:{leg['tradingsymbol']}"
        for leg in raw_legs
        if leg.get("tradingsymbol")
    ]
    quotes = prov.fetch_quotes(quote_keys)

    per_strike: dict[float, dict[str, Any]] = {}
    resolved: list[tuple[float, int, int, str, float]] = []
    quoted = 0
    totals = {
        "gex": 0.0,
        "vex_inr": 0.0,
        "speed_x": 0.0,
        "vomma_x": 0.0,
        "charm_x": 0.0,
        "color_x": 0.0,
        "zomma_x": 0.0,
        "net_delta": 0.0,
        "net_gamma": 0.0,
        "net_vega": 0.0,
        "net_theta": 0.0,
    }

    for leg in raw_legs:
        exchange = leg.get("exchange", chain.get("exchange", "NFO"))
        symbol = leg.get("tradingsymbol")
        if not symbol:
            continue
        built = _leg_exposure(
            leg,
            quotes.get(f"{exchange}:{symbol}"),
            spot,
            tte,
            theta_mode=mode,
        )
        if not built:
            continue
        quoted += 1
        strike = built["strike"]
        resolved.append(
            (strike, built["oi"], built["lot"], built["option_type"], built["iv_dec"])
        )
        sign = 1.0 if built["option_type"] == "CE" else -1.0
        scale = built["oi"] * built["lot"]
        totals["gex"] += built["gex"]
        totals["vex_inr"] += built["vex_inr"]
        totals["speed_x"] += built["speed_x"]
        totals["vomma_x"] += built["vomma_x"]
        totals["charm_x"] += built["charm_x"]
        totals["color_x"] += built["color_x"]
        totals["zomma_x"] += built["zomma_x"]
        totals["net_delta"] += sign * built["delta"] * scale
        totals["net_gamma"] += sign * built["gamma"] * scale
        totals["net_vega"] += sign * built["vega"] * scale
        totals["net_theta"] += sign * built["theta"] * scale

        row = per_strike.setdefault(
            strike,
            {
                "strike": strike,
                "ce_oi": 0,
                "pe_oi": 0,
                "ce_iv": None,
                "pe_iv": None,
                "ce_gex": 0.0,
                "pe_gex": 0.0,
                "net_gex": 0.0,
                "ce_vex_inr": 0.0,
                "pe_vex_inr": 0.0,
                "net_vex_inr": 0.0,
                "ce_charm": 0.0,
                "pe_charm": 0.0,
                "net_charm": 0.0,
                "ce_vanna": 0.0,
                "pe_vanna": 0.0,
                "net_speed": 0.0,
                "net_vomma": 0.0,
                "net_color": 0.0,
                "net_zomma": 0.0,
                "vanna_gamma_score": 0.0,
                "ce_delta": None,
                "pe_delta": None,
            },
        )
        if built["option_type"] == "CE":
            row["ce_oi"] = built["oi"]
            row["ce_iv"] = built["iv"]
            row["ce_delta"] = round(built["delta"], 4)
            row["ce_gex"] = built["gex"]
            row["ce_vex_inr"] = built["vex_inr"]
            row["ce_charm"] = built["charm_x"]
            row["ce_vanna"] = built["vex_inr"]  # ₹ VEX proxy for CE vanna exposure
        else:
            row["pe_oi"] = built["oi"]
            row["pe_iv"] = built["iv"]
            row["pe_delta"] = round(built["delta"], 4)
            row["pe_gex"] = built["gex"]
            row["pe_vex_inr"] = built["vex_inr"]
            row["pe_charm"] = built["charm_x"]
            row["pe_vanna"] = built["vex_inr"]
        row["net_gex"] += built["gex"]
        row["net_vex_inr"] += built["vex_inr"]
        row["net_speed"] += built["speed_x"]
        row["net_vomma"] += built["vomma_x"]
        row["net_charm"] += built["charm_x"]
        row["net_color"] += built["color_x"]
        row["net_zomma"] += built["zomma_x"]
        row["vanna_gamma_score"] += built["vanna_gamma_score"]

    strikes = sorted(per_strike.values(), key=lambda r: r["strike"])
    if not strikes:
        raise RuntimeError(f"No greek-resolvable legs for {underlying} expiry {exp}")

    all_k = [r["strike"] for r in strikes]
    s_min, s_max = min(all_k), max(all_k)
    atm = min(strikes, key=lambda r: abs(r["strike"] - spot))

    if window > 0:
        atm_idx = strikes.index(atm)
        lo = max(0, atm_idx - window)
        hi = min(len(strikes), atm_idx + window + 1)
        strikes = strikes[lo:hi]

    for r in strikes:
        for key in (
            "ce_gex",
            "pe_gex",
            "net_gex",
            "ce_vex_inr",
            "pe_vex_inr",
            "net_vex_inr",
            "ce_charm",
            "pe_charm",
            "net_charm",
            "ce_vanna",
            "pe_vanna",
            "net_speed",
            "net_vomma",
            "net_color",
            "net_zomma",
            "vanna_gamma_score",
        ):
            r[key] = round(float(r.get(key) or 0), 4)

    # Wider chains (BANKNIFTY / SENSEX) use a coarser flip grid for latency.
    flip_steps = 120 if len(resolved) > 80 else 280
    flip = gamma_flip_level(resolved, spot, tte, s_min, s_max, steps=flip_steps)
    if len(resolved) > 120:
        # Skip expensive Speed re-scan on very wide chains; classic flip is enough.
        dyn_flip = flip
    else:
        dyn_flip = dynamic_gamma_flip(resolved, spot, tte, s_min, s_max)
    vanna_line = vanna_line_level(resolved, spot, tte, s_min, s_max, steps=flip_steps)

    atm_ivs = [v for v in (atm.get("ce_iv"), atm.get("pe_iv")) if v is not None]
    atm_iv = round(sum(atm_ivs) / len(atm_ivs), 2) if atm_ivs else None

    call_wall = max(strikes, key=lambda r: abs(r.get("ce_oi") or 0))["strike"] if strikes else None
    put_wall = max(strikes, key=lambda r: abs(r.get("pe_oi") or 0))["strike"] if strikes else None
    # Prefer density-like walls from |gex| contribution
    if strikes:
        call_wall = max(strikes, key=lambda r: max(0.0, r["net_gex"]))["strike"]
        put_wall = min(strikes, key=lambda r: r["net_gex"])["strike"]

    hot = sorted(strikes, key=lambda r: r["vanna_gamma_score"], reverse=True)[:5]
    hot_zones = [
        {
            "strike": z["strike"],
            "vanna_gamma_score": z["vanna_gamma_score"],
            "net_gex": z["net_gex"],
            "net_vex_inr": z["net_vex_inr"],
            "net_speed": z["net_speed"],
            "net_vomma": z["net_vomma"],
        }
        for z in hot
        if z["vanna_gamma_score"] > 0
    ]

    total_gex = round(totals["gex"], 2)
    total_vex = round(totals["vex_inr"], 2)
    total_vomma = round(totals["vomma_x"], 4)

    # IV regime: VEX tells delta flow on vol-up; Vomma tells vega convexity
    if total_vex >= 0 and total_vomma >= 0:
        iv_flow = "vol_up_supportive_convex"
    elif total_vex >= 0 and total_vomma < 0:
        iv_flow = "vol_up_supportive_concave"
    elif total_vex < 0 and total_vomma >= 0:
        iv_flow = "vol_up_pressure_convex"
    else:
        iv_flow = "vol_up_pressure_concave"

    gamma_regime = "positive" if total_gex >= 0 else "negative"
    vanna_regime = "positive" if total_vex >= 0 else "negative"

    # Hedgewall-style narratives
    call_charm = sum(float(r.get("ce_charm") or 0) for r in strikes)
    put_charm = sum(float(r.get("pe_charm") or 0) for r in strikes)
    if call_charm * put_charm < 0:
        charm_signal = "Mixed charm signals — low confidence in direction"
        charm_detail = "Call and put sides show opposite charm sensitivity."
    elif totals["charm_x"] < 0:
        charm_signal = "Negative charm — sell-side delta bleed into expiry"
        charm_detail = "Dealers shed long delta as time passes; favor fades into strength."
    else:
        charm_signal = "Positive charm — buy-side delta accretion"
        charm_detail = "Delta grows with time; dips may see supportive dealer flow."

    if total_vex >= 0:
        vanna_signal = "Long Vanna — IV rise supports dealer buying pressure"
        vanna_detail = (
            "As IV rises, dealers gain delta and may sell underlying into vol spikes "
            "— a headwind during vol expansion."
        )
    else:
        vanna_signal = "Short Vanna — IV rise forces dealer selling pressure"
        vanna_detail = (
            "As IV rises, dealers lose delta and may sell underlying — "
            "amplifies downside on vol spikes."
        )

    if gamma_regime == "positive":
        gamma_signal = "Positive Gamma — dealers dampen moves (mean-revert bias)"
        vol_regime = "dampening"
    else:
        gamma_signal = "Negative Gamma — dealers amplify moves (trend bias)"
        vol_regime = "amplifying"

    peak_charm_row = max(strikes, key=lambda r: abs(r.get("net_charm") or 0)) if strikes else None
    peak_vanna_row = max(strikes, key=lambda r: abs(r.get("net_vex_inr") or 0)) if strikes else None
    pin_level = atm["strike"]

    return {
        "underlying": underlying,
        "expiry": exp,
        "provider": prov.name,
        "spot": spot,
        "updated_at": datetime.now().astimezone().isoformat(),
        "tte_years": round(tte, 6),
        "atm_strike": atm["strike"],
        "atm_iv": atm_iv,
        "risk_free_rate": float(cfg["risk_free_rate"]),
        "dividend_yield": float(cfg["dividend_yield"]),
        "theta_mode": mode,
        "total_gex": total_gex,
        "total_vex_inr": total_vex,
        "total_vex_cr": round(total_vex / CRORE, 4),
        "total_speed": round(totals["speed_x"], 6),
        "total_vomma": total_vomma,
        "total_charm": round(totals["charm_x"], 6),
        "total_color": round(totals["color_x"], 6),
        "total_zomma": round(totals["zomma_x"], 6),
        "net_delta": round(totals["net_delta"], 2),
        "net_gamma": round(totals["net_gamma"], 6),
        "net_vega": round(totals["net_vega"], 2),
        "net_theta": round(totals["net_theta"], 2),
        "gamma_regime": gamma_regime,
        "vanna_regime": vanna_regime,
        "iv_flow_regime": iv_flow,
        "flip_level": flip,
        "dynamic_flip_level": dyn_flip,
        "vanna_line": vanna_line,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "hot_zones": hot_zones,
        "pin_level": pin_level,
        "vol_regime": vol_regime,
        "signals": {
            "gamma": gamma_signal,
            "charm": charm_signal,
            "charm_detail": charm_detail,
            "vanna": vanna_signal,
            "vanna_detail": vanna_detail,
        },
        "charm_sides": {
            "net": round(totals["charm_x"], 4),
            "call": round(call_charm, 4),
            "put": round(put_charm, 4),
            "peak_strike": peak_charm_row["strike"] if peak_charm_row else None,
        },
        "vanna_sides": {
            "net_cr": round(total_vex / CRORE, 4),
            "peak_strike": peak_vanna_row["strike"] if peak_vanna_row else None,
            "delta_from_1pt_iv_cr": round(total_vex / CRORE, 4),
        },
        "chain_legs_quoted": quoted,
        "chain_legs_total": len(raw_legs),
        "strike_window": window,
        "sign_convention": "gex_style_ce_plus_pe_minus",
        "strikes": strikes,
        "gex_vex": {
            "classic_flip": flip,
            "speed_refined_flip": dyn_flip,
            "vanna_line": vanna_line,
            "spot_vs_flip": (
                round(spot - flip, 2) if flip is not None else None
            ),
            "spot_vs_dyn_flip": (
                round(spot - dyn_flip, 2) if dyn_flip is not None else None
            ),
            "note": (
                "dynamic_flip uses GEX(S)+Speed·ΔS; VEX×Vomma maps IV crush/spike "
                "dealer flow (iv_flow_regime)."
            ),
        },
    }


def desk_config() -> dict[str, Any]:
    from config import TRADE_SUGGESTIONS_DEFAULTS

    base = greeks_engine_config()
    prov = get_gamma_density_provider()
    idxs = list(
        TRADE_SUGGESTIONS_DEFAULTS.get("index_underlyings")
        or ("NIFTY", "BANKNIFTY", "SENSEX")
    )
    base["underlyings"] = idxs
    base["provider"] = prov.name
    base["requires_session"] = prov.requires_session()
    return base
