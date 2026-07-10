"""Single-leg ATM option builders for rolling straddle execution."""

from __future__ import annotations

from typing import Any, Literal

from config import INDEX_OPTIONS
from options.chain import atm_strike, find_option_leg, get_index_spot

OptionSide = Literal["CE", "PE"]


def build_atm_leg(
    underlying: str,
    expiry: str,
    option_type: OptionSide,
    *,
    spot: float | None = None,
    strike: float | None = None,
) -> dict[str, Any]:
    """
    Resolve a single ATM CE or PE leg for order placement.

    Returns tradingsymbol, exchange, instrument_token, side=BUY, quantity=lot_size, strike.
    """
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    meta = INDEX_OPTIONS[underlying]
    step = meta["strike_step"]
    lot = meta["lot_size"]

    if spot is None:
        spot = get_index_spot(underlying)
    if spot is None:
        raise RuntimeError(f"Could not fetch spot for {underlying}. Log in to Kite.")

    atm = float(strike if strike is not None else atm_strike(spot, step))
    opt = option_type.upper()
    if opt not in {"CE", "PE"}:
        raise ValueError("option_type must be CE or PE")

    found = find_option_leg(underlying, expiry, atm, opt)
    if not found:
        raise RuntimeError(
            f"No {opt} found for {underlying} expiry {expiry} strike {atm}"
        )

    return {
        "tradingsymbol": found["tradingsymbol"],
        "exchange": found["exchange"],
        "instrument_token": found["instrument_token"],
        "side": "BUY",
        "quantity": int(found.get("lot_size") or lot),
        "strike": found["strike"],
        "option_type": opt,
        "spot": float(spot),
        "atm": atm,
    }
