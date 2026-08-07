"""Gamma squeeze / momentum scoring (v1) — thin layer on Gamma Density inputs.

Composite 0–100 score from five weighted components (weights sum to 100):

* **gex** (30) — sign/magnitude of ``total_gex`` vs session ``|gex|`` history
* **squeeze** (25) — spot proximity to call/put wall or pin in strike steps,
  combined with GEX sign/trend
* **oi_flow** (20) — CE/PE ΔOI from strike rows when present; else ~neutral
* **iv** (15) — short-horizon Δ ATM IV when ≥2 history points carry ``atm_iv``
* **structure** (10) — high HHI near pin/cliff amplifies; diffuse dampens

Labels (directional bias, not a trade signal):

* ``score > 60`` → ``bullish`` (squeeze-up bias)
* ``40 <= score <= 60`` → ``neutral``
* ``score < 40`` → ``bearish`` (fade / downside pressure)

Does not replace GEX math; null-safe when history or ΔOI are missing.
"""

from __future__ import annotations

from typing import Any

# Max contribution points (must sum to 100)
WEIGHT_GEX = 30.0
WEIGHT_SQUEEZE = 25.0
WEIGHT_OI_FLOW = 20.0
WEIGHT_IV = 15.0
WEIGHT_STRUCTURE = 10.0

NEAR_WALL_STEPS = 3.0
NEAR_PIN_STEPS = 2.0
NEAR_CLIFF_STEPS = 2.0


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _label(score: float) -> str:
    if score > 60.0:
        return "bullish"
    if score < 40.0:
        return "bearish"
    return "neutral"


def _history_gex_values(history: list[dict[str, Any]] | None) -> list[float]:
    out: list[float] = []
    for p in history or []:
        g = _safe_float(p.get("total_gex"))
        if g is not None:
            out.append(g)
    return out


def _gex_falling(history: list[dict[str, Any]] | None, current: float) -> bool:
    vals = _history_gex_values(history)
    if len(vals) < 1:
        return False
    # Compare current to recent prior tick
    prev = vals[-1]
    # If current already in history as last point, use previous
    if abs(prev - current) < 1e-6 and len(vals) >= 2:
        prev = vals[-2]
    return current < prev


def _abs_gex_percentile(history: list[dict[str, Any]] | None, current: float) -> float | None:
    """Percentile rank of |current| among session |gex| values (0–1)."""
    abs_vals = [abs(g) for g in _history_gex_values(history)]
    abs_vals.append(abs(current))
    if len(abs_vals) < 2:
        return None
    cur = abs(current)
    n_le = sum(1 for v in abs_vals if v <= cur + 1e-12)
    return n_le / len(abs_vals)


def _score_gex(
    total_gex: float,
    history: list[dict[str, Any]] | None,
    distance_to_flip: float | None,
    spot: float | None,
) -> tuple[float, list[str]]:
    """0–100: neg GEX → up-bias; pos GEX → fade/down-bias; magnitude amplifies."""
    drivers: list[str] = []
    # Sign base: neg → bullish tilt, pos → bearish tilt
    if total_gex < 0:
        base = 62.0
        drivers.append("negative GEX")
    elif total_gex > 0:
        base = 38.0
        drivers.append("positive GEX")
    else:
        base = 50.0

    pct = _abs_gex_percentile(history, total_gex)
    if pct is not None:
        # Push further from 50 as |gex| is large vs session
        amp = (pct - 0.5) * 28.0  # up to ±14
        if total_gex < 0:
            base += amp
        elif total_gex > 0:
            base -= amp
        if pct >= 0.75:
            drivers.append("elevated |GEX| vs session")

    if _gex_falling(history, total_gex):
        base += 6.0
        drivers.append("GEX falling")

    # Near flip with material |GEX| → squeeze risk nudge toward extremes
    if (
        distance_to_flip is not None
        and spot
        and spot > 0
        and abs(float(distance_to_flip)) / float(spot) < 0.005
        and abs(total_gex) > 0
    ):
        base += 4.0 if total_gex < 0 else -4.0
        drivers.append("near flip")

    return _clamp(base), drivers


def _steps_away(spot: float, level: float | None, strike_step: float) -> float | None:
    if level is None or strike_step <= 0:
        return None
    return abs(float(spot) - float(level)) / float(strike_step)


def _score_squeeze(
    *,
    spot: float,
    total_gex: float,
    call_wall: float | None,
    put_wall: float | None,
    pin_strike: float | None,
    strike_step: float,
    history: list[dict[str, Any]] | None,
) -> tuple[float, list[str]]:
    """0–100: near call wall/pin + neg/falling GEX → up; near put wall + pos → down."""
    drivers: list[str] = []
    step = max(float(strike_step), 1.0)
    d_call = _steps_away(spot, call_wall, step)
    d_put = _steps_away(spot, put_wall, step)
    d_pin = _steps_away(spot, pin_strike, step)
    falling = _gex_falling(history, total_gex)
    neg_or_falling = total_gex < 0 or falling

    near_call = d_call is not None and d_call <= NEAR_WALL_STEPS
    near_put = d_put is not None and d_put <= NEAR_WALL_STEPS
    near_pin = d_pin is not None and d_pin <= NEAR_PIN_STEPS

    score = 50.0

    if near_call and neg_or_falling:
        # Closer → stronger up bias
        prox = 1.0 - (d_call or 0.0) / NEAR_WALL_STEPS
        score = 72.0 + prox * 20.0
        drivers.append("near call wall")
        if total_gex < 0:
            drivers.append("neg GEX squeeze-up")
        elif falling:
            drivers.append("falling GEX near call wall")
    elif near_pin and neg_or_falling:
        prox = 1.0 - (d_pin or 0.0) / NEAR_PIN_STEPS
        score = 68.0 + prox * 18.0
        drivers.append("near pin")
        if total_gex < 0:
            drivers.append("neg GEX at pin")
    elif near_put and total_gex > 0:
        prox = 1.0 - (d_put or 0.0) / NEAR_WALL_STEPS
        score = 28.0 - prox * 18.0
        drivers.append("near put wall")
        drivers.append("pos GEX fade")
    elif near_call:
        score = 58.0
        drivers.append("near call wall")
    elif near_put:
        score = 42.0
        drivers.append("near put wall")
    else:
        # Far from walls — soft neutral
        far = True
        if d_call is not None and d_call < 8:
            far = False
        if d_put is not None and d_put < 8:
            far = False
        if far:
            score = 50.0

    return _clamp(score), drivers


def _score_oi_flow(strikes: list[dict[str, Any]] | None) -> tuple[float, list[str]]:
    """0–100 from CE/PE ΔOI; missing → neutral mid (~50)."""
    drivers: list[str] = []
    if not strikes:
        return 50.0, []

    ce_sum = 0.0
    pe_sum = 0.0
    n_ce = 0
    n_pe = 0
    for row in strikes:
        ce = _safe_float(row.get("ce_doi"))
        pe = _safe_float(row.get("pe_doi"))
        if ce is not None:
            ce_sum += ce
            n_ce += 1
        if pe is not None:
            pe_sum += pe
            n_pe += 1

    if n_ce == 0 and n_pe == 0:
        return 50.0, []

    # Positive CE ΔOI / negative PE ΔOI → bullish tilt (call demand / put unwind)
    net = ce_sum - pe_sum
    scale = max(abs(ce_sum) + abs(pe_sum), 1.0)
    # Map net/scale ∈ [-1,1] → score around 50 ± 35
    ratio = max(-1.0, min(1.0, net / scale))
    score = 50.0 + ratio * 35.0

    if ratio > 0.15:
        drivers.append("call ΔOI lead")
    elif ratio < -0.15:
        drivers.append("put ΔOI lead")

    return _clamp(score), drivers


def _score_iv(
    atm_iv: float | None,
    history: list[dict[str, Any]] | None,
) -> tuple[float, list[str]]:
    """0–100 from Δ ATM IV when ≥2 history points have atm_iv; else neutral."""
    drivers: list[str] = []
    ivs: list[float] = []
    for p in history or []:
        v = _safe_float(p.get("atm_iv"))
        if v is not None:
            ivs.append(v)
    cur = _safe_float(atm_iv)
    if cur is not None:
        ivs.append(cur)

    if len(ivs) < 2:
        return 50.0, []

    # Prefer prior history point vs current
    prev = ivs[-2]
    delta = ivs[-1] - prev
    # ~±2 vol points → full swing
    score = 50.0 + max(-2.0, min(2.0, delta)) * 15.0
    if delta > 0.25:
        drivers.append(f"ATM IV rising ({delta:+.1f})")
    elif delta < -0.25:
        drivers.append(f"ATM IV falling ({delta:+.1f})")
    return _clamp(score), drivers


def _score_structure(
    *,
    concentration: dict[str, Any] | None,
    spot: float,
    strike_step: float,
) -> tuple[float, list[str]]:
    """0–100: concentrated near pin/cliff amplifies directional lean via |score-50|;
    we encode amplify as moving away from 50 toward regime implied by proximity.
    """
    drivers: list[str] = []
    conc = concentration or {}
    hhi = _safe_float(conc.get("hhi"))
    band = conc.get("band")
    pin = _safe_float(conc.get("pin_strike"))
    cliff = _safe_float(conc.get("cliff_strike"))
    step = max(float(strike_step), 1.0)

    # Base from band: concentrated → more extreme (we'll blend toward 60 as "active"),
    # diffuse → dampen toward 50.
    if band == "concentrated" or (hhi is not None and hhi >= 0.25):
        score = 58.0
        drivers.append("concentrated HHI")
    elif band == "diffuse" or (hhi is not None and hhi < 0.12):
        score = 50.0
        drivers.append("diffuse HHI")
        return score, drivers
    else:
        score = 52.0

    near_pin = pin is not None and abs(spot - pin) / step <= NEAR_PIN_STEPS
    near_cliff = cliff is not None and abs(spot - cliff) / step <= NEAR_CLIFF_STEPS
    if near_pin or near_cliff:
        score = min(72.0, score + 12.0)
        if near_pin:
            drivers.append("near pin structure")
        if near_cliff:
            drivers.append("near cliff")

    return _clamp(score), drivers


def compute_gamma_momentum(
    snapshot_like_inputs: dict[str, Any] | None = None,
    /,
    **kwargs: Any,
) -> dict[str, Any]:
    """Compute squeeze/momentum score from snapshot-like fields.

    Accepts a dict and/or keyword overrides. Expected keys:

    ``spot``, ``total_gex``, ``call_wall``, ``put_wall``, ``strike_step``,
    ``atm_iv``, ``strikes``, ``concentration``, ``history``,
    ``distance_to_flip``, ``flip_level`` (optional).

    Returns
    -------
    dict
        ``score`` (0–100 float), ``label``, ``components`` (0–100 each),
        ``drivers`` (short strings, top contributors).
    """
    src: dict[str, Any] = dict(snapshot_like_inputs or {})
    src.update(kwargs)

    spot = _safe_float(src.get("spot")) or 0.0
    total_gex = _safe_float(src.get("total_gex"))
    if total_gex is None:
        total_gex = 0.0
    call_wall = _safe_float(src.get("call_wall"))
    put_wall = _safe_float(src.get("put_wall"))
    strike_step = _safe_float(src.get("strike_step")) or 50.0
    atm_iv = _safe_float(src.get("atm_iv"))
    distance_to_flip = _safe_float(src.get("distance_to_flip"))
    strikes = src.get("strikes") if isinstance(src.get("strikes"), list) else []
    concentration = src.get("concentration") if isinstance(src.get("concentration"), dict) else {}
    history = src.get("history") if isinstance(src.get("history"), list) else []
    pin_strike = _safe_float(concentration.get("pin_strike"))

    gex_s, gex_d = _score_gex(total_gex, history, distance_to_flip, spot)
    sq_s, sq_d = _score_squeeze(
        spot=spot,
        total_gex=total_gex,
        call_wall=call_wall,
        put_wall=put_wall,
        pin_strike=pin_strike,
        strike_step=strike_step,
        history=history,
    )
    oi_s, oi_d = _score_oi_flow(strikes)
    iv_s, iv_d = _score_iv(atm_iv, history)
    st_s, st_d = _score_structure(
        concentration=concentration, spot=spot, strike_step=strike_step
    )

    # Weighted sum of 0–100 components → 0–100 composite
    score = (
        gex_s * (WEIGHT_GEX / 100.0)
        + sq_s * (WEIGHT_SQUEEZE / 100.0)
        + oi_s * (WEIGHT_OI_FLOW / 100.0)
        + iv_s * (WEIGHT_IV / 100.0)
        + st_s * (WEIGHT_STRUCTURE / 100.0)
    )
    score = round(_clamp(score), 1)
    label = _label(score)

    # Rank drivers by how far their component sits from neutral, keep top ~5
    ranked = sorted(
        [
            (abs(gex_s - 50.0), gex_d),
            (abs(sq_s - 50.0), sq_d),
            (abs(oi_s - 50.0), oi_d),
            (abs(iv_s - 50.0), iv_d),
            (abs(st_s - 50.0), st_d),
        ],
        key=lambda x: x[0],
        reverse=True,
    )
    drivers: list[str] = []
    for _, parts in ranked:
        for d in parts:
            if d not in drivers:
                drivers.append(d)
            if len(drivers) >= 5:
                break
        if len(drivers) >= 5:
            break

    return {
        "score": score,
        "label": label,
        "components": {
            "gex": round(gex_s, 1),
            "squeeze": round(sq_s, 1),
            "oi_flow": round(oi_s, 1),
            "iv": round(iv_s, 1),
            "structure": round(st_s, 1),
        },
        "drivers": drivers,
    }
