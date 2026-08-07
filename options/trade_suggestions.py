"""Trade suggestions from GEX / VEX / higher-order Greeks.

Read-only structures — never arms, orders, or mutates 3ST state.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import INDEX_OPTIONS, TRADE_SUGGESTIONS_DEFAULTS
from options.gamma_density_provider import (
    GammaDensityDataProvider,
    get_gamma_density_provider,
)
from options.greeks_desk import build_greeks_snapshot, desk_config

IST = ZoneInfo("Asia/Kolkata")


def _cfg() -> dict[str, Any]:
    return dict(TRADE_SUGGESTIONS_DEFAULTS)


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:
        return None
    return x


def _disclaimer() -> str:
    return str(_cfg().get("disclaimer") or "Analytics suggestion — not advice.")


def _step(underlying: str) -> float:
    meta = INDEX_OPTIONS.get(underlying) or {}
    return float(meta.get("strike_step") or 50)


def _lot(underlying: str) -> int:
    meta = INDEX_OPTIONS.get(underlying) or {}
    return int(meta.get("lot_size") or 1)


def _weekend_ahead(as_of: datetime | None = None) -> bool:
    now = as_of or datetime.now(tz=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    # Fri (or Thu after 15:30) → weekend bleed ahead
    if now.weekday() == 4:
        return True
    if now.weekday() == 3 and now.hour >= 15:
        return True
    return False


def _approx_pop_credit(width: float, credit: float) -> float | None:
    """Rough POP for short vertical / iron (credit / width)."""
    if width <= 0 or credit <= 0:
        return None
    return round(min(0.95, max(0.05, credit / width)), 4)


def _net_greeks_placeholder() -> dict[str, float]:
    return {
        "net_delta": 0.0,
        "net_gamma": 0.0,
        "net_vega": 0.0,
        "net_theta": 0.0,
    }


def _idea(
    *,
    id_: str,
    structure: str,
    title: str,
    bias: str,
    score: float,
    reasoning: str,
    legs: list[dict[str, Any]],
    risk: dict[str, Any],
    adjustments: list[str],
    strikes_focus: list[float],
    category: str,
) -> dict[str, Any]:
    return {
        "id": id_,
        "structure": structure,
        "title": title,
        "bias": bias,
        "category": category,
        "score": round(score, 4),
        "reasoning": reasoning,
        "legs": legs,
        "risk_profile": risk,
        "adjustment_rules": adjustments,
        "strikes_focus": strikes_focus,
        "pricing_hint": "Refine premiums / edge on Pricing Engine before sizing.",
        "disclaimer": _disclaimer(),
    }


def _iron_condor_idea(
    greeks: dict[str, Any],
    *,
    underlying: str,
) -> dict[str, Any] | None:
    spot = _f(greeks.get("spot"))
    atm = _f(greeks.get("atm_strike"))
    call_wall = _f(greeks.get("call_wall"))
    put_wall = _f(greeks.get("put_wall"))
    regime = str(greeks.get("gamma_regime") or "")
    flip = _f(greeks.get("dynamic_flip_level") or greeks.get("flip_level"))
    if spot is None or atm is None:
        return None
    if regime != "positive":
        return None

    step = _step(underlying)
    lot = _lot(underlying)
    # Short strikes inside walls; wings 1–2 steps out
    short_put = put_wall if put_wall is not None else atm - 2 * step
    short_call = call_wall if call_wall is not None else atm + 2 * step
    if short_put >= spot or short_call <= spot:
        short_put = atm - 2 * step
        short_call = atm + 2 * step
    long_put = short_put - step
    long_call = short_call + step
    width = step
    # Approximate credit ~ 0.25–0.35 of width in long-gamma pin regimes
    credit = round(width * 0.30, 2)
    max_profit = credit
    max_loss = round(width - credit, 2)
    pop = _approx_pop_credit(width, credit)
    be_lo = round(short_put - credit, 2)
    be_hi = round(short_call + credit, 2)

    score = 55.0
    if flip is not None and abs(spot - flip) < 2 * step:
        score += 15.0  # pin / flip magnet
    if _weekend_ahead():
        score += 10.0

    reasoning = (
        f"Positive dealer GEX ({regime}) — MM long gamma dampens moves. "
        f"Iron condor short {short_put:.0f}/{short_call:.0f} with wings "
        f"{long_put:.0f}/{long_call:.0f}. Spot {spot:.1f}"
    )
    if flip is not None:
        reasoning += f"; dynamic gamma flip ≈ {flip:.1f}."
    reasoning += " Prefer defined-risk short-vol while regime stays long-gamma."

    legs = [
        {"side": "buy", "option_type": "PE", "strike": long_put},
        {"side": "sell", "option_type": "PE", "strike": short_put},
        {"side": "sell", "option_type": "CE", "strike": short_call},
        {"side": "buy", "option_type": "CE", "strike": long_call},
    ]
    risk = {
        "max_risk": max_loss,
        "max_return": max_profit,
        "max_risk_inr": round(max_loss * lot, 2),
        "max_return_inr": round(max_profit * lot, 2),
        "pop": pop,
        "breakevens": [be_lo, be_hi],
        "net_premium": credit,
        "width": width,
        "lot_size": lot,
        **_net_greeks_placeholder(),
        "net_delta": 0.0,
        "net_gamma": -0.02,  # short gamma approx marker
        "net_vega": -1.0,
        "net_theta": 0.5,
    }
    adj = [
        f"Exit or roll if spot breaks dynamic flip ({flip if flip else 'n/a'}) with volume.",
        "Delta creep: if |net Δ| > 0.15 per lot, roll threatened side or convert to iron fly.",
        "Cut if gamma regime flips negative (total GEX < 0).",
    ]
    return _idea(
        id_="short_iron_condor_long_gex",
        structure="short_iron_condor",
        title=f"Short Iron Condor {short_put:.0f}/{short_call:.0f} (long-GEX pin)",
        bias="neutral_short_vol",
        score=score,
        reasoning=reasoning,
        legs=legs,
        risk=risk,
        adjustments=adj,
        strikes_focus=[long_put, short_put, short_call, long_call],
        category="delta_neutral",
    )


def _vanna_gamma_scalp(
    greeks: dict[str, Any],
    *,
    underlying: str,
) -> dict[str, Any] | None:
    hot = greeks.get("hot_zones") or []
    if not hot:
        return None
    top = hot[0]
    strike = _f(top.get("strike"))
    score_vg = _f(top.get("vanna_gamma_score")) or 0.0
    spot = _f(greeks.get("spot"))
    atm = _f(greeks.get("atm_strike"))
    iv_flow = str(greeks.get("iv_flow_regime") or "")
    vex = _f(greeks.get("total_vex_cr")) or 0.0
    if strike is None or spot is None or atm is None:
        return None
    if score_vg <= 0:
        return None

    step = _step(underlying)
    lot = _lot(underlying)
    # Asymmetric: long ATM straddle if vol-up supportive; else long risk-reversal hedge
    if "supportive" in iv_flow or vex > 0:
        structure = "long_atm_straddle"
        title = f"Vanna–Gamma scalp — long straddle {atm:.0f}"
        bias = "long_vol_asym"
        legs = [
            {"side": "buy", "option_type": "CE", "strike": atm},
            {"side": "buy", "option_type": "PE", "strike": atm},
        ]
        reasoning = (
            f"Peak |Vanna×Gamma| at {strike:.0f} (score {score_vg:.2g}). "
            f"IV flow `{iv_flow}` with net VEX {vex:+.2f} ₹Cr — dealers buy delta on vol up. "
            f"ATM straddle captures gamma scalps + vanna assist."
        )
        score = 50.0 + min(30.0, math_log_score(score_vg))
        risk = {
            "max_risk": None,
            "max_return": None,
            "pop": None,
            "breakevens": [atm - step, atm + step],
            "net_premium": None,
            "width": 0.0,
            "lot_size": lot,
            "net_delta": 0.0,
            "net_gamma": 0.05,
            "net_vega": 2.0,
            "net_theta": -1.0,
            "note": "Debit defined by straddle premium; scalp gamma on spot moves.",
        }
    else:
        structure = "put_debit_bias"
        title = f"Vanna–Gamma pressure — put debit near {atm:.0f}"
        bias = "bearish_vol_pressure"
        put_k = atm - step
        legs = [
            {"side": "buy", "option_type": "PE", "strike": atm},
            {"side": "sell", "option_type": "PE", "strike": put_k},
        ]
        reasoning = (
            f"Hot zone {strike:.0f} with `{iv_flow}` — vol-up dealer flow presses delta. "
            f"Prefer defined-risk put debit; avoid naked short puts through PE wall."
        )
        score = 48.0 + min(25.0, math_log_score(score_vg))
        width = step
        debit = round(width * 0.35, 2)
        risk = {
            "max_risk": debit,
            "max_return": round(width - debit, 2),
            "max_risk_inr": round(debit * lot, 2),
            "max_return_inr": round((width - debit) * lot, 2),
            "pop": round(1.0 - debit / width, 4) if width else None,
            "breakevens": [round(atm - debit, 2)],
            "net_premium": -debit,
            "width": width,
            "lot_size": lot,
            "net_delta": -0.25,
            "net_gamma": 0.02,
            "net_vega": 0.8,
            "net_theta": -0.3,
        }

    adj = [
        "Scale out 50% into 1σ expected move; trail remainder.",
        "If Vanna Line reclaims/rejects with IV crush, flatten vanna-heavy legs.",
        f"Watch dynamic flip {_f(greeks.get('dynamic_flip_level'))} for gamma regime shift.",
    ]
    return _idea(
        id_="vanna_gamma_scalp",
        structure=structure,
        title=title,
        bias=bias,
        score=score,
        reasoning=reasoning,
        legs=legs,
        risk=risk,
        adjustments=adj,
        strikes_focus=[strike, atm],
        category="vanna_gamma",
    )


def math_log_score(x: float) -> float:
    import math

    if x <= 0:
        return 0.0
    return math.log10(1.0 + x)


def _theta_vega_weekend(
    greeks: dict[str, Any],
    *,
    underlying: str,
) -> dict[str, Any] | None:
    if not _weekend_ahead():
        # Still surface if |theta/vega| attractive near expiry
        tte = _f(greeks.get("tte_years")) or 1.0
        if tte > 5.0 / 365.0:
            return None

    spot = _f(greeks.get("spot"))
    atm = _f(greeks.get("atm_strike"))
    net_theta = _f(greeks.get("net_theta")) or 0.0
    net_vega = _f(greeks.get("net_vega")) or 0.0
    regime = str(greeks.get("gamma_regime") or "")
    if spot is None or atm is None:
        return None

    # Dealer net theta positive ⇒ short options dominate ⇒ sell premium carefully with hedge
    step = _step(underlying)
    lot = _lot(underlying)
    short_put = atm - step
    short_call = atm + step
    long_put = short_put - step
    long_call = short_call + step
    width = step
    credit = round(width * 0.28, 2)

    ratio = abs(net_theta) / max(abs(net_vega), 1e-6)
    score = 40.0 + min(25.0, ratio * 5.0)
    if _weekend_ahead():
        score += 12.0
    if regime == "positive":
        score += 8.0

    reasoning = (
        f"Theta/Vega focus: dealer net θ≈{net_theta:.1f}, ν≈{net_vega:.1f} "
        f"(θ/ν≈{ratio:.2f}). "
    )
    if _weekend_ahead():
        reasoning += "Weekend calendar bleed ahead — favor short-dated credit with wings. "
    reasoning += (
        f"Short iron fly/condor around ATM {atm:.0f} while GEX stays {regime or 'n/a'}."
    )

    legs = [
        {"side": "buy", "option_type": "PE", "strike": long_put},
        {"side": "sell", "option_type": "PE", "strike": short_put},
        {"side": "sell", "option_type": "CE", "strike": short_call},
        {"side": "buy", "option_type": "CE", "strike": long_call},
    ]
    risk = {
        "max_risk": round(width - credit, 2),
        "max_return": credit,
        "max_risk_inr": round((width - credit) * lot, 2),
        "max_return_inr": round(credit * lot, 2),
        "pop": _approx_pop_credit(width, credit),
        "breakevens": [round(short_put - credit, 2), round(short_call + credit, 2)],
        "net_premium": credit,
        "width": width,
        "lot_size": lot,
        "net_delta": 0.0,
        "net_gamma": -0.03,
        "net_vega": -1.2,
        "net_theta": 0.8,
        "theta_vega_ratio": round(ratio, 4),
    }
    adj = [
        "Flatten before Monday open if overnight gap risk exceeds wing width.",
        "If IV crush fails and vega bite > 2× collected theta, cut.",
        "Delta creep > 0.20 → roll untested side in.",
    ]
    return _idea(
        id_="theta_vega_weekend",
        structure="short_iron_condor",
        title="Theta/Vega harvest (weekend / short-dated)",
        bias="neutral_theta",
        score=score,
        reasoning=reasoning,
        legs=legs,
        risk=risk,
        adjustments=adj,
        strikes_focus=[long_put, short_put, short_call, long_call],
        category="theta_vega",
    )


def _reverse_calendar_charm(
    greeks: dict[str, Any],
    *,
    underlying: str,
) -> dict[str, Any] | None:
    charm = _f(greeks.get("total_charm")) or 0.0
    color = _f(greeks.get("total_color")) or 0.0
    tte = _f(greeks.get("tte_years")) or 1.0
    atm = _f(greeks.get("atm_strike"))
    spot = _f(greeks.get("spot"))
    if atm is None or spot is None:
        return None
    # Near expiry + material charm/color → delta bleed / gamma decay trades
    if tte > 10.0 / 365.0:
        return None
    if abs(charm) < 1e-9 and abs(color) < 1e-9:
        return None

    lot = _lot(underlying)
    structure = "reverse_calendar_bias"
    title = f"Charm/Color — reverse calendar bias ATM {atm:.0f}"
    bias = "short_front_long_back" if charm < 0 else "long_front_bleed_fade"
    legs = [
        {"side": "sell", "option_type": "CE", "strike": atm, "tenor": "front"},
        {"side": "sell", "option_type": "PE", "strike": atm, "tenor": "front"},
        {"side": "buy", "option_type": "CE", "strike": atm, "tenor": "back"},
        {"side": "buy", "option_type": "PE", "strike": atm, "tenor": "back"},
    ]
    score = 42.0 + min(20.0, abs(charm) * 1e3) + min(10.0, abs(color) * 1e4)
    reasoning = (
        f"Near expiry (T≈{tte * 365:.1f}d). Dealer charm/day≈{charm:.4g}, "
        f"color/day≈{color:.4g}. Reverse calendar (short front / long back) "
        f"harvests front charm bleed while keeping vega cushion on the back week."
    )
    risk = {
        "max_risk": None,
        "max_return": None,
        "pop": None,
        "breakevens": None,
        "net_premium": None,
        "width": 0.0,
        "lot_size": lot,
        "net_delta": 0.0,
        "net_gamma": -0.01,
        "net_vega": 0.5,
        "net_theta": 0.4,
        "note": "Requires two expiries; verify back-week liquidity on Pricing Engine.",
    }
    adj = [
        "Close front before final 30-min VWAP settlement window.",
        "If spot trends through dynamic flip, convert to ratio or flatten front.",
    ]
    return _idea(
        id_="charm_reverse_calendar",
        structure=structure,
        title=title,
        bias=bias,
        score=score,
        reasoning=reasoning,
        legs=legs,
        risk=risk,
        adjustments=adj,
        strikes_focus=[atm],
        category="charm_color",
    )


def _ratio_spread_speed(
    greeks: dict[str, Any],
    *,
    underlying: str,
) -> dict[str, Any] | None:
    speed = _f(greeks.get("total_speed")) or 0.0
    dyn = _f(greeks.get("dynamic_flip_level"))
    flip = _f(greeks.get("flip_level"))
    spot = _f(greeks.get("spot"))
    atm = _f(greeks.get("atm_strike"))
    regime = str(greeks.get("gamma_regime") or "")
    if spot is None or atm is None:
        return None
    if dyn is None and flip is None:
        return None

    step = _step(underlying)
    lot = _lot(underlying)
    target = dyn if dyn is not None else flip
    assert target is not None

    # If spot above flip and speed negative for dealers → upside acceleration risk
    if spot > target and regime == "negative":
        structure = "call_ratio_credit"
        title = f"Speed/flip — call ratio above {target:.0f}"
        bias = "fade_upside_accel"
        short_k = atm + step
        long_k = atm + 2 * step
        legs = [
            {"side": "buy", "option_type": "CE", "strike": short_k},
            {"side": "sell", "option_type": "CE", "strike": long_k},
            {"side": "sell", "option_type": "CE", "strike": long_k},
        ]
        reasoning = (
            f"Spot {spot:.1f} above dynamic flip {target:.1f} with negative GEX — "
            f"short-gamma acceleration. Call ratio (1×2) fades convex upside; "
            f"dealer speed≈{speed:.4g}."
        )
        score = 52.0
    elif spot < target and regime == "positive":
        structure = "bull_put_credit"
        title = f"Below flip support — bull put {atm - step:.0f}/{atm:.0f}"
        bias = "bullish_below_flip"
        legs = [
            {"side": "sell", "option_type": "PE", "strike": atm},
            {"side": "buy", "option_type": "PE", "strike": atm - step},
        ]
        reasoning = (
            f"Spot {spot:.1f} below flip {target:.1f} but GEX still positive — "
            f"dips toward flip are dampened. Bull put credit preferred."
        )
        score = 50.0
    else:
        return None

    width = step
    credit = round(width * 0.32, 2)
    risk = {
        "max_risk": round(width - credit, 2) if structure == "bull_put_credit" else None,
        "max_return": credit,
        "max_risk_inr": round((width - credit) * lot, 2) if structure == "bull_put_credit" else None,
        "max_return_inr": round(credit * lot, 2),
        "pop": _approx_pop_credit(width, credit),
        "breakevens": [round(atm - credit, 2)] if structure == "bull_put_credit" else None,
        "net_premium": credit,
        "width": width,
        "lot_size": lot,
        "net_delta": 0.15 if structure == "bull_put_credit" else -0.1,
        "net_gamma": -0.02,
        "net_vega": -0.8,
        "net_theta": 0.4,
        "note": "Ratio spreads have undefined upside risk — size small or use defined wings.",
    }
    adj = [
        f"Hard stop if spot crosses dynamic flip {target:.1f} against the structure.",
        "Delta creep: hedge with futures or roll ratio when |Δ| doubles.",
    ]
    return _idea(
        id_="speed_flip_structure",
        structure=structure,
        title=title,
        bias=bias,
        score=score,
        reasoning=reasoning,
        legs=legs,
        risk=risk,
        adjustments=adj,
        strikes_focus=[atm, target],
        category="gamma_flip",
    )


def build_trade_suggestions(
    underlying: str,
    expiry: str | None = None,
    *,
    strike_window: int | None = None,
    provider: GammaDensityDataProvider | None = None,
) -> dict[str, Any]:
    """Aggregate GEX + VEX + higher-order desk into ranked trade ideas."""
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    prov = provider or get_gamma_density_provider()
    window = strike_window if strike_window is not None else int(_cfg().get("strike_window") or 20)

    greeks = build_greeks_snapshot(
        underlying, expiry=expiry, strike_window=window, provider=prov
    )
    exp = greeks["expiry"]

    # Derive GEX/VEX context from the single greeks pass (no second/third chain fetch).
    # Re-building gamma/vanna desks was timing out on SENSEX/BANKNIFTY.
    gamma_ctx = {
        "total_gex": greeks.get("total_gex"),
        "flip_level": greeks.get("flip_level"),
        "expected_move": None,
    }
    vanna_ctx = {
        "total_vex_cr": greeks.get("total_vex_cr"),
        "vanna_line": greeks.get("vanna_line"),
        "iv_shocks": [],
    }

    ideas: list[dict[str, Any]] = []
    for builder in (
        _iron_condor_idea,
        _vanna_gamma_scalp,
        _theta_vega_weekend,
        _reverse_calendar_charm,
        _ratio_spread_speed,
    ):
        try:
            idea = builder(greeks, underlying=underlying)
        except Exception:
            idea = None
        if not idea:
            continue
        idea["underlying"] = underlying
        idea["expiry"] = exp
        idea["spot"] = greeks.get("spot")
        idea["atm_strike"] = greeks.get("atm_strike")
        idea["context"] = {
            "gamma_regime": greeks.get("gamma_regime"),
            "vanna_regime": greeks.get("vanna_regime"),
            "iv_flow_regime": greeks.get("iv_flow_regime"),
            "flip_level": greeks.get("flip_level"),
            "dynamic_flip_level": greeks.get("dynamic_flip_level"),
            "vanna_line": greeks.get("vanna_line"),
            "total_gex": greeks.get("total_gex"),
            "total_vex_cr": greeks.get("total_vex_cr"),
            "call_wall": greeks.get("call_wall"),
            "put_wall": greeks.get("put_wall"),
        }
        ideas.append(idea)

    ideas.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    max_ideas = int(_cfg().get("max_ideas") or 8)
    ideas = ideas[:max_ideas]

    return {
        "underlying": underlying,
        "expiry": exp,
        "provider": prov.name,
        "spot": greeks.get("spot"),
        "atm_strike": greeks.get("atm_strike"),
        "atm_iv": greeks.get("atm_iv"),
        "updated_at": datetime.now().astimezone().isoformat(),
        "tte_years": greeks.get("tte_years"),
        "weekend_bleed_window": _weekend_ahead(),
        "regimes": {
            "gamma": greeks.get("gamma_regime"),
            "vanna": greeks.get("vanna_regime"),
            "iv_flow": greeks.get("iv_flow_regime"),
            "vol": greeks.get("vol_regime"),
        },
        "signals": greeks.get("signals") or {},
        "charm_sides": greeks.get("charm_sides") or {},
        "vanna_sides": greeks.get("vanna_sides") or {},
        "levels": {
            "flip_level": greeks.get("flip_level"),
            "dynamic_flip_level": greeks.get("dynamic_flip_level"),
            "vanna_line": greeks.get("vanna_line"),
            "call_wall": greeks.get("call_wall"),
            "put_wall": greeks.get("put_wall"),
            "pin_level": greeks.get("pin_level"),
        },
        "portfolio_greeks": {
            "net_delta": greeks.get("net_delta"),
            "net_gamma": greeks.get("net_gamma"),
            "net_vega": greeks.get("net_vega"),
            "net_theta": greeks.get("net_theta"),
            "total_speed": greeks.get("total_speed"),
            "total_vomma": greeks.get("total_vomma"),
            "total_charm": greeks.get("total_charm"),
            "total_color": greeks.get("total_color"),
            "total_zomma": greeks.get("total_zomma"),
            "total_gex": greeks.get("total_gex"),
            "total_vex_cr": greeks.get("total_vex_cr"),
        },
        "hot_zones": greeks.get("hot_zones") or [],
        "suggestions": ideas,
        "greeks_snapshot": {
            "strikes": greeks.get("strikes"),
            "gex_vex": greeks.get("gex_vex"),
        },
        "gamma_context": (
            {
                "total_gex": gamma_ctx.get("total_gex"),
                "flip_level": gamma_ctx.get("flip_level"),
                "expected_move": gamma_ctx.get("expected_move"),
            }
            if gamma_ctx
            else None
        ),
        "vanna_context": (
            {
                "total_vex_cr": vanna_ctx.get("total_vex_cr"),
                "vanna_line": vanna_ctx.get("vanna_line"),
                "iv_shocks": vanna_ctx.get("iv_shocks"),
            }
            if vanna_ctx
            else None
        ),
        "disclaimer": _disclaimer(),
        "note": "Read-only analytics — does not arm or place orders.",
    }


def suggestions_config() -> dict[str, Any]:
    d = _cfg()
    base = desk_config()
    idxs = list(d.get("index_underlyings") or ("NIFTY", "BANKNIFTY", "SENSEX"))
    return {
        "underlyings": idxs,
        "refresh_seconds": d.get("refresh_seconds", 60),
        "strike_window": d.get("strike_window", 20),
        "max_ideas": d.get("max_ideas", 8),
        "provider": base.get("provider"),
        "requires_session": base.get("requires_session"),
        "risk_free_rate": base.get("risk_free_rate"),
        "dividend_yield": base.get("dividend_yield"),
        "theta_mode": base.get("theta_mode"),
        "disclaimer": _disclaimer(),
        "note": "Unified trade suggestions from GEX + VEX + higher-order Greeks.",
    }
