"""Desk pre-close forecast / estimate — proxy blend (v1) + constituent rebuild scaffold.

**Objective 1 (shipped):** estimate Nifty closing price **before 15:15 IST** via
Fut LTP, Synth F, and a pre-close VWAP proxy. Display-only — does not feed
GEX / OI / ``require_index_spot``.

Phase A (``proxy_v1``): blend Synth F, front-month fut LTP, and a VWAP
reference (see ``resolve_ref_vwap_window``); clamp to that ref ±3% when present.

VWAP reference ladder (documented):
  1. **pre_close_1515** — full 15:00–15:15 IST window once ``now >= 15:15``
  2. **running_1500** — 15:00→now while the reference window is still open
  3. **session** — session VWAP 09:15→now when the 15:00+ window has no bars yet
     (morning / early afternoon fallback)

Phase B (``constituent_v1``): scaffold only — rebuild index from stock CAS
indicatives × weights / divisor when coverage ≥ threshold. Full rebuild waits
for a live CAS-window stock-field spike; we do **not** scrape NSE HTML.
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Phase A default blend weights (renormalized when a component is missing).
PROXY_WEIGHT_SYNTH_F = 0.40
PROXY_WEIGHT_FUT_LTP = 0.35
PROXY_WEIGHT_REF_VWAP = 0.25

# Aligns with CAS ±3% band spirit (also used to sanity-check official index indicative).
CAS_BAND_PCT = 0.03

# Phase B: promote constituent rebuild only when enough weight has valid CAS px.
CONSTITUENT_COVERAGE_THRESHOLD = 0.90

SESSION_VWAP_START = time(9, 15)
REF_VWAP_START = time(15, 0)
REF_VWAP_END = time(15, 15)


def _now_ist(when: datetime | None = None) -> datetime:
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def _asof_iso(when: datetime | None = None) -> str:
    return _now_ist(when).isoformat(timespec="seconds")


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def sanitize_official_indicative(
    indicative: float | None,
    spot: float | None,
    *,
    max_rel_diff: float = CAS_BAND_PCT,
) -> float | None:
    """Reject Kite index indicative unless within ``max_rel_diff`` of continuous spot.

    Garbage values (e.g. 15 / 1866 vs ~24,550) return ``None`` so they cannot
    poison Δ / basis-vs-CAS.
    """
    ind = _finite(indicative)
    sp = _finite(spot)
    if ind is None:
        return None
    if sp is None or sp == 0:
        # Without a spot anchor, refuse to trust index-level indicative.
        return None
    if abs(ind - sp) / abs(sp) > float(max_rel_diff):
        return None
    return ind


def resolve_ref_vwap_window(when: datetime | None = None) -> tuple[time, time, str]:
    """Choose VWAP ``[start, end)`` IST for the pre-close forecast.

    Returns ``(start, end_exclusive, mode)``:
    - ``pre_close_1515`` — complete 15:00–15:15 reference (``now >= 15:15``)
    - ``running_1500`` — 15:00→now during the open reference window
    - ``session`` — 09:15→now before 15:00 (documented early-day fallback)
    """
    now = _now_ist(when)
    t = now.timetz().replace(tzinfo=None)
    if t >= REF_VWAP_END:
        return REF_VWAP_START, REF_VWAP_END, "pre_close_1515"
    # Inclusive of the current minute bar → exclusive end = next minute boundary.
    end_dt = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = end_dt.timetz().replace(tzinfo=None)
    if t >= REF_VWAP_START:
        if end > REF_VWAP_END:
            end = REF_VWAP_END
        return REF_VWAP_START, end, "running_1500"
    if end <= SESSION_VWAP_START:
        return SESSION_VWAP_START, SESSION_VWAP_START, "session"
    return SESSION_VWAP_START, end, "session"


def vwap_from_bars(
    bars: list[dict[str, Any]],
    *,
    start: time = REF_VWAP_START,
    end: time = REF_VWAP_END,
    session_date: datetime | None = None,
) -> float | None:
    """Volume-weighted typical price over bars whose IST clock is in ``[start, end)``.

    Falls back to simple average of typical prices when total volume is zero.
    """
    if not bars:
        return None
    if start >= end:
        return None

    day = _now_ist(session_date).date()
    num = 0.0
    den = 0.0
    typicals: list[float] = []

    for bar in bars:
        ts = bar.get("date") if "date" in bar else bar.get("t")
        try:
            if isinstance(ts, datetime):
                dt = ts
            else:
                import pandas as pd

                dt = pd.Timestamp(ts).to_pydatetime()
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        if dt.date() != day:
            continue
        t = dt.timetz().replace(tzinfo=None)
        if t < start or t >= end:
            continue
        try:
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            vol = float(bar.get("volume") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        typical = (high + low + close) / 3.0
        if not math.isfinite(typical):
            continue
        typicals.append(typical)
        if vol > 0:
            num += typical * vol
            den += vol

    if den > 0:
        return num / den
    if typicals:
        return sum(typicals) / len(typicals)
    return None


def blend_proxy_estimate(
    *,
    synth_f: float | None,
    fut_ltp: float | None,
    ref_vwap: float | None,
    weight_synth: float = PROXY_WEIGHT_SYNTH_F,
    weight_fut: float = PROXY_WEIGHT_FUT_LTP,
    weight_ref: float = PROXY_WEIGHT_REF_VWAP,
    band_pct: float = CAS_BAND_PCT,
) -> tuple[float | None, dict[str, Any]]:
    """Weighted blend with renormalization; clamp to ``ref_vwap ± band_pct`` when present.

    Returns ``(estimate, components)``.
    """
    parts: list[tuple[str, float, float]] = []
    sf = _finite(synth_f)
    fl = _finite(fut_ltp)
    rv = _finite(ref_vwap)
    if sf is not None:
        parts.append(("synth_f", sf, float(weight_synth)))
    if fl is not None:
        parts.append(("fut_ltp", fl, float(weight_fut)))
    if rv is not None:
        parts.append(("ref_vwap", rv, float(weight_ref)))

    components: dict[str, Any] = {
        "synth_f": sf,
        "fut_ltp": fl,
        "ref_vwap": rv,
        "fut_poc": None,
        "weights": {
            "synth_f": float(weight_synth),
            "fut_ltp": float(weight_fut),
            "ref_vwap": float(weight_ref),
        },
        "weights_used": {},
        "clamped": False,
        "clamp_low": None,
        "clamp_high": None,
    }

    if not parts:
        return None, components

    w_sum = sum(w for _, _, w in parts)
    if w_sum <= 0:
        return None, components

    estimate = 0.0
    used: dict[str, float] = {}
    for name, px, w in parts:
        nw = w / w_sum
        used[name] = nw
        estimate += nw * px
    components["weights_used"] = used

    if rv is not None and rv > 0:
        lo = rv * (1.0 - float(band_pct))
        hi = rv * (1.0 + float(band_pct))
        components["clamp_low"] = lo
        components["clamp_high"] = hi
        if estimate < lo:
            estimate = lo
            components["clamped"] = True
        elif estimate > hi:
            estimate = hi
            components["clamped"] = True

    return float(estimate), components


def _lookback_minutes(now: datetime, start: time) -> int:
    """Minutes of history needed to cover ``start`` on the session day."""
    start_dt = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if now < start_dt:
        return 40
    return max(40, int((now - start_dt).total_seconds() // 60) + 10)


def _fetch_ref_vwap_bars(
    underlying: str,
    *,
    when: datetime | None = None,
) -> tuple[float | None, str | None, str | None]:
    """Pre-close VWAP from index minutes; fall back to front future / session window.

    Returns ``(vwap, source, window_mode)``.
    """
    from kite_client import fetch_front_month_minute_candles, fetch_index_minute_spot

    u = underlying.strip().upper()
    now = _now_ist(when)
    start, end, mode = resolve_ref_vwap_window(now)
    # Before 15:00 we may need full session; during/after prefer enough for 15:00+.
    lookback_start = SESSION_VWAP_START if mode == "session" else REF_VWAP_START
    minutes = _lookback_minutes(now, lookback_start)
    if mode != "session":
        # Also keep enough history to fall back to session if 15:00+ bars are empty.
        minutes = max(minutes, _lookback_minutes(now, SESSION_VWAP_START))

    source: str | None = None
    bars: list[dict[str, Any]] = []
    try:
        bars = fetch_index_minute_spot(u, minutes=minutes) or []
        if bars:
            source = "index"
    except Exception:
        bars = []

    if not bars:
        try:
            bars = fetch_front_month_minute_candles(u, minutes=minutes) or []
            if bars:
                source = "future"
        except Exception:
            bars = []
            source = None

    if not bars:
        return None, source, mode

    vwap = vwap_from_bars(bars, start=start, end=end, session_date=now)
    if vwap is not None:
        return vwap, source, mode

    # Incomplete / empty primary window → session VWAP fallback (documented).
    if mode != "session":
        sess_end = now.timetz().replace(tzinfo=None)
        if sess_end > SESSION_VWAP_START:
            vwap = vwap_from_bars(
                bars,
                start=SESSION_VWAP_START,
                end=sess_end,
                session_date=now,
            )
            if vwap is not None:
                return vwap, source, "session"

    return None, source, mode


def _fetch_fut_ltp(underlying: str) -> tuple[float | None, str | None]:
    from instruments import resolve_future
    from kite_client import fetch_quote_batch

    u = underlying.strip().upper()
    try:
        fut = resolve_future(u)
    except Exception:
        return None, None
    symbol = str(fut.get("tradingsymbol") or "")
    exchange = str(fut.get("exchange") or "NFO")
    if not symbol:
        return None, None
    key = f"{exchange}:{symbol}"
    try:
        batch = fetch_quote_batch([key])
    except Exception:
        return None, symbol
    q = batch.get(key) if batch else None
    if q is None and batch:
        q = next(iter(batch.values()), None)
    if not isinstance(q, dict):
        return None, symbol
    ltp = _finite(q.get("last_price"))
    return ltp, symbol


# ---------------------------------------------------------------------------
# Phase B — constituent rebuild scaffold (not live until stock CAS spike)
# ---------------------------------------------------------------------------


def constituent_coverage(
    weights: dict[str, float],
    prices: dict[str, float | None],
) -> float:
    """Fraction of total weight that has a valid positive CAS price."""
    total = sum(float(w) for w in weights.values() if _finite(w) is not None and float(w) > 0)
    if total <= 0:
        return 0.0
    covered = 0.0
    for sym, w in weights.items():
        wf = _finite(w)
        px = _finite(prices.get(sym))
        if wf is not None and wf > 0 and px is not None and px > 0:
            covered += wf
    return covered / total


def rebuild_index_from_constituents(
    weights: dict[str, float],
    prices: dict[str, float | None],
    *,
    divisor: float,
    coverage_threshold: float = CONSTITUENT_COVERAGE_THRESHOLD,
) -> tuple[float | None, dict[str, Any]]:
    """``index_est = Σ(w_i * p_i) / divisor`` when coverage ≥ threshold; else ``None``.

    Scaffold for Phase B. Callers must supply free-float weights / divisor from a
    checked-in seed (not NSE HTML scrape). Until a CAS-window stock-field spike
    confirms equity ``indicative_close_price`` / equilibrium on Kite, production
    should keep ``estimate_method`` on ``proxy_v1``.
    """
    meta: dict[str, Any] = {
        "coverage": 0.0,
        "coverage_threshold": float(coverage_threshold),
        "divisor": float(divisor) if _finite(divisor) is not None else None,
        "n_constituents": len(weights),
        "n_with_price": 0,
        "ready": False,
        "reason": None,
    }
    div = _finite(divisor)
    if div is None or div <= 0:
        meta["reason"] = "invalid_divisor"
        return None, meta

    cov = constituent_coverage(weights, prices)
    meta["coverage"] = cov
    meta["n_with_price"] = sum(
        1 for sym in weights if _finite(prices.get(sym)) is not None and float(prices[sym]) > 0  # type: ignore[arg-type]
    )
    if cov < float(coverage_threshold):
        meta["reason"] = "coverage_below_threshold"
        return None, meta

    numerator = 0.0
    for sym, w in weights.items():
        wf = _finite(w)
        px = _finite(prices.get(sym))
        if wf is None or wf <= 0 or px is None or px <= 0:
            continue
        numerator += wf * px

    meta["ready"] = True
    return numerator / div, meta


def try_constituent_estimate(
    underlying: str,
    *,
    when: datetime | None = None,
) -> tuple[float | None, dict[str, Any]]:
    """Phase B entry point — returns ``(None, stub_meta)`` until seed + spike exist.

    Intentionally does **not** scrape NSE. Next step after a live CAS window:
    batch-quote Nifty-50 equities, read stock indicative fields, load weights
    from ``data/`` seed, then call ``rebuild_index_from_constituents``.
    """
    u = underlying.strip().upper()
    meta: dict[str, Any] = {
        "underlying": u,
        "method": "constituent_v1",
        "status": "scaffold",
        "coverage_threshold": CONSTITUENT_COVERAGE_THRESHOLD,
        "reason": "awaiting_cas_window_stock_field_spike",
        "asof": _asof_iso(when),
        "note": (
            "Full constituent rebuild waits for confirmed Kite equity CAS fields "
            "during 15:20–15:30; no NSE HTML scrape. Objective 1 ships proxy "
            "pre-close forecast only."
        ),
    }
    # Future: load seed weights from data/ + batch equity quotes during 15:20–15:30.
    return None, meta


def compute_cas_estimate(
    underlying: str,
    *,
    when: datetime | None = None,
    synth_f: float | None = None,
    fut_ltp: float | None = None,
    ref_vwap: float | None = None,
    fut_poc: float | None = None,
    fetch_missing: bool = True,
    try_constituent: bool = True,
) -> dict[str, Any]:
    """Build pre-close forecast payload for the desk / ``/cas/indicative`` attach.

    Available any time Kite legs resolve — not gated on the CAS window.
    Prefers Phase B constituent rebuild when ready; otherwise ``proxy_v1``.
    Inject ``synth_f`` / ``fut_ltp`` / ``ref_vwap`` (and set ``fetch_missing=False``)
    in unit tests to avoid live Kite.
    """
    u = str(underlying or "").upper()
    now = _now_ist(when)
    _, _, window_mode = resolve_ref_vwap_window(now)

    constituent_meta: dict[str, Any] | None = None
    if try_constituent:
        c_est, constituent_meta = try_constituent_estimate(u, when=now)
        if c_est is not None:
            return {
                "estimate": float(c_est),
                "estimate_method": "constituent_v1",
                "estimate_components": {
                    "synth_f": _finite(synth_f),
                    "fut_ltp": _finite(fut_ltp),
                    "ref_vwap": _finite(ref_vwap),
                    "fut_poc": _finite(fut_poc),
                    "ref_vwap_window": window_mode,
                    "constituent": constituent_meta,
                },
                "asof": _asof_iso(now),
            }

    sf = _finite(synth_f)
    fl = _finite(fut_ltp)
    rv = _finite(ref_vwap)
    fp = _finite(fut_poc)
    fut_symbol: str | None = None
    ref_source: str | None = None
    ref_window: str | None = window_mode if rv is not None else None

    if fetch_missing:
        if rv is None:
            rv, ref_source, ref_window = _fetch_ref_vwap_bars(u, when=now)
        if fl is None:
            fl, fut_symbol = _fetch_fut_ltp(u)

    estimate, components = blend_proxy_estimate(synth_f=sf, fut_ltp=fl, ref_vwap=rv)
    components["fut_poc"] = fp
    components["ref_vwap_window"] = ref_window
    if fut_symbol:
        components["fut_symbol"] = fut_symbol
    if ref_source:
        components["ref_vwap_source"] = ref_source
    if constituent_meta is not None:
        components["constituent"] = constituent_meta

    return {
        "estimate": estimate,
        "estimate_method": "proxy_v1",
        "estimate_components": components,
        "asof": _asof_iso(now),
    }
