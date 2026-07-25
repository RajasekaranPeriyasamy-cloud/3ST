"""Gamma density desk — Γ×OI density, dealer GEX, gamma-flip and expected-move bands.

Enhancements (P0–P3)
--------------------
* Mid IV (bid/ask) with LTP fallback; BSM gamma with dividend yield ``q``.
* GEX(S) profile, all flip crossings, distance-to-flip, flip slope.
* Sticky-strike + sticky-delta flip levels.
* Hedge-flow translation for ±N pt moves; multi-expiry TTE-weighted GEX.
* Dealer sign modes (naive / customer / oi_delta); magnet walls; straddle EM.
* Intraday GEX/flip history + compact Vanna joint strip.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal

from config import GAMMA_DENSITY_DEFAULTS, INDEX_OPTIONS
from options.gamma_density_provider import (
    GammaDensityDataProvider,
    get_gamma_density_provider,
)
from options.greeks_engine import compute_greeks, d1_d2
from options.iv import implied_volatility, time_to_expiry_years
from options.oi_var import _flatten_chain_legs

CRORE = 1e7
SignMode = Literal["naive", "customer", "oi_delta"]
ConcentrationBand = Literal["concentrated", "mixed", "diffuse"]

# HHI bands + pin clarity (tunable; also overridable via GAMMA_DENSITY_DEFAULTS)
HHI_CONCENTRATED = 0.25
HHI_MIXED = 0.12
PIN_SHARE_THRESHOLD = 0.18
PIN_STABILITY_LOOKBACK = 12


def _cfg() -> dict[str, Any]:
    return dict(GAMMA_DENSITY_DEFAULTS)


def gamma_config() -> dict[str, Any]:
    d = _cfg()
    prov = get_gamma_density_provider()
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "refresh_seconds": d["refresh_seconds"],
        "strike_window": d["strike_window"],
        "risk_free_rate": d["risk_free_rate"],
        "dividend_yield": d["dividend_yield"],
        "sign_modes": ["naive", "customer", "oi_delta"],
        "sign_mode": d.get("sign_mode", "naive"),
        "hedge_moves_pts": list(d.get("hedge_moves_pts") or (50, 100)),
        "multi_expiry_count": int(d.get("multi_expiry_count") or 2),
        "provider": prov.name,
        "requires_session": prov.requires_session(),
    }


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bsm_price(
    spot: float,
    strike: float,
    tte: float,
    iv: float,
    option_type: str,
    r: float,
    q: float,
) -> float | None:
    pair = d1_d2(spot, strike, tte, iv, r, q)
    if pair is None:
        return None
    d1, d2 = pair
    disc_q = math.exp(-q * tte)
    disc_r = math.exp(-r * tte)
    if option_type.upper() == "CE":
        return spot * disc_q * _norm_cdf(d1) - strike * disc_r * _norm_cdf(d2)
    return strike * disc_r * _norm_cdf(-d2) - spot * disc_q * _norm_cdf(-d1)


def _implied_vol(
    price: float,
    spot: float,
    strike: float,
    tte: float,
    option_type: str,
    r: float,
    q: float,
) -> float | None:
    """IV with continuous dividend yield (Newton); falls back to q=0 vollib."""
    if q and abs(q) > 1e-12:
        iv = 0.2
        for _ in range(40):
            px = _bsm_price(spot, strike, tte, iv, option_type, r, q)
            if px is None:
                break
            greeks = compute_greeks(
                spot=spot,
                strike=strike,
                tte_years=tte,
                iv=iv,
                option_type=option_type,
                risk_free_rate=r,
                dividend_yield=q,
            )
            vega = greeks.get("vega")
            if vega is None or abs(float(vega)) < 1e-12:
                break
            vega_sigma = float(vega) * 100.0
            diff = px - price
            iv = max(1e-4, min(5.0, iv - diff / vega_sigma))
            if abs(diff) < 1e-4:
                return iv if 0 < iv <= 5.0 else None
    return implied_volatility(price, spot, strike, tte, option_type, risk_free_rate=r)


def _quote_price(quote: dict[str, Any], max_spread_pct: float) -> tuple[float | None, str]:
    """Prefer bid/ask mid when spread is tight; else LTP."""
    ltp = quote.get("last_price")
    try:
        ltp_f = float(ltp) if ltp is not None else None
    except (TypeError, ValueError):
        ltp_f = None

    depth = quote.get("depth") if isinstance(quote.get("depth"), dict) else {}
    buy = depth.get("buy") or []
    sell = depth.get("sell") or []
    bid = buy[0].get("price") if buy else None
    ask = sell[0].get("price") if sell else None
    if bid is None:
        bid = quote.get("buy_price") or quote.get("best_bid")
    if ask is None:
        ask = quote.get("sell_price") or quote.get("best_ask")
    try:
        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        bid_f, ask_f = None, None

    if bid_f and ask_f and bid_f > 0 and ask_f > 0 and ask_f >= bid_f:
        mid = 0.5 * (bid_f + ask_f)
        if mid > 0 and (ask_f - bid_f) / mid <= max_spread_pct:
            return mid, "mid"
    if ltp_f and ltp_f > 0:
        return ltp_f, "ltp"
    return None, "none"


def _dealer_sign(option_type: str, sign_mode: str, oi_delta: int | None = None) -> float:
    """+1 = dealer long gamma; −1 = dealer short gamma."""
    mode = (sign_mode or "naive").lower()
    is_ce = option_type.upper() == "CE"
    if mode == "customer":
        return -1.0 if is_ce else 1.0
    if mode == "oi_delta" and oi_delta is not None and oi_delta != 0:
        cust_bought = oi_delta > 0
        return -1.0 if cust_bought else 1.0
    return 1.0 if is_ce else -1.0


def _unit_gamma(
    spot: float,
    strike: float,
    tte: float,
    iv: float | None,
    option_type: str,
    r: float,
    q: float,
) -> float | None:
    if iv is None or iv <= 0 or tte <= 0 or spot <= 0 or strike <= 0:
        return None
    greeks = compute_greeks(
        spot=spot,
        strike=strike,
        tte_years=tte,
        iv=iv,
        option_type=option_type,
        risk_free_rate=r,
        dividend_yield=q,
    )
    g = greeks.get("gamma")
    if g is None or not math.isfinite(float(g)) or float(g) < 0:
        return None
    return float(g)


def _leg_gamma(
    leg: dict[str, Any],
    quote: dict[str, Any] | None,
    spot: float,
    tte: float,
    *,
    r: float,
    q: float,
    sign_mode: str,
    max_spread_pct: float,
    min_oi: int,
    oi_baseline: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    if not quote:
        return None

    oi_raw = quote.get("oi")
    if oi_raw is None:
        oi_raw = quote.get("open_interest")
    if oi_raw is None:
        return None

    oi = int(oi_raw)
    if oi < min_oi:
        return None

    px, px_src = _quote_price(quote, max_spread_pct)
    if px is None or px <= 0:
        return None

    strike = float(leg["strike"])
    option_type = "CE" if leg["side"] == "call" else "PE"
    lot_size = int(
        leg.get("lot_size")
        or INDEX_OPTIONS.get(leg.get("_underlying", ""), {}).get("lot_size")
        or 1
    )

    iv = _implied_vol(px, spot, strike, tte, option_type, r, q)
    unit_gamma = _unit_gamma(spot, strike, tte, iv, option_type, r, q)
    if unit_gamma is None or iv is None:
        return None

    token = leg.get("instrument_token")
    baseline = None
    if oi_baseline is not None and token is not None:
        baseline = oi_baseline.get(str(token))
    oi_delta = (oi - baseline) if baseline is not None else None
    sign = _dealer_sign(option_type, sign_mode, oi_delta)

    density = unit_gamma * oi * lot_size
    gex_mag = unit_gamma * oi * lot_size * spot * spot * 0.01
    signed_gex = sign * gex_mag

    return {
        "strike": strike,
        "option_type": option_type,
        "oi": oi,
        "oi_delta": oi_delta,
        "ltp": round(float(quote.get("last_price") or px), 2),
        "price": round(px, 2),
        "price_source": px_src,
        "iv": round(iv * 100, 2),
        "iv_dec": iv,
        "lot": lot_size,
        "gamma": unit_gamma,
        "density": density,
        "gex": signed_gex,
        "sign": sign,
    }


def total_gex_at_spot(
    legs: list[tuple],
    spot: float,
    tte: float,
    *,
    r: float | None = None,
    q: float | None = None,
) -> float:
    """Aggregate dealer GEX (₹ per 1% move) at hypothetical ``spot``.

    Legs are either legacy 5-tuples ``(K, oi, lot, type, iv)`` with naive signs,
    or 6-tuples ``(..., dealer_sign)``.
    """
    d = _cfg()
    rr = float(r if r is not None else d["risk_free_rate"])
    qq = float(q if q is not None else d.get("dividend_yield", 0.0))
    total = 0.0
    for leg in legs:
        if len(leg) == 6:
            strike, oi, lot, otype, iv, sign = leg
        else:
            strike, oi, lot, otype, iv = leg[:5]
            sign = 1.0 if str(otype).upper() == "CE" else -1.0
        g = _unit_gamma(spot, strike, tte, iv, otype, rr, qq)
        if g is None:
            continue
        total += float(sign) * g * oi * lot * spot * spot * 0.01
    return total


def _interp_iv(smile: list[tuple[float, float]], strike: float) -> float | None:
    if not smile:
        return None
    if strike <= smile[0][0]:
        return smile[0][1]
    if strike >= smile[-1][0]:
        return smile[-1][1]
    for i in range(1, len(smile)):
        k0, v0 = smile[i - 1]
        k1, v1 = smile[i]
        if k0 <= strike <= k1:
            if k1 == k0:
                return v0
            t = (strike - k0) / (k1 - k0)
            return v0 + t * (v1 - v0)
    return smile[-1][1]


def total_gex_sticky_delta(
    legs: list[tuple],
    spot: float,
    tte: float,
    s_ref: float,
    smiles: dict[str, list[tuple[float, float]]],
    *,
    r: float,
    q: float,
) -> float:
    if s_ref <= 0 or spot <= 0:
        return 0.0
    total = 0.0
    for leg in legs:
        if len(leg) == 6:
            strike, oi, lot, otype, iv, sign = leg
        else:
            strike, oi, lot, otype, iv = leg[:5]
            sign = 1.0 if str(otype).upper() == "CE" else -1.0
        k_lookup = strike * s_ref / spot
        iv_sd = _interp_iv(smiles.get(str(otype).upper(), []), k_lookup) or iv
        g = _unit_gamma(spot, strike, tte, iv_sd, otype, r, q)
        if g is None:
            continue
        total += float(sign) * g * oi * lot * spot * spot * 0.01
    return total


def _scan_crossings(values: list[tuple[float, float]]) -> list[float]:
    crossings: list[float] = []
    if not values:
        return crossings
    prev_s, prev_g = values[0]
    for s, g in values[1:]:
        if prev_g == 0.0:
            crossings.append(prev_s)
        elif (prev_g < 0 < g) or (prev_g > 0 > g):
            frac = -prev_g / (g - prev_g)
            crossings.append(prev_s + frac * (s - prev_s))
        prev_s, prev_g = s, g
    return crossings


def gamma_flip_level(
    legs: list[tuple],
    spot: float,
    tte: float,
    s_min: float,
    s_max: float,
    *,
    r: float | None = None,
    q: float | None = None,
    steps: int = 400,
) -> float | None:
    if not legs or s_max <= s_min:
        return None
    d = _cfg()
    rr = float(r if r is not None else d["risk_free_rate"])
    qq = float(q if q is not None else d.get("dividend_yield", 0.0))
    step = (s_max - s_min) / steps
    grid = []
    for i in range(steps + 1):
        s = s_min + i * step
        grid.append((s, total_gex_at_spot(legs, s, tte, r=rr, q=qq)))
    crossings = _scan_crossings(grid)
    if not crossings:
        return None
    return round(min(crossings, key=lambda x: abs(x - spot)), 2)


def _gex_profile_and_flip(
    legs: list[tuple],
    spot: float,
    tte: float,
    s_min: float,
    s_max: float,
    *,
    r: float,
    q: float,
    steps: int,
    sticky_delta: bool = False,
    smiles: dict[str, list[tuple[float, float]]] | None = None,
) -> dict[str, Any]:
    empty = {
        "profile": [],
        "flip": None,
        "crossings": [],
        "distance_to_flip": None,
        "flip_slope": None,
    }
    if s_max <= s_min or not legs:
        return empty
    step = (s_max - s_min) / max(steps, 2)
    profile: list[dict[str, float]] = []
    grid: list[tuple[float, float]] = []
    for i in range(steps + 1):
        s = s_min + i * step
        if sticky_delta and smiles is not None:
            g = total_gex_sticky_delta(legs, s, tte, spot, smiles, r=r, q=q)
        else:
            g = total_gex_at_spot(legs, s, tte, r=r, q=q)
        grid.append((s, g))
        profile.append({"spot": round(s, 2), "gex": round(g, 2), "gex_cr": round(g / CRORE, 4)})

    crossings = [round(c, 2) for c in _scan_crossings(grid)]
    flip = min(crossings, key=lambda x: abs(x - spot)) if crossings else None
    dist = round(flip - spot, 2) if flip is not None else None

    slope = None
    if flip is not None and len(grid) >= 2:
        idx = min(range(len(grid)), key=lambda i: abs(grid[i][0] - flip))
        i0 = max(0, idx - 1)
        i1 = min(len(grid) - 1, idx + 1)
        if grid[i1][0] != grid[i0][0]:
            slope = round((grid[i1][1] - grid[i0][1]) / (grid[i1][0] - grid[i0][0]), 4)

    return {
        "profile": profile,
        "flip": flip,
        "crossings": crossings,
        "distance_to_flip": dist,
        "flip_slope": slope,
    }


def _magnet_walls(
    strikes: list[dict[str, Any]],
    spot: float,
    strike_step: float,
) -> tuple[float | None, float | None, float | None, float | None]:
    if not strikes:
        return None, None, None, None
    floor = max(strike_step, 1.0)

    def score(density: float, k: float) -> float:
        return density / max(abs(k - spot), floor)

    call = max(strikes, key=lambda row: score(row["ce_density"], row["strike"]))
    put = max(strikes, key=lambda row: score(row["pe_density"], row["strike"]))
    return (
        call["strike"],
        put["strike"],
        round(score(call["ce_density"], call["strike"]), 4),
        round(score(put["pe_density"], put["strike"]), 4),
    )


def _hedge_flow(
    total_gex: float,
    spot: float,
    lot_size: int,
    moves: list[int],
) -> list[dict[str, Any]]:
    if spot <= 0 or lot_size <= 0:
        return []
    gamma_units = total_gex / (spot * spot * 0.01)
    out: list[dict[str, Any]] = []
    for pts in moves:
        for signed_pts in (pts, -pts):
            delta_units = gamma_units * signed_pts
            fut_lots = delta_units / lot_size
            if delta_units > 0:
                direction = "dealers_buy"
            elif delta_units < 0:
                direction = "dealers_sell"
            else:
                direction = "flat"
            out.append(
                {
                    "move_pts": signed_pts,
                    "delta_units": round(delta_units, 2),
                    "futures_lots": round(fut_lots, 1),
                    "direction": direction,
                    "notional_cr": round(abs(delta_units) * spot / CRORE, 4),
                }
            )
    return out


def _strike_mass(row: dict[str, Any]) -> float:
    """Absolute GEX mass; fall back to density when GEX is sparse/zero."""
    gex = abs(float(row.get("net_gex") or 0.0))
    if gex > 0:
        return gex
    return abs(float(row.get("total_density") or 0.0))


def _hhi_band(hhi: float) -> ConcentrationBand:
    if hhi >= HHI_CONCENTRATED:
        return "concentrated"
    if hhi >= HHI_MIXED:
        return "mixed"
    return "diffuse"


def _pin_stability(
    history: list[dict[str, Any]] | None,
    pin_strike: float | None,
    strike_step: float,
    *,
    lookback: int = PIN_STABILITY_LOOKBACK,
) -> tuple[bool | None, float | None]:
    """Share of recent ticks whose pin is within one strike step of current pin."""
    if pin_strike is None or not history:
        return None, None
    step = max(float(strike_step), 1.0)
    recent = [p for p in history[-lookback:] if p.get("pin_strike") is not None]
    if not recent:
        return None, None
    stable = 0
    for p in recent:
        try:
            prev = float(p["pin_strike"])
        except (TypeError, ValueError):
            continue
        if abs(prev - float(pin_strike)) <= step + 1e-9:
            stable += 1
    pct = round(100.0 * stable / len(recent), 1)
    return pct >= 70.0, pct


def compute_gamma_concentration(
    strikes: list[dict[str, Any]],
    *,
    spot: float,
    atm_strike: float | None,
    call_wall: float | None,
    put_wall: float | None,
    strike_step: float,
    history: list[dict[str, Any]] | None = None,
    pin_threshold: float | None = None,
) -> dict[str, Any]:
    """Herfindahl-Hirschman concentration of |net_gex| across strikes."""
    empty = {
        "hhi": None,
        "top1_share": None,
        "top3_share": None,
        "effective_strikes": None,
        "band": None,
        "dominant_strike": None,
        "dominant_share": None,
        "pin_strike": None,
        "pin_share": None,
        "pin_stable": None,
        "pin_stability_pct": None,
    }
    if not strikes:
        return empty

    masses: list[tuple[float, float]] = []
    for row in strikes:
        try:
            k = float(row["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        masses.append((k, _strike_mass(row)))

    total = sum(m for _, m in masses)
    if total <= 0:
        return empty

    shares = [(k, m / total) for k, m in masses]
    hhi = sum(s * s for _, s in shares)
    ranked = sorted(shares, key=lambda x: x[1], reverse=True)
    top1_share = ranked[0][1]
    top3_share = sum(s for _, s in ranked[:3])
    dominant_strike = ranked[0][0]
    dominant_share = ranked[0][1]
    threshold = float(pin_threshold if pin_threshold is not None else PIN_SHARE_THRESHOLD)

    if top1_share >= threshold:
        pin_strike = dominant_strike
        pin_share = top1_share
    elif call_wall is not None and put_wall is not None:
        step = max(float(strike_step), 1.0)
        mid = (float(call_wall) + float(put_wall)) / 2.0
        pin_strike = round(mid / step) * step
        pin_share = next((s for k, s in shares if abs(k - pin_strike) < 1e-9), top1_share)
    elif atm_strike is not None:
        pin_strike = float(atm_strike)
        pin_share = next((s for k, s in shares if abs(k - pin_strike) < 1e-9), top1_share)
    else:
        pin_strike = dominant_strike
        pin_share = top1_share

    pin_stable, pin_stability_pct = _pin_stability(history, pin_strike, strike_step)
    eff = (1.0 / hhi) if hhi > 0 else None

    return {
        "hhi": round(hhi, 4),
        "top1_share": round(top1_share, 4),
        "top3_share": round(top3_share, 4),
        "effective_strikes": round(eff, 2) if eff is not None else None,
        "band": _hhi_band(hhi),
        "dominant_strike": dominant_strike,
        "dominant_share": round(dominant_share, 4),
        "pin_strike": pin_strike,
        "pin_share": round(float(pin_share), 4) if pin_share is not None else None,
        "pin_stable": pin_stable,
        "pin_stability_pct": pin_stability_pct,
    }


def compute_gamma_conviction(
    *,
    total_gex: float,
    gamma_regime: str,
    concentration: dict[str, Any],
    distance_to_flip: float | None,
    spot: float,
    expected_move: dict[str, Any] | None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministic 0–100 conviction blend (regime + HHI + pin + flip distance)."""
    hhi = concentration.get("hhi")
    top1 = concentration.get("top1_share")
    if hhi is None:
        return {"score": None, "delta": None, "direction": "flat"}

    # Regime clarity from |GEX| vs 1σ expected-move notional scale
    sigma_pts = None
    if expected_move and expected_move.get("sigma1_pts") is not None:
        try:
            sigma_pts = abs(float(expected_move["sigma1_pts"]))
        except (TypeError, ValueError):
            sigma_pts = None
    gex_cr = abs(float(total_gex)) / CRORE
    if sigma_pts and sigma_pts > 0 and spot > 0:
        # Rough: GEX Cr relative to move size; larger → clearer regime
        regime_score = min(40.0, (gex_cr / max(sigma_pts / 50.0, 0.5)) * 12.0)
    else:
        regime_score = min(40.0, gex_cr * 2.0)

    conc_score = float(hhi) * 30.0
    pin_score = float(top1 or 0.0) * 20.0

    if distance_to_flip is None or spot <= 0:
        flip_score = 5.0
    else:
        dist_pct = abs(float(distance_to_flip)) / float(spot)
        # Closer flip → lower conviction of "stay pinned"
        flip_score = min(10.0, max(0.0, (dist_pct / 0.01) * 10.0))

    score = int(round(max(0.0, min(100.0, regime_score + conc_score + pin_score + flip_score))))

    prev_score = None
    if history:
        for p in reversed(history):
            if p.get("conviction") is not None:
                try:
                    prev_score = float(p["conviction"])
                except (TypeError, ValueError):
                    prev_score = None
                break
    delta = None if prev_score is None else round(score - prev_score, 1)
    if delta is None:
        direction = "flat"
    elif delta > 1:
        direction = "rising"
    elif delta < -1:
        direction = "falling"
    else:
        direction = "flat"

    _ = gamma_regime  # reserved for future signed conviction variants
    return {"score": score, "delta": delta, "direction": direction}


def build_gamma_market_read(
    *,
    gamma_regime: str,
    concentration: dict[str, Any],
    conviction: dict[str, Any],
    flip_level: float | None,
    distance_to_flip: float | None,
    call_wall: float | None,
    put_wall: float | None,
) -> dict[str, str]:
    """Short narrative answering the five trader questions."""
    band = concentration.get("band") or "unknown"
    hhi = concentration.get("hhi")
    top1 = concentration.get("top1_share")
    dom = concentration.get("dominant_strike")
    pin = concentration.get("pin_strike")
    pin_stable = concentration.get("pin_stable")
    pos = gamma_regime == "positive"

    regime_line = (
        f"{'Positive' if pos else 'Negative'} gamma · {band} dealer positioning"
        + (f" · conviction {conviction.get('score')}" if conviction.get("score") is not None else "")
    )

    if pos and band == "concentrated":
        vol_line = "Volatility likely suppressed — dealers hedge against moves near the pin"
    elif not pos:
        vol_line = "Volatility likely amplified — negative gamma dealers chase the move"
    elif band == "diffuse":
        vol_line = "Volatility less constrained — gamma mass is spread across the chain"
    else:
        vol_line = "Mixed vol regime — watch flip and walls for a shift in behaviour"

    if hhi is not None and top1 is not None:
        shape_line = (
            f"HHI {hhi:.2f} · top strike holds {top1 * 100:.0f}% of |GEX| "
            f"({concentration.get('effective_strikes') or '—'} effective strikes)"
        )
    else:
        shape_line = "Concentration unavailable — insufficient GEX mass on strikes"

    if flip_level is not None and distance_to_flip is not None:
        change_line = (
            f"Behaviour shifts near flip {flip_level:,.0f} "
            f"({distance_to_flip:+.0f} pts from spot)"
        )
    elif flip_level is not None:
        change_line = f"Behaviour shifts near flip {flip_level:,.0f}"
    else:
        change_line = "No clear gamma flip in the scanned window"

    pin_note = "stable" if pin_stable else ("moving" if pin_stable is False else "n/a")
    levels_line = (
        f"Dominant {dom if dom is not None else '—'} · "
        f"Pin candidate {pin if pin is not None else '—'} ({pin_note}) · "
        f"Walls C{call_wall if call_wall is not None else '—'}/P{put_wall if put_wall is not None else '—'}"
    )

    return {
        "regime_line": regime_line,
        "vol_line": vol_line,
        "shape_line": shape_line,
        "change_line": change_line,
        "levels_line": levels_line,
    }


def _joint_read(
    gamma_regime: str,
    distance_to_flip: float | None,
    spot: float,
    vanna_regime: str | None,
) -> str:
    dist_pct = abs(distance_to_flip) / spot if distance_to_flip is not None and spot else None
    if gamma_regime == "positive" and dist_pct is not None and dist_pct > 0.005:
        return "pin_fade"
    if gamma_regime == "negative" and dist_pct is not None and dist_pct < 0.01:
        return "trend_breakout"
    if gamma_regime == "negative" and vanna_regime == "negative":
        return "vol_amp"
    if gamma_regime == "positive":
        return "mean_revert"
    return "mixed"


def _build_legs_for_expiry(
    underlying: str,
    exp: str,
    spot: float,
    *,
    provider: GammaDensityDataProvider,
    r: float,
    q: float,
    sign_mode: str,
    max_spread_pct: float,
    min_oi: int,
    oi_baseline: dict[str, int] | None,
) -> tuple[list[dict[str, Any]], list[tuple], int, int, dict[str, int]]:
    tte = time_to_expiry_years(exp)
    if tte is None:
        raise RuntimeError(f"Expiry {exp} is in the past for {underlying}")

    chain = provider.get_chain(underlying, exp)
    raw_legs = _flatten_chain_legs(chain)
    for leg in raw_legs:
        leg["_underlying"] = underlying

    quote_keys = [
        f"{leg.get('exchange', chain.get('exchange', 'NFO'))}:{leg['tradingsymbol']}"
        for leg in raw_legs
        if leg.get("tradingsymbol")
    ]
    quotes = provider.fetch_quotes(quote_keys)

    built_rows: list[dict[str, Any]] = []
    resolved: list[tuple] = []
    px_stats = {"mid": 0, "ltp": 0, "none": 0}
    for leg in raw_legs:
        exchange = leg.get("exchange", chain.get("exchange", "NFO"))
        symbol = leg.get("tradingsymbol")
        if not symbol:
            continue
        built = _leg_gamma(
            leg,
            quotes.get(f"{exchange}:{symbol}"),
            spot,
            tte,
            r=r,
            q=q,
            sign_mode=sign_mode,
            max_spread_pct=max_spread_pct,
            min_oi=min_oi,
            oi_baseline=oi_baseline,
        )
        if not built:
            continue
        px_stats[built["price_source"]] = px_stats.get(built["price_source"], 0) + 1
        built_rows.append(built)
        resolved.append(
            (
                built["strike"],
                built["oi"],
                built["lot"],
                built["option_type"],
                built["iv_dec"],
                built["sign"],
            )
        )
    return built_rows, resolved, len(raw_legs), int(chain.get("strike_step") or 50), px_stats


def build_gamma_snapshot(
    underlying: str,
    expiry: str | None = None,
    *,
    strike_window: int | None = None,
    sign_mode: str | None = None,
    provider: GammaDensityDataProvider | None = None,
    include_multi_expiry: bool = True,
    include_history: bool = True,
    include_vanna_strip: bool = True,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    d = _cfg()
    prov = provider or get_gamma_density_provider()
    window = int(strike_window) if strike_window is not None else int(d["strike_window"])
    mode = (sign_mode or d.get("sign_mode") or "naive").lower()
    if mode not in ("naive", "customer", "oi_delta"):
        mode = "naive"
    r = float(d["risk_free_rate"])
    q = float(d["dividend_yield"])
    max_spread = float(d.get("max_mid_spread_pct") or 0.12)
    min_oi = int(d.get("min_oi") or 0)
    profile_steps = int(d.get("gex_profile_steps") or 80)
    hist_max = int(d.get("history_max_points") or 120)
    hedge_moves = [int(x) for x in (d.get("hedge_moves_pts") or (50, 100))]
    multi_n = int(d.get("multi_expiry_count") or 2)

    exp = expiry or prov.nearest_expiry(underlying)
    if not exp:
        raise RuntimeError(f"No expiries found for {underlying}")

    tte = time_to_expiry_years(exp)
    if tte is None:
        raise RuntimeError(f"Expiry {exp} is in the past for {underlying}")

    spot = prov.get_spot(underlying)
    if spot is None:
        raise RuntimeError(f"Index spot unavailable for {underlying}")
    spot = float(spot)

    oi_baseline: dict[str, int] | None = None
    if mode == "oi_delta":
        try:
            from options.chain import get_chain
            from options.oi_var_store import ensure_eod_baseline

            chain0 = get_chain(underlying, exp)
            raw0 = _flatten_chain_legs(chain0)
            _, oi_baseline = ensure_eod_baseline(underlying, exp, raw0)
        except Exception:
            oi_baseline = None

    built_rows, resolved_legs, raw_total, strike_step, px_stats = _build_legs_for_expiry(
        underlying,
        exp,
        spot,
        provider=prov,
        r=r,
        q=q,
        sign_mode=mode,
        max_spread_pct=max_spread,
        min_oi=min_oi,
        oi_baseline=oi_baseline,
    )
    if not built_rows:
        raise RuntimeError(f"No gamma-resolvable legs for {underlying} expiry {exp}")

    per_strike: dict[float, dict[str, Any]] = {}
    for built in built_rows:
        strike = built["strike"]
        row = per_strike.setdefault(
            strike,
            {
                "strike": strike,
                "ce_oi": 0,
                "pe_oi": 0,
                "ce_density": 0.0,
                "pe_density": 0.0,
                "ce_gex": 0.0,
                "pe_gex": 0.0,
                "ce_iv": None,
                "pe_iv": None,
                "ce_price_source": None,
                "pe_price_source": None,
            },
        )
        if built["option_type"] == "CE":
            row["ce_oi"] = built["oi"]
            row["ce_density"] = built["density"]
            row["ce_gex"] = built["gex"]
            row["ce_iv"] = built["iv"]
            row["ce_price_source"] = built["price_source"]
        else:
            row["pe_oi"] = built["oi"]
            row["pe_density"] = built["density"]
            row["pe_gex"] = built["gex"]
            row["pe_iv"] = built["iv"]
            row["pe_price_source"] = built["price_source"]

    strikes = sorted(per_strike.values(), key=lambda x: x["strike"])
    all_strike_vals = [x["strike"] for x in strikes]
    s_min, s_max = min(all_strike_vals), max(all_strike_vals)

    atm = min(strikes, key=lambda x: abs(x["strike"] - spot))
    if window > 0:
        atm_idx = strikes.index(atm)
        lo = max(0, atm_idx - window)
        hi = min(len(strikes), atm_idx + window + 1)
        strikes = strikes[lo:hi]

    magnet_floor = max(float(strike_step), 1.0)
    for row in strikes:
        row["total_density"] = round(row["ce_density"] + row["pe_density"], 2)
        row["net_gex"] = round(row["ce_gex"] + row["pe_gex"], 2)
        row["ce_gex"] = round(row["ce_gex"], 2)
        row["pe_gex"] = round(row["pe_gex"], 2)
        row["ce_density"] = round(row["ce_density"], 2)
        row["pe_density"] = round(row["pe_density"], 2)
        row["magnet"] = round(
            row["total_density"] / max(abs(row["strike"] - spot), magnet_floor),
            4,
        )

    total_gex = round(total_gex_at_spot(resolved_legs, spot, tte, r=r, q=q), 2)
    gamma_regime = "positive" if total_gex >= 0 else "negative"

    smiles: dict[str, list[tuple[float, float]]] = {"CE": [], "PE": []}
    for strike, _oi, _lot, otype, iv, _sign in resolved_legs:
        smiles[str(otype).upper()].append((strike, iv))
    for k in smiles:
        smiles[k].sort(key=lambda x: x[0])

    scan = _gex_profile_and_flip(
        resolved_legs, spot, tte, s_min, s_max, r=r, q=q, steps=profile_steps
    )
    scan_sd = _gex_profile_and_flip(
        resolved_legs,
        spot,
        tte,
        s_min,
        s_max,
        r=r,
        q=q,
        steps=max(40, profile_steps // 2),
        sticky_delta=True,
        smiles=smiles,
    )

    call_wall, put_wall, call_magnet, put_magnet = _magnet_walls(
        strikes, spot, float(strike_step)
    )

    atm_ce = next(
        (b for b in built_rows if b["strike"] == atm["strike"] and b["option_type"] == "CE"),
        None,
    )
    atm_pe = next(
        (b for b in built_rows if b["strike"] == atm["strike"] and b["option_type"] == "PE"),
        None,
    )
    atm_ivs = [v for v in (atm.get("ce_iv"), atm.get("pe_iv")) if v is not None]
    atm_iv = round(sum(atm_ivs) / len(atm_ivs), 2) if atm_ivs else None

    bands: dict[str, Any] | None = None
    straddle_pts = None
    if atm_ce and atm_pe:
        straddle_pts = round(float(atm_ce["price"]) + float(atm_pe["price"]), 2)
    if straddle_pts and straddle_pts > 0:
        bands = {
            "source": "straddle",
            "straddle_pts": straddle_pts,
            "sigma1_pts": straddle_pts,
            "sigma1_up": round(spot + straddle_pts, 2),
            "sigma1_dn": round(spot - straddle_pts, 2),
            "sigma2_up": round(spot + 2 * straddle_pts, 2),
            "sigma2_dn": round(spot - 2 * straddle_pts, 2),
        }
    elif atm_iv:
        sigma1 = spot * (atm_iv / 100.0) * math.sqrt(tte)
        bands = {
            "source": "atm_iv",
            "straddle_pts": None,
            "sigma1_pts": round(sigma1, 2),
            "sigma1_up": round(spot + sigma1, 2),
            "sigma1_dn": round(spot - sigma1, 2),
            "sigma2_up": round(spot + 2 * sigma1, 2),
            "sigma2_dn": round(spot - 2 * sigma1, 2),
        }

    lot_size = int(INDEX_OPTIONS.get(underlying, {}).get("lot_size") or 65)
    hedge_flow = _hedge_flow(total_gex, spot, lot_size, hedge_moves)

    zones = sorted(strikes, key=lambda x: x["total_density"], reverse=True)[:5]
    convexity_zones = [
        {
            "strike": z["strike"],
            "total_density": z["total_density"],
            "net_gex": z["net_gex"],
            "magnet": z.get("magnet"),
        }
        for z in zones
    ]

    multi_expiry: list[dict[str, Any]] = []
    if include_multi_expiry and multi_n > 1:
        try:
            all_exps = prov.list_expiries(underlying)
        except Exception:
            all_exps = [exp]
        extras = [e for e in all_exps if e != exp][: max(0, multi_n - 1)]
        for e2 in extras:
            try:
                tte2 = time_to_expiry_years(e2)
                if tte2 is None:
                    continue
                rows2, legs2, _, _, _ = _build_legs_for_expiry(
                    underlying,
                    e2,
                    spot,
                    provider=prov,
                    r=r,
                    q=q,
                    sign_mode=mode,
                    max_spread_pct=max_spread,
                    min_oi=min_oi,
                    oi_baseline=None,
                )
                if not legs2:
                    continue
                g2 = round(total_gex_at_spot(legs2, spot, tte2, r=r, q=q), 2)
                ks = sorted({leg[0] for leg in legs2})
                flip2 = gamma_flip_level(
                    legs2, spot, tte2, min(ks), max(ks), r=r, q=q, steps=120
                )
                multi_expiry.append(
                    {
                        "expiry": e2,
                        "tte_years": round(tte2, 6),
                        "total_gex": g2,
                        "flip_level": flip2,
                        "legs": len(rows2),
                    }
                )
            except Exception:
                continue

    weight_rows: list[dict[str, Any]] = [
        {"expiry": exp, "tte_years": tte, "total_gex": total_gex, "flip_level": scan["flip"]}
    ]
    weight_rows.extend(multi_expiry)
    inv_sqrt = [1.0 / math.sqrt(max(float(w["tte_years"]), 1e-6)) for w in weight_rows]
    wsum = sum(inv_sqrt) or 1.0
    weights = [x / wsum for x in inv_sqrt]
    for i, wrow in enumerate(weight_rows):
        wrow["weight"] = round(weights[i], 4)
    multi_expiry_gex = round(
        sum(float(wrow["total_gex"]) * weights[i] for i, wrow in enumerate(weight_rows)),
        2,
    )
    primary_weight = weights[0] if weights else 1.0
    for i, me in enumerate(multi_expiry):
        me["weight"] = weights[i + 1] if i + 1 < len(weights) else None

    vanna_strip: dict[str, Any] | None = None
    if include_vanna_strip:
        try:
            from options.greeks import bs_vanna

            total_vex_inr = 0.0
            for strike, oi, lot, otype, iv, sign in resolved_legs:
                vn = bs_vanna(
                    spot=spot,
                    strike=strike,
                    tte_years=tte,
                    iv=iv,
                    risk_free_rate=r,
                    dividend_yield=q,
                )
                if vn is None:
                    continue
                total_vex_inr += float(sign) * vn * oi * lot * spot * 0.01
            vanna_regime = "positive" if total_vex_inr >= 0 else "negative"
            vanna_strip = {
                "total_vex_cr": round(total_vex_inr / CRORE, 4),
                "vanna_regime": vanna_regime,
                "joint_read": _joint_read(
                    gamma_regime, scan["distance_to_flip"], spot, vanna_regime
                ),
            }
        except Exception:
            vanna_strip = None

    history: list[dict[str, Any]] = []
    chart_series: list[dict[str, Any]] = []
    reversals: list[dict[str, Any]] = []
    prior_history: list[dict[str, Any]] = []
    if include_history:
        try:
            from options.gamma_density_history import get_history

            prior_history = get_history(underlying, exp)
        except Exception:
            prior_history = []

    pin_threshold = float(d.get("pin_share_threshold") or PIN_SHARE_THRESHOLD)
    concentration = compute_gamma_concentration(
        strikes,
        spot=spot,
        atm_strike=atm["strike"],
        call_wall=call_wall,
        put_wall=put_wall,
        strike_step=float(strike_step),
        history=prior_history,
        pin_threshold=pin_threshold,
    )
    conviction = compute_gamma_conviction(
        total_gex=total_gex,
        gamma_regime=gamma_regime,
        concentration=concentration,
        distance_to_flip=scan["distance_to_flip"],
        spot=spot,
        expected_move=bands,
        history=prior_history,
    )
    market_read = build_gamma_market_read(
        gamma_regime=gamma_regime,
        concentration=concentration,
        conviction=conviction,
        flip_level=scan["flip"],
        distance_to_flip=scan["distance_to_flip"],
        call_wall=call_wall,
        put_wall=put_wall,
    )

    if include_history:
        try:
            from kite_client import fetch_index_minute_spot
            from options.gamma_density_history import (
                append_history_point,
                build_chart_series,
                detect_spot_reversals,
                get_history,
                minutes_since_session_open,
            )

            history = append_history_point(
                underlying,
                exp,
                spot=spot,
                total_gex=total_gex,
                flip_level=scan["flip"],
                gamma_regime=gamma_regime,
                max_points=max(hist_max, 400),
                hhi=concentration.get("hhi"),
                conviction=conviction.get("score"),
                pin_strike=concentration.get("pin_strike"),
            )
            if not history:
                history = get_history(underlying, exp)

            spot_candles: list[dict[str, Any]] = []
            try:
                lookback = minutes_since_session_open(underlying)
                spot_candles = fetch_index_minute_spot(underlying, minutes=lookback)
            except Exception:
                spot_candles = []

            # Adaptive min move: ~0.15% of spot (Nifty ~35 pts, Crude ~10)
            min_move = max(25.0, round(float(spot) * 0.0015, 1))
            chart_series = build_chart_series(underlying, history, spot_candles)
            reversals = detect_spot_reversals(chart_series, min_move_pts=min_move)
        except Exception:
            history = prior_history
            chart_series = []
            reversals = []

    return {
        "underlying": underlying,
        "expiry": exp,
        "provider": prov.name,
        "spot": spot,
        "updated_at": datetime.now().astimezone().isoformat(),
        "tte_years": round(tte, 6),
        "atm_strike": atm["strike"],
        "atm_iv": atm_iv,
        "total_gex": total_gex,
        "total_gex_cr": round(total_gex / CRORE, 4),
        "gamma_regime": gamma_regime,
        "sign_mode": mode,
        "dividend_yield": q,
        "risk_free_rate": r,
        "price_source_stats": px_stats,
        "flip_level": scan["flip"],
        "flip_sticky_delta": scan_sd["flip"],
        "flip_crossings": scan["crossings"],
        "distance_to_flip": scan["distance_to_flip"],
        "flip_slope": scan["flip_slope"],
        "gex_profile": scan["profile"],
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_wall_magnet": call_magnet,
        "put_wall_magnet": put_magnet,
        "expected_move": bands,
        "hedge_flow": hedge_flow,
        "multi_expiry": multi_expiry,
        "multi_expiry_gex": multi_expiry_gex,
        "primary_weight": round(primary_weight, 4),
        "vanna_strip": vanna_strip,
        "concentration": concentration,
        "conviction": conviction,
        "market_read": market_read,
        "history": history,
        "chart_series": chart_series,
        "reversals": reversals,
        "chain_legs_quoted": len(built_rows),
        "chain_legs_total": raw_total,
        "strike_window": window,
        "convexity_zones": convexity_zones,
        "strikes": strikes,
    }
