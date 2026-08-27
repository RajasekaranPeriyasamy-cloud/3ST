"""Expiry Magnet — which strike is pulling settlement, and how hard.

The question this answers is narrower than "where is the pin": it is *will
dealer hedging drag settlement to a strike by expiry*. Two things drive that,
and the metric fuses them.

Pressure
--------
A strike's pull is its dealer gamma **weighted by the chance price settles
there**::

    P(K) ∝ Γ(K) · exp( −(K − S)² / 2σ² )        normalised so max(P) = 1.0

σ is the option market's own expected move to expiry (the ATM straddle), so the
weight is a normal density centred on spot with the market's width — not an
arbitrary decay. A large gamma stack four expected-moves away is not a magnet,
and this says so; raw gamma ranking does not.

**Pressure and raw gamma genuinely invert.** A nearer strike with less gamma can
outrank a farther one with more. That is the whole point of ranking on pressure,
and it is why both columns are reported rather than one.

Time boost
----------
σ shrinks as √t, and peak pressure scales as 1/σ, so the same book exerts

    boost = √( t_ref / t_now )

more pull at 1 DTE than a week out. This is why pins tighten into expiry: not
because positioning changed, but because the distribution collapsed onto it.

Conviction
----------
Unlike :mod:`options.pin_lock`, which deliberately refuses a blended score, this
module **does** emit one — the desk it models is built around it. The discipline
is different rather than absent: every input, weight and normalisation is on the
payload, and ``calibrated: False`` rides along until the ``daily_pin`` trail can
fit the weights against outcomes. Read it as a summary of the four components
beside it, not as an independent measurement.

Pure functions over numbers already on the snapshot — no I/O.
"""

from __future__ import annotations

import math
from typing import Any, Literal

PinState = Literal["no_pin", "shifting", "stable", "locked"]

#: Reference horizon for the time boost, in trading days. A pin one day out
#: exerts √6 ≈ 2.45× the pull of the same book six sessions out.
TIME_BOOST_REFERENCE_DTE = 6.0
#: Expiry day still has hours in it; without a floor the boost goes to infinity.
MIN_DTE_FLOOR = 0.2
#: Beyond this the number stops being informative and starts being alarming.
MAX_TIME_BOOST = 6.0

#: Leader must hold at least this share of total pressure to count as a pin.
MIN_LEADER_SHARE = 0.18
#: Margin over the runner-up, in pressure, for "dominates".
DOMINANT_MARGIN = 0.20
#: Share of session ticks the leader must have held to count as entrenched.
HELD_STABILITY_PCT = 70.0

#: Conviction weights. Named and exposed because the score is provisional —
#: these are reasoned, not fitted.
CONVICTION_WEIGHTS: dict[str, float] = {
    "margin": 0.35,      # how far clear of the runner-up
    "stability": 0.30,   # has this leader actually held today
    "proximity": 0.20,   # distance to spot, as a fraction of expiry σ
    "time": 0.15,        # how hard the clock is squeezing
}

_STATE_LABEL: dict[str, str] = {
    "no_pin": "No pin",
    "shifting": "Shifting",
    "stable": "Stable",
    "locked": "Locked",
}

_STATE_DESC: dict[str, str] = {
    "no_pin": "Gamma is spread, nothing anchors.",
    "shifting": "A leader exists, but it keeps changing.",
    "stable": "A clear leader, not yet entrenched.",
    "locked": (
        "Dealer gamma is concentrated at a single strike with no close rival, and it "
        "has stayed there. Hedging flow is organised around it, so pushes away tend "
        "to get absorbed."
    ),
}


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def time_boost(dte: float | None, *, reference_dte: float = TIME_BOOST_REFERENCE_DTE) -> float | None:
    """√(t_ref / t_now), clamped. See the module docstring for why √."""
    t = _f(dte)
    if t is None:
        return None
    return round(min(math.sqrt(reference_dte / max(t, MIN_DTE_FLOOR)), MAX_TIME_BOOST), 2)


def pressure_by_strike(
    strikes: list[dict[str, Any]],
    spot: float | None,
    sigma_pts: float | None,
) -> list[dict[str, Any]]:
    """Every strike's pressure, gamma and net gamma, ranked by pressure.

    ``gamma`` is **gross** — |CE γ| + |PE γ| — the magnitude dealers must hedge.
    ``net_gamma`` keeps the sign gross drops, because a magnet sitting on short
    dealer gamma is a different read from one on long, and the two are decoupled.
    """
    s, sig = _f(spot), _f(sigma_pts)
    if s is None or not sig or sig <= 0:
        return []

    rows: list[dict[str, Any]] = []
    for row in strikes or []:
        k = _f(row.get("strike"))
        if k is None:
            continue
        ce, pe = _f(row.get("ce_gex")) or 0.0, _f(row.get("pe_gex")) or 0.0
        gross = abs(ce) + abs(pe)
        d = k - s
        weight = math.exp(-(d * d) / (2.0 * sig * sig))
        rows.append(
            {
                "strike": k,
                "gamma": round(gross, 2),
                "net_gamma": round(_f(row.get("net_gex")) or 0.0, 2),
                "distance_pts": round(d, 2),
                "distance_sigma": round(d / sig, 3),
                "weight": round(weight, 6),
                "_raw": gross * weight,
            }
        )

    peak = max((r["_raw"] for r in rows), default=0.0)
    total_gamma = sum(r["gamma"] for r in rows) or 1.0
    for r in rows:
        r["pressure"] = round(r.pop("_raw") / peak, 4) if peak > 0 else 0.0
        # Reported alongside so the inversion against pressure stays visible.
        r["gamma_share"] = round(r["gamma"] / total_gamma, 4)
    rows.sort(key=lambda r: (-r["pressure"], abs(r["distance_pts"])))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def leader_stability_pct(
    history: list[dict[str, Any]] | None,
    leader: float | None,
    strike_step: float,
) -> float | None:
    """Share of session ticks whose pin sat within one step of ``leader``.

    ``None`` when there is no history to judge — an unmeasured leader must not
    read as an unstable one.
    """
    if leader is None or not history:
        return None
    step = max(_f(strike_step) or 0.0, 1.0)
    seen = [p for p in (_f(h.get("pin_strike")) for h in history) if p is not None]
    if not seen:
        return None
    held = sum(1 for p in seen if abs(p - leader) <= step + 1e-9)
    return round(100.0 * held / len(seen), 1)


def classify_pin_state(
    *,
    leader_share: float | None,
    margin: float | None,
    stability_pct: float | None,
) -> PinState:
    """NO PIN → SHIFTING → STABLE → LOCKED, first condition that fits."""
    if leader_share is None or leader_share < MIN_LEADER_SHARE:
        return "no_pin"
    if stability_pct is not None and stability_pct < HELD_STABILITY_PCT:
        return "shifting"
    if margin is not None and margin >= DOMINANT_MARGIN and (stability_pct or 0) >= HELD_STABILITY_PCT:
        return "locked"
    return "stable"


def _conviction(
    *,
    margin: float | None,
    stability_pct: float | None,
    distance_sigma: float | None,
    boost: float | None,
) -> dict[str, Any]:
    """0–100 from four normalised components. Provisional — weights are reasoned."""
    parts: dict[str, float | None] = {
        "margin": None if margin is None else min(max(margin / DOMINANT_MARGIN, 0.0), 1.0),
        "stability": None if stability_pct is None else min(max(stability_pct / 100.0, 0.0), 1.0),
        # Right on top of spot = 1.0; a full expected move away = 0.
        "proximity": (
            None if distance_sigma is None else min(max(1.0 - abs(distance_sigma), 0.0), 1.0)
        ),
        "time": (
            None
            if boost is None
            else min(max((boost - 1.0) / (MAX_TIME_BOOST - 1.0), 0.0), 1.0)
        ),
    }
    usable = {k: v for k, v in parts.items() if v is not None}
    if not usable:
        return {"score": None, "parts": parts, "weights": CONVICTION_WEIGHTS, "calibrated": False}
    # Re-weight over what is measurable rather than scoring a missing input zero.
    wsum = sum(CONVICTION_WEIGHTS[k] for k in usable) or 1.0
    score = sum(CONVICTION_WEIGHTS[k] * v for k, v in usable.items()) / wsum
    return {
        "score": round(100.0 * score, 1),
        "parts": {k: (None if v is None else round(v, 4)) for k, v in parts.items()},
        "weights": CONVICTION_WEIGHTS,
        # Until daily_pin can fit these against outcomes, say so on every payload.
        "calibrated": False,
    }


def build_expiry_magnet(
    *,
    strikes: list[dict[str, Any]],
    spot: float | None,
    sigma_pts: float | None,
    dte: int | float | None,
    strike_step: float,
    history: list[dict[str, Any]] | None = None,
    top_n: int = 5,
) -> dict[str, Any] | None:
    """Assemble the desk payload, or ``None`` when pressure cannot be computed."""
    ranked = pressure_by_strike(strikes, spot, sigma_pts)
    if not ranked:
        return None

    leader = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    margin = None if runner is None else round(leader["pressure"] - runner["pressure"], 4)

    total_pressure = sum(r["pressure"] for r in ranked) or 1.0
    leader_share = round(leader["pressure"] / total_pressure, 4)

    stability = leader_stability_pct(history, leader["strike"], strike_step)
    boost = time_boost(dte)
    state = classify_pin_state(
        leader_share=leader_share, margin=margin, stability_pct=stability
    )
    conviction = _conviction(
        margin=margin,
        stability_pct=stability,
        distance_sigma=leader["distance_sigma"],
        boost=boost,
    )

    return {
        "pin": leader["strike"],
        "pin_gamma": leader["gamma"],
        "pin_net_gamma": leader["net_gamma"],
        "distance_pts": leader["distance_pts"],
        "distance_sigma": leader["distance_sigma"],
        "sigma_pts": _f(sigma_pts),
        "dte": dte,
        "time_boost": boost,
        "time_boost_reference_dte": TIME_BOOST_REFERENCE_DTE,
        "runner_up": None if runner is None else runner["strike"],
        "runner_up_pressure": None if runner is None else runner["pressure"],
        "margin": margin,
        "leader_share": leader_share,
        "stability_pct": stability,
        "state": state,
        "state_label": _STATE_LABEL[state],
        "state_description": _STATE_DESC[state],
        "conviction": conviction,
        "top": ranked[: max(1, top_n)],
        "ladder": ranked,
        "thresholds": {
            "min_leader_share": MIN_LEADER_SHARE,
            "dominant_margin": DOMINANT_MARGIN,
            "held_stability_pct": HELD_STABILITY_PCT,
        },
    }
