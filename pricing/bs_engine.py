"""Black–Scholes European pricing helpers for the complementary desk."""

from __future__ import annotations

from typing import Any, Literal

from vollib.black_scholes import black_scholes as _bs_price

from options.greeks import option_greeks
from options.iv import implied_volatility

OptionType = Literal["CE", "PE"]


def price_black_scholes(
    *,
    spot: float,
    strike: float,
    tte_years: float,
    iv: float,
    option_type: OptionType | str,
    risk_free_rate: float = 0.065,
) -> float | None:
    """Theoretical BS premium (rupees)."""
    if spot <= 0 or strike <= 0 or tte_years <= 0 or iv <= 0:
        return None
    flag = "c" if str(option_type).upper() == "CE" else "p"
    try:
        return round(float(_bs_price(flag, spot, strike, tte_years, risk_free_rate, iv)), 4)
    except Exception:
        return None


def solve_iv_and_greeks(
    *,
    market_price: float | None,
    spot: float,
    strike: float,
    tte_years: float,
    option_type: OptionType | str,
    risk_free_rate: float = 0.065,
    model_iv: float | None = None,
) -> dict[str, Any]:
    """IV from LTP (if given), greeks, BS fair value, and edge vs market.

    ``model_iv`` (decimal) overrides ATM/user vol for fair-value pricing when set;
    otherwise fair value uses solved IV from market (identity) or None.
    """
    otype = "CE" if str(option_type).upper() == "CE" else "PE"
    iv_mkt = implied_volatility(
        market_price, spot, strike, tte_years, otype, risk_free_rate
    )
    iv_for_fv = model_iv if model_iv is not None and model_iv > 0 else iv_mkt
    fv = (
        price_black_scholes(
            spot=spot,
            strike=strike,
            tte_years=tte_years,
            iv=iv_for_fv,
            option_type=otype,
            risk_free_rate=risk_free_rate,
        )
        if iv_for_fv
        else None
    )
    greeks = (
        option_greeks(
            spot=spot,
            strike=strike,
            tte_years=tte_years,
            iv=iv_mkt if iv_mkt else (iv_for_fv or 0.0),
            option_type=otype,
            risk_free_rate=risk_free_rate,
        )
        if (iv_mkt or iv_for_fv)
        else {"delta": None, "gamma": None, "theta": None, "vega": None}
    )

    edge: float | None = None
    edge_pct: float | None = None
    if market_price is not None and market_price > 0 and fv is not None:
        edge = round(float(market_price) - fv, 4)
        edge_pct = round(100.0 * edge / float(market_price), 2)

    return {
        "option_type": otype,
        "spot": round(spot, 4),
        "strike": float(strike),
        "tte_years": round(tte_years, 8),
        "risk_free_rate": risk_free_rate,
        "market_price": round(float(market_price), 4) if market_price else None,
        "iv": round(iv_mkt * 100, 4) if iv_mkt is not None else None,  # percent
        "iv_decimal": round(iv_mkt, 6) if iv_mkt is not None else None,
        "model_iv": round(iv_for_fv * 100, 4) if iv_for_fv is not None else None,
        "bs_fair_value": fv,
        "edge": edge,
        "edge_pct": edge_pct,
        "rich_cheap": (
            "rich" if edge is not None and edge > 0 else "cheap" if edge is not None and edge < 0 else None
        ),
        **greeks,
    }
