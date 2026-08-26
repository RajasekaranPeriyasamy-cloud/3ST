"""Session volume profile / footprint for one underlying.

Wraps the vendored :mod:`vendor.volume_footprint` engine with the three things
3ST needs and the port deliberately does not provide: a Kite data source, basis
alignment onto the index price axis, and a cache so the gamma poll and the
standalone desk share one computation.

Three properties this module is responsible for holding
------------------------------------------------------

**The buy/sell split is a model, not a measurement.** Only the Geometric engine
is reachable here: the Intrabar engine needs sub-minute history Kite does not
serve, and the Footprint engine needs a per-tick aggressor feed nothing in this
repo records. Two very different order-flow paths produce the same candle, so
``tilt``, ``overlap`` and imbalance are *structural estimates*. Every payload
carries ``engine`` and ``estimate: True`` so no consumer can quietly forget.

**Volume is on the future; strikes are on the index.** Cash-index candles carry
no volume, so the profile is built from front-month futures bars — which trade
at a basis to spot. For NFO/BFO underlyings each bar is shifted by its own
``fut_close - index_close`` so the profile lands on true index prices and lines
up with strikes. MCX options are written on the future itself
(``spot_source == "future"`` in ``INDEX_OPTIONS``), so no shift is applied and
none is needed — that case is exact by construction, not by correction.

**A thin session reports unmeasured, never a confident shape.** Below
:data:`MIN_PROFILE_BARS` the payload returns ``available: False`` with a reason
and a bar count rather than a POC computed off a handful of opening minutes.
``None`` means "could not be measured"; it never means zero.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config import INDEX_OPTIONS
from utils.logging import get_logger, log_event
from vendor.volume_footprint import (
    BarSeries,
    FootprintResult,
    Settings,
    VolumeEngine,
    apply_engine,
    compute,
)
from vendor.volume_footprint.profile import band_mass

IST = ZoneInfo("Asia/Kolkata")
_log = get_logger("volume_profile")

#: Shared with the gamma poll, so one Kite pull and one integration per window.
PROFILE_CACHE_TTL_SEC = 45.0
#: Below this the session has not traded enough to shape a profile worth reading.
MIN_PROFILE_BARS = 15
#: Samples along the returned density curve (display only; POC/VA use the model).
PROFILE_SAMPLES = 120
#: Fallback when the instrument dump carries no tick_size.
DEFAULT_MINTICK = 0.05

_CACHE: dict[str, tuple[float, dict[str, Any], FootprintResult | None]] = {}
_CACHE_LOCK = threading.Lock()


def _now_ist(when: datetime | None = None) -> datetime:
    now = when or datetime.now(tz=IST)
    return now.replace(tzinfo=IST) if now.tzinfo is None else now.astimezone(IST)


def _unavailable(underlying: str, reason: str, *, when: datetime | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "underlying": underlying,
        "available": False,
        "reason": reason,
        "asof": _now_ist(when).isoformat(timespec="seconds"),
        "engine": "geometric",
        "estimate": True,
        "bars": int(extra.pop("bars", 0) or 0),
        **extra,
    }


def _is_future_settled(underlying: str) -> bool:
    """True when options are written on the future, so no basis correction applies."""
    meta = INDEX_OPTIONS.get(underlying.strip().upper()) or {}
    return str(meta.get("spot_source") or "").lower() == "future"


def _bar_key(bar: dict[str, Any]) -> str | None:
    raw = bar.get("date") if "date" in bar else bar.get("t")
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return _now_ist(raw).isoformat(timespec="minutes")
    try:
        return _now_ist(datetime.fromisoformat(str(raw))).isoformat(timespec="minutes")
    except ValueError:
        return str(raw)[:16]


def align_to_index_axis(
    fut_bars: list[dict[str, Any]],
    index_bars: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Shift futures OHLC onto the index price axis, bar by bar.

    ``basis_t = fut_close_t - index_close_t`` on matching minutes; bars with no
    index partner carry the last known basis forward, which is why
    ``matched_bars`` is reported — a low match count means most of the profile
    was shifted by a stale number and the caller should say so.

    Volume is untouched: it is still futures volume, now attributed to the
    index-equivalent price at which it traded.
    """
    if not index_bars:
        return list(fut_bars), {"mode": "none", "matched_bars": 0, "median": None, "last": None, "reason": "no_index_bars"}

    idx_close: dict[str, float] = {}
    for bar in index_bars:
        key = _bar_key(bar)
        if key is None:
            continue
        try:
            idx_close[key] = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue

    out: list[dict[str, Any]] = []
    basis_values: list[float] = []
    last_basis: float | None = None
    matched = 0
    for bar in fut_bars:
        key = _bar_key(bar)
        spot_close = idx_close.get(key) if key else None
        if spot_close is not None:
            try:
                last_basis = float(bar["close"]) - spot_close
                basis_values.append(last_basis)
                matched += 1
            except (KeyError, TypeError, ValueError):
                pass
        shifted = dict(bar)
        if last_basis is not None:
            for field in ("open", "high", "low", "close"):
                if shifted.get(field) is not None:
                    try:
                        shifted[field] = float(shifted[field]) - last_basis
                    except (TypeError, ValueError):
                        pass
        out.append(shifted)

    ordered = sorted(basis_values)
    median = ordered[len(ordered) // 2] if ordered else None
    return out, {
        "mode": "per_bar" if matched else "none",
        "matched_bars": matched,
        "median": round(median, 2) if median is not None else None,
        "last": round(last_basis, 2) if last_basis is not None else None,
        "reason": None if matched else "no_matching_minutes",
    }


def _rows_for_engine(bars: list[dict[str, Any]]) -> list[tuple]:
    rows: list[tuple] = []
    for bar in bars:
        try:
            rows.append(
                (
                    _bar_key(bar),
                    float(bar["open"]),
                    float(bar["high"]),
                    float(bar["low"]),
                    float(bar["close"]),
                    float(bar.get("volume") or 0.0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def list_contracts(underlying: str, limit: int = 4) -> list[dict[str, Any]]:
    """Futures contracts available for ``underlying``, nearest expiry first.

    ``rank`` is 0 for the front month, 1 for the next, and so on, so the UI can
    label near/far without re-deriving the calendar.
    """
    u = underlying.strip().upper()
    try:
        from instruments import _future_candidates

        cand = _future_candidates(u)
    except Exception:
        return []
    if cand is None or getattr(cand, "empty", True):
        return []

    from datetime import date as _date

    today = _date.today()
    rows: list[dict[str, Any]] = []
    for _, row in cand.iterrows():
        try:
            exp = row["_exp"].date()
        except Exception:
            continue
        if exp < today:
            continue
        rows.append(
            {
                "expiry": exp.isoformat(),
                "tradingsymbol": str(row["tradingsymbol"]),
                "instrument_token": int(row["instrument_token"]),
                "tick_size": float(row["tick_size"]) if row.get("tick_size") is not None else None,
            }
        )
    rows.sort(key=lambda r: r["expiry"])
    for i, r in enumerate(rows[:limit]):
        r["rank"] = i
        r["label"] = "near" if i == 0 else ("far" if i == 1 else f"+{i}")
    return rows[:limit]


def compute_volume_profile(
    underlying: str,
    *,
    when: datetime | None = None,
    bars: list[dict[str, Any]] | None = None,
    index_bars: list[dict[str, Any]] | None = None,
    mintick: float | None = None,
    expiry: str | None = None,
) -> tuple[dict[str, Any], FootprintResult | None]:
    """Session profile for ``underlying``. Pure when ``bars`` are injected.

    Returns ``(payload, result)``; the raw :class:`FootprintResult` is handed back
    so :func:`strike_band_volume` can reuse the fitted mixture instead of
    refitting it per consumer.
    """
    u = underlying.strip().upper()
    if u not in INDEX_OPTIONS:
        return _unavailable(u, "unknown_underlying", when=when), None

    fut_meta: dict[str, Any] = {}
    if bars is None:
        try:
            from instruments import resolve_future
            from kite_client import fetch_index_minute_spot, fetch_minute_candles
            from options.gamma_density_history import minutes_since_session_open

            # Session-anchored: never the 40-bar default, which would silently
            # build the profile from the last 40 minutes and look plausible.
            minutes = max(int(minutes_since_session_open(u)), MIN_PROFILE_BARS)
            # expiry=None resolves the front month, so near/far share one path.
            fut_meta = resolve_future(u, expiry=expiry)
            bars = fetch_minute_candles(int(fut_meta["instrument_token"]), minutes=minutes)
            if index_bars is None and not _is_future_settled(u):
                try:
                    index_bars = fetch_index_minute_spot(u, minutes=minutes)
                except Exception:
                    index_bars = None
        except Exception as exc:
            log_event(_log, logging.WARNING, "volume_profile_fetch_failed", underlying=u, error=str(exc)[:200])
            return _unavailable(u, "fetch_failed", when=when), None

    bars = bars or []
    if len(bars) < MIN_PROFILE_BARS:
        return (
            _unavailable(u, "too_few_bars" if bars else "no_session_bars", when=when, bars=len(bars)),
            None,
        )

    future_settled = _is_future_settled(u)
    if future_settled:
        aligned, basis = list(bars), {"mode": "none", "matched_bars": 0, "median": None, "last": None, "reason": "options_on_future"}
    else:
        aligned, basis = align_to_index_axis(bars, index_bars)

    rows = _rows_for_engine(aligned)
    if len(rows) < MIN_PROFILE_BARS:
        return _unavailable(u, "too_few_bars", when=when, bars=len(rows)), None

    tick = float(mintick or fut_meta.get("tick_size") or DEFAULT_MINTICK)
    if tick <= 0:
        tick = DEFAULT_MINTICK

    started = time.perf_counter()
    try:
        series = BarSeries.from_ohlcv(rows, mintick=tick, symbol=u)
        series = apply_engine(series, VolumeEngine.GEOMETRIC)
        # profile_period spans the whole session — that is the point of anchoring.
        res = compute(series, Settings(engine=VolumeEngine.GEOMETRIC, profile_period=len(rows)))
    except Exception as exc:
        log_event(_log, logging.WARNING, "volume_profile_engine_failed", underlying=u, error=str(exc)[:200])
        return _unavailable(u, "engine_error", when=when, bars=len(rows)), None

    prof = res.profile
    vah, val = res.chart_vah_price, res.chart_val_price
    payload: dict[str, Any] = {
        "underlying": u,
        "available": True,
        "reason": None,
        "asof": _now_ist(when).isoformat(timespec="seconds"),
        "engine": "geometric",
        # The one flag every consumer must respect: this is an inferred split.
        "estimate": True,
        "bars": len(rows),
        # Integration is ~O(bars²): ~200 ms over a full NIFTY session, ~900 ms
        # over a full MCX one. Surfaced so a slow desk is diagnosable rather than
        # folklore; the cache is what keeps it off every poll.
        "compute_ms": round((time.perf_counter() - started) * 1000.0, 1),
        "mintick": tick,
        "price_axis": "future" if future_settled else "index",
        "basis": basis,
        "fut_symbol": fut_meta.get("tradingsymbol"),
        "fut_token": fut_meta.get("instrument_token"),
        "contract": {
            "expiry": fut_meta.get("expiry"),
            "tradingsymbol": fut_meta.get("tradingsymbol"),
            "instrument_token": fut_meta.get("instrument_token"),
            # Far months trade thinner; bar count and total volume are how the
            # page can tell a real profile from a sparse one.
            "requested": expiry,
        },
        "poc": res.chart_poc_price,
        "vah": vah,
        "val": val,
        "value_area_pts": (round(vah - val, 2) if vah is not None and val is not None else None),
        "tilt_pp": (round(prof.tilt, 2) if prof and prof.tilt is not None else None),
        "overlap_pct": (round(prof.overlap, 2) if prof and prof.overlap is not None else None),
        "balance_verdict": res.balance_verdict(),
        "residual_ppm": (round(prof.residual_ppm, 4) if prof and prof.residual_ppm is not None else None),
        # Health of the arithmetic, not of the market — see the vendored README.
        "residual_label": res.residual_label,
        "total_buy": round(prof.sum_buy, 2) if prof else None,
        "total_sell": round(prof.sum_sell, 2) if prof else None,
        "price_lo": prof.price_lo if prof else None,
        "price_hi": prof.price_hi if prof else None,
        "curve": _sample_curve(res),
        # Price-axis gridline spacing: the configured strike lattice, so a line
        # lands on a strike rather than an arbitrary round number.
        "grid_step": _grid_step(u),
        # Where each side's own mass peaks. Not the POC — that is the peak of the
        # *combined* profile, and on a one-sided session the two can be far apart.
        "buy_peak": _side_peak(res, "buy"),
        "sell_peak": _side_peak(res, "sell"),
        # Top peaks per side by prominence, each carrying both readings of "how
        # did this level trade": band_tilt_pp from the fitted mixture, and
        # flow_tilt_pp from the bars that actually traded through it. [0] is the
        # tallest, so it is the same level buy_peak / sell_peak names.
        "buy_peaks": _side_peaks(res, series.bars, "buy", strike_step=_grid_step(u)),
        "sell_peaks": _side_peaks(res, series.bars, "sell", strike_step=_grid_step(u)),
    }
    return payload, res


def _grid_step(underlying: str) -> float | None:
    """Strike step for ``underlying`` — NIFTY 50, SENSEX/BANKNIFTY 100, NG 5.

    Read from ``INDEX_OPTIONS`` rather than inferred from the price range: it is
    the lattice the options actually trade on, so a gridline is a strike and the
    profile can be read against the ladder beside it.
    """
    meta = INDEX_OPTIONS.get(underlying.strip().upper()) or {}
    try:
        step = float(meta.get("strike_step") or 0)
    except (TypeError, ValueError):
        return None
    return step if step > 0 else None


def _side_peak(res: FootprintResult, side: str) -> dict[str, float] | None:
    """Price at which one side's density is greatest, and that density.

    Read off the **full-resolution** arrays, never ``curve`` — the sampled curve
    exists only for drawing and its ~120 points can miss a sharp peak by several
    ticks. This is the same rule POC and the value area already follow.

    ``None`` when the side carried no volume: a peak of an empty side is not a
    price, and returning the axis minimum would draw a confident line at the
    bottom of the chart.
    """
    prices = res.profile_prices
    vals = res.profile_buy if side == "buy" else res.profile_sell
    if not prices or not vals:
        return None
    best_i = max(range(min(len(prices), len(vals))), key=lambda i: vals[i])
    if float(vals[best_i]) <= 0.0:
        return None
    return {"price": round(float(prices[best_i]), 2), "density": round(float(vals[best_i]), 4)}


#: A bump smaller than this share of the side's tallest peak is fitting noise,
#: not a level. Without it a local-max scan returns a dozen wobbles and the
#: chart ends up labelling its own smoothing artefacts.
MIN_PROMINENCE_SHARE = 0.08

#: Peaks closer than half a strike step are the same level twice; the taller wins.
#: Keeps labels off each other and keeps peaks comparable to the OI ladder.
PEAK_MIN_SEPARATION_STEPS = 0.5

#: Peaks reported per side, most prominent first.
MAX_PEAKS_PER_SIDE = 4

#: A peak whose middle-50% of volume landed inside this many minutes really did
#: form at a time, and saying so is honest. Wider than this and naming a moment
#: would be a fiction — today's NIFTY POC drew from 244 bars across 10:21-15:27.
CONCENTRATED_IQR_MIN = 45


def _prominence_peaks(
    prices: list[float],
    vals: list[float],
) -> list[dict[str, Any]]:
    """Local maxima with topographic prominence and the basin each one owns.

    Prominence is height above the higher of the two saddles that separate a
    peak from any taller neighbour — the standard definition. A tall spike on
    the shoulder of a taller spike scores low, which is what stops the second
    label landing two ticks from the first.

    Walks the **full-resolution** arrays, never the sampled draw curve: the curve
    keeps ~1 point in N and both invents and hides bumps at this scale.
    """
    n = min(len(prices), len(vals))
    out: list[dict[str, Any]] = []
    for i in range(1, n - 1):
        if not (vals[i] > vals[i - 1] and vals[i] >= vals[i + 1]):
            continue
        # Walk out each way to the first higher point, tracking the low water
        # mark. Running off the end means no taller neighbour that side.
        left_min = vals[i]
        j = i - 1
        while j >= 0 and vals[j] <= vals[i]:
            left_min = min(left_min, vals[j])
            j -= 1
        lo_idx = j + 1
        right_min = vals[i]
        k = i + 1
        while k < n and vals[k] <= vals[i]:
            right_min = min(right_min, vals[k])
            k += 1
        hi_idx = k - 1
        prom = vals[i] - max(left_min, right_min)
        if prom <= 0:
            continue
        out.append(
            {
                "index": i,
                "price": float(prices[i]),
                "density": float(vals[i]),
                "prominence": float(prom),
                "lo_idx": lo_idx,
                "hi_idx": hi_idx,
            }
        )
    return out


def _select_peaks(
    peaks: list[dict[str, Any]],
    *,
    min_separation: float,
    limit: int,
) -> list[dict[str, Any]]:
    """Most prominent first, dropping anything too close to one already kept."""
    kept: list[dict[str, Any]] = []
    for p in sorted(peaks, key=lambda d: -d["prominence"]):
        if any(abs(p["price"] - q["price"]) < min_separation for q in kept):
            continue
        kept.append(p)
        if len(kept) >= limit:
            break
    return kept


def _band_tilt(
    buys: list[float],
    sells: list[float],
    lo_idx: int,
    hi_idx: int,
) -> tuple[float | None, float, float]:
    """Option A — tilt of the fitted mixture inside one peak's price band.

    The session tilt formula restricted in price, so a level that traded 70% buy
    reads +40pp regardless of what the rest of the session did. This is the
    number that answers "was *this* level buy-led", and it is unrelated to the
    current session tilt.
    """
    b = sum(buys[lo_idx : hi_idx + 1])
    s = sum(sells[lo_idx : hi_idx + 1])
    total = b + s
    if total <= 0:
        return None, b, s
    return round(100.0 * (b - s) / total, 2), b, s


def _flow_at_band(
    bars: list[Any],
    band_lo: float,
    band_hi: float,
) -> dict[str, Any]:
    """Option C — the bars that actually traded through this band, and when.

    Uses each bar's own engine-assigned buy/sell split rather than the fitted
    mixture, so this and :func:`_band_tilt` genuinely differ: one weights by
    where the model placed the mass, the other by raw bar volume.

    The time fields are a **window, not a moment**. A profile peak is a
    price-domain feature; the volume under it can accumulate across the whole
    session in many separate visits, so ``concentrated`` says whether naming a
    time would be honest at all.
    """
    hits: list[tuple[str, float, float, float]] = []
    for bar in bars:
        if bar.high < band_lo or bar.low > band_hi:
            continue
        when = str(bar.time or "")
        hits.append(
            (
                when[11:16] if len(when) >= 16 else when,
                float(bar.volume or 0.0),
                float(bar.buy_volume or 0.0),
                float(bar.sell_volume or 0.0),
            )
        )
    if not hits:
        return {"flow_tilt_pp": None, "bar_count": 0, "first": None, "last": None,
                "q1": None, "q3": None, "concentrated": False}

    buy = sum(h[2] for h in hits)
    sell = sum(h[3] for h in hits)
    tot = buy + sell
    tilt = round(100.0 * (buy - sell) / tot, 2) if tot > 0 else None

    vol_total = sum(h[1] for h in hits)
    q1 = q3 = None
    if vol_total > 0:
        cum = 0.0
        for hhmm, vol, _b, _s in hits:
            cum += vol
            if q1 is None and cum >= 0.25 * vol_total:
                q1 = hhmm
            if q3 is None and cum >= 0.75 * vol_total:
                q3 = hhmm
                break

    def _mins(hhmm: str | None) -> int | None:
        if not hhmm or ":" not in hhmm:
            return None
        try:
            h, m = hhmm.split(":")[:2]
            return int(h) * 60 + int(m)
        except ValueError:
            return None

    a, b_ = _mins(q1), _mins(q3)
    concentrated = a is not None and b_ is not None and (b_ - a) <= CONCENTRATED_IQR_MIN

    return {
        "flow_tilt_pp": tilt,
        "bar_count": len(hits),
        "first": hits[0][0],
        "last": hits[-1][0],
        "q1": q1,
        "q3": q3,
        "concentrated": bool(concentrated),
    }


def _side_peaks(
    res: FootprintResult,
    bars: list[Any],
    side: str,
    *,
    strike_step: float | None,
) -> list[dict[str, Any]]:
    """Top peaks of one side, each with its band tilt, flow tilt and window."""
    prices = res.profile_prices
    vals = res.profile_buy if side == "buy" else res.profile_sell
    buys, sells = res.profile_buy, res.profile_sell
    if not prices or not vals:
        return []
    peak_max = max(vals)
    if peak_max <= 0:
        return []

    found = _prominence_peaks(list(prices), list(vals))
    found = [p for p in found if p["prominence"] / peak_max >= MIN_PROMINENCE_SHARE]
    step = float(strike_step or 0) or 50.0
    kept = _select_peaks(
        found,
        min_separation=step * PEAK_MIN_SEPARATION_STEPS,
        limit=MAX_PEAKS_PER_SIDE,
    )

    half = step / 2.0
    out: list[dict[str, Any]] = []
    for p in kept:
        # Basin, clipped to half a strike step so one broad peak cannot swallow
        # the neighbours it is being compared against.
        band_lo = max(float(prices[p["lo_idx"]]), p["price"] - half)
        band_hi = min(float(prices[p["hi_idx"]]), p["price"] + half)
        lo_idx = max(p["lo_idx"], _nearest_index(prices, band_lo))
        hi_idx = min(p["hi_idx"], _nearest_index(prices, band_hi))
        tilt, mass_buy, mass_sell = _band_tilt(list(buys), list(sells), lo_idx, hi_idx)
        row = {
            "price": round(p["price"], 2),
            "density": round(p["density"], 4),
            "prominence_pct": round(100.0 * p["prominence"] / peak_max, 1),
            "band_lo": round(band_lo, 2),
            "band_hi": round(band_hi, 2),
            "band_tilt_pp": tilt,
            "band_buy": round(mass_buy, 2),
            "band_sell": round(mass_sell, 2),
        }
        row.update(_flow_at_band(bars, band_lo, band_hi))
        out.append(row)
    # Tallest first so the chart's primary marker is index 0.
    out.sort(key=lambda r: -r["density"])
    return out


def _nearest_index(prices: Any, target: float) -> int:
    lo, hi = 0, len(prices) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if prices[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo



def _sample_curve(res: FootprintResult) -> list[dict[str, float]]:
    """Down-sampled density curve for drawing. POC/VA never read this."""
    prices, buys, sells = res.profile_prices, res.profile_buy, res.profile_sell
    if not prices:
        return []
    step = max(1, len(prices) // PROFILE_SAMPLES)
    out: list[dict[str, float]] = []
    for i in range(0, len(prices), step):
        out.append(
            {
                "price": round(float(prices[i]), 2),
                "buy": round(float(buys[i]) if i < len(buys) else 0.0, 4),
                "sell": round(float(sells[i]) if i < len(sells) else 0.0, 4),
            }
        )
    return out


def _cache_key(underlying: str, expiry: str | None) -> str:
    """Contract-scoped: switching to the far month must not serve the near one."""
    return f"{underlying.strip().upper()}|{expiry or 'front'}"


def get_volume_profile(
    underlying: str,
    *,
    when: datetime | None = None,
    expiry: str | None = None,
) -> dict[str, Any]:
    """Cached session profile. One Kite pull and one integration per TTL."""
    u = underlying.strip().upper()
    key = _cache_key(u, expiry)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < PROFILE_CACHE_TTL_SEC:
            return hit[1]
    payload, res = compute_volume_profile(u, when=when, expiry=expiry)
    with _CACHE_LOCK:
        _CACHE[key] = (now, payload, res)
    return payload


def peek_volume_profile(underlying: str) -> dict[str, Any] | None:
    """Cached profile if one is already fresh, else ``None`` — never computes.

    Exists so cheap callers (``session_poc``) can prefer the footprint POC when
    it happens to be on hand without ever paying the ~200 ms integration
    themselves. "Available" here means *already computed*, not *computable*.
    """
    u = underlying.strip().upper()
    with _CACHE_LOCK:
        hit = _CACHE.get(_cache_key(u, None))
    if not hit or time.time() - hit[0] >= PROFILE_CACHE_TTL_SEC:
        return None
    payload = hit[1]
    return payload if payload.get("available") else None


def strike_band_volume(
    underlying: str,
    strikes: list[float],
    strike_step: float,
    *,
    result: FootprintResult | None = None,
) -> dict[str, Any]:
    """Session volume attributed to each strike band, exactly.

    Each strike owns ``[K - step/2, K + step/2]`` and ``band_mass`` returns the
    exact truncated-normal mass of that band — summing over a full lattice
    recovers the side's total volume, so this is an aggregation rather than a
    resampling.

    Mass outside the supplied strike window is returned as ``off_frame`` instead
    of being normalised away: a ladder covering ±20 strikes can easily miss a
    fifth of the session, and silently rescaling would hide that.
    """
    u = underlying.strip().upper()
    if result is None:
        with _CACHE_LOCK:
            hit = _CACHE.get(_cache_key(u, None))
        result = hit[2] if hit else None
        if result is None:
            payload, result = compute_volume_profile(u)
            if result is None:
                return {"available": False, "reason": payload.get("reason"), "bands": [], "off_frame": None}

    prof = result.profile
    if prof is None or not strikes:
        return {"available": False, "reason": "no_profile", "bands": [], "off_frame": None}

    half = max(float(strike_step), 1.0) / 2.0
    bands: list[dict[str, Any]] = []
    covered_buy = covered_sell = 0.0
    for k in strikes:
        lo, hi = float(k) - half, float(k) + half
        buy = band_mass(lo, hi, prof.comps_buy)
        sell = band_mass(lo, hi, prof.comps_sell)
        # Flat-bar atoms are point masses outside the continuous components.
        for price, a_buy, a_sell in zip(prof.atom_prices, prof.atom_buys, prof.atom_sells):
            if lo <= price < hi:
                buy += a_buy
                sell += a_sell
        covered_buy += buy
        covered_sell += sell
        bands.append(
            {
                "strike": float(k),
                "buy": round(buy, 2),
                "sell": round(sell, 2),
                "total": round(buy + sell, 2),
            }
        )

    total_all = (prof.sum_buy or 0.0) + (prof.sum_sell or 0.0)
    covered = covered_buy + covered_sell
    off = max(total_all - covered, 0.0)
    return {
        "available": True,
        "reason": None,
        "bands": bands,
        "max_total": round(max((b["total"] for b in bands), default=0.0), 2),
        "covered": round(covered, 2),
        "off_frame": round(off, 2),
        "off_frame_pct": round(100.0 * off / total_all, 1) if total_all > 0 else None,
    }


#: ``(ts, levels, ladder)`` — one gamma snapshot feeds both, so the OI ladder
#: never costs a second chain pull.
_LEVELS_CACHE: dict[str, tuple[float, dict[str, Any], dict[str, Any]]] = {}


def _infer_strike_step(strikes: list[dict[str, Any]]) -> float:
    """Modal gap between adjacent strikes.

    The snapshot does not carry ``strike_step`` at top level, and the modal gap
    is exact on a regular lattice — which every index chain here is. Taking the
    mode rather than the mean keeps one missing strike from widening the bands.
    """
    gaps: dict[float, int] = {}
    prev: float | None = None
    for row in strikes:
        try:
            k = float(row.get("strike"))
        except (TypeError, ValueError):
            continue
        if prev is not None:
            gap = round(k - prev, 4)
            if gap > 0:
                gaps[gap] = gaps.get(gap, 0) + 1
        prev = k
    if not gaps:
        return 50.0
    return max(gaps.items(), key=lambda kv: kv[1])[0]


def _oi_ladder_from_snapshot(u: str, snap: dict[str, Any]) -> dict[str, Any]:
    """Per-strike session-open OI, current OI and ΔOI, on the profile's price axis.

    Every OI column is read straight off the gamma snapshot, which already ran
    ``attach_strike_oi_baselines``. This module does not re-derive a baseline
    and must not, or the ladder and the gamma desk could disagree about what
    "session open" means on the same strike.

    ``ce_doi`` / ``pe_doi`` stay ``None`` when no baseline was captured. That is
    deliberate and matches the rest of this module: ``None`` means *could not be
    measured*, never zero. A strike that genuinely did not move shows ``0``.

    Volume is merged in from :func:`strike_band_volume`, so a row's ΔOI sits
    against the volume actually traded in that strike's band. When the session
    is too thin to shape a profile the OI half still renders and ``volume`` is
    ``None`` throughout — the two halves fail independently on purpose.
    """
    strikes = snap.get("strikes") or []
    if not strikes:
        return {
            "underlying": u,
            "available": False,
            "reason": "no_strikes",
            "rows": [],
        }

    step = _infer_strike_step(strikes)
    ks = [float(r["strike"]) for r in strikes if r.get("strike") is not None]

    # Thin session → no bands. The OI columns are still worth showing.
    vol = strike_band_volume(u, ks, step)
    by_strike: dict[float, dict[str, Any]] = {}
    if vol.get("available"):
        for band in vol.get("bands") or []:
            by_strike[float(band["strike"])] = band

    def _as_int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _pct(doi: int | None, base: int | None) -> float | None:
        """ΔOI as a share of the baseline.

        Same formula as ``oi_movers.build_session_change_boards`` so the two
        desks cannot disagree about the number. ``None`` on a zero baseline —
        a strike that opened empty has no percentage, and printing one would
        turn the first contract written into an infinite move.
        """
        if doi is None or not base:
            return None
        return round(doi / base * 100.0, 1)

    rows: list[dict[str, Any]] = []
    tot_ce_doi = 0
    tot_pe_doi = 0
    saw_doi = False
    for row in strikes:
        raw_k = row.get("strike")
        if raw_k is None:
            continue
        k = float(raw_k)
        ce_doi = _as_int(row.get("ce_doi"))
        pe_doi = _as_int(row.get("pe_doi"))
        if ce_doi is not None:
            tot_ce_doi += ce_doi
            saw_doi = True
        if pe_doi is not None:
            tot_pe_doi += pe_doi
            saw_doi = True
        band = by_strike.get(k) or {}
        rows.append(
            {
                "strike": k,
                "ce_open_oi": _as_int(row.get("ce_oi_base")),
                "pe_open_oi": _as_int(row.get("pe_oi_base")),
                "ce_oi": _as_int(row.get("ce_oi")),
                "pe_oi": _as_int(row.get("pe_oi")),
                "ce_doi": ce_doi,
                "pe_doi": pe_doi,
                "ce_doi_pct": _pct(ce_doi, _as_int(row.get("ce_oi_base"))),
                "pe_doi_pct": _pct(pe_doi, _as_int(row.get("pe_oi_base"))),
                # Null unless *both* sides are measured. Netting a known side
                # against an unmeasured one would print a confident number for
                # a strike half of which was never observed.
                "net_doi": (
                    ce_doi + pe_doi
                    if (ce_doi is not None and pe_doi is not None)
                    else None
                ),
                "ce_oi_base_source": row.get("ce_oi_base_source"),
                "pe_oi_base_source": row.get("pe_oi_base_source"),
                "volume": band.get("total"),
                "buy_volume": band.get("buy"),
                "sell_volume": band.get("sell"),
            }
        )

    # Shared bar scale for both sides, so a CE bar and a PE bar of equal length
    # mean equal contracts.
    max_abs_doi = max(
        (
            abs(v)
            for r in rows
            for v in (r["ce_doi"], r["pe_doi"])
            if v is not None
        ),
        default=0,
    )

    return {
        "underlying": u,
        "available": True,
        "reason": None,
        "asof": _now_ist().isoformat(timespec="seconds"),
        "expiry": snap.get("expiry"),
        "spot": snap.get("spot"),
        "atm_strike": snap.get("atm_strike"),
        "strike_step": step,
        "rows": rows,
        # Straight from the gamma snapshot so both desks tell the same story
        # about whether today's baseline is a real 09:20 capture or a fallback.
        "oi_baseline_mode": snap.get("oi_baseline_mode"),
        "oi_baseline_note": snap.get("oi_baseline_note"),
        "oi_baseline_open_count": snap.get("oi_baseline_open_count"),
        "oi_baseline_prev_close_count": snap.get("oi_baseline_prev_close_count"),
        "total_ce_doi": tot_ce_doi if saw_doi else None,
        "total_pe_doi": tot_pe_doi if saw_doi else None,
        "max_abs_doi": max_abs_doi or None,
        "volume_available": bool(vol.get("available")),
        "volume_reason": vol.get("reason"),
        "price_axis": "future" if _is_future_settled(u) else "index",
    }


def _levels_and_ladder(underlying: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build both payloads from **one** history-free gamma snapshot, cached together.

    ``include_history=False`` so this never appends to the session trail or
    upserts ``daily_hhi`` / ``daily_pin``. Calling the full snapshot from a
    second page would double-write both, and the pin sampler would end up
    recording a page visit as a session checkpoint.

    Session-open OI capture (``ensure_session_open_oi``) *is* reached through
    this snapshot, but it is idempotent — it persists once per day and returns
    the stored map on every later call — so serving this page cannot move a
    baseline the gamma desk already recorded.

    Cached on the same TTL as the profile so all three stay roughly in step.
    """
    u = underlying.strip().upper()
    now = time.time()
    with _CACHE_LOCK:
        hit = _LEVELS_CACHE.get(u)
        if hit and now - hit[0] < PROFILE_CACHE_TTL_SEC:
            return hit[1], hit[2]

    levels: dict[str, Any] = {"underlying": u, "available": False, "reason": None}
    ladder: dict[str, Any] = {
        "underlying": u,
        "available": False,
        "reason": None,
        "rows": [],
    }
    try:
        from options.gamma_density import build_gamma_snapshot

        snap = build_gamma_snapshot(
            u,
            include_multi_expiry=False,
            include_history=False,
            include_vanna_strip=False,
            build_session_chart=False,
        )
        conc = snap.get("concentration") or {}
        levels = {
            "underlying": u,
            "available": True,
            "reason": None,
            "asof": _now_ist().isoformat(timespec="seconds"),
            "expiry": snap.get("expiry"),
            "spot": snap.get("spot"),
            "call_wall": snap.get("call_wall"),
            "put_wall": snap.get("put_wall"),
            "flip": snap.get("flip_level"),
            "pin": conc.get("pin_strike"),
            # Only `dominant` is a real gamma pin — the consumer should say so.
            "pin_source": conc.get("pin_source"),
            "pos_gamma_peak": conc.get("pos_gamma_peak_strike"),
            "neg_gamma_peak": conc.get("neg_gamma_peak_strike"),
            "gamma_regime": snap.get("gamma_regime"),
        }
        ladder = _oi_ladder_from_snapshot(u, snap)
    except Exception as exc:
        log_event(_log, logging.WARNING, "gamma_levels_failed", underlying=u, error=str(exc)[:200])
        levels["reason"] = "gamma_unavailable"
        ladder["reason"] = "gamma_unavailable"

    with _CACHE_LOCK:
        _LEVELS_CACHE[u] = (now, levels, ladder)
    return levels, ladder


def gamma_levels(underlying: str) -> dict[str, Any]:
    """GEX reference levels to overlay on the profile chart.

    Both these levels and the profile sit on the **index** price axis for cash
    indices (the profile having been basis-shifted to get there) and on the
    futures axis for MCX, so they are directly comparable either way.

    See :func:`_levels_and_ladder` for why the snapshot behind this is
    history-free, and how it is shared with the OI ladder.
    """
    return _levels_and_ladder(underlying)[0]


def strike_oi_ladder(underlying: str) -> dict[str, Any]:
    """Session-open OI, current OI and ΔOI per strike, merged with session volume.

    Shares one gamma snapshot with :func:`gamma_levels`, so opening this desk
    costs the same single chain pull it always did.
    """
    return _levels_and_ladder(underlying)[1]


def reset_cache() -> None:
    """Test hook — the module-level caches are process-wide."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _LEVELS_CACHE.clear()
