"""Structural regime: confluence, levels in σ, and a state classifier.

Three readings the desk could previously only be eyeballed into:

1. **Confluence** — how far the gamma pin sits from the volume POC. Gamma says
   where dealers *must* hedge; volume says where business *actually* happened.
   When they coincide a level has both mechanical and participatory support;
   when they do not, the magnet is structural only.
2. **Levels in σ** — every key level expressed as a multiple of the session's
   own expected move, so distances are measured against the band the market is
   pricing rather than judged by eye. 110 points means nothing on its own; 0.63σ
   does.
3. **A state classifier** over the feature vector below.

What this module does *not* do
------------------------------
It names a **market-structure state and shows its evidence**. It does not suggest
positions, sizing, or direction, and must not grow into that — the desk's job is
to describe the book, and what to do about it is the operator's call. Adding a
"suggested trade" field here would be the wrong kind of authority for a tool that
cannot see the account behind it.

Everything is a pure function of numbers already on the snapshot, so the whole
module is testable offline and re-derivable from a stored payload.
"""

from __future__ import annotations

from typing import Any, Literal

RegimeState = Literal[
    "unmeasured",
    "pinned",
    "coiled_box",
    "short_gamma_trend",
    "long_gamma_drift",
    "transition",
    "mixed",
]

#: Flip closer than this (in σ of the session's expected move) means one ordinary
#: move flips the hedging regime — the book is near a behavioural boundary.
FLIP_NEAR_SIGMA = 0.75
#: Pin and POC within this many strike steps count as the same level.
CONFLUENCE_STEPS = 1.0
#: Share of window minutes inside the pin band before containment "holds".
CONTAINMENT_HOLDS_PCT = 80.0
#: The footprint engine's own balanced boundary, restated so this module is
#: readable on its own.
OVL_BALANCED = 75.0

_LABELS: dict[str, str] = {
    "unmeasured": "Unmeasured",
    "pinned": "Pinned",
    "coiled_box": "Coiled box",
    "short_gamma_trend": "Short-gamma trend",
    "long_gamma_drift": "Long-gamma drift",
    "transition": "At the flip",
    "mixed": "Mixed",
}

_DESCRIPTIONS: dict[str, str] = {
    "unmeasured": "Not enough of the book is measurable to name a state.",
    "pinned": (
        "Dealers are long gamma at a dominant strike and price is holding to it — "
        "hedging dampens moves back toward the level."
    ),
    "coiled_box": (
        "Price is contained between walls while dealers are short gamma. Containment "
        "is being supplied by positioning, not by dealer hedging, so it holds until "
        "it does not."
    ),
    "short_gamma_trend": (
        "Dealers are short gamma with room to run — hedging chases the move rather "
        "than fading it."
    ),
    "long_gamma_drift": (
        "Dealers are long gamma away from a dominant strike — moves are dampened "
        "without a specific magnet."
    ),
    "transition": (
        "Spot sits within one ordinary move of the gamma flip; dealer behaviour "
        "changes sign either side of it."
    ),
    "mixed": "The structural signals do not agree on a single state.",
}


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # reject NaN


def levels_in_sigma(
    spot: float | None,
    sigma1_pts: float | None,
    levels: dict[str, float | None],
) -> dict[str, dict[str, Any]]:
    """Each level as points and σ from spot, signed (+ above, − below).

    σ is the session's own 1-sigma expected move, so "how far" is expressed in
    the units the option market is actually pricing rather than index points,
    which mean different things at different vol.
    """
    out: dict[str, dict[str, Any]] = {}
    s = _f(spot)
    sig = _f(sigma1_pts)
    for key, raw in (levels or {}).items():
        lvl = _f(raw)
        if lvl is None or s is None:
            out[key] = {"level": lvl, "pts": None, "sigma": None}
            continue
        pts = lvl - s
        out[key] = {
            "level": round(lvl, 2),
            "pts": round(pts, 2),
            "sigma": round(pts / sig, 3) if sig and sig > 0 else None,
        }
    return out


def compute_confluence(
    *,
    pin: float | None,
    poc: float | None,
    vah: float | None,
    val: float | None,
    strike_step: float,
    confluence_steps: float = CONFLUENCE_STEPS,
) -> dict[str, Any]:
    """Gamma pin against the volume POC and value area.

    ``aligned`` means the two sit within ``confluence_steps`` strike steps —
    a magnet with both mechanical and participatory support. ``pin_in_value``
    asks the weaker question of whether the pin at least falls inside the range
    price was accepted in.

    Returns ``None`` fields rather than zeros when either side is missing:
    "we could not compare" is not "they agree".
    """
    step = max(_f(strike_step) or 0.0, 1.0)
    p, q = _f(pin), _f(poc)
    gap_pts = None if p is None or q is None else round(q - p, 2)
    gap_steps = None if gap_pts is None else round(gap_pts / step, 2)

    lo, hi = _f(val), _f(vah)
    pin_in_value = None
    if p is not None and lo is not None and hi is not None:
        pin_in_value = lo <= p <= hi

    return {
        "pin": p,
        "poc": q,
        "gap_pts": gap_pts,
        "gap_steps": gap_steps,
        "aligned": None if gap_steps is None else abs(gap_steps) <= confluence_steps,
        "confluence_steps": confluence_steps,
        "pin_in_value": pin_in_value,
        "value_area": None if lo is None or hi is None else {"low": lo, "high": hi,
                                                             "width_pts": round(hi - lo, 2)},
    }


def regime_features(
    *,
    gamma_regime: str | None,
    spot: float | None,
    sigma1_pts: float | None,
    flip_level: float | None,
    call_wall: float | None,
    put_wall: float | None,
    pin: float | None,
    pin_source: str | None,
    pin_gates_passed: bool | None,
    containment_pct: float | None,
    hhi_band: str | None,
    overlap_pct: float | None,
    confluence: dict[str, Any] | None,
) -> dict[str, Any]:
    """The structural feature vector every rule below reads from.

    Exposed on the payload deliberately: a rule set encoded downstream — in the
    UI, a notebook, or a future runner — should read the same numbers the
    built-in classifier does rather than re-deriving them and drifting.
    """
    sig = _f(sigma1_pts)
    s = _f(spot)
    flip = _f(flip_level)
    flip_sigma = None
    if flip is not None and s is not None and sig and sig > 0:
        flip_sigma = round((flip - s) / sig, 3)

    cw, pw = _f(call_wall), _f(put_wall)
    box_pts = None if cw is None or pw is None else round(abs(cw - pw), 2)
    box_sigma = None if box_pts is None or not sig or sig <= 0 else round(box_pts / sig, 3)
    in_box = None
    if cw is not None and pw is not None and s is not None:
        in_box = min(pw, cw) <= s <= max(pw, cw)

    return {
        "gamma_sign": (
            "positive" if str(gamma_regime).lower() == "positive"
            else "negative" if str(gamma_regime).lower() == "negative"
            else None
        ),
        "flip_sigma": flip_sigma,
        "flip_near": None if flip_sigma is None else abs(flip_sigma) <= FLIP_NEAR_SIGMA,
        "box_pts": box_pts,
        "box_sigma": box_sigma,
        "spot_in_box": in_box,
        "pin_is_dominant": None if pin_source is None else pin_source == "dominant",
        "pin_gates_passed": pin_gates_passed,
        "containment_pct": _f(containment_pct),
        "containment_holds": (
            None if _f(containment_pct) is None
            else _f(containment_pct) >= CONTAINMENT_HOLDS_PCT
        ),
        "hhi_band": hhi_band,
        "overlap_pct": _f(overlap_pct),
        "volume_balanced": (
            None if _f(overlap_pct) is None else _f(overlap_pct) >= OVL_BALANCED
        ),
        "confluence_aligned": (confluence or {}).get("aligned"),
        "confluence_gap_steps": (confluence or {}).get("gap_steps"),
        "pin": _f(pin),
    }


def classify_regime(features: dict[str, Any]) -> dict[str, Any]:
    """First matching rule wins; every state carries the evidence that fired it.

    The rules are ordered most-specific first. They describe the book — no rule
    may return a directional or positional suggestion (see the module docstring).
    """
    f = features or {}
    sign = f.get("gamma_sign")
    if sign is None:
        return _verdict("unmeasured", ["gamma regime unavailable"])

    long_gamma = sign == "positive"
    flip_near = f.get("flip_near")
    contained = f.get("containment_holds")
    dominant = f.get("pin_is_dominant")
    aligned = f.get("confluence_aligned")

    def sigma_txt() -> str:
        fs = f.get("flip_sigma")
        return f"flip {fs:+.2f}σ" if isinstance(fs, (int, float)) else "flip distance unknown"

    # 1. A pin worth the name: dealers dampening, a real gamma strike, price held.
    if long_gamma and dominant and contained:
        ev = ["dealers long gamma", "dominant gamma strike", "price contained at the pin"]
        if aligned:
            ev.append("POC confirms the pin")
        return _verdict("pinned", ev)

    # 2. Today's shape: contained, but by positioning rather than by hedging.
    #    Kept ahead of `transition` because it is the more actionable description
    #    of the same geometry, and ahead of `short_gamma_trend` because price is
    #    demonstrably not trending.
    if not long_gamma and contained:
        ev = ["dealers short gamma", "price contained between the walls"]
        if flip_near:
            ev.append(sigma_txt() + " — one ordinary move flips the regime")
        if aligned is False:
            ev.append("POC and pin disagree — magnet is structural, not traded")
        return _verdict("coiled_box", ev)

    # 3. Near the boundary with nothing holding price.
    if flip_near:
        return _verdict("transition", [sigma_txt(), "containment not established"])

    # 4. Short gamma with room: hedging chases.
    if not long_gamma:
        return _verdict("short_gamma_trend", ["dealers short gamma", sigma_txt()])

    # 5. Long gamma, no magnet.
    if long_gamma:
        return _verdict("long_gamma_drift", ["dealers long gamma", "no dominant pin holding"])

    return _verdict("mixed", ["signals disagree"])


def _verdict(state: str, evidence: list[str]) -> dict[str, Any]:
    return {
        "state": state,
        "label": _LABELS.get(state, state),
        "description": _DESCRIPTIONS.get(state, ""),
        "evidence": evidence,
    }


def build_regime_block(
    *,
    gamma_regime: str | None,
    spot: float | None,
    sigma1_pts: float | None,
    flip_level: float | None,
    call_wall: float | None,
    put_wall: float | None,
    strike_step: float,
    concentration: dict[str, Any] | None,
    pin_lock: dict[str, Any] | None,
    volume_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble confluence + σ levels + classification for the snapshot."""
    conc = concentration or {}
    pl = pin_lock or {}
    vp = volume_profile or {}
    comps = pl.get("components") or {}
    gates = pl.get("gates") or {}

    pin = pl.get("pin_mode") or conc.get("pin_strike")
    poc = vp.get("poc") if vp.get("available") else None

    confluence = compute_confluence(
        pin=pin,
        poc=poc,
        vah=vp.get("vah") if vp.get("available") else None,
        val=vp.get("val") if vp.get("available") else None,
        strike_step=strike_step,
    )

    sigma_levels = levels_in_sigma(
        spot,
        sigma1_pts,
        {
            "flip": flip_level,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "pin": pin,
            "poc": poc,
            "vah": vp.get("vah") if vp.get("available") else None,
            "val": vp.get("val") if vp.get("available") else None,
            "pos_gamma_peak": conc.get("pos_gamma_peak_strike"),
            "neg_gamma_peak": conc.get("neg_gamma_peak_strike"),
        },
    )

    features = regime_features(
        gamma_regime=gamma_regime,
        spot=spot,
        sigma1_pts=sigma1_pts,
        flip_level=flip_level,
        call_wall=call_wall,
        put_wall=put_wall,
        pin=pin,
        pin_source=conc.get("pin_source"),
        pin_gates_passed=gates.get("passed"),
        containment_pct=comps.get("containment_pct"),
        hhi_band=conc.get("band_label"),
        overlap_pct=vp.get("overlap_pct") if vp.get("available") else None,
        confluence=confluence,
    )

    verdict = classify_regime(features)
    return {
        **verdict,
        "sigma1_pts": _f(sigma1_pts),
        "confluence": confluence,
        "levels": sigma_levels,
        "features": features,
    }
