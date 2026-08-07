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
    "short_straddle",
    "short_strangle",
    "long_call",
    "long_put",
    "long_strangle",
]

SPREAD_TEMPLATES: dict[str, str] = {
    "bull_call": "Bull Call Spread — buy CE ATM, sell CE OTM",
    "bear_put": "Bear Put Spread — buy PE ATM, sell PE further OTM",
    "bear_call": "Bear Call Spread — sell CE OTM, buy CE further OTM",
    "bull_put": "Bull Put Spread — sell PE OTM, buy PE further OTM",
    "iron_condor": "Iron Condor — sell OTM strangle, buy wings",
    "short_straddle": "Short Straddle — sell ATM CE + ATM PE",
    "short_strangle": "Short Strangle — sell OTM CE + OTM PE",
    "long_call": "Long Call — buy CE at ATM ± OTM offset",
    "long_put": "Long Put — buy PE at ATM ± OTM offset",
    "long_strangle": "Long Strangle — buy OTM CE + OTM PE",
}

SHORT_PREMIUM_TEMPLATES = frozenset({"bear_call", "bull_put", "short_straddle", "short_strangle"})
BUY_HOLD_TEMPLATES = frozenset({"long_call", "long_put", "bull_call", "bear_put", "long_strangle"})


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
    if otm_offset < 0:
        raise ValueError("otm_offset must be >= 0")

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
    if template == "long_call":
        return [_leg(underlying, expiry, atm + off, "CE", "BUY", lot)]
    if template == "long_put":
        return [_leg(underlying, expiry, atm - off, "PE", "BUY", lot)]
    if template == "long_strangle":
        ce_strike = atm + off + step
        pe_strike = atm - off - step
        return [
            _leg(underlying, expiry, ce_strike, "CE", "BUY", lot),
            _leg(underlying, expiry, pe_strike, "PE", "BUY", lot),
        ]
    if template == "bear_call":
        # Short ATM+1 (+ otm_offset), wing further OTM
        sell_strike = atm + off + step
        return [
            _leg(underlying, expiry, sell_strike, "CE", "SELL", lot),
            _leg(underlying, expiry, sell_strike + width, "CE", "BUY", lot),
        ]
    if template == "bull_put":
        # Short ATM−1 (− otm_offset), wing further OTM
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
    if template == "short_straddle":
        return [
            _leg(underlying, expiry, atm, "CE", "SELL", lot),
            _leg(underlying, expiry, atm, "PE", "SELL", lot),
        ]
    if template == "short_strangle":
        # ATM±(1+otm_offset) each side; width_steps unused (no wings in V1)
        ce_strike = atm + off + step
        pe_strike = atm - off - step
        return [
            _leg(underlying, expiry, ce_strike, "CE", "SELL", lot),
            _leg(underlying, expiry, pe_strike, "PE", "SELL", lot),
        ]
    raise ValueError(f"Unknown template '{template}'")


def hedge_wing_for_short_leg(
    *,
    option_type: str,
    short_strike: float,
    width_steps: int,
    strike_step: float,
) -> dict[str, Any]:
    """
    SL-convert decision for naked short CE/PE → credit vertical wing.

    CE SL → bear_call wing (further OTM CE buy)
    PE SL → bull_put wing (further OTM PE buy)
    """
    opt = str(option_type).upper()
    if width_steps < 1:
        raise ValueError("width_steps must be >= 1")
    step = float(strike_step)
    short_k = float(short_strike)
    width = width_steps * step
    if opt == "CE":
        return {
            "option_type": "CE",
            "side": "BUY",
            "strike": short_k + width,
            "template": "bear_call",
            "short_strike": short_k,
        }
    if opt == "PE":
        return {
            "option_type": "PE",
            "side": "BUY",
            "strike": short_k - width,
            "template": "bull_put",
            "short_strike": short_k,
        }
    raise ValueError(f"option_type must be CE or PE, got {option_type!r}")


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


def _max_loss_estimate(
    template: SpreadTemplate,
    width_steps: int,
    underlying: str,
    net_debit: float,
) -> float | None:
    if underlying not in INDEX_OPTIONS:
        return None
    step = INDEX_OPTIONS[underlying]["strike_step"]
    width = width_steps * step
    if template in ("bull_call", "bear_put"):
        return max(0.0, net_debit) if net_debit > 0 else width
    if template in ("long_call", "long_put", "long_strangle"):
        return max(0.0, net_debit) if net_debit > 0 else None
    if template in ("bear_call", "bull_put"):
        # Credit spreads: max loss ≈ width − credit (net_debit negative when credit)
        credit = abs(net_debit) if net_debit < 0 else 0.0
        if net_debit < 0:
            return max(0.0, width - credit)
        return max(0.0, width - net_debit) if net_debit < width else net_debit
    if template == "iron_condor":
        return max(0.0, width - abs(net_debit))
    # Naked short straddle/strangle — theoretically unlimited
    if template in ("short_straddle", "short_strangle"):
        return None
    return None


def preview_spread(
    underlying: str,
    expiry: str,
    template: SpreadTemplate,
    width_steps: int = 1,
    spot: float | None = None,
    legs_override: list[dict[str, Any]] | None = None,
    ltp_fn=None,
    otm_offset: int = 0,
) -> dict[str, Any]:
    """Build legs and attach LTP + net premium."""
    meta = INDEX_OPTIONS[underlying]
    legs = build_legs(
        underlying=underlying,
        expiry=expiry,
        template=template,
        width_steps=width_steps,
        spot=spot,
        otm_offset=otm_offset,
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
    resolved_spot = spot or get_index_spot(underlying)
    return {
        "underlying": underlying,
        "expiry": expiry,
        "template": template,
        "template_label": SPREAD_TEMPLATES.get(template, template),
        "width_steps": width_steps,
        "otm_offset": otm_offset,
        "spot": resolved_spot,
        "atm": atm_strike(float(resolved_spot), meta["strike_step"]) if resolved_spot else None,
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
    otm_offset: int = 0,
) -> dict[str, Any]:
    """Build both long and short 3ST-mapped spreads."""
    return {
        "long": preview_spread(
            underlying, expiry, long_template, width_steps, spot, ltp_fn=ltp_fn, otm_offset=otm_offset
        ),
        "short": preview_spread(
            underlying, expiry, short_template, width_steps, spot, ltp_fn=ltp_fn, otm_offset=otm_offset
        ),
    }
