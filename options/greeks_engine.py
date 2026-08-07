"""NSE-calibrated Black–Scholes Greeks engine (1st + 2nd order).

European cash-settled index options (NIFTY / BANKNIFTY / SENSEX). Formulas follow
standard quantitative option theory (Haug / Passarelli-compatible BS Greeks).

Units (unless noted):
  delta, gamma       — per unit underlying
  vega               — per 1% IV (absolute 0.01)
  theta_*            — premium per day (calendar or trading-day mode)
  rho                — per 1% rate move
  vanna              — ∂Δ/∂σ  (= ∂vega_raw/∂S), σ in decimal
  charm              — Δ bleed per calendar year (÷365 → per day)
  vomma              — ∂vega_raw/∂σ  (σ decimal); also /100 → per 1% vol
  zomma              — ∂Γ/∂σ
  speed              — ∂Γ/∂S
  color              — ∂Γ/∂t (per year; ÷365 → per day)
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from config import GREEKS_ENGINE_DEFAULTS

IST = ZoneInfo("Asia/Kolkata")
OptionType = Literal["CE", "PE"]
ThetaMode = Literal["calendar", "trading_hours"]

_MIN_TTE = 1.0 / (365.0 * 24.0 * 60.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _defaults() -> dict[str, Any]:
    return dict(GREEKS_ENGINE_DEFAULTS)


def d1_d2(
    spot: float,
    strike: float,
    tte_years: float,
    iv: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> tuple[float, float] | None:
    if spot <= 0 or strike <= 0 or tte_years <= 0 or iv <= 0:
        return None
    sqrt_t = math.sqrt(tte_years)
    d1 = (
        math.log(spot / strike)
        + (risk_free_rate - dividend_yield + 0.5 * iv * iv) * tte_years
    ) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    if not (math.isfinite(d1) and math.isfinite(d2)):
        return None
    return d1, d2


def time_to_expiry_years_nse(
    expiry: str | date,
    as_of: datetime | None = None,
    *,
    mode: ThetaMode | str = "calendar",
) -> float | None:
    """Years to 15:40 IST expiry (CAS / equity derivatives close).

    * calendar — wall-clock / 365 (weekend decay included).
    * trading_hours — remaining NSE session hours / (252 × session_hours).
    """
    if isinstance(expiry, str):
        exp_date = date.fromisoformat(expiry[:10])
    else:
        exp_date = expiry

    now = as_of or datetime.now(tz=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    expiry_dt = datetime.combine(exp_date, time(15, 40), tzinfo=IST)
    if expiry_dt <= now:
        return None

    mode_s = str(mode or "calendar").lower()
    if mode_s != "trading_hours":
        years = (expiry_dt - now).total_seconds() / (365.0 * 24.0 * 3600.0)
        return max(years, _MIN_TTE)

    cfg = _defaults()
    session_h = float(cfg.get("nse_session_hours") or 6.42)
    trading_days = float(cfg.get("trading_days_per_year") or 252)
    open_t = time(9, 15)
    close_t = time(15, 40)

    remaining_hours = 0.0
    day = now.date()
    while day <= exp_date:
        if day.weekday() < 5:  # Mon–Fri
            if day == now.date() and day == exp_date:
                start = max(now, datetime.combine(day, open_t, tzinfo=IST))
                end = min(expiry_dt, datetime.combine(day, close_t, tzinfo=IST))
                remaining_hours += max(0.0, (end - start).total_seconds() / 3600.0)
            elif day == now.date():
                start = max(now, datetime.combine(day, open_t, tzinfo=IST))
                end = datetime.combine(day, close_t, tzinfo=IST)
                remaining_hours += max(0.0, (end - start).total_seconds() / 3600.0)
            elif day == exp_date:
                start = datetime.combine(day, open_t, tzinfo=IST)
                remaining_hours += max(0.0, (expiry_dt - start).total_seconds() / 3600.0)
            else:
                remaining_hours += session_h
        day += timedelta(days=1)

    years = remaining_hours / (trading_days * session_h)
    return max(years, _MIN_TTE)


def compute_greeks(
    *,
    spot: float,
    strike: float,
    tte_years: float,
    iv: float,
    option_type: OptionType | str,
    risk_free_rate: float | None = None,
    dividend_yield: float | None = None,
    theta_mode: ThetaMode | str | None = None,
) -> dict[str, float | None]:
    """Full 1st + 2nd order European BS Greeks for one option."""
    cfg = _defaults()
    r = float(risk_free_rate if risk_free_rate is not None else cfg["risk_free_rate"])
    q = float(dividend_yield if dividend_yield is not None else cfg["dividend_yield"])
    mode = str(theta_mode or cfg.get("theta_mode") or "calendar").lower()
    trading_days = float(cfg.get("trading_days_per_year") or 252)

    empty = {
        "delta": None,
        "gamma": None,
        "vega": None,
        "theta": None,
        "theta_calendar": None,
        "theta_trading": None,
        "rho": None,
        "vanna": None,
        "charm": None,
        "charm_day": None,
        "vomma": None,
        "zomma": None,
        "speed": None,
        "color": None,
        "color_day": None,
        "d1": None,
        "d2": None,
    }
    pair = d1_d2(spot, strike, tte_years, iv, r, q)
    if pair is None:
        return empty
    d1, d2 = pair
    flag = "c" if str(option_type).upper() == "CE" else "p"
    sqrt_t = math.sqrt(tte_years)
    nd1 = _norm_pdf(d1)
    Nd1 = _norm_cdf(d1)
    Nd2 = _norm_cdf(d2)
    disc_q = math.exp(-q * tte_years)
    disc_r = math.exp(-r * tte_years)

    try:
        if flag == "c":
            delta = disc_q * Nd1
            theta_yr = (
                -spot * disc_q * nd1 * iv / (2.0 * sqrt_t)
                - r * strike * disc_r * Nd2
                + q * spot * disc_q * Nd1
            )
            rho = strike * tte_years * disc_r * Nd2 / 100.0
            charm = -disc_q * (
                nd1 * (2.0 * (r - q) * tte_years - d2 * iv * sqrt_t) / (2.0 * tte_years * iv * sqrt_t)
                + q * Nd1
            )
        else:
            delta = disc_q * (Nd1 - 1.0)
            theta_yr = (
                -spot * disc_q * nd1 * iv / (2.0 * sqrt_t)
                + r * strike * disc_r * _norm_cdf(-d2)
                - q * spot * disc_q * _norm_cdf(-d1)
            )
            rho = -strike * tte_years * disc_r * _norm_cdf(-d2) / 100.0
            charm = -disc_q * (
                nd1 * (2.0 * (r - q) * tte_years - d2 * iv * sqrt_t) / (2.0 * tte_years * iv * sqrt_t)
                - q * _norm_cdf(-d1)
            )

        gamma = disc_q * nd1 / (spot * iv * sqrt_t)
        vega_raw = spot * disc_q * nd1 * sqrt_t  # per 1.0 σ
        vega = vega_raw / 100.0
        vanna = -disc_q * nd1 * d2 / iv
        vomma = vega_raw * d1 * d2 / iv
        zomma = gamma * (d1 * d2 - 1.0) / iv
        speed = -gamma / spot * (d1 / (iv * sqrt_t) + 1.0)
        color = (
            -disc_q
            * nd1
            / (2.0 * spot * tte_years * iv * sqrt_t)
            * (
                2.0 * q * tte_years
                + 1.0
                + (2.0 * (r - q) * tte_years - d2 * iv * sqrt_t) * d1 / (iv * sqrt_t)
            )
        )

        theta_cal = theta_yr / 365.0
        theta_trd = theta_yr / trading_days
        theta = theta_trd if mode == "trading_hours" else theta_cal

        out = {
            "delta": round(delta, 6),
            "gamma": round(gamma, 8),
            "vega": round(vega, 6),
            "theta": round(theta, 6),
            "theta_calendar": round(theta_cal, 6),
            "theta_trading": round(theta_trd, 6),
            "rho": round(rho, 6),
            "vanna": round(vanna, 8),
            "charm": round(charm, 8),
            "charm_day": round(charm / 365.0, 8),
            "vomma": round(vomma / 100.0, 6),  # per 1% vol
            "zomma": round(zomma, 8),
            "speed": round(speed, 10),
            "color": round(color, 10),
            "color_day": round(color / 365.0, 10),
            "d1": round(d1, 6),
            "d2": round(d2, 6),
        }
        for k, v in out.items():
            if v is not None and not math.isfinite(float(v)):
                out[k] = None
        return out
    except Exception:
        return empty


def bs_vanna(
    *,
    spot: float,
    strike: float,
    tte_years: float,
    iv: float,
    risk_free_rate: float | None = None,
    dividend_yield: float | None = None,
) -> float | None:
    """∂Δ/∂σ (= ∂vega_raw/∂S). Identical for CE/PE under BS."""
    cfg = _defaults()
    r = float(risk_free_rate if risk_free_rate is not None else cfg["risk_free_rate"])
    q = float(dividend_yield if dividend_yield is not None else cfg["dividend_yield"])
    g = compute_greeks(
        spot=spot,
        strike=strike,
        tte_years=tte_years,
        iv=iv,
        option_type="CE",
        risk_free_rate=r,
        dividend_yield=q,
    )
    return g.get("vanna")


def greeks_engine_config() -> dict[str, Any]:
    from config import INDEX_OPTIONS

    d = _defaults()
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "refresh_seconds": d["refresh_seconds"],
        "strike_window": d["strike_window"],
        "risk_free_rate": d["risk_free_rate"],
        "dividend_yield": d["dividend_yield"],
        "theta_mode": d["theta_mode"],
        "trading_days_per_year": d["trading_days_per_year"],
        "nse_session_hours": d["nse_session_hours"],
        "style": "european",
        "settlement": "cash_vwap_1530_ist",
        "rate_note": "risk_free_rate ≈ MIBOR / RBI reference proxy",
        "note": "Higher-order Greeks — complementary to GEX/VEX; does not drive 3ST execution.",
    }
