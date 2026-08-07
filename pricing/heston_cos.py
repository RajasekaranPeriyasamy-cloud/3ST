"""Heston European option via COS method (Fang & Oosterlee).

Complementary model price only — not used by 3ST execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

import numpy as np

OptionType = Literal["CE", "PE"]


@dataclass
class HestonParams:
    """Heston SV parameters (annualized)."""

    v0: float = 0.04  # initial variance
    kappa: float = 2.0  # mean reversion speed
    theta: float = 0.04  # long-run variance
    sigma: float = 0.5  # vol-of-vol
    rho: float = -0.7  # spot-vol correlation
    r: float = 0.065  # risk-free rate
    q: float = 0.0  # dividend / index yield

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in asdict(self).items()}


def _heston_cf(
    u: np.ndarray,
    *,
    t: float,
    spot: float,
    params: HestonParams,
) -> np.ndarray:
    """Characteristic function of log(S_T) under Heston (risk-neutral)."""
    i = 1j
    kappa, theta, sigma, rho, v0 = (
        params.kappa,
        params.theta,
        params.sigma,
        params.rho,
        params.v0,
    )
    r, q = params.r, params.q

    a = kappa * theta
    b = kappa - rho * sigma * u * i
    d = np.sqrt(b * b + (sigma * sigma) * (u * u + i * u))
    g = (b - d) / (b + d)

    exp_dt = np.exp(-d * t)
    C = (r - q) * u * i * t + (a / (sigma * sigma)) * (
        (b - d) * t - 2.0 * np.log((1.0 - g * exp_dt) / (1.0 - g))
    )
    D = ((b - d) / (sigma * sigma)) * ((1.0 - exp_dt) / (1.0 - g * exp_dt))
    return np.exp(C + D * v0 + i * u * np.log(spot))


def _cos_coefficients_put(
    a: float,
    b: float,
    k: np.ndarray,
    strike: float,
) -> np.ndarray:
    """COS payoff coefficients for a put on [a, b] (Fang–Oosterlee)."""
    # χ and ψ integrals for (K - e^x)+
    omega = k * np.pi / (b - a)

    def chi(c: float, d: float) -> np.ndarray:
        # ∫_c^d e^x cos(ω(x-a)) dx
        num = np.exp(d) * (np.cos(omega * (d - a)) + omega * np.sin(omega * (d - a))) - np.exp(
            c
        ) * (np.cos(omega * (c - a)) + omega * np.sin(omega * (c - a)))
        return num / (1.0 + omega * omega)

    def psi(c: float, d: float) -> np.ndarray:
        out = np.empty_like(omega, dtype=float)
        mask0 = np.abs(omega) < 1e-14
        out[mask0] = d - c
        om = omega[~mask0]
        out[~mask0] = (np.sin(om * (d - a)) - np.sin(om * (c - a))) / om
        return out

    # x = log(S_T); put payoff max(K - e^x, 0) on [a, log K]
    log_k = np.log(strike)
    c = a
    d = min(log_k, b)
    if d <= c:
        return np.zeros_like(k, dtype=float)

    chi_kd = chi(c, d)
    psi_kd = psi(c, d)
    vk = (2.0 / (b - a)) * (strike * psi_kd - chi_kd)
    vk[0] *= 0.5
    return vk


def heston_cos_price(
    *,
    spot: float,
    strike: float,
    tte_years: float,
    option_type: OptionType | str,
    params: HestonParams | None = None,
    n_terms: int = 128,
    L: float = 10.0,
) -> dict[str, Any]:
    """Price European option with Heston + COS expansion.

    Returns model price and echo of inputs. Call via put-call parity.
    """
    if spot <= 0 or strike <= 0 or tte_years <= 0:
        return {"price": None, "error": "invalid inputs"}

    p = params or HestonParams()
    otype = "CE" if str(option_type).upper() == "CE" else "PE"
    n_terms = max(32, min(int(n_terms), 512))

    # Truncation interval for x = log(S_T)
    c1 = (p.r - p.q) * tte_years
    c2 = (
        p.theta * tte_years
        + (p.v0 - p.theta) * (1.0 - np.exp(-p.kappa * tte_years)) / p.kappa
    )
    c2 = max(float(c2), 1e-8)
    a = float(np.log(spot) + c1 - L * np.sqrt(c2))
    b = float(np.log(spot) + c1 + L * np.sqrt(c2))

    k = np.arange(n_terms, dtype=float)
    u = k * np.pi / (b - a)
    cf = _heston_cf(u, t=tte_years, spot=spot, params=p)
    # Recover φ(u) e^{-i u a}
    phi = cf * np.exp(-1j * u * a)
    vk = _cos_coefficients_put(a, b, k, strike)
    put = float(np.exp(-p.r * tte_years) * np.real(np.sum(phi * vk)))
    put = max(put, 0.0)

    # Put-call parity: C = P + S e^{-qT} - K e^{-rT}
    call = put + spot * np.exp(-p.q * tte_years) - strike * np.exp(-p.r * tte_years)
    call = max(float(call), 0.0)

    price = call if otype == "CE" else put
    return {
        "model": "heston_cos",
        "option_type": otype,
        "spot": round(spot, 4),
        "strike": float(strike),
        "tte_years": round(tte_years, 8),
        "price": round(price, 4),
        "call_price": round(call, 4),
        "put_price": round(put, 4),
        "params": p.as_dict(),
        "n_terms": n_terms,
    }
