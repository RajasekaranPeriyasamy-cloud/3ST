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

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from config import ANALYTICS_HISTORY_SAMPLE_UNDERLYINGS, GAMMA_DENSITY_DEFAULTS, INDEX_OPTIONS
from options.gamma_density_provider import (
    GammaDensityDataProvider,
    get_gamma_density_provider,
)
from options.gamma_momentum import compute_gamma_momentum
from options.greeks_engine import compute_greeks, d1_d2
from options.iv import implied_volatility, time_to_expiry_years
from options.oi_var import _flatten_chain_legs
from utils.logging import get_logger, log_event

CRORE = 1e7
IST = ZoneInfo("Asia/Kolkata")
SignMode = Literal["naive", "customer", "oi_delta"]
ConcentrationBand = Literal["concentrated", "mixed", "diffuse"]
MassBasis = Literal["gross", "net"]
_log = get_logger("gamma_density")

# Concentration mass basis.
#   gross — |CE γ| + |PE γ| at each strike: "where is dealer gamma clustered".
#   net   — |CE γ + PE γ|: concentration of the *net* dealer imbalance. Under the
#           naive sign mode CE is dealer-long and PE dealer-short, so a balanced
#           strike cancels to ~0 mass and drops out of the index entirely.
# Gross is the default: it answers the question the desk actually asks, and it is
# the same basis as call_hhi / put_hhi (which have always been gross).
DEFAULT_MASS_BASIS: MassBasis = "gross"

# Band cuts are basis-dependent — gross and net masses do not live on the same
# scale. Gross cuts are calibrated against BSM gamma × index-scale OI at NIFTY
# (spot ~24.6k, step 50, ±20 strikes): 0-DTE pinning lands at 0.18–0.32,
# 1–2 DTE at 0.08–0.13, weekly/monthly at 0.02–0.07. Net cuts are the legacy
# 0.25/0.12 pair. Both are overridable via GAMMA_DENSITY_DEFAULTS.
HHI_BAND_CUTS: dict[str, tuple[float, float]] = {
    "gross": (0.18, 0.08),
    "net": (0.25, 0.12),
}
# Legacy names — the net-basis pair, kept for callers/tests that import them.
HHI_CONCENTRATED, HHI_MIXED = HHI_BAND_CUTS["net"]
GINI_EQUAL_CUT = 0.40  # Ávila-style equal vs unequal (fixed; not rolling median)
PIN_SHARE_THRESHOLD = 0.18
PIN_STABILITY_LOOKBACK = 12

# HHI band → Ávila quadrant suffix (UI-facing labels)
_QUADRANT_SUFFIX: dict[ConcentrationBand, str] = {
    "diffuse": "dispersed",
    "mixed": "balanced",
    "concentrated": "compressed",
}

# Internal band key → desk vocabulary shown on the concentration board.
BAND_LABELS: dict[ConcentrationBand, str] = {
    "diffuse": "dispersed",
    "mixed": "balanced",
    "concentrated": "compressed",
}


def _empty_reference_levels() -> dict[str, float | None]:
    return {
        "prev_day_high": None,
        "prev_day_low": None,
        "prev_day_close": None,
        "prev_week_high": None,
        "prev_week_low": None,
        "prev_week_close": None,
    }


def _today_ist() -> date:
    return datetime.now(IST).date()


def _days_to_expiry(expiry: str | None) -> int | None:
    """Calendar days from today (IST) to ``expiry``; 0 on expiry day."""
    try:
        exp_d = date.fromisoformat(str(expiry)[:10])
    except (TypeError, ValueError):
        return None
    return (exp_d - _today_ist()).days


def _bar_date(raw: Any) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        # Normalize to IST calendar day so UTC midnights don't shift the session date.
        if raw.tzinfo is not None:
            return raw.astimezone(IST).date()
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(IST).date()
        return parsed.date()
    except Exception:
        return None


def compute_reference_levels_from_daily_bars(
    bars: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, float | None]:
    """Derive Prev Day / Prev Week H/L/C from completed daily candles.

    Excludes bars dated ``today`` (session may still be open). Prev week is the
    prior ISO calendar week (Mon–Sun) relative to ``today``.
    """
    out = _empty_reference_levels()
    if not bars:
        return out
    today = today or _today_ist()

    completed: list[tuple[date, float, float, float]] = []
    for bar in bars:
        d = _bar_date(bar.get("date") or bar.get("time") or bar.get("timestamp"))
        if d is None or d >= today:
            continue
        try:
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if high <= 0 or low <= 0 or close <= 0:
            continue
        completed.append((d, high, low, close))

    if not completed:
        return out

    completed.sort(key=lambda x: x[0])
    # Prev day = last completed daily bar
    _, pdh, pdl, pdc = completed[-1]
    out["prev_day_high"] = round(pdh, 2)
    out["prev_day_low"] = round(pdl, 2)
    out["prev_day_close"] = round(pdc, 2)

    # Prior ISO week relative to today
    this_week_monday = today - timedelta(days=today.weekday())
    prior_week_monday = this_week_monday - timedelta(days=7)
    prior_week_sunday = this_week_monday - timedelta(days=1)
    week_bars = [
        row for row in completed if prior_week_monday <= row[0] <= prior_week_sunday
    ]
    if week_bars:
        out["prev_week_high"] = round(max(r[1] for r in week_bars), 2)
        out["prev_week_low"] = round(min(r[2] for r in week_bars), 2)
        out["prev_week_close"] = round(week_bars[-1][3], 2)

    return out


def _resolve_spot_instrument_token(underlying: str) -> int | None:
    """Index cash token or MCX front-month future — same source as minute spot."""
    und = str(underlying or "").upper()
    if und not in INDEX_OPTIONS:
        return None
    meta = INDEX_OPTIONS[und]
    try:
        key = meta.get("index_token_key")
        if key:
            from instruments import resolve_instrument

            return int(resolve_instrument(key)["instrument_token"])
        if (meta.get("spot_source") or "").lower() == "future" or str(
            meta.get("exchange") or ""
        ).upper() == "MCX":
            from instruments import resolve_future

            return int(resolve_future(und)["instrument_token"])
    except Exception:
        return None
    return None


def fetch_underlying_daily_ohlc_bars(
    underlying: str,
    *,
    lookback_days: int = 21,
) -> list[dict[str, Any]]:
    """Raw Kite daily candles for the underlying spot instrument (fail soft → [])."""
    token = _resolve_spot_instrument_token(underlying)
    if token is None:
        return []
    end = _today_ist()
    start = end - timedelta(days=max(7, int(lookback_days)))
    last_err: Exception | None = None
    for factory in ("_kite_direct_client", "get_kite_client"):
        try:
            from kite_client import _kite_direct_client, get_kite_client

            kite = _kite_direct_client() if factory == "_kite_direct_client" else get_kite_client()
            raw = kite.historical_data(
                instrument_token=int(token),
                from_date=start,
                to_date=end,
                interval="day",
                continuous=False,
                oi=False,
            )
            if raw:
                return list(raw)
        except Exception as exc:  # noqa: BLE001 — soft-fail with fallback client
            last_err = exc
            continue
    _ = last_err
    return []


def build_reference_levels(underlying: str, *, today: date | None = None) -> dict[str, float | None]:
    """Fetch day candles and compute Prev Day / Prev Week H/L/C (nulls on failure)."""
    try:
        bars = fetch_underlying_daily_ohlc_bars(underlying)
        return compute_reference_levels_from_daily_bars(bars, today=today or _today_ist())
    except Exception:
        return _empty_reference_levels()


def _cfg() -> dict[str, Any]:
    return dict(GAMMA_DENSITY_DEFAULTS)


def _hedge_moves_for_underlying(underlying: str, d: dict[str, Any] | None = None) -> list[int]:
    """Resolve hedge-flow point shocks for an underlying (global default or per-u override)."""
    cfg = d if d is not None else _cfg()
    default = [int(x) for x in (cfg.get("hedge_moves_pts") or (50, 100))]
    key = str(underlying or "").upper()
    by_u = cfg.get("hedge_moves_pts_by_underlying") or {}
    if key in by_u:
        return [int(x) for x in by_u[key]]
    # Fallback: scale with strike grid when step is larger than the default shocks
    # (covers new large-step commodities without an explicit override).
    meta = INDEX_OPTIONS.get(key) or {}
    step = int(meta.get("strike_step") or 0)
    if step > 0 and default and step > max(default):
        return [step, step * 2]
    return default


def gamma_config() -> dict[str, Any]:
    d = _cfg()
    prov = get_gamma_density_provider()
    basis = normalize_mass_basis(d.get("mass_basis"))
    compressed_cut, balanced_cut = hhi_band_cuts(basis)
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "mass_basis": basis,
        "mass_bases": ["gross", "net"],
        "hhi_band_cuts": {"compressed": compressed_cut, "balanced": balanced_cut},
        "refresh_seconds": d["refresh_seconds"],
        "strike_window": d["strike_window"],
        "risk_free_rate": d["risk_free_rate"],
        "dividend_yield": d["dividend_yield"],
        "sign_modes": ["naive", "customer", "oi_delta"],
        "sign_mode": d.get("sign_mode", "naive"),
        "hedge_moves_pts": list(d.get("hedge_moves_pts") or (50, 100)),
        "hedge_moves_pts_by_underlying": {
            str(k).upper(): [int(x) for x in v]
            for k, v in (d.get("hedge_moves_pts_by_underlying") or {}).items()
        },
        "multi_expiry_count": int(d.get("multi_expiry_count") or 2),
        "concentration_summary_window": int(d.get("concentration_summary_window") or 8),
        "concentration_summary_refresh_seconds": int(
            d.get("concentration_summary_refresh_seconds") or 90
        ),
        "concentration_summary_underlyings": default_concentration_underlyings(),
        "provider": prov.name,
        "requires_session": prov.requires_session(),
    }


# Cash indices for the multi-index strip — never default every MCX underlying.
_CONCENTRATION_CASH_CANDIDATES = ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY")


def default_concentration_underlyings() -> list[str]:
    """NIFTY / BANKNIFTY / SENSEX (+ FINNIFTY when present in INDEX_OPTIONS)."""
    return [u for u in _CONCENTRATION_CASH_CANDIDATES if u in INDEX_OPTIONS]


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
        "instrument_token": int(token) if token is not None else None,
    }


def gex_components_from_strikes(strikes: list[dict[str, Any]]) -> tuple[float, float, float]:
    """Split strike GEX into +VE mass, −VE mass (absolute), and net.

    Returns ``(pos_gex, neg_gex_abs, net_gex)`` where ``pos`` / ``neg`` are the
    sums of positive and |negative| leg GEX (ce_gex / pe_gex), matching the
    Unusual-Whales-style dual component lines (both plotted above zero).
    """
    pos = 0.0
    neg_abs = 0.0
    for row in strikes:
        for key in ("ce_gex", "pe_gex"):
            try:
                g = float(row.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
            if g > 0:
                pos += g
            elif g < 0:
                neg_abs += -g
    net = pos - neg_abs
    return round(pos, 2), round(neg_abs, 2), round(net, 2)


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


def _f(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def normalize_mass_basis(basis: str | None) -> MassBasis:
    b = str(basis or DEFAULT_MASS_BASIS).strip().lower()
    return "net" if b == "net" else "gross"


def _strike_mass(row: dict[str, Any], basis: MassBasis = DEFAULT_MASS_BASIS) -> float:
    """Absolute GEX mass at one strike, on ``basis``.

    Gross uses |CE γ| + |PE γ| and only falls back to |net_gex| when neither side
    is populated (synthetic rows / older callers). Net uses |CE γ + PE γ|.
    Density fallback is applied by the caller at the *aggregate* level so a single
    cancelling strike cannot contribute a mass in the wrong unit — density and GEX
    differ by S²·0.01 (~6e6 at NIFTY scale).
    """
    if basis == "gross":
        gross = abs(_f(row.get("ce_gex"))) + abs(_f(row.get("pe_gex")))
        if gross > 0:
            return gross
    return abs(_f(row.get("net_gex")))


def _strike_masses(
    strikes: list[dict[str, Any]],
    basis: MassBasis = DEFAULT_MASS_BASIS,
) -> list[tuple[float, float, dict[str, Any]]]:
    """``[(strike, mass, row), ...]`` with an aggregate density fallback."""
    rows: list[tuple[float, float, dict[str, Any]]] = []
    for row in strikes:
        try:
            k = float(row["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((k, _strike_mass(row, basis), row))
    if rows and sum(m for _, m, _ in rows) <= 0:
        rows = [(k, abs(_f(row.get("total_density"))), row) for k, _, row in rows]
    return rows


def hhi_band_cuts(basis: str | None = None) -> tuple[float, float]:
    """``(compressed_cut, balanced_cut)`` for ``basis``, honouring config overrides."""
    b = normalize_mass_basis(basis)
    default_hi, default_lo = HHI_BAND_CUTS[b]
    cfg = _cfg()
    hi = cfg.get(f"hhi_compressed_cut_{b}")
    lo = cfg.get(f"hhi_balanced_cut_{b}")
    try:
        hi_f = float(hi) if hi is not None else default_hi
    except (TypeError, ValueError):
        hi_f = default_hi
    try:
        lo_f = float(lo) if lo is not None else default_lo
    except (TypeError, ValueError):
        lo_f = default_lo
    return hi_f, lo_f


def _hhi_band(hhi: float, basis: str | None = None) -> ConcentrationBand:
    hi, lo = hhi_band_cuts(basis)
    if hhi >= hi:
        return "concentrated"
    if hhi >= lo:
        return "mixed"
    return "diffuse"


def _hhi_from_masses(masses: list[float]) -> float | None:
    total = sum(masses)
    if total <= 0:
        return None
    return sum((m / total) ** 2 for m in masses)


def _gini_from_masses(masses: list[float]) -> float | None:
    """Strike-level Gini of absolute masses (same base as HHI).

    Sort ascending x_1 <= ... <= x_n, X = sum x_i, n >= 2:

        G = 2 * sum(i * x_i) / (n * X) - (n + 1) / n

    Clamped to [0, 1]. Returns None when n < 2 or X <= 0.
    """
    xs = [float(m) for m in masses]
    n = len(xs)
    if n < 2:
        return None
    total = sum(xs)
    if total <= 0:
        return None
    xs_sorted = sorted(xs)
    weighted = sum((i + 1) * x for i, x in enumerate(xs_sorted))
    g = (2.0 * weighted) / (n * total) - (n + 1) / n
    return max(0.0, min(1.0, g))


def _shape_quadrant(hhi: float, gini: float | None, basis: str | None = None) -> str | None:
    """Ávila HHI×Gini quadrant label (e.g. unequal-dispersed)."""
    if gini is None:
        return None
    prefix = "unequal" if gini >= GINI_EQUAL_CUT else "equal"
    return f"{prefix}-{_QUADRANT_SUFFIX[_hhi_band(hhi, basis)]}"


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


def _side_masses(strikes: list[dict[str, Any]], gex_key: str) -> list[float]:
    """Absolute GEX masses for one side (ce_gex or pe_gex).

    Falls back to side density, then signed |net_gex|, matching Call/Put HHI/Gini.
    Returns [] when no positive mass is available.
    """
    density_key = "ce_density" if gex_key == "ce_gex" else "pe_density"

    def _masses_from(key: str) -> list[float]:
        out: list[float] = []
        for row in strikes:
            try:
                out.append(abs(float(row.get(key) or 0.0)))
            except (TypeError, ValueError):
                continue
        return out

    masses = _masses_from(gex_key)
    total = sum(masses)
    if total <= 0:
        masses = _masses_from(density_key)
        total = sum(masses)
    if total <= 0:
        # Last resort: attribute |net_gex| by sign (call ← positive, put ← negative).
        masses = []
        for row in strikes:
            try:
                net = float(row.get("net_gex") or 0.0)
            except (TypeError, ValueError):
                continue
            if gex_key == "ce_gex":
                masses.append(max(net, 0.0))
            else:
                masses.append(abs(min(net, 0.0)))
        total = sum(masses)
    if total <= 0:
        return []
    return masses


def _side_hhi(strikes: list[dict[str, Any]], gex_key: str) -> float | None:
    """HHI of absolute GEX shares for one side (ce_gex or pe_gex)."""
    masses = _side_masses(strikes, gex_key)
    if not masses:
        return None
    total = sum(masses)
    return round(sum((m / total) ** 2 for m in masses), 4)


def _side_gini(strikes: list[dict[str, Any]], gex_key: str) -> float | None:
    """Gini of absolute GEX masses for one side (same fallbacks as ``_side_hhi``)."""
    g = _gini_from_masses(_side_masses(strikes, gex_key))
    return round(g, 4) if g is not None else None


def _contributor_side_bias(row: dict[str, Any]) -> str:
    try:
        ce = abs(float(row.get("ce_gex") or 0.0))
        pe = abs(float(row.get("pe_gex") or 0.0))
    except (TypeError, ValueError):
        return "mixed"
    if ce > pe * 1.05:
        return "call"
    if pe > ce * 1.05:
        return "put"
    return "mixed"


def _cliff_strike(
    strikes: list[dict[str, Any]],
    *,
    spot: float,
    flip_level: float | None,
    call_wall: float | None,
    put_wall: float | None,
) -> float | None:
    """Nearest destabilizer: flip if in strike window, else breakout-side wall."""
    ks: list[float] = []
    for row in strikes:
        try:
            ks.append(float(row["strike"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not ks:
        return None
    lo, hi = min(ks), max(ks)
    if flip_level is not None:
        try:
            flip = float(flip_level)
        except (TypeError, ValueError):
            flip = None
        else:
            if lo - 1e-9 <= flip <= hi + 1e-9:
                return flip

    # Breakout-side wall: call wall above spot (upside), put wall below (downside).
    # When both exist, pick the farther wall from spot (the cliff you break into).
    candidates: list[float] = []
    if call_wall is not None:
        try:
            cw = float(call_wall)
        except (TypeError, ValueError):
            cw = None
        else:
            if cw >= float(spot) - 1e-9:
                candidates.append(cw)
    if put_wall is not None:
        try:
            pw = float(put_wall)
        except (TypeError, ValueError):
            pw = None
        else:
            if pw <= float(spot) + 1e-9:
                candidates.append(pw)
    if not candidates:
        for w in (call_wall, put_wall):
            if w is None:
                continue
            try:
                candidates.append(float(w))
            except (TypeError, ValueError):
                continue
    if not candidates:
        return None
    return max(candidates, key=lambda w: abs(w - float(spot)))


def _gamma_peaks(strikes: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    """``(+γ peak, −γ peak)`` — strikes holding the most dealer long / short gamma.

    Peaks are read off signed ``net_gex`` regardless of the HHI mass basis: the
    board labels them "most dealer long/short gamma", which is a net statement.
    Returns ``None`` for a side with no mass on that sign.
    """
    pos: tuple[float, float] | None = None
    neg: tuple[float, float] | None = None
    for row in strikes:
        try:
            k = float(row["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        net = _f(row.get("net_gex"))
        if net > 0 and (pos is None or net > pos[0]):
            pos = (net, k)
        elif net < 0 and (neg is None or net < neg[0]):
            neg = (net, k)
    return (pos[1] if pos else None), (neg[1] if neg else None)


def _daily_hhi_stats(
    series: list[dict[str, Any]] | None,
    current_hhi: float,
    *,
    basis: str | None = None,
    mean_window: int = 5,
) -> dict[str, Any]:
    """Prior-session / rolling-mean / range stats over the day-end HHI series.

    ``series`` must already be filtered to the current measurement basis (see
    :func:`gamma_density_history.filter_daily_hhi_basis`) and sorted oldest →
    newest, with today's value present as the last row. Prior-session comparisons
    exclude today so a mid-session refresh cannot compare today against itself.
    """
    out: dict[str, Any] = {
        "hhi_prev_session": None,
        "hhi_prev_session_date": None,
        "hhi_dod_pct": None,
        "hhi_mean_5": None,
        "hhi_mean_5_band": None,
        "hhi_mean_30": None,
        "hhi_vs_mean_pct": None,
        "hhi_low_30": None,
        "hhi_high_30": None,
        "hhi_session_assumed_count": 0,
    }
    try:
        from options.gamma_density_history import count_assumed_window_rows

        out["hhi_session_assumed_count"] = count_assumed_window_rows(series)
    except Exception:
        pass
    rows: list[dict[str, Any]] = []
    for row in series or []:
        if row.get("hhi") is None:
            continue
        try:
            rows.append({"date": str(row.get("date"))[:10], "hhi": float(row["hhi"])})
        except (TypeError, ValueError):
            continue
    if not rows:
        return out

    today = _today_ist().isoformat()
    prior = [r for r in rows if r["date"] != today]
    if prior:
        prev = prior[-1]
        out["hhi_prev_session"] = round(prev["hhi"], 4)
        out["hhi_prev_session_date"] = prev["date"]
        if prev["hhi"] > 0:
            out["hhi_dod_pct"] = round(100.0 * (current_hhi - prev["hhi"]) / prev["hhi"], 1)

    # Rolling mean over the last N *prior* sessions — a mean that moves with today's
    # own polls is not a baseline you can compare today against.
    window = prior[-int(mean_window):] if prior else []
    if window:
        mean_n = sum(r["hhi"] for r in window) / len(window)
        out["hhi_mean_5"] = round(mean_n, 4)
        out["hhi_mean_5_band"] = _hhi_band(mean_n, basis)
        if mean_n > 0:
            out["hhi_vs_mean_pct"] = round(100.0 * (current_hhi - mean_n) / mean_n, 1)

    vals = [r["hhi"] for r in rows]
    out["hhi_mean_30"] = round(sum(vals) / len(vals), 4)
    out["hhi_low_30"] = round(min(vals), 4)
    out["hhi_high_30"] = round(max(vals), 4)
    return out


def _hhi_intraday_stats(
    history: list[dict[str, Any]] | None,
    current_hhi: float,
) -> tuple[float | None, float | None]:
    """Session mean + percentile rank of current HHI among today's ticks (incl. current)."""
    vals: list[float] = []
    if history:
        for p in history:
            if p.get("hhi") is None:
                continue
            try:
                vals.append(float(p["hhi"]))
            except (TypeError, ValueError):
                continue
    vals.append(float(current_hhi))
    if not vals:
        return None, None
    mean = round(sum(vals) / len(vals), 4)
    # Percentile: share of ticks ≤ current (intraday rank, not cross-session).
    n_le = sum(1 for v in vals if v <= float(current_hhi) + 1e-12)
    pct = round(100.0 * n_le / len(vals), 1)
    return mean, pct


def _hhi_sessions_stats(
    daily_history: list[dict[str, Any]] | None,
    current_hhi: float,
    *,
    n: int = 30,
) -> tuple[float | None, int | None]:
    """Percentile of current HHI among last ``n`` trading-day HHIs (incl. today)."""
    from options.gamma_density_history import hhi_percentile_sessions

    pct, count = hhi_percentile_sessions(daily_history, current_hhi, n=n)
    return pct, count


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
    flip_level: float | None = None,
    daily_hhi_history: list[dict[str, Any]] | None = None,
    mass_basis: str | None = None,
) -> dict[str, Any]:
    """Herfindahl-Hirschman concentration of dealer gamma across strikes.

    ``mass_basis`` selects the per-strike mass: ``gross`` (default) uses
    |CE γ| + |PE γ|, ``net`` uses |CE γ + PE γ|. Both are always reported as
    ``hhi_gross`` / ``hhi_net``; ``hhi`` echoes the selected basis.
    """
    basis = normalize_mass_basis(mass_basis)
    compressed_cut, balanced_cut = hhi_band_cuts(basis)
    empty = {
        "hhi": None,
        "hhi_gross": None,
        "hhi_net": None,
        "mass_basis": basis,
        "band_cut_compressed": compressed_cut,
        "band_cut_balanced": balanced_cut,
        "top1_share": None,
        "top3_share": None,
        "top5_share": None,
        "effective_strikes": None,
        "band": None,
        "band_label": None,
        "dominant_strike": None,
        "dominant_share": None,
        "pin_strike": None,
        "pin_share": None,
        "pin_source": None,
        "pin_stable": None,
        "pin_stability_pct": None,
        "call_hhi": None,
        "put_hhi": None,
        "call_band": None,
        "put_band": None,
        "pos_gamma_peak_strike": None,
        "neg_gamma_peak_strike": None,
        "gini": None,
        "call_gini": None,
        "put_gini": None,
        "shape_quadrant": None,
        "top_contributors": [],
        "cliff_strike": None,
        "hhi_session_mean": None,
        "hhi_percentile_intraday": None,
        "hhi_percentile_30d": None,
        "hhi_session_count": None,
        "daily_hhi": [],
        "hhi_prev_session": None,
        "hhi_prev_session_date": None,
        "hhi_dod_pct": None,
        "hhi_mean_5": None,
        "hhi_mean_5_band": None,
        "hhi_mean_30": None,
        "hhi_vs_mean_pct": None,
        "hhi_low_30": None,
        "hhi_high_30": None,
        "hhi_session_assumed_count": 0,
    }
    if not strikes:
        return empty

    masses = _strike_masses(strikes, basis)
    pos_peak, neg_peak = _gamma_peaks(strikes)
    empty["pos_gamma_peak_strike"] = pos_peak
    empty["neg_gamma_peak_strike"] = neg_peak

    total = sum(m for _, m, _ in masses)
    if total <= 0:
        empty["cliff_strike"] = _cliff_strike(
            strikes, spot=spot, flip_level=flip_level, call_wall=call_wall, put_wall=put_wall
        )
        empty["call_hhi"] = _side_hhi(strikes, "ce_gex")
        empty["put_hhi"] = _side_hhi(strikes, "pe_gex")
        empty["call_band"] = (
            _hhi_band(empty["call_hhi"], basis) if empty["call_hhi"] is not None else None
        )
        empty["put_band"] = (
            _hhi_band(empty["put_hhi"], basis) if empty["put_hhi"] is not None else None
        )
        empty["call_gini"] = _side_gini(strikes, "ce_gex")
        empty["put_gini"] = _side_gini(strikes, "pe_gex")
        return empty

    shares = [(k, m / total, row) for k, m, row in masses]
    hhi = sum(s * s for _, s, _ in shares)
    ranked = sorted(shares, key=lambda x: x[1], reverse=True)
    top1_share = ranked[0][1]
    top3_share = sum(s for _, s, _ in ranked[:3])
    top5_share = sum(s for _, s, _ in ranked[:5])
    dominant_strike = ranked[0][0]
    dominant_share = ranked[0][1]
    threshold = float(pin_threshold if pin_threshold is not None else PIN_SHARE_THRESHOLD)

    # Which rule produced the pin matters as much as the number. Only ``dominant``
    # is an actual gamma pin; ``wall_mid`` is an inference and ``atm`` is a
    # placeholder that sits next to spot by construction — so it reads rock-steady
    # whenever spot is quiet, which is precisely when there is no pin. Downstream
    # pin-strength logic must gate on this rather than on the bare strike.
    if top1_share >= threshold:
        pin_strike = dominant_strike
        pin_share = top1_share
        pin_source = "dominant"
    elif call_wall is not None and put_wall is not None:
        step = max(float(strike_step), 1.0)
        mid = (float(call_wall) + float(put_wall)) / 2.0
        pin_strike = round(mid / step) * step
        pin_share = next((s for k, s, _ in shares if abs(k - pin_strike) < 1e-9), top1_share)
        pin_source = "wall_mid"
    elif atm_strike is not None:
        pin_strike = float(atm_strike)
        pin_share = next((s for k, s, _ in shares if abs(k - pin_strike) < 1e-9), top1_share)
        pin_source = "atm"
    else:
        pin_strike = dominant_strike
        pin_share = top1_share
        pin_source = "fallback"

    pin_stable, pin_stability_pct = _pin_stability(history, pin_strike, strike_step)
    eff = (1.0 / hhi) if hhi > 0 else None

    # Every strike in the window, ranked — the board's contributor bars slice the
    # head of this list, and the cumulative-Γ ladder tooltips read HHI contribution
    # (share²) off the tail, so a 25-row cap would blank most of the ladder.
    top_contributors: list[dict[str, Any]] = []
    for k, share, row in ranked:
        top_contributors.append(
            {
                "strike": k,
                "share": round(share, 4),
                "share_sq": round(share * share, 6),
                "net_gex": round(_f(row.get("net_gex")), 2),
                "gross_gex": round(
                    abs(_f(row.get("ce_gex"))) + abs(_f(row.get("pe_gex"))), 2
                ),
                "side_bias": _contributor_side_bias(row),
            }
        )

    call_hhi = _side_hhi(strikes, "ce_gex")
    put_hhi = _side_hhi(strikes, "pe_gex")
    gini = _gini_from_masses([m for _, m, _ in masses])
    call_gini = _side_gini(strikes, "ce_gex")
    put_gini = _side_gini(strikes, "pe_gex")
    shape_quadrant = _shape_quadrant(hhi, gini, basis)
    cliff = _cliff_strike(
        strikes, spot=spot, flip_level=flip_level, call_wall=call_wall, put_wall=put_wall
    )
    hhi_mean, hhi_pct = _hhi_intraday_stats(history, hhi)
    hhi_30d: float | None = None
    hhi_session_count: int | None = None
    if daily_hhi_history is not None:
        hhi_30d, hhi_session_count = _hhi_sessions_stats(daily_hhi_history, hhi)

    # The other basis, for the board's secondary readout. Cheap (pure arithmetic
    # over rows already in hand) and it keeps the two numbers from drifting apart.
    other: MassBasis = "net" if basis == "gross" else "gross"
    other_hhi = _hhi_from_masses([m for _, m, _ in _strike_masses(strikes, other)])
    hhi_gross, hhi_net = (hhi, other_hhi) if basis == "gross" else (other_hhi, hhi)
    band = _hhi_band(hhi, basis)

    out = {
        "hhi": round(hhi, 4),
        "hhi_gross": round(hhi_gross, 4) if hhi_gross is not None else None,
        "hhi_net": round(hhi_net, 4) if hhi_net is not None else None,
        "mass_basis": basis,
        "band_cut_compressed": compressed_cut,
        "band_cut_balanced": balanced_cut,
        "top1_share": round(top1_share, 4),
        "top3_share": round(top3_share, 4),
        "top5_share": round(top5_share, 4),
        "effective_strikes": round(eff, 2) if eff is not None else None,
        "band": band,
        "band_label": BAND_LABELS[band],
        "dominant_strike": dominant_strike,
        "dominant_share": round(dominant_share, 4),
        "pin_strike": pin_strike,
        "pin_share": round(float(pin_share), 4) if pin_share is not None else None,
        "pin_source": pin_source,
        "pin_stable": pin_stable,
        "pin_stability_pct": pin_stability_pct,
        "call_hhi": call_hhi,
        "put_hhi": put_hhi,
        "call_band": _hhi_band(call_hhi, basis) if call_hhi is not None else None,
        "put_band": _hhi_band(put_hhi, basis) if put_hhi is not None else None,
        "pos_gamma_peak_strike": pos_peak,
        "neg_gamma_peak_strike": neg_peak,
        "gini": round(gini, 4) if gini is not None else None,
        "call_gini": call_gini,
        "put_gini": put_gini,
        "shape_quadrant": shape_quadrant,
        "top_contributors": top_contributors,
        "cliff_strike": cliff,
        "hhi_session_mean": hhi_mean,
        "hhi_percentile_intraday": hhi_pct,
        "hhi_percentile_30d": hhi_30d,
        "hhi_session_count": hhi_session_count,
        "daily_hhi": [
            {
                "date": str(r.get("date"))[:10],
                "hhi": round(float(r["hhi"]), 4),
                "band": _hhi_band(float(r["hhi"]), basis),
            }
            for r in (daily_hhi_history or [])
            if r.get("hhi") is not None
        ],
    }
    out.update(_daily_hhi_stats(daily_hhi_history, hhi, basis=basis))
    return out


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
    reference_levels: dict[str, Any] | None = None,
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
    ref = reference_levels or {}
    pdc = ref.get("prev_day_close")
    pwc = ref.get("prev_week_close")
    if pdc is not None or pwc is not None:
        levels_line += (
            f" · Day close {pdc if pdc is not None else '—'} · "
            f"Week close {pwc if pwc is not None else '—'}"
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


def _pick_strike_oi_baseline(
    open_oi: int | None,
    prev_close_oi: int | None,
    mode: str,
) -> tuple[int | None, str | None]:
    """Resolve baseline OI for a CE/PE strike side.

    ``session_open`` prefers open then prev-close (OI Movers rule).
    ``prev_close`` uses previous-day close only.
    """
    from options.oi_movers import pick_baseline_oi

    if mode == "prev_close":
        if prev_close_oi is not None:
            return int(prev_close_oi), "prev_close"
        return None, None
    return pick_baseline_oi(open_oi, prev_close_oi)


def attach_strike_oi_baselines(
    strikes: list[dict[str, Any]],
    underlying: str,
    expiry: str,
    *,
    oi_baseline_mode: str = "session_open",
    after_hhmm: str = "09:20",
    open_map: dict[str, int] | None = None,
    prev_map: dict[str, int] | None = None,
    session_capture_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Attach ``ce/pe_oi_base``, ``*_doi``, and ``*_oi_base_source`` to strike rows.

    Soft-fails when session/prev maps are unavailable: bases and ΔOI stay null
    and the snapshot still builds. When ``open_map`` / ``prev_map`` are passed
    (tests), network/session stores are skipped.

    ``session_capture_rows`` (optional) should be the pre-window chain legs so
    session-open persistence is not narrowed to the ATM window.
    """
    mode = (oi_baseline_mode or "session_open").lower()
    if mode not in ("session_open", "prev_close"):
        mode = "session_open"

    meta: dict[str, Any] = {
        "oi_baseline_mode": mode,
        "oi_baseline_note": None,
        "oi_baseline_open_count": 0,
        "oi_baseline_prev_close_count": 0,
    }

    # Initialise null fields so the schema is stable even on soft-fail.
    for row in strikes:
        for side in ("ce", "pe"):
            row.setdefault(f"{side}_oi_base", None)
            row.setdefault(f"{side}_doi", None)
            row.setdefault(f"{side}_oi_base_source", None)

    token_rows: list[dict[str, Any]] = []
    tokens: list[int] = []
    seen_tok: set[str] = set()

    def _collect(row: dict[str, Any]) -> None:
        for side in ("ce", "pe"):
            tok = row.get(f"_{side}_token")
            if tok is None:
                continue
            try:
                tok_i = int(tok)
            except (TypeError, ValueError):
                continue
            tok_s = str(tok_i)
            if tok_s in seen_tok:
                continue
            seen_tok.add(tok_s)
            tokens.append(tok_i)
            curr_oi = row.get(f"{side}_oi")
            token_rows.append(
                {
                    "instrument_token": tok_i,
                    "latest_oi": int(curr_oi) if curr_oi is not None else 0,
                }
            )

    if session_capture_rows:
        for row in session_capture_rows:
            _collect(row)
    for row in strikes:
        _collect(row)

    try:
        from options.oi_movers import ensure_session_open_oi, get_prev_day_oi_map

        resolved_open = open_map
        resolved_prev = prev_map
        if resolved_open is None and mode == "session_open":
            resolved_open = (
                ensure_session_open_oi(
                    underlying, expiry, token_rows, after_hhmm=after_hhmm
                )
                if token_rows
                else {}
            )
        if resolved_open is None:
            resolved_open = {}
        if resolved_prev is None:
            resolved_prev = get_prev_day_oi_map(tokens) if tokens else {}
    except Exception:
        resolved_open = open_map or {}
        resolved_prev = prev_map or {}

    open_count = 0
    prev_count = 0
    for row in strikes:
        for side in ("ce", "pe"):
            tok = row.pop(f"_{side}_token", None)
            open_oi = None
            prev_close = None
            if tok is not None:
                tok_s = str(tok)
                raw_open = resolved_open.get(tok_s)
                raw_prev = resolved_prev.get(tok_s)
                try:
                    open_oi = int(raw_open) if raw_open is not None else None
                except (TypeError, ValueError):
                    open_oi = None
                try:
                    prev_close = int(raw_prev) if raw_prev is not None else None
                except (TypeError, ValueError):
                    prev_close = None

            base, source = _pick_strike_oi_baseline(open_oi, prev_close, mode)
            curr = row.get(f"{side}_oi")
            doi = None
            if base is not None and curr is not None:
                try:
                    doi = int(curr) - int(base)
                except (TypeError, ValueError):
                    doi = None

            row[f"{side}_oi_base"] = base
            row[f"{side}_doi"] = doi
            row[f"{side}_oi_base_source"] = source
            if source == "open":
                open_count += 1
            elif source == "prev_close":
                prev_count += 1

    # Drop private tokens from any pre-window capture rows still held by caller.
    if session_capture_rows:
        for row in session_capture_rows:
            row.pop("_ce_token", None)
            row.pop("_pe_token", None)

    meta["oi_baseline_open_count"] = open_count
    meta["oi_baseline_prev_close_count"] = prev_count
    if mode == "session_open":
        if open_count and prev_count:
            note = f"session open · {prev_count} strikes with prev_close fallback"
        elif open_count:
            note = f"session open · {open_count} legs"
        elif prev_count:
            note = f"prev_close fallback · {prev_count} legs"
        else:
            note = "OI baseline unavailable"
    else:
        note = (
            f"prev day close · {prev_count} legs"
            if prev_count
            else "prev_close baseline unavailable"
        )
    meta["oi_baseline_note"] = note
    return meta


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
    build_session_chart: bool = True,
    oi_baseline_mode: str | None = None,
    reversal_tf: str | None = None,
    reversal_gex_gate: bool = True,
    reversal_gex_mode: str | None = "live",
    reversal_oi_gate: bool = False,
    mass_basis: str | None = None,
    pin_window: str | None = None,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    d = _cfg()
    prov = provider or get_gamma_density_provider()
    window = int(strike_window) if strike_window is not None else int(d["strike_window"])
    mode = (sign_mode or d.get("sign_mode") or "naive").lower()
    if mode not in ("naive", "customer", "oi_delta"):
        mode = "naive"
    baseline_mode = (oi_baseline_mode or "session_open").lower()
    if baseline_mode not in ("session_open", "prev_close"):
        baseline_mode = "session_open"
    basis = normalize_mass_basis(mass_basis or d.get("mass_basis"))
    r = float(d["risk_free_rate"])
    q = float(d["dividend_yield"])
    max_spread = float(d.get("max_mid_spread_pct") or 0.12)
    min_oi = int(d.get("min_oi") or 0)
    profile_steps = int(d.get("gex_profile_steps") or 80)
    hist_max = int(d.get("history_max_points") or 120)
    hedge_moves = _hedge_moves_for_underlying(underlying, d)
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
            row["_ce_token"] = built.get("instrument_token")
        else:
            row["pe_oi"] = built["oi"]
            row["pe_density"] = built["density"]
            row["pe_gex"] = built["gex"]
            row["pe_iv"] = built["iv"]
            row["pe_price_source"] = built["price_source"]
            row["_pe_token"] = built.get("instrument_token")

    strikes = sorted(per_strike.values(), key=lambda x: x["strike"])
    all_strike_vals = [x["strike"] for x in strikes]
    s_min, s_max = min(all_strike_vals), max(all_strike_vals)
    # Keep a full-chain copy for session-open capture before ATM window trim.
    all_strikes_for_oi = [dict(r) for r in strikes]

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

    oi_baseline_meta = attach_strike_oi_baselines(
        strikes,
        underlying,
        exp,
        oi_baseline_mode=baseline_mode,
        session_capture_rows=all_strikes_for_oi,
    )

    total_gex = round(total_gex_at_spot(resolved_legs, spot, tte, r=r, q=q), 2)
    gamma_regime = "positive" if total_gex >= 0 else "negative"
    # Chart-only +VE/−VE masses (Day spot dual lines). Signal triggers still use total_gex.
    pos_gex, neg_gex, _net_from_strikes = gex_components_from_strikes(strikes)

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
    reversals_gex_relaxed = False
    reversals_gex_waiting = False
    reversals_gex_samples = 0
    from options.gamma_density_history import (
        REVERSAL_GEX_MIN_SAMPLES,
        normalize_reversal_gex_mode,
    )

    gex_mode = normalize_reversal_gex_mode(reversal_gex_mode)
    reversals_gex_min_samples = REVERSAL_GEX_MIN_SAMPLES
    prior_history: list[dict[str, Any]] = []
    prior_daily_hhi: list[dict[str, Any]] = []
    if include_history:
        try:
            from options.gamma_density_history import (
                filter_daily_hhi_basis,
                get_daily_hhi_series,
                get_history,
            )

            prior_history = get_history(underlying, exp)
            # Like-for-like only: HHI's floor is 1/N, so a day recorded at a
            # different strike window (or mass basis) is a different measurement.
            prior_daily_hhi = filter_daily_hhi_basis(
                get_daily_hhi_series(underlying),
                basis=basis,
                strike_window=window,
                sign_mode=mode,
            )
        except Exception:
            prior_history = []
            prior_daily_hhi = []

    pin_threshold = float(d.get("pin_share_threshold") or PIN_SHARE_THRESHOLD)
    # Provisional concentration (intraday stats); session stats are refreshed after
    # upserting today's HHI so the sample includes the latest day-end value.
    concentration = compute_gamma_concentration(
        strikes,
        spot=spot,
        atm_strike=atm["strike"],
        call_wall=call_wall,
        put_wall=put_wall,
        strike_step=float(strike_step),
        history=prior_history,
        pin_threshold=pin_threshold,
        flip_level=scan["flip"],
        daily_hhi_history=prior_daily_hhi or None,
        mass_basis=basis,
    )
    if include_history and concentration.get("hhi") is not None:
        try:
            from options.gamma_density_history import (
                DAILY_HHI_PERCENTILE_N,
                filter_daily_hhi_basis,
                hhi_percentile_sessions,
                upsert_daily_hhi,
            )

            hhi_now = float(concentration["hhi"])
            daily_series = filter_daily_hhi_basis(
                upsert_daily_hhi(
                    underlying,
                    hhi_now,
                    basis=basis,
                    strike_window=window,
                    sign_mode=mode,
                    # Persist both measures so switching basis keeps the
                    # cross-session comparison instead of restarting it.
                    hhi_gross=concentration.get("hhi_gross"),
                    hhi_net=concentration.get("hhi_net"),
                ),
                basis=basis,
                strike_window=window,
                sign_mode=mode,
            )
            pct_30d, sess_n = hhi_percentile_sessions(
                daily_series, hhi_now, n=DAILY_HHI_PERCENTILE_N
            )
            concentration["hhi_percentile_30d"] = pct_30d
            concentration["hhi_session_count"] = sess_n
            recent = daily_series[-DAILY_HHI_PERCENTILE_N:]
            concentration["daily_hhi"] = [
                {
                    "date": str(row.get("date"))[:10],
                    "hhi": round(float(row["hhi"]), 4),
                    "band": _hhi_band(float(row["hhi"]), basis),
                }
                for row in recent
                if row.get("hhi") is not None
            ]
            concentration.update(_daily_hhi_stats(recent, hhi_now, basis=basis))
        except Exception:
            pass

    if include_history:
        # Calibration trail for pin strength. Its own try/except: the intraday
        # ``series`` trail is pruned to today, so this is the only record that
        # survives to answer "did a pin that looked strong at midday hold?" —
        # but a store failure must never cost the caller its snapshot.
        try:
            from options.gamma_density_history import record_pin_sample

            record_pin_sample(
                underlying,
                pin=concentration.get("pin_strike"),
                pin_source=concentration.get("pin_source"),
                pin_share=concentration.get("pin_share"),
                spot=spot,
                total_gex=total_gex,
                gamma_regime=gamma_regime,
                hhi=concentration.get("hhi"),
                flip_level=scan["flip"],
                sigma1_pts=(bands or {}).get("sigma1_pts"),
                strike_step=float(strike_step),
            )
        except Exception:
            pass

    conviction = compute_gamma_conviction(
        total_gex=total_gex,
        gamma_regime=gamma_regime,
        concentration=concentration,
        distance_to_flip=scan["distance_to_flip"],
        spot=spot,
        expected_move=bands,
        history=prior_history,
    )
    reference_levels = build_reference_levels(underlying)
    market_read = build_gamma_market_read(
        gamma_regime=gamma_regime,
        concentration=concentration,
        conviction=conviction,
        flip_level=scan["flip"],
        distance_to_flip=scan["distance_to_flip"],
        call_wall=call_wall,
        put_wall=put_wall,
        reference_levels=reference_levels,
    )
    momentum = compute_gamma_momentum(
        {
            "spot": spot,
            "total_gex": total_gex,
            "call_wall": call_wall,
            "put_wall": put_wall,
            "strike_step": float(strike_step),
            "atm_iv": atm_iv,
            "strikes": strikes,
            "concentration": concentration,
            "history": prior_history,
            "distance_to_flip": scan["distance_to_flip"],
            "flip_level": scan["flip"],
        }
    )

    if include_history:
        try:
            from kite_client import fetch_front_month_minute_candles, fetch_index_minute_spot
            from options.gamma_density_history import (
                adaptive_reversal_min_move,
                append_history_point,
                apply_partial_history_gex_policy,
                attach_volume_from_candles,
                build_chart_series,
                count_usable_gex_points,
                detect_spot_reversals,
                enrich_series_nearest_gex,
                gex_gate_match_sec,
                gex_history_recording_meta,
                get_history,
                minutes_since_session_open,
                normalize_reversal_tf,
                persist_session_reversals,
                resample_chart_series,
                resolve_gex_gate,
                reversal_tf_params,
                series_has_usable_volume,
                session_history_max_points,
            )

            # append_history_point reloads disk under lock; never starts from empty memory.
            # UI polls (build_session_chart=True) also append — belt-and-suspenders with
            # the background scheduler so the open desk never starves its own trail.
            history = append_history_point(
                underlying,
                exp,
                spot=spot,
                total_gex=total_gex,
                flip_level=scan["flip"],
                gamma_regime=gamma_regime,
                max_points=max(hist_max, session_history_max_points(underlying)),
                hhi=concentration.get("hhi"),
                conviction=conviction.get("score"),
                pin_strike=concentration.get("pin_strike"),
                atm_iv=atm_iv,
                pos_gex=pos_gex,
                neg_gex=neg_gex,
            )
            if not history:
                history = get_history(underlying, exp)

            # Scheduler samples only need the tick persisted — skip candle/reversal work.
            if not build_session_chart:
                reversals_gex_samples = count_usable_gex_points(history)
            else:
                note_gamma_desk_underlying(underlying)

                lookback = 40
                spot_candles: list[dict[str, Any]] = []
                try:
                    lookback = minutes_since_session_open(underlying)
                    spot_candles = fetch_index_minute_spot(underlying, minutes=lookback)
                except Exception:
                    spot_candles = []

                # Blue path stays 1m with short GEX attach (chart gaps stay honest).
                # Detection uses a TF-wider GEX lookback so sparse polls don't wipe pivots.
                chart_series = build_chart_series(underlying, history, spot_candles)
                # Cash-index volume is usually 0 — overlay front-month futures volume (chart-only).
                if not series_has_usable_volume(chart_series):
                    try:
                        fut_candles = fetch_front_month_minute_candles(
                            underlying, minutes=max(int(lookback), 40)
                        )
                        chart_series = attach_volume_from_candles(chart_series, fut_candles)
                    except Exception:
                        pass
                tf_key = normalize_reversal_tf(reversal_tf)
                tf_params = reversal_tf_params(tf_key)
                reversals_gex_samples = count_usable_gex_points(history)
                recording_meta_early = gex_history_recording_meta(underlying, history)
                gex_partial = bool(recording_meta_early.get("gex_history_partial"))
                effective_gate, reversals_gex_relaxed, reversals_gex_waiting = resolve_gex_gate(
                    bool(reversal_gex_gate),
                    history,
                    min_points=reversals_gex_min_samples,
                    mode=gex_mode,
                    history_partial=gex_partial,
                )
                # Live + Require GEX/OI: surface price pivots immediately as provisional
                # (muted) even while sparse-waiting or hard gate fails; Research keeps
                # hard reject / relax behavior (no provisional path).
                live_mode = gex_mode == "live"
                want_provisional = live_mode and (
                    bool(reversal_gex_gate) or bool(reversal_oi_gate)
                )
                detection_base = enrich_series_nearest_gex(
                    chart_series,
                    history,
                    match_sec=gex_gate_match_sec(tf_key),
                    underlying=underlying,
                )
                tf_series = resample_chart_series(detection_base, tf_key)
                min_move = adaptive_reversal_min_move(float(spot), tf_series)
                # Mid-session GEX: detect ungated, then gate only where samples exist.
                use_partial_hybrid = bool(reversal_gex_gate) and gex_partial
                # Sparse Live wait: still detect with hard gate + provisional emit so
                # the board is not blank until samples arrive (chips stay muted).
                if reversals_gex_waiting:
                    gate_for_detect = True
                elif use_partial_hybrid:
                    gate_for_detect = False
                else:
                    gate_for_detect = effective_gate
                candidates = detect_spot_reversals(
                    tf_series,
                    swing_bars=tf_params["swing_bars"],
                    confirm_bars=tf_params["confirm_bars"],
                    lock_ms=tf_params["lock_ms"],
                    min_move_pts=min_move,
                    gex_gate=gate_for_detect,
                    oi_gate=bool(reversal_oi_gate),
                    provisional_ungated=want_provisional,
                    tf=tf_key,
                    pin_strike=concentration.get("pin_strike"),
                    call_wall=call_wall,
                    put_wall=put_wall,
                    cliff_strike=concentration.get("cliff_strike"),
                    strike_step=float(strike_step),
                    strikes=strikes,
                )
                if use_partial_hybrid and effective_gate:
                    candidates = apply_partial_history_gex_policy(
                        tf_series,
                        candidates,
                        confirm_bars=tf_params["confirm_bars"],
                        provisional_ungated=want_provisional,
                    )
                # Freeze move_pts after confirm window so TF rebuilds don't drift labels.
                reversals = persist_session_reversals(
                    underlying,
                    exp,
                    candidates,
                    tf=tf_key,
                    confirm_bars=tf_params["confirm_bars"],
                    lock_ms=tf_params["lock_ms"],
                )
                # Display stays sparse: no reverse/forward GEX fill (avoids flat invented lines).
        except Exception:
            history = prior_history or history
            if not build_session_chart:
                try:
                    from options.gamma_density_history import (
                        count_usable_gex_points,
                        get_history,
                    )

                    if not history:
                        history = get_history(underlying, exp)
                    reversals_gex_samples = count_usable_gex_points(history)
                except Exception:
                    reversals_gex_samples = 0
                chart_series = []
                reversals = []
                reversals_gex_relaxed = False
                reversals_gex_waiting = False
            else:
                note_gamma_desk_underlying(underlying)
                try:
                    from options.gamma_density_history import (
                        build_chart_series,
                        get_history,
                        get_session_reversals,
                        normalize_reversal_tf,
                    )

                    if not history:
                        history = get_history(underlying, exp)
                    chart_series = build_chart_series(underlying, history, None)
                    reversals = get_session_reversals(
                        underlying, exp, normalize_reversal_tf(reversal_tf)
                    )
                except Exception:
                    chart_series = []
                    reversals = []
                reversals_gex_relaxed = False
                reversals_gex_waiting = False
                reversals_gex_samples = 0

    recording_meta: dict[str, Any] = {
        "gex_history_started_at": None,
        "gex_history_partial": False,
        "gex_history_points": 0,
    }
    if include_history:
        try:
            from options.gamma_density_history import gex_history_recording_meta

            recording_meta = gex_history_recording_meta(underlying, history)
        except Exception:
            pass

    # Pin strength. Needs chart_series (minute spot) for containment, so it runs
    # after the history block. Soft-fails to None — a desk must not lose its
    # snapshot because one derived read could not be computed.
    pin_lock_block = None
    try:
        from options.pin_lock import compute_pin_lock

        pin_lock_block = compute_pin_lock(
            pin_strike=concentration.get("pin_strike"),
            pin_source=concentration.get("pin_source"),
            spot=spot,
            strike_step=float(strike_step),
            history=history,
            chart_series=chart_series,
            strikes=strikes,
            flip_level=scan["flip"],
            sigma1_pts=(bands or {}).get("sigma1_pts"),
            window=pin_window,
        )
    except Exception:
        pin_lock_block = None

    # Session volume profile + per-strike volume, for the Concentration tab's
    # confluence readout and the Γ ladder's row tint. Gated on the full desk poll:
    # the multi-index strip runs thin snapshots and must not trigger one profile
    # integration per underlying (~200 ms each on a full NIFTY session).
    volume_profile_block = None
    strike_volume_block = None
    if build_session_chart:
        try:
            from analysis.volume_profile import get_volume_profile, strike_band_volume

            volume_profile_block = get_volume_profile(underlying)
            if volume_profile_block.get("available"):
                strike_volume_block = strike_band_volume(
                    underlying,
                    [float(r["strike"]) for r in strikes],
                    float(strike_step),
                )
        except Exception:
            volume_profile_block = None
            strike_volume_block = None

    # Expiry magnet: pressure = gamma weighted by the chance of settling there.
    # Pure arithmetic over strikes already in hand, so it costs nothing.
    expiry_magnet_block = None
    try:
        from options.expiry_magnet import build_expiry_magnet

        expiry_magnet_block = build_expiry_magnet(
            strikes=strikes,
            spot=spot,
            sigma_pts=(bands or {}).get("sigma1_pts"),
            dte=_days_to_expiry(exp),
            strike_step=float(strike_step),
            history=history,
        )
    except Exception:
        expiry_magnet_block = None

    # Structural state: confluence, levels in σ, and the classifier. Pure
    # arithmetic over blocks already computed above, so it costs nothing and
    # cannot fail the snapshot on its own.
    regime_block = None
    try:
        from options.regime import build_regime_block

        regime_block = build_regime_block(
            gamma_regime=gamma_regime,
            spot=spot,
            sigma1_pts=(bands or {}).get("sigma1_pts"),
            flip_level=scan["flip"],
            call_wall=call_wall,
            put_wall=put_wall,
            strike_step=float(strike_step),
            concentration=concentration,
            pin_lock=pin_lock_block,
            volume_profile=volume_profile_block,
        )
    except Exception:
        regime_block = None

    cas_block = None
    try:
        from options.cas_indicative import cas_for_snapshot

        cas_block = cas_for_snapshot(underlying)
    except Exception:
        cas_block = None

    # session_poc stays dict-or-None (unchanged contract). session_poc_status is
    # additive and always present, so the UI can say why Fut POC is blank instead
    # of silently dropping the chip — a closed market and a broken fetch used to
    # look identical.
    session_poc_block = None
    session_poc_status: dict[str, Any] = {"ok": False, "reason": "error"}
    try:
        from options.session_poc import compute_session_poc_detail

        detail = compute_session_poc_detail(underlying)
        if detail.get("poc") is not None:
            session_poc_block = detail
        session_poc_status = {
            "ok": detail.get("poc") is not None,
            "reason": detail.get("reason"),
        }
    except Exception as exc:
        log_event(
            _log,
            logging.WARNING,
            "session_poc_failed",
            underlying=underlying,
            error=f"{type(exc).__name__}: {exc}",
        )

    return {
        "underlying": underlying,
        "expiry": exp,
        "dte": _days_to_expiry(exp),
        "provider": prov.name,
        "spot": spot,
        "updated_at": datetime.now().astimezone().isoformat(),
        "tte_years": round(tte, 6),
        "atm_strike": atm["strike"],
        "atm_iv": atm_iv,
        "total_gex": total_gex,
        "total_gex_cr": round(total_gex / CRORE, 4),
        "pos_gex": pos_gex,
        "neg_gex": neg_gex,
        "pos_gex_cr": round(pos_gex / CRORE, 4),
        "neg_gex_cr": round(neg_gex / CRORE, 4),
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
        "pin_lock": pin_lock_block,
        "volume_profile": volume_profile_block,
        "strike_volume": strike_volume_block,
        "regime": regime_block,
        "expiry_magnet": expiry_magnet_block,
        "conviction": conviction,
        "momentum": momentum,
        "market_read": market_read,
        "reference_levels": reference_levels,
        "history": history,
        "chart_series": chart_series,
        "reversals": reversals,
        "reversals_gex_relaxed": reversals_gex_relaxed,
        "reversals_gex_waiting": reversals_gex_waiting,
        "reversals_gex_samples": reversals_gex_samples,
        "reversals_gex_min_samples": reversals_gex_min_samples,
        "reversal_gex_mode": gex_mode,
        "gex_history_started_at": recording_meta.get("gex_history_started_at"),
        "gex_history_partial": bool(recording_meta.get("gex_history_partial")),
        "gex_history_points": int(recording_meta.get("gex_history_points") or 0),
        "chain_legs_quoted": len(built_rows),
        "chain_legs_total": raw_total,
        "strike_window": window,
        "convexity_zones": convexity_zones,
        "strikes": strikes,
        "oi_baseline_mode": oi_baseline_meta.get("oi_baseline_mode", baseline_mode),
        "oi_baseline_note": oi_baseline_meta.get("oi_baseline_note"),
        "oi_baseline_open_count": oi_baseline_meta.get("oi_baseline_open_count", 0),
        "oi_baseline_prev_close_count": oi_baseline_meta.get(
            "oi_baseline_prev_close_count", 0
        ),
        "cas": cas_block,
        "session_poc": session_poc_block,
        "session_poc_status": session_poc_status,
    }


def _empty_concentration_summary_row(
    underlying: str,
    *,
    error: str | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    return {
        "underlying": underlying,
        "expiry": None,
        "spot": None,
        "hhi": None,
        "band": None,
        "band_label": None,
        "mass_basis": None,
        "pin_strike": None,
        "cliff_strike": None,
        "gini": None,
        "shape_quadrant": None,
        "hhi_percentile_30d": None,
        "hhi_session_count": None,
        "source": source,
        "error": error,
    }


def _enrich_summary_daily_hhi(
    row: dict[str, Any],
    underlying: str,
    *,
    basis: str | None = None,
    strike_window: int | None = None,
    sign_mode: str | None = None,
) -> None:
    """Fill 30d HHI percentile from the daily store (read-only; no upsert).

    The sample is restricted to days measured the same way as ``row`` — the strip
    runs a narrower window than the main board, so an unfiltered percentile would
    rank a window-8 HHI against window-20 history.
    """
    if row.get("hhi") is None:
        return
    try:
        from options.gamma_density_history import (
            DAILY_HHI_PERCENTILE_N,
            filter_daily_hhi_basis,
            get_daily_hhi_series,
            hhi_percentile_sessions,
        )

        daily = filter_daily_hhi_basis(
            get_daily_hhi_series(underlying),
            basis=basis,
            strike_window=strike_window,
            sign_mode=sign_mode,
        )
        pct, n = hhi_percentile_sessions(
            daily, float(row["hhi"]), n=DAILY_HHI_PERCENTILE_N
        )
        row["hhi_percentile_30d"] = pct
        row["hhi_session_count"] = n if n > 0 else row.get("hhi_session_count")
    except Exception:
        pass


def _summary_row_from_snapshot(snap: dict[str, Any]) -> dict[str, Any]:
    conc = snap.get("concentration") if isinstance(snap.get("concentration"), dict) else {}
    cliff = conc.get("cliff_strike")
    if cliff is None:
        cliff = snap.get("flip_level")
    row = {
        "underlying": snap.get("underlying"),
        "expiry": snap.get("expiry"),
        "spot": snap.get("spot"),
        "hhi": conc.get("hhi"),
        "band": conc.get("band"),
        "band_label": conc.get("band_label"),
        "mass_basis": conc.get("mass_basis"),
        "pin_strike": conc.get("pin_strike"),
        "cliff_strike": cliff,
        "gini": conc.get("gini"),
        "shape_quadrant": conc.get("shape_quadrant"),
        "hhi_percentile_30d": conc.get("hhi_percentile_30d"),
        "hhi_session_count": conc.get("hhi_session_count"),
        "source": "live",
        "error": None,
    }
    und = str(row.get("underlying") or "")
    if und and (row.get("hhi_percentile_30d") is None or row.get("hhi_session_count") is None):
        _enrich_summary_daily_hhi(
            row,
            und,
            basis=conc.get("mass_basis"),
            strike_window=snap.get("strike_window"),
            sign_mode=snap.get("sign_mode"),
        )
    return row


def _fallback_concentration_row(
    underlying: str,
    *,
    provider: GammaDensityDataProvider | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Soft degrade when a thin live snapshot fails.

    Prefer live thin snapshot first. Fallback uses last ``daily_hhi`` plus the
    latest pin / flip from session history so one bad underlying does not blank
    the multi-index strip.
    """
    row = _empty_concentration_summary_row(
        underlying, error=error, source="history"
    )
    expiry: str | None = None
    try:
        prov = provider or get_gamma_density_provider()
        expiry = prov.nearest_expiry(underlying)
        row["expiry"] = expiry
        spot = prov.get_spot(underlying)
        if spot is not None:
            row["spot"] = float(spot)
    except Exception:
        pass

    try:
        from options.gamma_density_history import (
            LEGACY_DAILY_HHI_BASIS,
            filter_daily_hhi_basis,
            get_daily_hhi_series,
            get_history,
        )

        daily = get_daily_hhi_series(underlying)
        # Prefer a day measured on the current basis; otherwise show the newest
        # cached value labelled with *its own* basis rather than mislabelling it.
        want_basis = normalize_mass_basis(_cfg().get("mass_basis"))
        matched = filter_daily_hhi_basis(daily, basis=want_basis)
        source_rows = matched or daily
        if source_rows:
            last = source_rows[-1]
            last_hhi = last.get("hhi")
            if last_hhi is not None:
                hhi = float(last_hhi)
                row_basis = str(last.get("basis") or LEGACY_DAILY_HHI_BASIS)
                band = _hhi_band(hhi, row_basis)
                row["hhi"] = round(hhi, 4)
                row["band"] = band
                row["band_label"] = BAND_LABELS[band]
                row["mass_basis"] = row_basis
                _enrich_summary_daily_hhi(
                    row,
                    underlying,
                    basis=row_basis,
                    strike_window=last.get("strike_window"),
                    sign_mode=last.get("sign_mode"),
                )

        hist: list[dict[str, Any]] = []
        if expiry:
            hist = get_history(underlying, expiry)
        if hist:
            last = hist[-1]
            pin = last.get("pin_strike")
            if pin is not None:
                row["pin_strike"] = float(pin)
            flip = last.get("flip_level")
            if flip is not None:
                row["cliff_strike"] = float(flip)
            if row.get("spot") is None and last.get("spot") is not None:
                row["spot"] = float(last["spot"])
    except Exception:
        pass

    if row.get("hhi") is None and error:
        row["source"] = "error"
    return row


def _thin_concentration_for_underlying(
    underlying: str,
    *,
    strike_window: int,
    provider: GammaDensityDataProvider | None = None,
    sign_mode: str | None = None,
) -> dict[str, Any]:
    """Nearest-expiry concentration via a narrow-window snapshot (no history write)."""
    try:
        snap = build_gamma_snapshot(
            underlying,
            strike_window=strike_window,
            sign_mode=sign_mode,
            provider=provider,
            include_multi_expiry=False,
            include_history=False,
            include_vanna_strip=False,
        )
        return _summary_row_from_snapshot(snap)
    except Exception as exc:
        return _fallback_concentration_row(
            underlying, provider=provider, error=str(exc)[:240]
        )


def build_concentration_summary(
    underlyings: list[str] | None = None,
    *,
    strike_window: int | None = None,
    provider: GammaDensityDataProvider | None = None,
    sign_mode: str | None = None,
    parallel: bool = True,
) -> dict[str, Any]:
    """Multi-index concentration strip — thin live snapshots in parallel.

    Defaults to cash indices only (not every MCX). Each underlying uses a narrow
    ``strike_window`` (default 8) with ``include_history=False`` so the strip
    stays cheap vs the full desk snapshot.
    """
    d = _cfg()
    window = (
        int(strike_window)
        if strike_window is not None
        else int(d.get("concentration_summary_window") or 8)
    )
    window = max(1, min(window, 60))

    if underlyings is None:
        names = default_concentration_underlyings()
    else:
        names = []
        for raw in underlyings:
            u = str(raw or "").upper().strip()
            if not u:
                continue
            if u not in INDEX_OPTIONS:
                names.append(u)  # keep order; row will carry error
                continue
            if u not in names:
                names.append(u)

    if not names:
        names = default_concentration_underlyings()

    prov = provider or get_gamma_density_provider()
    items: list[dict[str, Any]] = []

    def _one(u: str) -> dict[str, Any]:
        if u not in INDEX_OPTIONS:
            return _empty_concentration_summary_row(
                u, error=f"Unknown underlying '{u}'", source="error"
            )
        return _thin_concentration_for_underlying(
            u,
            strike_window=window,
            provider=prov,
            sign_mode=sign_mode,
        )

    if parallel and len(names) > 1:
        by_u: dict[str, dict[str, Any]] = {}
        workers = min(4, len(names))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_one, u): u for u in names}
            for fut in as_completed(futs):
                u = futs[fut]
                try:
                    by_u[u] = fut.result()
                except Exception as exc:
                    by_u[u] = _fallback_concentration_row(
                        u, provider=prov, error=str(exc)[:240]
                    )
        items = [by_u[u] for u in names if u in by_u]
    else:
        items = [_one(u) for u in names]

    return {
        "underlyings": names,
        "strike_window": window,
        "updated_at": datetime.now().astimezone().isoformat(),
        "provider": prov.name,
        "items": items,
    }


# ---------------------------------------------------------------------------
# Background session GEX recorder (scheduler) — persists ticks without UI open
# ---------------------------------------------------------------------------

# Cash + major MCX names the Gamma desk charts. Each wake samples *all* due
# in-session names (budgeted), not one round-robin pick — RR of 1 left CRUDE
# with near-empty trails while NIFTY UI polls filled only the open desk.
# Shared with options/oi_var.py via config.ANALYTICS_HISTORY_SAMPLE_UNDERLYINGS
# so both desks record the same underlyings.
_GEX_HISTORY_SAMPLE_CANDIDATES = ANALYTICS_HISTORY_SAMPLE_UNDERLYINGS
GEX_HISTORY_SAMPLE_INTERVAL_SEC = 30
# Failures retry sooner than a full success interval so a bad tick does not
# silence an underlying for a full minute while the desk is open.
GEX_HISTORY_SAMPLE_FAIL_BACKOFF_SEC = 20
# Cap work per scheduler wake so a slow MCX chain does not stall runners.
GEX_HISTORY_SAMPLE_BUDGET_SEC = 50
_gex_sample_last_ok: dict[str, float] = {}
_gex_last_desk_underlying: str | None = None


def default_gex_history_sample_underlyings() -> list[str]:
    """Underlyings the scheduler records GEX for while their session is open."""
    return [u for u in _GEX_HISTORY_SAMPLE_CANDIDATES if u in INDEX_OPTIONS]


def note_gamma_desk_underlying(underlying: str) -> None:
    """Remember the last underlying the Gamma Density UI charted (priority sample)."""
    global _gex_last_desk_underlying
    u = str(underlying or "").strip().upper()
    if u:
        _gex_last_desk_underlying = u


def _prioritized_gex_sample_names(due: list[str]) -> list[str]:
    """Desk-open underlying first, then majors, then remaining due names."""
    due_set = set(due)
    out: list[str] = []
    desk = _gex_last_desk_underlying
    if desk and desk in due_set:
        out.append(desk)
    for u in _GEX_HISTORY_SAMPLE_CANDIDATES:
        if u in due_set and u not in out:
            out.append(u)
    for u in due:
        if u not in out:
            out.append(u)
    return out


def maybe_sample_gex_history_periodic() -> bool:
    """Scheduler hook: persist GEX ticks for every due in-session underlying.

    Spot candles always cover the full session; GEX lines only exist where we
    recorded samples. UI polls append for the open underlying; this hook must
    cover the rest (especially MCX after cash close) or CRUDE shows a late
    fragment only.

    Samples all names whose last success is older than
    ``GEX_HISTORY_SAMPLE_INTERVAL_SEC``, prioritized by last-viewed desk +
    majors, within ``GEX_HISTORY_SAMPLE_BUDGET_SEC``. Never raises.
    """
    from options.gamma_density_history import in_session

    now = datetime.now(tz=IST)
    if now.weekday() >= 5:
        return False

    names = default_gex_history_sample_underlyings()
    if not names:
        return False

    now_ts = time.time()
    due = [
        u
        for u in names
        if in_session(u, now)
        and (now_ts - _gex_sample_last_ok.get(u, 0.0)) >= GEX_HISTORY_SAMPLE_INTERVAL_SEC
    ]
    if not due:
        return False

    ordered = _prioritized_gex_sample_names(due)
    deadline = now_ts + GEX_HISTORY_SAMPLE_BUDGET_SEC
    any_ok = False
    sampled = 0
    for underlying in ordered:
        if sampled > 0 and time.time() >= deadline:
            log_event(
                _log,
                logging.WARNING,
                "gex_history_sample_budget",
                sampled=sampled,
                remaining=",".join(ordered[ordered.index(underlying) :]),
            )
            break
        try:
            # Persist tick only — skip candle fetch / reversal / display fill.
            build_gamma_snapshot(
                underlying,
                include_multi_expiry=False,
                include_vanna_strip=False,
                include_history=True,
                build_session_chart=False,
            )
            _gex_sample_last_ok[underlying] = time.time()
            any_ok = True
            sampled += 1
        except Exception as exc:
            # Short backoff — do not silence the name for a full success interval.
            _gex_sample_last_ok[underlying] = (
                time.time()
                - GEX_HISTORY_SAMPLE_INTERVAL_SEC
                + GEX_HISTORY_SAMPLE_FAIL_BACKOFF_SEC
            )
            log_event(
                _log,
                logging.WARNING,
                "gex_history_sample_failed",
                underlying=underlying,
                error=str(exc),
            )
    return any_ok
