"""Trade ideas from Vanna Exposure (dealer VEX / vol-up flow).

Read-only positioning tilts — never arms, orders, or mutates 3ST state.
Sign convention: CE +, PE − (GEX-style). Positive net VEX ≈ dealers buy delta on vol up.
"""

from __future__ import annotations

from typing import Any

from config import VANNA_EXPOSURE_DEFAULTS


def _cfg() -> dict[str, Any]:
    return dict(VANNA_EXPOSURE_DEFAULTS.get("recommendations") or {})


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
    return str(
        _cfg().get(
            "disclaimer",
            "Dealer-flow model — not advice; does not arm or place orders. "
            "Size premiums on Pricing Engine.",
        )
    )


def _shock_one(iv_shocks: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    if not iv_shocks:
        return None
    for s in iv_shocks:
        if _f(s.get("vol_points")) == 1.0:
            return s
    return iv_shocks[0]


def _regime_idea(snap: dict[str, Any]) -> dict[str, Any] | None:
    regime = str(snap.get("vanna_regime") or "")
    total_cr = _f(snap.get("total_vex_cr")) or 0.0
    spot = _f(snap.get("spot"))
    line = _f(snap.get("vanna_line"))
    atm = _f(snap.get("atm_strike"))
    shock = _shock_one(snap.get("iv_shocks"))
    direction = (shock or {}).get("direction") or ""

    if regime == "positive" or total_cr > 0:
        bias = "bullish_vol_support"
        structure = "long_call_debit_bias"
        title = "Positive VEX — prefer long call debit near ATM"
        tilt = (
            "Prefer defined-risk long calls (call debit) near ATM; "
            "avoid naked short calls into a vol spike."
        )
        score = abs(total_cr) + (25.0 if direction == "dealers_buy_delta" else 0.0)
    elif regime == "negative" or total_cr < 0:
        bias = "bearish_vol_pressure"
        structure = "hedge_or_put_debit_bias"
        title = "Negative VEX — prefer hedges / put debit bias"
        tilt = (
            "Vol-up dealer flow presses delta; prefer put debits or reduce short-vol. "
            "Avoid naked long calls without a hedge."
        )
        score = abs(total_cr) + (25.0 if direction == "dealers_sell_delta" else 0.0)
    else:
        return None

    shock_txt = ""
    if shock:
        shock_txt = (
            f" IV+{shock.get('vol_points')} shock → {direction or 'flat'} "
            f"(Δδ ≈ {shock.get('delta_shares')})."
        )
    line_txt = ""
    if spot is not None and line is not None:
        side = "above" if spot >= line else "below"
        line_txt = f" Spot {spot:.1f} is {side} Vanna Line {line:.1f}."

    reasoning = (
        f"Net VEX {total_cr:+.2f} ₹Cr ({regime or 'flat'} regime).{shock_txt}{line_txt} {tilt}"
    )

    focus: list[float] = []
    if atm is not None:
        focus.append(atm)

    return {
        "id": "vanna_regime",
        "structure": structure,
        "title": title,
        "bias": bias,
        "strikes_focus": focus,
        "score": round(score, 4),
        "reasoning": reasoning,
        "pricing_hint": "Refine strikes/premium on Pricing Engine (call debit or put debit).",
    }


def _wall_reasoning(
    *,
    spot: float | None,
    call_wall: float | None,
    put_wall: float | None,
    wall: float | None,
    kind: str,
    regime: str,
    total_cr: float,
) -> str:
    cw = f"{call_wall:.0f}" if call_wall is not None else "—"
    pw = f"{put_wall:.0f}" if put_wall is not None else "—"
    sp = f"{spot:.1f}" if spot is not None else "—"
    w = f"{wall:.0f}" if wall is not None else "—"
    if kind == "call":
        if total_cr >= 0 or regime == "positive":
            tilt = (
                f"Peak CE density at {w}. Into the wall, take profits on long calls "
                f"or use call credits above the wall; dips below remain more tradable while VEX stays positive."
            )
        else:
            tilt = (
                f"Peak CE density at {w} with negative VEX — upside is supply-heavy; "
                f"prefer call credits above the wall or stay light on naked calls."
            )
    else:
        if total_cr < 0 or regime == "negative":
            tilt = (
                f"Peak PE density at {w} with negative/pressured VEX — "
                f"prefer put debits toward the wall; avoid naked short puts through it."
            )
        else:
            tilt = (
                f"Peak PE density at {w}. Put wall is a demand zone; "
                f"bull put credits above the wall fit better than short puts through it."
            )
    return f"Spot {sp}; CE wall {cw}; PE wall {pw}. {tilt}"


def _wall_idea(snap: dict[str, Any]) -> dict[str, Any] | None:
    spot = _f(snap.get("spot"))
    call_wall = _f(snap.get("call_wall"))
    put_wall = _f(snap.get("put_wall"))
    regime = str(snap.get("vanna_regime") or "")
    total_cr = _f(snap.get("total_vex_cr")) or 0.0

    if spot is None:
        return None

    candidates: list[tuple[float, str, float]] = []
    if call_wall is not None:
        candidates.append((abs(call_wall - spot), "call", call_wall))
    if put_wall is not None:
        candidates.append((abs(put_wall - spot), "put", put_wall))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    _, kind, wall = candidates[0]

    if kind == "call":
        title = f"CE wall {wall:.0f} — respect call supply"
        bias = "fade_into_call_wall"
        structure = "call_wall_respect"
        score = 40.0 + max(0.0, 10.0 - abs(wall - spot) / 50.0)
    else:
        title = f"PE wall {wall:.0f} — respect put demand"
        bias = "respect_put_wall"
        structure = "put_wall_respect"
        score = 35.0 + max(0.0, 10.0 - abs(wall - spot) / 50.0)

    return {
        "id": f"vanna_{kind}_wall",
        "structure": structure,
        "title": title,
        "bias": bias,
        "strikes_focus": [wall],
        "score": round(score, 4),
        "reasoning": _wall_reasoning(
            spot=spot,
            call_wall=call_wall,
            put_wall=put_wall,
            wall=wall,
            kind=kind,
            regime=regime,
            total_cr=total_cr,
        ),
        "pricing_hint": "Map wall strikes on Pricing Engine for credit/debit sizing.",
    }


def _pivot_idea(snap: dict[str, Any]) -> dict[str, Any] | None:
    spot = _f(snap.get("spot"))
    line = _f(snap.get("vanna_line"))
    regime = str(snap.get("vanna_regime") or "")
    total_cr = _f(snap.get("total_vex_cr")) or 0.0
    if spot is None or line is None:
        return None

    dist = spot - line
    if abs(dist) < 1e-6:
        title = f"At Vanna Line {line:.1f} — pivot zone"
        bias = "pivot"
        structure = "vanna_line_pivot"
        tilt = (
            "Spot is hugging the Vanna Line (zero aggregate ₹ VEX). "
            "Wait for a clean reclaim/reject before adding directional premium."
        )
        score = 30.0
    elif spot > line:
        title = f"Above Vanna Line {line:.1f} — dips toward line"
        bias = "bullish_above_line"
        structure = "vanna_line_support"
        if total_cr >= 0 or regime == "positive":
            tilt = (
                f"Spot {spot:.1f} is above the line with positive VEX — "
                f"dips toward {line:.1f} are preferred buy/debit zones; hold bias until line breaks."
            )
            score = 45.0 + min(20.0, abs(dist) / 10.0)
        else:
            tilt = (
                f"Spot is above the line but VEX is not supportive — "
                f"treat {line:.1f} as fragile support; size smaller or hedge."
            )
            score = 28.0
    else:
        title = f"Below Vanna Line {line:.1f} — reclaim or hedge"
        bias = "cautious_below_line"
        structure = "vanna_line_resist"
        if total_cr < 0 or regime == "negative":
            tilt = (
                f"Spot {spot:.1f} is below the line with negative VEX — "
                f"vol-up flow is unfriendly to naked longs. Wait for reclaim of {line:.1f} "
                f"or use put debit hedges."
            )
            score = 45.0 + min(20.0, abs(dist) / 10.0)
        else:
            tilt = (
                f"Spot is below the line despite non-negative VEX — "
                f"prioritize reclaim of {line:.1f} before chasing upside premium."
            )
            score = 32.0

    reasoning = (
        f"Vanna Line {line:.1f}; spot {spot:.1f} ({dist:+.1f} pts). "
        f"Regime {regime or 'n/a'}, net VEX {total_cr:+.2f} ₹Cr. {tilt}"
    )

    return {
        "id": "vanna_line_pivot",
        "structure": structure,
        "title": title,
        "bias": bias,
        "strikes_focus": [line],
        "score": round(score, 4),
        "reasoning": reasoning,
        "pricing_hint": "Use Pricing Engine once bias/strikes are chosen.",
    }


def build_vanna_recommendations(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Ranked positioning ideas from a Vanna snapshot dict."""
    max_ideas = int(_cfg().get("max_ideas", 3))
    disc = _disclaimer()

    ideas: list[dict[str, Any]] = []
    for builder in (_regime_idea, _wall_idea, _pivot_idea):
        try:
            idea = builder(snapshot)
        except Exception:
            idea = None
        if not idea:
            continue
        idea["disclaimer"] = disc
        idea["underlying"] = snapshot.get("underlying")
        idea["expiry"] = snapshot.get("expiry")
        idea["vex_context"] = {
            "regime": snapshot.get("vanna_regime"),
            "vanna_line": snapshot.get("vanna_line"),
            "spot": snapshot.get("spot"),
            "call_wall": snapshot.get("call_wall"),
            "put_wall": snapshot.get("put_wall"),
            "total_vex_cr": snapshot.get("total_vex_cr"),
            "shock_1": _shock_one(snapshot.get("iv_shocks")),
        }
        ideas.append(idea)

    ideas.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    return ideas[:max_ideas]
