"""Complementary option pricing desk (BS + Heston-COS).

Isolated from the 3ST signal/execution engine — read-only analytics only.
"""

from pricing.bs_engine import price_black_scholes, solve_iv_and_greeks
from pricing.desk import build_pricing_desk, pricing_config
from pricing.heston_cos import heston_cos_price
from pricing.recommendations import build_recommendations

__all__ = [
    "price_black_scholes",
    "solve_iv_and_greeks",
    "heston_cos_price",
    "build_pricing_desk",
    "pricing_config",
    "build_recommendations",
]
