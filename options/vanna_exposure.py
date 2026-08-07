"""Vanna Exposure desk — VEX (raw + ₹), Vanna Line, IV-shock scenarios.

Dealer sign convention matches Gamma Density / GEX: CE → +, PE → −.

* Raw VEX   = vanna × OI × lot  (signed)
* ₹ VEX     = vanna × OI × lot × S × 0.01
              ≈ ₹ notional delta rehedge per +1 vol point (0.01 absolute IV)

Vanna Line = spot where aggregate ₹ VEX crosses zero (sticky-strike IV grid scan).

Market data reuses :mod:`options.gamma_density_provider` (default Kite).
Isolated from 3ST / Rolling Straddle execution.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from config import INDEX_OPTIONS, VANNA_EXPOSURE_DEFAULTS
from options.gamma_density_provider import (
    GammaDensityDataProvider,
    get_gamma_density_provider,
)
from options.greeks import bs_vanna
from options.iv import implied_volatility, time_to_expiry_years
from options.oi_var import _flatten_chain_legs
from options.vanna_recommendations import build_vanna_recommendations

CRORE = 1e7


def vanna_config() -> dict[str, Any]:
    d = VANNA_EXPOSURE_DEFAULTS
    prov = get_gamma_density_provider()
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "refresh_seconds": d["refresh_seconds"],
        "strike_window": d["strike_window"],
        "risk_free_rate": d["risk_free_rate"],
        "iv_shock_vol_points": list(d.get("iv_shock_vol_points") or (1, 2)),
        "provider": prov.name,
        "requires_session": prov.requires_session(),
        "sign_convention": "gex_style_ce_plus_pe_minus",
        "note": "Complementary analytics — does not drive 3ST execution.",
    }


def _unit_vanna(
    spot: float,
    strike: float,
    tte: float,
    iv: float | None,
) -> float | None:
    if iv is None or iv <= 0 or tte <= 0 or spot <= 0 or strike <= 0:
        return None
    return bs_vanna(
        spot=spot,
        strike=strike,
        tte_years=tte,
        iv=iv,
        risk_free_rate=float(VANNA_EXPOSURE_DEFAULTS["risk_free_rate"]),
    )


def _leg_vanna(
    leg: dict[str, Any],
    quote: dict[str, Any] | None,
    spot: float,
    tte: float,
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
    lot_size = int(
        leg.get("lot_size")
        or INDEX_OPTIONS.get(leg.get("_underlying", ""), {}).get("lot_size")
        or 1
    )

    iv = implied_volatility(ltp, spot, strike, tte, option_type)
    unit = _unit_vanna(spot, strike, tte, iv)
    if unit is None:
        return None

    # Magnitude density (unsigned) and dealer-signed exposures
    density = abs(unit) * oi * lot_size
    raw = unit * oi * lot_size
    inr = unit * oi * lot_size * spot * 0.01
    sign = 1.0 if option_type == "CE" else -1.0

    return {
        "strike": strike,
        "option_type": option_type,
        "oi": oi,
        "ltp": round(ltp, 2),
        "iv": round(iv * 100, 2) if iv is not None else None,
        "iv_dec": iv,
        "lot": lot_size,
        "vanna": unit,
        "density": density,
        "vex_raw": sign * raw,
        "vex_inr": sign * inr,
    }


def total_vex_inr_at_spot(
    legs: list[tuple[float, int, int, str, float]],
    spot: float,
    tte: float,
) -> float:
    """Aggregate dealer ₹ VEX at hypothetical ``spot`` (sticky-strike IV)."""
    total = 0.0
    for strike, oi, lot, otype, iv in legs:
        vn = _unit_vanna(spot, strike, tte, iv)
        if vn is None:
            continue
        inr = vn * oi * lot * spot * 0.01
        total += inr if otype == "CE" else -inr
    return total


def total_vex_raw_at_spot(
    legs: list[tuple[float, int, int, str, float]],
    spot: float,
    tte: float,
) -> float:
    total = 0.0
    for strike, oi, lot, otype, iv in legs:
        vn = _unit_vanna(spot, strike, tte, iv)
        if vn is None:
            continue
        raw = vn * oi * lot
        total += raw if otype == "CE" else -raw
    return total


def vanna_line_level(
    legs: list[tuple[float, int, int, str, float]],
    spot: float,
    tte: float,
    s_min: float,
    s_max: float,
    *,
    steps: int = 400,
) -> float | None:
    """Zero-₹-VEX spot (Vanna Line): nearest sign change to ``spot``."""
    if not legs or s_max <= s_min:
        return None
    step = (s_max - s_min) / steps
    prev_s = s_min
    prev_v = total_vex_inr_at_spot(legs, s_min, tte)
    crossings: list[float] = []
    for i in range(1, steps + 1):
        s = s_min + i * step
        v = total_vex_inr_at_spot(legs, s, tte)
        if prev_v == 0.0:
            crossings.append(prev_s)
        elif (prev_v < 0 < v) or (prev_v > 0 > v):
            frac = -prev_v / (v - prev_v)
            crossings.append(prev_s + frac * (s - prev_s))
        prev_s, prev_v = s, v
    if not crossings:
        return None
    return round(min(crossings, key=lambda x: abs(x - spot)), 2)


def iv_shock_scenarios(
    *,
    total_vex_raw: float,
    spot: float,
    vol_points: list[int | float],
) -> list[dict[str, Any]]:
    """Dealer delta rehedge estimate if IV rises by N vol points (sticky OI/vanna).

    Δδ (shares) ≈ total_vex_raw × (N × 0.01)
    ₹ notional ≈ Δδ × spot
    """
    out: list[dict[str, Any]] = []
    for n in vol_points:
        n = float(n)
        if n <= 0:
            continue
        d_sigma = n * 0.01
        delta_shares = total_vex_raw * d_sigma
        notional = delta_shares * spot
        out.append(
            {
                "vol_points": n,
                "delta_shares": round(delta_shares, 2),
                "notional_inr": round(notional, 2),
                "notional_cr": round(notional / CRORE, 4),
                "direction": (
                    "dealers_buy_delta"
                    if delta_shares > 0
                    else "dealers_sell_delta"
                    if delta_shares < 0
                    else "flat"
                ),
            }
        )
    return out


def build_vanna_snapshot(
    underlying: str,
    expiry: str | None = None,
    *,
    strike_window: int | None = None,
    provider: GammaDensityDataProvider | None = None,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    prov = provider or get_gamma_density_provider()
    window = (
        int(strike_window)
        if strike_window is not None
        else int(VANNA_EXPOSURE_DEFAULTS["strike_window"])
    )

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
    resolved_legs: list[tuple[float, int, int, str, float]] = []
    quoted = 0
    for leg in raw_legs:
        exchange = leg.get("exchange", chain.get("exchange", "NFO"))
        symbol = leg.get("tradingsymbol")
        if not symbol:
            continue
        built = _leg_vanna(leg, quotes.get(f"{exchange}:{symbol}"), spot, tte)
        if not built:
            continue
        quoted += 1
        strike = built["strike"]
        resolved_legs.append(
            (strike, built["oi"], built["lot"], built["option_type"], built["iv_dec"])
        )
        row = per_strike.setdefault(
            strike,
            {
                "strike": strike,
                "ce_oi": 0,
                "pe_oi": 0,
                "ce_density": 0.0,
                "pe_density": 0.0,
                "ce_vex_raw": 0.0,
                "pe_vex_raw": 0.0,
                "ce_vex_inr": 0.0,
                "pe_vex_inr": 0.0,
                "ce_iv": None,
                "pe_iv": None,
            },
        )
        if built["option_type"] == "CE":
            row["ce_oi"] = built["oi"]
            row["ce_density"] = built["density"]
            row["ce_vex_raw"] = built["vex_raw"]
            row["ce_vex_inr"] = built["vex_inr"]
            row["ce_iv"] = built["iv"]
        else:
            row["pe_oi"] = built["oi"]
            row["pe_density"] = built["density"]
            row["pe_vex_raw"] = built["vex_raw"]
            row["pe_vex_inr"] = built["vex_inr"]
            row["pe_iv"] = built["iv"]

    strikes = sorted(per_strike.values(), key=lambda r: r["strike"])
    if not strikes:
        raise RuntimeError(f"No vanna-resolvable legs for {underlying} expiry {exp}")

    all_strike_vals = [r["strike"] for r in strikes]
    s_min, s_max = min(all_strike_vals), max(all_strike_vals)
    atm = min(strikes, key=lambda r: abs(r["strike"] - spot))

    if window > 0:
        atm_idx = strikes.index(atm)
        lo = max(0, atm_idx - window)
        hi = min(len(strikes), atm_idx + window + 1)
        strikes = strikes[lo:hi]

    for r in strikes:
        r["total_density"] = round(r["ce_density"] + r["pe_density"], 4)
        r["net_vex_raw"] = round(r["ce_vex_raw"] + r["pe_vex_raw"], 4)
        r["net_vex_inr"] = round(r["ce_vex_inr"] + r["pe_vex_inr"], 2)
        for k in (
            "ce_density",
            "pe_density",
            "ce_vex_raw",
            "pe_vex_raw",
            "ce_vex_inr",
            "pe_vex_inr",
        ):
            r[k] = round(r[k], 4 if "raw" in k or "density" in k else 2)

    total_raw = total_vex_raw_at_spot(resolved_legs, spot, tte)
    total_inr = total_vex_inr_at_spot(resolved_legs, spot, tte)
    line = vanna_line_level(resolved_legs, spot, tte, s_min, s_max)

    call_wall = max(strikes, key=lambda r: r["ce_density"])["strike"] if strikes else None
    put_wall = max(strikes, key=lambda r: r["pe_density"])["strike"] if strikes else None

    atm_ivs = [v for v in (atm.get("ce_iv"), atm.get("pe_iv")) if v is not None]
    atm_iv = round(sum(atm_ivs) / len(atm_ivs), 2) if atm_ivs else None

    shock_pts = list(VANNA_EXPOSURE_DEFAULTS.get("iv_shock_vol_points") or (1, 2))
    shocks = iv_shock_scenarios(
        total_vex_raw=total_raw, spot=spot, vol_points=shock_pts
    )

    zones = sorted(strikes, key=lambda r: abs(r["net_vex_inr"]), reverse=True)[:5]
    hot_zones = [
        {
            "strike": z["strike"],
            "net_vex_raw": z["net_vex_raw"],
            "net_vex_inr": z["net_vex_inr"],
            "total_density": z["total_density"],
        }
        for z in zones
    ]

    return {
        "underlying": underlying,
        "expiry": exp,
        "provider": prov.name,
        "spot": spot,
        "updated_at": datetime.now().astimezone().isoformat(),
        "tte_years": round(tte, 6),
        "atm_strike": atm["strike"],
        "atm_iv": atm_iv,
        "total_vex_raw": round(total_raw, 4),
        "total_vex_inr": round(total_inr, 2),
        "total_vex_cr": round(total_inr / CRORE, 4),
        "vanna_regime": "positive" if total_inr >= 0 else "negative",
        "vanna_line": line,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "iv_shocks": shocks,
        "chain_legs_quoted": quoted,
        "chain_legs_total": len(raw_legs),
        "strike_window": window,
        "hot_zones": hot_zones,
        "recommendations": build_vanna_recommendations(
            {
                "underlying": underlying,
                "expiry": exp,
                "spot": spot,
                "atm_strike": atm["strike"],
                "total_vex_raw": round(total_raw, 4),
                "total_vex_inr": round(total_inr, 2),
                "total_vex_cr": round(total_inr / CRORE, 4),
                "vanna_regime": "positive" if total_inr >= 0 else "negative",
                "vanna_line": line,
                "call_wall": call_wall,
                "put_wall": put_wall,
                "iv_shocks": shocks,
            }
        ),
        "sign_convention": "gex_style_ce_plus_pe_minus",
        "units": {
            "vex_raw": "vanna × OI × lot (signed, CE+, PE−)",
            "vex_inr": "vanna × OI × lot × S × 0.01 (₹ per +1 vol point)",
        },
        "note": "Does not affect Rolling Straddle / 3ST execution.",
        "strikes": strikes,
    }
