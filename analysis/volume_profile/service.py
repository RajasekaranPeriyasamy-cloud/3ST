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
    }
    return payload, res


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
