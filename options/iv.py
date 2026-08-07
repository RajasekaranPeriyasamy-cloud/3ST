"""Implied volatility helpers for index options (Black-Scholes, European)."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from zoneinfo import ZoneInfo

from vollib.black_scholes.implied_volatility import implied_volatility as bs_iv

IST = ZoneInfo("Asia/Kolkata")
OptionType = Literal["CE", "PE"]

# Minimum TTE (~1 minute) to avoid solver blow-ups near expiry.
_MIN_TTE_YEARS = 1.0 / (365.0 * 24.0 * 60.0)


def time_to_expiry_years(expiry: str | date, as_of: datetime | None = None) -> float | None:
    """Years to expiry; index options expire at 15:40 IST (CAS / F&O close) on expiry date."""
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
    delta = expiry_dt - now
    seconds = delta.total_seconds()
    if seconds <= 0:
        return None
    years = seconds / (365.0 * 24.0 * 3600.0)
    return max(years, _MIN_TTE_YEARS)


def implied_volatility(
    price: float | None,
    spot: float | None,
    strike: float | None,
    tte_years: float | None,
    option_type: OptionType | str,
    risk_free_rate: float = 0.065,
) -> float | None:
    """Return annualized IV (decimal, e.g. 0.14 = 14%) or None if unsolvable."""
    if price is None or spot is None or strike is None or tte_years is None:
        return None
    if price <= 0 or spot <= 0 or strike <= 0 or tte_years <= 0:
        return None

    flag = "c" if str(option_type).upper() == "CE" else "p"
    try:
        iv = float(bs_iv(price, spot, strike, tte_years, risk_free_rate, flag))
    except Exception:
        return None
    if iv <= 0 or iv > 5.0:
        return None
    return iv
