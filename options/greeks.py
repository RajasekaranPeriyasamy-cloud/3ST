"""Black-Scholes greeks for index options (European).

Thin compatibility wrapper over :mod:`options.greeks_engine` (1st + 2nd order).
"""

from __future__ import annotations

from typing import Literal

from options.greeks_engine import bs_vanna as _engine_vanna
from options.greeks_engine import compute_greeks

OptionType = Literal["CE", "PE"]


def bs_vanna(
    *,
    spot: float,
    strike: float,
    tte_years: float,
    iv: float,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
) -> float | None:
    """Black–Scholes vanna ∂Δ/∂σ (= ∂vega/∂S). Same for CE/PE under BS."""
    return _engine_vanna(
        spot=spot,
        strike=strike,
        tte_years=tte_years,
        iv=iv,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
    )


def option_greeks(
    *,
    spot: float,
    strike: float,
    tte_years: float,
    iv: float,
    option_type: OptionType | str,
    risk_free_rate: float = 0.065,
    dividend_yield: float = 0.0,
) -> dict[str, float | None]:
    """Return core greeks; includes second-order fields when available."""
    full = compute_greeks(
        spot=spot,
        strike=strike,
        tte_years=tte_years,
        iv=iv,
        option_type=option_type,
        risk_free_rate=risk_free_rate,
        dividend_yield=dividend_yield,
    )
    return {
        "delta": round(full["delta"], 4) if full.get("delta") is not None else None,
        "gamma": round(full["gamma"], 6) if full.get("gamma") is not None else None,
        "theta": round(full["theta"], 4) if full.get("theta") is not None else None,
        "vega": round(full["vega"], 4) if full.get("vega") is not None else None,
        "vanna": round(full["vanna"], 6) if full.get("vanna") is not None else None,
        "rho": full.get("rho"),
        "charm": full.get("charm"),
        "vomma": full.get("vomma"),
        "zomma": full.get("zomma"),
        "speed": full.get("speed"),
        "color": full.get("color"),
    }
