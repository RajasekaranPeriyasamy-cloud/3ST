"""Build a live IV-skew snapshot: chain -> quotes -> forward -> delta-space skew.

Two quote passes, both batched across every requested expiry, so the cost is two
Kite calls per snapshot no matter how many expiries are asked for:

1. **Sizing probe** — the handful of strikes nearest the reference. Enough to
   recover the forward and the ATM vol, and nothing more.
2. **Full window** — strikes sized from *that measured ATM vol*, so the window
   is as wide as the vol and the tenor actually require.

The second pass is why this is not a fixed strike count. A fixed count is a
guess that fails in opposite directions on the same desk: ±20 strikes still
falls short of 25Δ on 48-DTE BANKNIFTY, while the same 20 on 1-DTE SENSEX quotes
30 worthless legs to reach a delta the first three strikes already passed.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime
from statistics import NormalDist
from typing import Any
from zoneinfo import ZoneInfo

from config import INDEX_OPTIONS, IV_SKEW_DEFAULTS
from options.chain import get_chain, get_index_spot_detail, list_expiries
from options.iv import time_to_expiry_years
from options.oi_var import _quote_batches
from options.skew_metrics import atm_vol, build_skew, forward_from_chain

IST = ZoneInfo("Asia/Kolkata")

# Strikes each side of the money used purely to measure the forward and ATM vol
# before the real window is sized. 4 leaves slack over the 3 the forward median
# needs, so one strike with a missing leg does not sink the probe.
_PROBE_HALF_WIDTH = 4


def iv_skew_config() -> dict[str, Any]:
    d = IV_SKEW_DEFAULTS
    return {
        "underlyings": list(d["underlyings"]),
        "max_expiries": d["max_expiries"],
        "target_delta": d["target_delta"],
        "refresh_seconds": d["refresh_seconds"],
        "wing_delta": d["wing_delta"],
    }


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def usable_expiries(underlying: str, max_expiries: int) -> list[str]:
    """Listed expiries from today forward.

    The instrument dump keeps yesterday's expiry listed after it has settled
    (``list_expiries('NIFTY')`` returned 2026-08-11 on 2026-08-12), and an
    expired contract has no time value to solve an IV from.
    """
    today = _now_ist().date().isoformat()
    return [e for e in list_expiries(underlying) if str(e) >= today][:max_expiries]


def half_width_for(
    forward: float,
    tte_years: float,
    atm_iv: float,
    step: float,
    *,
    wing_delta: float,
    min_half_width: int,
    max_half_width: int,
) -> int:
    """Strikes each side needed to reach ``wing_delta`` on the call wing.

    Inverts Black-76 delta: ``N(d1) = w`` gives
    ``K = F·exp(σ²T/2 − z_w·σ√T)``. The call wing is the binding one — it sits
    further from the money than the put wing at equal delta — so sizing on it
    covers both.
    """
    if forward <= 0 or tte_years <= 0 or atm_iv <= 0 or step <= 0:
        return min_half_width
    z = NormalDist().inv_cdf(wing_delta)
    vol_t = atm_iv * math.sqrt(tte_years)
    strike = forward * math.exp(0.5 * atm_iv * atm_iv * tte_years - z * vol_t)
    offset = abs(strike - forward)
    return max(min_half_width, min(max_half_width, math.ceil(offset / step)))


def price_from_quote(quote: dict[str, Any] | None, max_relative_spread: float) -> tuple[float | None, str]:
    """Mid when there is a two-sided market, else LTP. Returns (price, reason).

    A leg whose bid/ask straddle is wider than ``max_relative_spread`` of its own
    mid is dropped rather than priced: on MCX wings that is a 5-paisa bid against
    a 40-paisa offer, and the IV it solves to is an artifact of the spread rather
    than a vol anyone is trading.
    """
    if not quote:
        return None, "no_quote"

    depth = quote.get("depth") or {}
    try:
        bid = float((depth.get("buy") or [{}])[0].get("price") or 0)
        ask = float((depth.get("sell") or [{}])[0].get("price") or 0)
    except (TypeError, ValueError):
        bid = ask = 0.0

    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
        if mid > 0 and (ask - bid) / mid > max_relative_spread:
            return None, "wide_spread"
        return mid, "mid"

    try:
        ltp = float(quote.get("last_price") or 0)
    except (TypeError, ValueError):
        return None, "no_quote"
    return (ltp, "ltp") if ltp > 0 else (None, "no_price")


# Strike -> tradingsymbol resolution only changes when the instrument dump is
# refreshed (daily), but get_chain rebuilds it from a pandas frame every call:
# profiled at 3.5s per expiry, 10.6s of a 16.4s snapshot, almost all of it a
# per-row pd.to_datetime re-guessing the date format 5,382 times. Cached here
# rather than in options/chain.py because that module is on the order-placement
# leg-resolution path and is not this desk's to change.
_CHAIN_TTL_SEC = 600.0

_chain_cache: dict[tuple[str, str], tuple[float, dict[float, dict[str, Any]], str]] = {}
_chain_lock = threading.Lock()


def clear_chain_cache() -> None:
    """Drop the cached strike maps. For tests and instrument-dump refreshes."""
    with _chain_lock:
        _chain_cache.clear()


def _strike_rows(underlying: str, expiry: str) -> tuple[dict[float, dict[str, Any]], str]:
    key = (underlying, expiry)
    now = time.monotonic()
    with _chain_lock:
        cached = _chain_cache.get(key)
        if cached and now - cached[0] < _CHAIN_TTL_SEC:
            return cached[1], cached[2]

    chain = get_chain(underlying, expiry)
    rows = {float(r["strike"]): r for r in chain.get("strikes", [])}
    exchange = str(chain.get("exchange") or INDEX_OPTIONS[underlying]["exchange"])
    with _chain_lock:
        _chain_cache[key] = (now, rows, exchange)
    return rows, exchange


def _leg_keys(
    rows: dict[float, dict[str, Any]],
    exchange: str,
    strikes: list[float],
) -> tuple[list[str], dict[str, tuple[float, str]]]:
    keys: list[str] = []
    index: dict[str, tuple[float, str]] = {}
    for k in strikes:
        row = rows.get(k)
        if not row:
            continue
        for lower, otype in (("ce", "CE"), ("pe", "PE")):
            leg = row.get(lower)
            if not leg or not leg.get("tradingsymbol"):
                continue
            key = f"{leg.get('exchange', exchange)}:{leg['tradingsymbol']}"
            keys.append(key)
            index[key] = (k, otype)
    return keys, index


def _pairs_from_quotes(
    index: dict[str, tuple[float, str]],
    quotes: dict[str, dict[str, Any]],
    max_relative_spread: float,
) -> tuple[dict[float, dict[str, float | None]], dict[str, int]]:
    pairs: dict[float, dict[str, float | None]] = {}
    dropped: dict[str, int] = {}
    for key, (strike, otype) in index.items():
        price, reason = price_from_quote(quotes.get(key), max_relative_spread)
        if price is None:
            dropped[reason] = dropped.get(reason, 0) + 1
        pairs.setdefault(float(strike), {})[otype] = price
    return pairs, dropped


def _window(atm: float, step: float, half_width: int) -> list[float]:
    return [atm + i * step for i in range(-half_width, half_width + 1)]


def build_iv_skew(
    underlying: str,
    expiries: list[str] | None = None,
    *,
    max_expiries: int | None = None,
    target_delta: float | None = None,
) -> dict[str, Any]:
    """Delta-space skew for one underlying across its nearest expiries."""
    u = str(underlying).upper()
    if u not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    cfg = IV_SKEW_DEFAULTS
    n_exp = int(max_expiries if max_expiries is not None else cfg["max_expiries"])
    tgt = float(target_delta if target_delta is not None else cfg["target_delta"])
    r = float(cfg["risk_free_rate"])
    max_spread = float(cfg["max_relative_spread"])
    step = float(INDEX_OPTIONS[u]["strike_step"])

    meta = INDEX_OPTIONS[u]
    # Mirrors the branch inside get_index_spot_detail. Its second return value is
    # a failure *reason*, not the source — reading it as one labelled every MCX
    # underlying "spot" on the page when the reference is the front future.
    ref_source = str(meta.get("spot_source") or ("index" if meta.get("index_token_key") else "future"))

    reference, reason = get_index_spot_detail(u)
    if not reference:
        raise RuntimeError(
            f"No reference price for {u}: {reason or 'is the Kite session live?'}"
        )
    reference = float(reference)

    available = usable_expiries(u, max_expiries=99)
    if expiries:
        chosen = [e for e in expiries if e in available]
        if not chosen:
            raise RuntimeError(f"None of {expiries} are listed for {u}. Available: {available[:5]}")
    else:
        chosen = available[:n_exp]
    if not chosen:
        raise RuntimeError(f"No unexpired expiries listed for {u}")

    atm = round(reference / step) * step

    # --- pass 1: sizing probe -------------------------------------------------
    rows_by_expiry: dict[str, tuple[dict[float, dict[str, Any]], str]] = {}
    probe_keys: list[str] = []
    probe_index: dict[str, dict[str, tuple[float, str]]] = {}
    for e in chosen:
        rows, exchange = _strike_rows(u, e)
        rows_by_expiry[e] = (rows, exchange)
        keys, index = _leg_keys(rows, exchange, _window(atm, step, _PROBE_HALF_WIDTH))
        probe_keys.extend(keys)
        probe_index[e] = index

    probe_quotes = _quote_batches(probe_keys)

    plan: dict[str, dict[str, Any]] = {}
    full_keys: list[str] = []
    full_index: dict[str, dict[str, tuple[float, str]]] = {}
    for e in chosen:
        tte = time_to_expiry_years(e)
        if tte is None:
            continue
        pairs, _ = _pairs_from_quotes(probe_index[e], probe_quotes, max_spread)
        fwd = forward_from_chain(pairs, reference, tte, r)
        forward = fwd["forward"]
        if forward is None:
            plan[e] = {"tte": tte, "error": "no strike near the money had both a call and a put quote"}
            continue
        seed = atm_vol(pairs, forward, tte, r)
        seed_iv = seed["iv"]
        if not seed_iv:
            plan[e] = {"tte": tte, "error": "ATM vol did not solve — quotes look stale"}
            continue

        half = half_width_for(
            forward,
            tte,
            seed_iv,
            step,
            wing_delta=float(cfg["wing_delta"]),
            min_half_width=int(cfg["min_half_width"]),
            max_half_width=int(cfg["max_half_width"]),
        )
        rows, exchange = rows_by_expiry[e]
        window_atm = round(forward / step) * step
        keys, index = _leg_keys(rows, exchange, _window(window_atm, step, half))
        full_keys.extend(keys)
        full_index[e] = index
        plan[e] = {
            "tte": tte,
            "half_width": half,
            "seed_iv": seed_iv,
            "legs": len(keys),
            "strikes": len({strike for strike, _ in index.values()}),
        }

    # --- pass 2: the sized window --------------------------------------------
    quotes = _quote_batches(full_keys) if full_keys else {}

    out_expiries: list[dict[str, Any]] = []
    today = _now_ist().date()
    for e in chosen:
        info = plan.get(e)
        if not info:
            continue
        dte = (date.fromisoformat(e[:10]) - today).days
        if info.get("error"):
            out_expiries.append({"expiry": e, "dte": dte, "ok": False, "error": info["error"]})
            continue

        pairs, dropped = _pairs_from_quotes(full_index[e], quotes, max_spread)
        skew = build_skew(pairs, reference, info["tte"], risk_free_rate=r, target_delta=tgt)
        out_expiries.append(_format_expiry(e, dte, skew, info, dropped, cfg))

    return {
        "underlying": u,
        "label": meta.get("label", u),
        "exchange": meta["exchange"],
        "reference": round(reference, 2),
        "reference_source": ref_source,
        "strike_step": step,
        "target_delta": tgt,
        "expiries": out_expiries,
        "updated_at": _now_ist().isoformat(timespec="seconds"),
    }


def _pct(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value * 100, digits)


def _format_expiry(
    expiry: str,
    dte: int,
    skew: dict[str, Any],
    info: dict[str, Any],
    dropped: dict[str, int],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    """Flatten one expiry's skew to the wire shape, in vol points."""
    if not skew.get("ok"):
        return {
            "expiry": expiry,
            "dte": dte,
            "ok": False,
            # Present on every row, resolved or not, so the page has one shape
            # to render rather than two.
            "confidence": "unavailable",
            "quality": skew.get("quality", "unavailable"),
            "error": skew.get("error", "skew did not resolve"),
            "warnings": [skew.get("error", "skew did not resolve")],
            "forward": skew.get("forward"),
            "half_width": info.get("half_width"),
            "legs_dropped": dropped,
        }

    atm = skew["atm"]
    call, put = skew["call_wing"], skew["put_wing"]
    warnings: list[str] = []

    gap = atm.get("parity_gap")
    if gap is None:
        # Measured 2026-08-12 on 76-DTE BANKNIFTY: 45 of 81 legs dropped for
        # wide spreads, the ATM put never solved, and the desk still printed
        # RR +6.90 labelled "interpolated". A missing parity check is the loudest
        # signal available that the chain is too thin to read, so it must warn.
        warnings.append("ATM call or put did not solve — no parity check on this expiry")
    elif gap > float(cfg["parity_gap_warn"]):
        warnings.append(
            f"ATM call and put solve {gap * 100:.2f} vol points apart — the forward or a quote is off"
        )

    forward = skew["forward"]
    spread_bps = (skew["forward_spread"] / forward) * 10_000 if forward else 0.0
    if spread_bps > float(cfg["forward_spread_warn_bps"]):
        warnings.append(f"near-ATM strikes disagree on the forward by {spread_bps:.0f} bps")

    if skew["quality"] == "extrapolated":
        warnings.append("25Δ was not reached by the strike window — the wing IV is extrapolated")
    if skew["quality"] == "unavailable":
        warnings.append("a wing had no resolvable legs")

    for name, wing in (("call", call), ("put", put)):
        bracket = wing.get("bracket_gap")
        if bracket is not None and bracket > float(cfg["max_bracket_gap"]):
            warnings.append(
                f"{name} wing is sparse near 25Δ — interpolated across {bracket:.2f} of delta"
            )

    # Measured against the strikes in the window, not the legs quoted: the
    # construction uses exactly one OTM leg per strike, so a strike that yielded
    # no vol point is the real loss. Counting all legs instead would flag every
    # healthy chain, whose deep ITM wing is always wide.
    strikes = int(info.get("strikes") or 0)
    missing = max(0, strikes - len(skew["points"]))
    if strikes and missing / strikes > float(cfg["max_drop_ratio"]):
        warnings.append(
            f"{missing} of {strikes} strikes yielded no usable vol point — chain is thin"
        )

    return {
        "expiry": expiry,
        "dte": dte,
        "ok": True,
        "tte_years": round(skew["tte_years"], 6),
        "forward": round(forward, 2),
        "forward_basis": round(skew["forward_basis"], 2),
        "forward_spread_bps": round(spread_bps, 1),
        "atm_strike": atm["strike"],
        "atm_iv": _pct(atm["iv"]),
        "atm_parity_gap": _pct(atm["parity_gap"], 3),
        "call_iv": _pct(call["iv"]),
        "put_iv": _pct(put["iv"]),
        "call_quality": call["quality"],
        "put_quality": put["quality"],
        "call_delta_range": [round(d, 3) for d in call["delta_range"]] if call["delta_range"] else None,
        "put_delta_range": [round(d, 3) for d in put["delta_range"]] if put["delta_range"] else None,
        "risk_reversal": _pct(skew["risk_reversal"]),
        "butterfly": _pct(skew["butterfly"]),
        "quality": skew["quality"],
        # Separate from ``quality``: that says how 25Δ was obtained, this says
        # whether the underlying chain was good enough to believe the answer.
        "confidence": "degraded" if warnings else "clean",
        "call_bracket_gap": round(call["bracket_gap"], 3) if call.get("bracket_gap") is not None else None,
        "put_bracket_gap": round(put["bracket_gap"], 3) if put.get("bracket_gap") is not None else None,
        "half_width": info.get("half_width"),
        "legs_resolved": len(skew["points"]),
        "legs_dropped": dropped,
        "warnings": warnings,
        "points": [
            {
                "strike": p["strike"],
                "abs_delta": p["abs_delta"],
                "iv": _pct(p["iv"]),
                "option_type": p["option_type"],
            }
            for p in skew["points"]
        ],
    }
