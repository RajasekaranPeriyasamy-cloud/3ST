"""Multi-leg index option spread templates and preview."""

from __future__ import annotations

from typing import Any, Literal

from config import INDEX_OPTIONS
from options.chain import atm_strike, find_option_leg, get_index_spot

SpreadTemplate = Literal[
    "bull_call",
    "bear_put",
    "bear_call",
    "bull_put",
    "iron_condor",
]

SPREAD_TEMPLATES: dict[str, str] = {
    "bull_call": "Bull Call Spread — buy CE ATM, sell CE OTM",
    "bear_put": "Bear Put Spread — buy PE ATM, sell PE ITM (bullish defined risk)",
    "bear_call": "Bear Call Spread — sell CE OTM, buy CE further OTM",
    "bull_put": "Bull Put Spread — sell PE OTM, buy PE further OTM",
    "iron_condor": "Iron Condor — sell OTM strangle, buy wings",
}


def _leg(
    underlying: str,
    expiry: str,
    strike: float,
    option_type: str,
    side: str,
    lot_size: int,
) -> dict[str, Any]:
    found = find_option_leg(underlying, expiry, strike, option_type)
    if not found:
        raise RuntimeError(
            f"No {option_type} found for {underlying} expiry {expiry} strike {strike}"
        )
    return {
        "tradingsymbol": found["tradingsymbol"],
        "exchange": found["exchange"],
        "instrument_token": found["instrument_token"],
        "side": side,
        "quantity": lot_size,
        "strike": found["strike"],
        "option_type": option_type.upper(),
    }


def build_legs(
    underlying: str,
    expiry: str,
    template: SpreadTemplate,
    width_steps: int = 1,
    spot: float | None = None,
    otm_offset: int = 0,
) -> list[dict[str, Any]]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'")
    if width_steps < 1:
        raise ValueError("width_steps must be >= 1")

    meta = INDEX_OPTIONS[underlying]
    step = meta["strike_step"]
    lot = meta["lot_size"]
    if spot is None:
        spot = get_index_spot(underlying)
    if spot is None:
        raise RuntimeError(f"Could not fetch spot for {underlying}. Log in to Kite.")

    atm = atm_strike(spot, step)
    width = width_steps * step
    off = otm_offset * step

    if template == "bull_call":
        return [
            _leg(underlying, expiry, atm, "CE", "BUY", lot),
            _leg(underlying, expiry, atm + width, "CE", "SELL", lot),
        ]
    if template == "bear_put":
        return [
            _leg(underlying, expiry, atm, "PE", "BUY", lot),
            _leg(underlying, expiry, atm - width, "PE", "SELL", lot),
        ]
    if template == "bear_call":
        sell_strike = atm + off + step
        return [
            _leg(underlying, expiry, sell_strike, "CE", "SELL", lot),
            _leg(underlying, expiry, sell_strike + width, "CE", "BUY", lot),
        ]
    if template == "bull_put":
        sell_strike = atm - off - step
        return [
            _leg(underlying, expiry, sell_strike, "PE", "SELL", lot),
            _leg(underlying, expiry, sell_strike - width, "PE", "BUY", lot),
        ]
    if template == "iron_condor":
        return [
            _leg(underlying, expiry, atm + step, "CE", "SELL", lot),
            _leg(underlying, expiry, atm + step + width, "CE", "BUY", lot),
            _leg(underlying, expiry, atm - step, "PE", "SELL", lot),
            _leg(underlying, expiry, atm - step - width, "PE", "BUY", lot),
        ]
    raise ValueError(f"Unknown template '{template}'")


def _apply_leg_overrides(
    legs: list[dict[str, Any]],
    overrides: list[dict[str, Any]] | None,
    underlying: str,
    expiry: str,
    lot_size: int,
) -> list[dict[str, Any]]:
    if not overrides:
        return legs
    out: list[dict[str, Any]] = []
    for i, leg in enumerate(legs):
        ov = overrides[i] if i < len(overrides) else {}
        strike = float(ov.get("strike", leg["strike"]))
        option_type = str(ov.get("option_type", leg["option_type"])).upper()
        side = str(ov.get("side", leg["side"])).upper()
        qty = int(ov.get("quantity", leg["quantity"]))
        if ov.get("strike") is not None or ov.get("option_type") is not None:
            rebuilt = _leg(underlying, expiry, strike, option_type, side, qty)
        else:
            rebuilt = {**leg, "side": side, "quantity": qty}
        out.append(rebuilt)
    return out


def _max_loss_estimate(template: SpreadTemplate, width_steps: int, underlying: str, net_debit: float) -> float | None:
    if underlying not in INDEX_OPTIONS:
        return None
    step = INDEX_OPTIONS[underlying]["strike_step"]
    width = width_steps * step
    if template in ("bull_call", "bear_put"):
        return max(0.0, net_debit)
    if template in ("bear_call", "bull_put"):
        return max(0.0, width - net_debit) if net_debit < width else net_debit
    if template == "iron_condor":
        return max(0.0, width - abs(net_debit))
    return None


def preview_spread(
    underlying: str,
    expiry: str,
    template: SpreadTemplate,
    width_steps: int = 1,
    spot: float | None = None,
    legs_override: list[dict[str, Any]] | None = None,
    ltp_fn=None,
) -> dict[str, Any]:
    """Build legs and attach LTP + net premium."""
    meta = INDEX_OPTIONS[underlying]
    legs = build_legs(
        underlying=underlying,
        expiry=expiry,
        template=template,
        width_steps=width_steps,
        spot=spot,
    )
    legs = _apply_leg_overrides(legs, legs_override, underlying, expiry, meta["lot_size"])

    if ltp_fn is None:
        from broker.kite_broker import KiteBroker

        broker = KiteBroker()
        ltp_fn = broker.ltp

    enriched: list[dict[str, Any]] = []
    net = 0.0
    for leg in legs:
        try:
            px = float(ltp_fn(leg["exchange"], leg["tradingsymbol"]))
        except Exception:
            px = 0.0
        sign = 1 if leg["side"] == "BUY" else -1
        leg_premium = sign * px * leg["quantity"]
        net += leg_premium
        enriched.append({**leg, "ltp": px, "premium": leg_premium})

    net_debit = net
    return {
        "underlying": underlying,
        "expiry": expiry,
        "template": template,
        "template_label": SPREAD_TEMPLATES.get(template, template),
        "width_steps": width_steps,
        "spot": spot or get_index_spot(underlying),
        "lot_size": meta["lot_size"],
        "legs": enriched,
        "net_premium": net_debit,
        "net_debit": net_debit if net_debit >= 0 else 0.0,
        "net_credit": abs(net_debit) if net_debit < 0 else 0.0,
        "is_debit": net_debit >= 0,
        "max_loss_estimate": _max_loss_estimate(template, width_steps, underlying, net_debit),
    }


def build_direction_spreads(
    underlying: str,
    expiry: str,
    long_template: SpreadTemplate = "bull_call",
    short_template: SpreadTemplate = "bear_call",
    width_steps: int = 1,
    spot: float | None = None,
    ltp_fn=None,
) -> dict[str, Any]:
    """Build both long and short 3ST-mapped spreads."""
    return {
        "long": preview_spread(underlying, expiry, long_template, width_steps, spot, ltp_fn=ltp_fn),
        "short": preview_spread(underlying, expiry, short_template, width_steps, spot, ltp_fn=ltp_fn),
    }
