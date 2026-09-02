"""Statutory + brokerage cost model for option legs and multi-leg combos.

Nothing on this desk means anything until edge is net of costs. In Indian
options the charge stack routinely exceeds the theoretical edge of a
model-free arbitrage, so every detector runs its gross number through
``combo_cost`` before it is allowed to surface.

Two costs dominate and are easy to forget:

* **Exchange transaction charges are on premium turnover**, not on notional.
  At ~0.05% per leg an eight-charge-event box eats several points on its own.
* **STT on exercise is 0.125% of *intrinsic*** for cash-settled index options
  (``stt_exercise_pct``). A box held to expiry has one deep-ITM long leg whose
  intrinsic grows without bound as spot moves away from the strikes — which is
  why an index box quoted at a discount to its width usually is still a loser.
  See ``box_exercise_cost``.

All ``*_pct`` fields are **percent**, not fractions: ``0.1`` means 0.1%. The
single conversion happens in ``_pct``.

Rates are Zerodha's published F&O / commodity charges as of ``RATES_ASOF`` and
they do change (BSE has revised its derivatives transaction charge more than
once). They are deliberately overridable at runtime from
``data/opt_arb_config.json`` via ``analysis.opt_arb.store`` — verify against
the current charge list before sizing anything real.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Literal

RATES_ASOF = "2026-08"

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class ChargeRates:
    """Per-segment charge schedule. Percentages, not fractions."""

    label: str
    brokerage_per_order: float = 20.0
    brokerage_pct: float = 0.0
    txn_pct: float = 0.0
    ipft_pct: float = 0.0
    sebi_pct: float = 0.0001
    stt_sell_pct: float = 0.0
    stt_exercise_pct: float = 0.0
    stamp_buy_pct: float = 0.003
    gst_pct: float = 18.0
    physical_settlement: bool = False


# NSE equity-derivative options. STT 0.1% on sell premium (since 2024-10-01),
# 0.125% of intrinsic on exercise. IPFT is Rs 50 per crore of premium turnover.
NFO_OPTIONS = ChargeRates(
    label="NSE F&O options",
    txn_pct=0.0495,
    ipft_pct=0.0005,
    stt_sell_pct=0.1,
    stt_exercise_pct=0.125,
)

# BSE (SENSEX / BANKEX) options. Same statutory stack as NSE; the exchange
# transaction charge is the field BSE keeps revising — check before trusting.
BFO_OPTIONS = replace(NFO_OPTIONS, label="BSE F&O options")

# MCX commodity options. CTT replaces STT (0.05% on option sell). On expiry an
# in-the-money MCX option devolves into a futures position rather than settling
# in cash, so there is no intrinsic-based exercise levy — the levy then applies
# to the resulting futures leg when it is closed, which this model treats as a
# separate futures trade rather than folding it in here.
MCX_OPTIONS = ChargeRates(
    label="MCX commodity options",
    txn_pct=0.0418,
    stt_sell_pct=0.05,
    stt_exercise_pct=0.0,
)

# NSE stock options are *physically settled*. An unbalanced leg at expiry is a
# delivery obligation, not a cash difference — flagged so detectors can refuse
# to recommend holding a stock structure through expiry.
NFO_STOCK_OPTIONS = replace(NFO_OPTIONS, label="NSE stock options", physical_settlement=True)

DEFAULT_RATES: dict[str, ChargeRates] = {
    "NFO": NFO_OPTIONS,
    "NFO_STOCK": NFO_STOCK_OPTIONS,
    "BFO": BFO_OPTIONS,
    "MCX": MCX_OPTIONS,
}

_RATES: dict[str, ChargeRates] = dict(DEFAULT_RATES)


def _pct(value: float) -> float:
    """The one place percent becomes a fraction."""
    return float(value) / 100.0


def rates_for(segment: str) -> ChargeRates:
    key = str(segment or "").upper()
    if key in _RATES:
        return _RATES[key]
    return _RATES.get("NFO", NFO_OPTIONS)


def all_rates() -> dict[str, dict[str, Any]]:
    return {k: asdict(v) for k, v in sorted(_RATES.items())}


def set_rates(segment: str, overrides: dict[str, Any]) -> ChargeRates:
    """Apply a partial override to one segment. Unknown keys are ignored."""
    key = str(segment or "").upper()
    base = _RATES.get(key) or DEFAULT_RATES.get(key) or NFO_OPTIONS
    fields = set(asdict(base))
    clean: dict[str, Any] = {}
    for name, value in (overrides or {}).items():
        if name not in fields or name == "label":
            continue
        if name == "physical_settlement":
            clean[name] = bool(value)
            continue
        try:
            clean[name] = float(value)
        except (TypeError, ValueError):
            continue
    updated = replace(base, **clean)
    _RATES[key] = updated
    return updated


def reset_rates() -> None:
    _RATES.clear()
    _RATES.update(DEFAULT_RATES)


@dataclass(frozen=True)
class LegCost:
    """Charges for one option leg. ``units`` is lots x contract size."""

    segment: str
    side: str
    price: float
    units: float
    turnover: float
    brokerage: float
    txn: float
    ipft: float
    sebi: float
    stt: float
    stamp: float
    gst: float
    total: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def leg_cost(
    segment: str,
    side: Side,
    price: float,
    units: float,
    *,
    rates: ChargeRates | None = None,
) -> LegCost:
    """Charges for a single option order.

    ``units`` is the *underlying quantity*, i.e. lots x contract size. For an
    MCX big/mini pair the two sides carry different contract sizes at the same
    per-unit premium, and that difference is exactly what the turnover-based
    charges pick up.
    """
    r = rates or rates_for(segment)
    px = max(float(price), 0.0)
    qty = max(float(units), 0.0)
    turnover = px * qty
    buying = str(side).upper() == "BUY"

    brokerage = r.brokerage_per_order + turnover * _pct(r.brokerage_pct)
    txn = turnover * _pct(r.txn_pct)
    ipft = turnover * _pct(r.ipft_pct)
    sebi = turnover * _pct(r.sebi_pct)
    stt = 0.0 if buying else turnover * _pct(r.stt_sell_pct)
    stamp = turnover * _pct(r.stamp_buy_pct) if buying else 0.0
    # GST is on the service fees only — never on STT/CTT or stamp duty.
    gst = (brokerage + txn + ipft + sebi) * _pct(r.gst_pct)
    total = brokerage + txn + ipft + sebi + stt + stamp + gst

    return LegCost(
        segment=str(segment).upper(),
        side="BUY" if buying else "SELL",
        price=round(px, 4),
        units=qty,
        turnover=round(turnover, 2),
        brokerage=round(brokerage, 2),
        txn=round(txn, 2),
        ipft=round(ipft, 2),
        sebi=round(sebi, 2),
        stt=round(stt, 2),
        stamp=round(stamp, 2),
        gst=round(gst, 2),
        total=round(total, 2),
    )


def combo_cost(legs: list[dict[str, Any]], *, round_trip: bool = True) -> dict[str, Any]:
    """Total charges for a multi-leg structure.

    Each item in ``legs`` needs ``segment``, ``side``, ``price``, ``units``.

    ``round_trip=True`` (the default) also charges the closing trade, each side
    reversed at the same price. That is the honest default for a scanner: a
    violation you cannot exit is not edge. Set it False only for a structure
    genuinely intended to be held to settlement, and then add
    ``box_exercise_cost`` yourself.
    """
    entry = [
        leg_cost(
            str(leg.get("segment") or "NFO"),
            str(leg.get("side") or "BUY"),  # type: ignore[arg-type]
            float(leg.get("price") or 0.0),
            float(leg.get("units") or 0.0),
        )
        for leg in legs
    ]
    rows = list(entry)
    if round_trip:
        for leg, cost in zip(legs, entry, strict=True):
            rows.append(
                leg_cost(
                    cost.segment,
                    "SELL" if cost.side == "BUY" else "BUY",  # type: ignore[arg-type]
                    float(leg.get("exit_price") or cost.price),
                    cost.units,
                )
            )

    return {
        "legs": [r.as_dict() for r in rows],
        "leg_count": len(rows),
        "round_trip": bool(round_trip),
        "entry_total": round(sum(r.total for r in entry), 2),
        "total": round(sum(r.total for r in rows), 2),
        "breakdown": {
            "brokerage": round(sum(r.brokerage for r in rows), 2),
            "txn": round(sum(r.txn for r in rows), 2),
            "ipft": round(sum(r.ipft for r in rows), 2),
            "sebi": round(sum(r.sebi for r in rows), 2),
            "stt": round(sum(r.stt for r in rows), 2),
            "stamp": round(sum(r.stamp for r in rows), 2),
            "gst": round(sum(r.gst for r in rows), 2),
        },
    }


def box_exercise_cost(
    segment: str,
    *,
    spot: float,
    lower_strike: float,
    upper_strike: float,
    units: float,
    long_box: bool = True,
) -> dict[str, Any]:
    """STT payable at expiry on the in-the-money long leg(s) of a box.

    Exercise STT falls on the holder who exercises, not on the assigned writer,
    so only the *long* legs matter — and which strikes those are flips with the
    direction of the box:

    * **Long box** (long call spread + long put spread) holds the long call at
      the lower strike and the long put at the upper strike. Its ITM intrinsic
      is measured to the *far* strike and is unbounded in spot. This is the
      classic reason an index box trading below its discounted width is still a
      loser.
    * **Short box** holds the long call at the upper strike and the long put at
      the lower strike, so its intrinsic is measured to the *near* strike —
      materially cheaper, and worth knowing before assuming both directions
      cost the same.

    Uses spot as the expiry-price estimate. That is a point estimate of a
    quantity that will move, so treat the result as a floor on the levy, not a
    forecast.
    """
    r = rates_for(segment)
    if r.stt_exercise_pct <= 0:
        return {
            "applies": False,
            "reason": f"{r.label}: ITM contracts devolve into futures, no intrinsic levy",
            "intrinsic": 0.0,
            "stt": 0.0,
        }

    s = float(spot)
    lo, hi = sorted((float(lower_strike), float(upper_strike)))
    call_strike, put_strike = (lo, hi) if long_box else (hi, lo)

    if s >= max(call_strike, put_strike):
        intrinsic, leg = s - call_strike, f"long {call_strike:g} CE"
    elif s <= min(call_strike, put_strike):
        intrinsic, leg = put_strike - s, f"long {put_strike:g} PE"
    elif long_box:
        # Between the strikes both long legs of a long box finish ITM.
        intrinsic, leg = (s - lo) + (hi - s), "long CE + long PE"
    else:
        # Between the strikes neither long leg of a short box is ITM.
        intrinsic, leg = 0.0, "no long leg in the money"

    stt = max(intrinsic, 0.0) * max(float(units), 0.0) * _pct(r.stt_exercise_pct)
    return {
        "applies": True,
        "reason": f"exercise STT {r.stt_exercise_pct}% of intrinsic on {leg}",
        "intrinsic": round(max(intrinsic, 0.0), 2),
        "stt": round(stt, 2),
    }
