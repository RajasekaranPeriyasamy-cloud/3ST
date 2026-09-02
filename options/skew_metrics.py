"""Delta-space volatility skew — 25Δ risk reversal, butterfly, ATM vol.

Separate from :mod:`options.iv` because it works in **forward** space, not spot
space, and that distinction is the whole point of the module.

``options/iv_smile.py`` and ``options/vol_surface.py`` solve IV from spot with a
flat ``r = 6.5%``. For NSE index options that is materially wrong: the market's
own forward carries more than the assumed rate (measured 2026-08-12 on NIFTY:
+54 points at 6 DTE against ~26 implied by 6.5%), and the residual lands in the
IV solve as a **put-call parity violation** — the same strike solving to 11.7%
off the call and 9.6% off the put. That gap is not skew; one strike has one
implied vol. It biases calls up and puts down by roughly a vol point each, which
is enough to flip the sign of a 25Δ risk reversal from −0.8 to +0.5.

So the forward is recovered from the options themselves via put-call parity
(``F = K + (C − P)·e^{rT}``) and every IV here is Black-76 on that forward. The
same code path serves MCX, where the underlying is already a future: parity
recovers the correct *per-expiry* contract month without needing to map an
option expiry onto a futures contract at all.

Pure functions only — no Kite, no I/O — so the tests run offline.
"""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Literal, NamedTuple

from vollib.black.greeks.analytical import delta as _b76_delta
from vollib.black.implied_volatility import implied_volatility as _b76_iv

OptionType = Literal["CE", "PE"]

DEFAULT_RISK_FREE = 0.065
DEFAULT_TARGET_DELTA = 0.25

# Same sanity band as options.iv.implied_volatility — a solve outside it is a
# bad quote, not a real vol.
_IV_MAX = 5.0

# How many near-ATM strikes contribute to the parity forward. One strike is
# enough in principle, but a single stale leg then moves the forward and every
# IV built on it; the median of three is robust to exactly that.
_FORWARD_STRIKES = 3

Quality = Literal["interpolated", "extrapolated", "unavailable"]


class SkewPoint(NamedTuple):
    """One resolved OTM leg on the delta axis."""

    strike: float
    abs_delta: float
    iv: float  # decimal, e.g. 0.11 = 11%
    option_type: OptionType


class DeltaQuote(NamedTuple):
    """IV read off the wing at a target delta, with how it was obtained.

    ``quality`` matters as much as ``iv``: an extrapolated 25Δ is a number the
    strike window never actually observed, and it should not be traded on.

    ``bracket_gap`` is the delta distance between the two points interpolated
    between. A wide bracket means the wing was sparse — legs dropped for bad
    quotes — and the 25Δ read is a long straight line across a curve rather than
    a measurement. It is the difference between "interpolated" and "interpolated
    from anything worth interpolating".
    """

    iv: float | None
    quality: Quality
    nearest_abs_delta: float | None
    points_used: int
    bracket_gap: float | None = None


def _flag(option_type: OptionType | str) -> str:
    return "c" if str(option_type).upper() == "CE" else "p"


def implied_forward(
    strike: float,
    call_price: float | None,
    put_price: float | None,
    tte_years: float,
    risk_free_rate: float = DEFAULT_RISK_FREE,
) -> float | None:
    """Forward implied by put-call parity at one strike: ``F = K + (C − P)·e^{rT}``."""
    if call_price is None or put_price is None:
        return None
    if call_price <= 0 or put_price <= 0 or strike <= 0 or tte_years <= 0:
        return None
    fwd = strike + (call_price - put_price) * math.exp(risk_free_rate * tte_years)
    return fwd if fwd > 0 else None


def forward_from_chain(
    pairs: dict[float, dict[str, float | None]],
    reference: float,
    tte_years: float,
    risk_free_rate: float = DEFAULT_RISK_FREE,
) -> dict[str, Any]:
    """Recover the forward from the strikes nearest ``reference``.

    ``pairs`` maps strike -> {"CE": price, "PE": price}. ``reference`` is spot
    (indices) or the front future (MCX) and is only used to choose which strikes
    sit closest to the money — it never enters the forward itself.
    """
    usable = [
        (k, implied_forward(k, legs.get("CE"), legs.get("PE"), tte_years, risk_free_rate))
        for k, legs in pairs.items()
    ]
    candidates = sorted(
        ((k, f) for k, f in usable if f is not None),
        key=lambda kv: abs(kv[0] - reference),
    )[:_FORWARD_STRIKES]

    if not candidates:
        return {"forward": None, "strikes_used": [], "basis": None, "spread": None}

    forwards = [f for _, f in candidates]
    fwd = float(median(forwards))
    return {
        "forward": fwd,
        "strikes_used": [k for k, _ in candidates],
        "basis": fwd - reference,
        # Disagreement between the near-ATM strikes. A wide spread means the
        # quotes are stale or crossed and everything downstream is suspect.
        "spread": max(forwards) - min(forwards) if len(forwards) > 1 else 0.0,
    }


def black76_iv(
    price: float | None,
    forward: float | None,
    strike: float,
    tte_years: float,
    option_type: OptionType | str,
    risk_free_rate: float = DEFAULT_RISK_FREE,
) -> float | None:
    """Black-76 IV from a market (already discounted) option price."""
    if price is None or forward is None or not price > 0 or not forward > 0:
        return None
    if strike <= 0 or tte_years <= 0:
        return None
    try:
        iv = float(_b76_iv(price, forward, strike, risk_free_rate, tte_years, _flag(option_type)))
    except Exception:
        return None
    if not 0 < iv <= _IV_MAX or math.isnan(iv):
        return None
    return iv


def black76_delta(
    forward: float | None,
    strike: float,
    tte_years: float,
    iv: float | None,
    option_type: OptionType | str,
    risk_free_rate: float = DEFAULT_RISK_FREE,
) -> float | None:
    """Black-76 delta (discounted, i.e. ``e^{−rT}·N(d1)`` for a call).

    Discounted rather than forward delta because that is what py_vollib returns;
    at index/MCX tenors the two differ by <0.1% of a delta and no 25Δ strike
    selection turns on it.
    """
    if forward is None or iv is None or not forward > 0 or not iv > 0 or tte_years <= 0:
        return None
    try:
        return float(_b76_delta(_flag(option_type), forward, strike, tte_years, risk_free_rate, iv))
    except Exception:
        return None


def otm_wings(
    pairs: dict[float, dict[str, float | None]],
    forward: float,
    tte_years: float,
    risk_free_rate: float = DEFAULT_RISK_FREE,
) -> tuple[list[SkewPoint], list[SkewPoint]]:
    """Split the chain into OTM call and OTM put wings, priced off the forward.

    OTM-only is the standard construction: ITM legs carry the wider spread and
    the early-exercise/parity noise, and under parity they add no information
    the OTM leg at the same strike does not already carry.
    """
    calls: list[SkewPoint] = []
    puts: list[SkewPoint] = []
    for strike, legs in sorted(pairs.items()):
        otype: OptionType = "CE" if strike >= forward else "PE"
        iv = black76_iv(legs.get(otype), forward, strike, tte_years, otype, risk_free_rate)
        if iv is None:
            continue
        d = black76_delta(forward, strike, tte_years, iv, otype, risk_free_rate)
        if d is None:
            continue
        point = SkewPoint(strike=float(strike), abs_delta=abs(d), iv=iv, option_type=otype)
        (calls if otype == "CE" else puts).append(point)
    return calls, puts


def iv_at_abs_delta(points: list[SkewPoint], target: float = DEFAULT_TARGET_DELTA) -> DeltaQuote:
    """Linear interpolation of IV onto the |delta| axis.

    Deliberately refuses to invent a wing it never saw: outside the observed
    delta range this returns the nearest point's IV flagged ``extrapolated``
    rather than projecting the smile slope outward.
    """
    if not points:
        return DeltaQuote(None, "unavailable", None, 0)

    ordered = sorted(points, key=lambda p: p.abs_delta)
    lo, hi = ordered[0], ordered[-1]

    if target < lo.abs_delta:
        return DeltaQuote(lo.iv, "extrapolated", lo.abs_delta, len(ordered))
    if target > hi.abs_delta:
        return DeltaQuote(hi.iv, "extrapolated", hi.abs_delta, len(ordered))

    for left, right in zip(ordered, ordered[1:], strict=False):
        if left.abs_delta <= target <= right.abs_delta:
            span = right.abs_delta - left.abs_delta
            if span <= 0:
                return DeltaQuote(left.iv, "interpolated", left.abs_delta, len(ordered), 0.0)
            w = (target - left.abs_delta) / span
            return DeltaQuote(
                left.iv + w * (right.iv - left.iv), "interpolated", target, len(ordered), span
            )

    return DeltaQuote(hi.iv, "extrapolated", hi.abs_delta, len(ordered))


def atm_vol(
    pairs: dict[float, dict[str, float | None]],
    forward: float,
    tte_years: float,
    risk_free_rate: float = DEFAULT_RISK_FREE,
) -> dict[str, Any]:
    """IV at the strike nearest the forward, solved from both legs.

    Both legs are reported because their agreement is the module's own
    correctness check: on a forward recovered from parity the call and put at a
    given strike must solve to the same vol. A ``parity_gap`` materially above
    zero means the forward is wrong or a quote is stale — the exact failure this
    module exists to remove, so it is surfaced rather than averaged away.
    """
    candidates = [k for k in pairs if k > 0]
    if not candidates:
        return {"strike": None, "iv": None, "ce_iv": None, "pe_iv": None, "parity_gap": None}

    strike = min(candidates, key=lambda k: abs(k - forward))
    legs = pairs[strike]
    ce = black76_iv(legs.get("CE"), forward, strike, tte_years, "CE", risk_free_rate)
    pe = black76_iv(legs.get("PE"), forward, strike, tte_years, "PE", risk_free_rate)
    solved = [v for v in (ce, pe) if v is not None]
    return {
        "strike": float(strike),
        "iv": sum(solved) / len(solved) if solved else None,
        "ce_iv": ce,
        "pe_iv": pe,
        "parity_gap": abs(ce - pe) if ce is not None and pe is not None else None,
    }


def build_skew(
    pairs: dict[float, dict[str, float | None]],
    reference: float,
    tte_years: float,
    *,
    risk_free_rate: float = DEFAULT_RISK_FREE,
    target_delta: float = DEFAULT_TARGET_DELTA,
) -> dict[str, Any]:
    """Full delta-space skew for one underlying/expiry.

    ``risk_reversal`` follows the market convention **IV(call) − IV(put)**:
    positive means the upside tail is the expensive one. Note this is the
    opposite sign to ``iv_smile._iv_skew``, which reports put − call at a fixed
    ±5% strike distance; the two are not interchangeable and a fixed-percentage
    skew is not comparable across days as vol and DTE move.
    """
    fwd = forward_from_chain(pairs, reference, tte_years, risk_free_rate)
    forward = fwd["forward"]
    if forward is None:
        return {
            "ok": False,
            "error": "no strike had both a call and a put quote — cannot recover the forward",
            "forward": None,
            "reference": reference,
        }

    calls, puts = otm_wings(pairs, forward, tte_years, risk_free_rate)
    call_q = iv_at_abs_delta(calls, target_delta)
    put_q = iv_at_abs_delta(puts, target_delta)
    atm = atm_vol(pairs, forward, tte_years, risk_free_rate)

    rr = fly = None
    if call_q.iv is not None and put_q.iv is not None:
        rr = call_q.iv - put_q.iv
        if atm["iv"] is not None:
            fly = (call_q.iv + put_q.iv) / 2 - atm["iv"]

    qualities = (call_q.quality, put_q.quality)
    quality: Quality = (
        "unavailable" if "unavailable" in qualities
        else "extrapolated" if "extrapolated" in qualities
        else "interpolated"
    )

    def _delta_range(points: list[SkewPoint]) -> list[float] | None:
        if not points:
            return None
        deltas = [p.abs_delta for p in points]
        return [min(deltas), max(deltas)]

    return {
        "ok": rr is not None,
        "reference": reference,
        "forward": forward,
        "forward_basis": fwd["basis"],
        "forward_strikes": fwd["strikes_used"],
        "forward_spread": fwd["spread"],
        "tte_years": tte_years,
        "target_delta": target_delta,
        "atm": atm,
        "call_wing": {
            "iv": call_q.iv,
            "quality": call_q.quality,
            "nearest_abs_delta": call_q.nearest_abs_delta,
            "points": len(calls),
            "delta_range": _delta_range(calls),
            "bracket_gap": call_q.bracket_gap,
        },
        "put_wing": {
            "iv": put_q.iv,
            "quality": put_q.quality,
            "nearest_abs_delta": put_q.nearest_abs_delta,
            "points": len(puts),
            "delta_range": _delta_range(puts),
            "bracket_gap": put_q.bracket_gap,
        },
        "risk_reversal": rr,
        "butterfly": fly,
        "quality": quality,
        "points": [
            {
                "strike": p.strike,
                "abs_delta": round(p.abs_delta, 4),
                "iv": round(p.iv, 6),
                "option_type": p.option_type,
            }
            for p in sorted(calls + puts, key=lambda p: p.strike)
        ],
    }
