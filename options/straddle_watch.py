"""Straddle Watch — CE/PE/straddle price + OI time series (Latest mode).

Read-only analytics. Never arms, orders, or mutates execution state.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd

from config import INDEX_OPTIONS, PRICING_ENGINE_DEFAULTS
from instruments import resolve_future
from kite_client import fetch_historical_by_token
from options.chain import (
    atm_strike,
    find_option_leg,
    get_chain,
    get_index_spot_detail,
    list_expiries,
    require_index_spot,
)
from options.gamma_density_history import session_window
from options.iv import implied_volatility, time_to_expiry_years
from pricing.bs_engine import price_black_scholes

IST = ZoneInfo("Asia/Kolkata")

RangeKey = Literal["1D", "5D", "30D"]
_VALID_RANGES: tuple[RangeKey, ...] = ("1D", "5D", "30D")
_RANGE_CALENDAR_DAYS: dict[RangeKey, int] = {"1D": 0, "5D": 10, "30D": 50}


def straddle_watch_config() -> dict[str, Any]:
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "ranges": list(_VALID_RANGES),
        "mode": "latest",
        "note": "Latest-only Straddle Watch — Historical mode not implemented.",
    }


def max_pain_strike(chain_rows: list[dict[str, Any]]) -> float | None:
    """Strike that minimizes total option-writer pain given CE/PE OI."""
    strikes: list[float] = []
    ce_oi: dict[float, float] = {}
    pe_oi: dict[float, float] = {}
    for row in chain_rows:
        try:
            k = float(row["strike"])
        except (KeyError, TypeError, ValueError):
            continue
        strikes.append(k)
        ce = row.get("ce") or {}
        pe = row.get("pe") or {}
        try:
            ce_oi[k] = float(ce.get("oi") or 0)
        except (TypeError, ValueError):
            ce_oi[k] = 0.0
        try:
            pe_oi[k] = float(pe.get("oi") or 0)
        except (TypeError, ValueError):
            pe_oi[k] = 0.0

    if not strikes:
        return None

    strikes = sorted(set(strikes))
    best_k: float | None = None
    best_pain = float("inf")
    for settle in strikes:
        pain = 0.0
        for k in strikes:
            if settle > k:
                pain += (settle - k) * ce_oi.get(k, 0.0)
            if settle < k:
                pain += (k - settle) * pe_oi.get(k, 0.0)
        if pain < best_pain:
            best_pain = pain
            best_k = settle
    return best_k


def iv_rank_and_percentile(
    current_iv: float | None,
    history: list[float],
) -> tuple[float | None, float | None]:
    """IV Rank = (cur-min)/(max-min)*100; IV Percentile = % of samples <= current."""
    if current_iv is None or not history:
        return None, None
    vals = [float(v) for v in history if v is not None and float(v) > 0]
    if len(vals) < 5:
        return None, None
    lo = min(vals)
    hi = max(vals)
    cur = float(current_iv)
    if hi > lo:
        ivr = round((cur - lo) / (hi - lo) * 100.0, 2)
    else:
        ivr = 50.0
    below = sum(1 for v in vals if v <= cur)
    ivp = round(below / len(vals) * 100.0, 2)
    return ivr, ivp


def straddle_vwap_series(
    prices: list[float | None],
    volumes: list[float | None],
) -> list[float | None]:
    """Cumulative VWAP of straddle price using combined CE+PE volume."""
    out: list[float | None] = []
    cum_pv = 0.0
    cum_v = 0.0
    for px, vol in zip(prices, volumes):
        if px is None or vol is None or vol <= 0:
            out.append(round(cum_pv / cum_v, 4) if cum_v > 0 else None)
            continue
        cum_pv += float(px) * float(vol)
        cum_v += float(vol)
        out.append(round(cum_pv / cum_v, 4) if cum_v > 0 else None)
    return out


def _parse_range(range_key: str) -> RangeKey:
    key = str(range_key or "1D").upper()
    if key not in _VALID_RANGES:
        raise ValueError(f"Unsupported range '{range_key}'. Use {_VALID_RANGES}")
    return key  # type: ignore[return-value]


def _now_ist() -> datetime:
    return datetime.now(tz=IST)


def _range_bounds(underlying: str, range_key: RangeKey, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or _now_ist()
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    start_t, end_t = session_window(underlying)
    if range_key == "1D":
        day = now.date()
        start = datetime.combine(day, start_t, tzinfo=IST)
        end = now
        if end.timetz().replace(tzinfo=None) < start_t:
            # Before open — show previous calendar weekday session
            prev = day - timedelta(days=1)
            while prev.weekday() >= 5:
                prev -= timedelta(days=1)
            start = datetime.combine(prev, start_t, tzinfo=IST)
            end = datetime.combine(prev, end_t, tzinfo=IST)
        return start, end

    cal_days = _RANGE_CALENDAR_DAYS[range_key]
    start_day = now.date() - timedelta(days=cal_days)
    start = datetime.combine(start_day, start_t, tzinfo=IST)
    return start, now


def _filter_session(df: pd.DataFrame, underlying: str) -> pd.DataFrame:
    if df.empty:
        return df
    start_t, end_t = session_window(underlying)
    idx = df.index
    if getattr(idx, "tz", None) is not None:
        local = idx.tz_convert(IST)
        times = local.time
        weekdays = local.weekday
    else:
        times = idx.time
        weekdays = idx.weekday
    mask = (times >= start_t) & (times <= end_t) & (weekdays < 5)
    return df.loc[mask]


def _to_naive_ist_index(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_convert(IST).tz_localize(None)
    return out


def align_leg_frames(
    ce_df: pd.DataFrame,
    pe_df: pd.DataFrame,
) -> pd.DataFrame:
    """Outer-join CE/PE on timestamp; compute straddle close and combined volume."""
    ce = _to_naive_ist_index(ce_df)
    pe = _to_naive_ist_index(pe_df)
    cols_ce = ce.rename(columns={"close": "call_price", "oi": "call_oi", "volume": "call_vol"})[
        [c for c in ("call_price", "call_oi", "call_vol") if c in ("call_price", "call_oi", "call_vol")]
    ]
    # Ensure columns exist
    for c, src in (("call_price", "close"), ("call_oi", "oi"), ("call_vol", "volume")):
        if c not in cols_ce.columns and src in ce.columns:
            cols_ce[c] = ce[src]
        if c not in cols_ce.columns:
            cols_ce[c] = pd.NA
    cols_pe = pe.copy()
    for c, src in (("put_price", "close"), ("put_oi", "oi"), ("put_vol", "volume")):
        if src in pe.columns:
            cols_pe[c] = pe[src]
        else:
            cols_pe[c] = pd.NA
    cols_pe = cols_pe[["put_price", "put_oi", "put_vol"]]

    joined = cols_ce[["call_price", "call_oi", "call_vol"]].join(
        cols_pe[["put_price", "put_oi", "put_vol"]],
        how="outer",
    )
    joined = joined.sort_index()
    joined["call_price"] = pd.to_numeric(joined["call_price"], errors="coerce")
    joined["put_price"] = pd.to_numeric(joined["put_price"], errors="coerce")
    joined["call_oi"] = pd.to_numeric(joined["call_oi"], errors="coerce")
    joined["put_oi"] = pd.to_numeric(joined["put_oi"], errors="coerce")
    joined["call_vol"] = pd.to_numeric(joined["call_vol"], errors="coerce").fillna(0.0)
    joined["put_vol"] = pd.to_numeric(joined["put_vol"], errors="coerce").fillna(0.0)
    joined["straddle_price"] = joined["call_price"] + joined["put_price"]
    joined["straddle_vol"] = joined["call_vol"] + joined["put_vol"]
    return joined


def _fetch_leg_history(token: int, start: datetime, end: datetime) -> pd.DataFrame:
    start_naive = start.astimezone(IST).replace(tzinfo=None) if start.tzinfo else start
    end_naive = end.astimezone(IST).replace(tzinfo=None) if end.tzinfo else end
    return fetch_historical_by_token(
        int(token),
        "1min",
        start_naive,
        end_naive,
        chunk_days=5,
        oi=True,
    )


def _quote_keys_for_chain(chain: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    exch = chain.get("exchange") or "NFO"
    for row in chain.get("strikes") or []:
        for side in ("ce", "pe"):
            leg = row.get(side)
            if not leg or not leg.get("tradingsymbol"):
                continue
            keys.append(f"{leg.get('exchange', exch)}:{leg['tradingsymbol']}")
    return keys


def _attach_quote_oi(chain: dict[str, Any], quotes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    exch = chain.get("exchange") or "NFO"
    out: list[dict[str, Any]] = []
    for row in chain.get("strikes") or []:
        entry: dict[str, Any] = {"strike": row["strike"], "ce": {}, "pe": {}}
        for side in ("ce", "pe"):
            leg = row.get(side)
            if not leg:
                continue
            key = f"{leg.get('exchange', exch)}:{leg['tradingsymbol']}"
            q = quotes.get(key) or {}
            oi_raw = q.get("oi")
            if oi_raw is None:
                oi_raw = q.get("open_interest")
            try:
                oi = float(oi_raw) if oi_raw is not None else 0.0
            except (TypeError, ValueError):
                oi = 0.0
            try:
                ltp = float(q.get("last_price") or 0) or None
            except (TypeError, ValueError):
                ltp = None
            entry[side] = {"oi": oi, "ltp": ltp, "tradingsymbol": leg.get("tradingsymbol")}
        out.append(entry)
    return out


def _chain_pcr(rows: list[dict[str, Any]]) -> float | None:
    ce_tot = sum(float((r.get("ce") or {}).get("oi") or 0) for r in rows)
    pe_tot = sum(float((r.get("pe") or {}).get("oi") or 0) for r in rows)
    if ce_tot <= 0:
        return None
    return round(pe_tot / ce_tot, 2)


def _fut_quote_summary(underlying: str) -> dict[str, Any]:
    from options.oi_var import _quote_batches

    fut = resolve_future(underlying)
    key = f"{fut['exchange']}:{fut['tradingsymbol']}"
    quotes = _quote_batches([key])
    q = quotes.get(key) or {}
    try:
        ltp = float(q.get("last_price") or 0) or None
    except (TypeError, ValueError):
        ltp = None
    ohlc = q.get("ohlc") or {}
    try:
        prev = float(ohlc.get("close") or 0) or None
    except (TypeError, ValueError):
        prev = None
    chg = chg_pct = None
    if ltp is not None and prev is not None and prev > 0:
        chg = round(ltp - prev, 2)
        chg_pct = round(chg / prev * 100.0, 2)
    exp = fut.get("expiry")
    label = str(fut["tradingsymbol"])
    if exp:
        try:
            d = date.fromisoformat(str(exp)[:10])
            label = f"{underlying.upper()}-{d.strftime('%d%b%y').upper()}"
        except Exception:
            pass
    asof = _now_ist().strftime("%d-%b-%Y %H:%M")
    return {
        "fut_symbol": label,
        "fut_tradingsymbol": str(fut["tradingsymbol"]),
        "fut_ltp": ltp,
        "fut_chg": chg,
        "fut_chg_pct": chg_pct,
        "asof": asof,
        "fut_token": int(fut["instrument_token"]),
        "fut_expiry": str(exp) if exp else None,
    }


def _spot_fair_summary(underlying: str) -> dict[str, Any]:
    """Fair Price ≈ cash index spot (futures for MCX). Change vs prior close when available."""
    from options.oi_var import _quote_batches
    from instruments import resolve_instrument

    meta = INDEX_OPTIONS[underlying]
    spot_source = meta.get("spot_source") or ("index" if meta.get("index_token_key") else "future")
    fair: float | None = None
    prev: float | None = None

    if spot_source == "future":
        px, _ = get_index_spot_detail(underlying)
        fair = float(px) if px is not None else None
    else:
        key = meta.get("index_token_key")
        if key:
            try:
                resolved = resolve_instrument(key)
                qkey = f"{resolved['exchange']}:{resolved['tradingsymbol']}"
                quotes = _quote_batches([qkey])
                q = quotes.get(qkey) or {}
                try:
                    fair = float(q.get("last_price") or 0) or None
                except (TypeError, ValueError):
                    fair = None
                ohlc = q.get("ohlc") or {}
                try:
                    prev = float(ohlc.get("close") or 0) or None
                except (TypeError, ValueError):
                    prev = None
            except Exception:
                px, _ = get_index_spot_detail(underlying)
                fair = float(px) if px is not None else None

    chg = chg_pct = None
    if fair is not None and prev is not None and prev > 0:
        chg = round(fair - prev, 2)
        chg_pct = round(chg / prev * 100.0, 2)
    return {"fair_price": round(fair, 2) if fair is not None else None, "fair_chg": chg, "fair_chg_pct": chg_pct}


def _selected_iv_and_fair(
    *,
    underlying: str,
    expiry: str,
    call_strike: float,
    put_strike: float,
    ce_ltp: float | None,
    pe_ltp: float | None,
) -> dict[str, Any]:
    r = float(PRICING_ENGINE_DEFAULTS.get("risk_free_rate", 0.065))
    spot = require_index_spot(underlying)
    tte = time_to_expiry_years(expiry)
    ce_iv = implied_volatility(ce_ltp, spot, call_strike, tte, "CE", r) if ce_ltp else None
    pe_iv = implied_volatility(pe_ltp, spot, put_strike, tte, "PE", r) if pe_ltp else None
    iv_dec: float | None = None
    if ce_iv is not None and pe_iv is not None:
        iv_dec = (ce_iv + pe_iv) / 2.0
    elif ce_iv is not None:
        iv_dec = ce_iv
    elif pe_iv is not None:
        iv_dec = pe_iv

    straddle_bs = None
    if iv_dec is not None and tte is not None:
        ce_fair = price_black_scholes(
            spot=spot, strike=call_strike, tte_years=tte, iv=iv_dec, option_type="CE", risk_free_rate=r
        )
        pe_fair = price_black_scholes(
            spot=spot, strike=put_strike, tte_years=tte, iv=iv_dec, option_type="PE", risk_free_rate=r
        )
        if ce_fair is not None and pe_fair is not None:
            straddle_bs = round(float(ce_fair) + float(pe_fair), 4)

    return {
        "spot": float(spot),
        "iv": round(iv_dec * 100.0, 2) if iv_dec is not None else None,
        "iv_dec": iv_dec,
        "straddle_bs": straddle_bs,
        "tte_years": tte,
    }


def _iv_series_from_aligned(
    aligned: pd.DataFrame,
    *,
    underlying: str,
    expiry: str,
    call_strike: float,
    put_strike: float,
    spot: float,
) -> list[float | None]:
    r = float(PRICING_ENGINE_DEFAULTS.get("risk_free_rate", 0.065))
    out: list[float | None] = []
    for ts, row in aligned.iterrows():
        as_of = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if isinstance(as_of, datetime) and as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=IST)
        tte = time_to_expiry_years(expiry, as_of=as_of if isinstance(as_of, datetime) else None)
        ce_px = row.get("call_price")
        pe_px = row.get("put_price")
        ce_iv = (
            implied_volatility(float(ce_px), spot, call_strike, tte, "CE", r)
            if pd.notna(ce_px) and ce_px
            else None
        )
        pe_iv = (
            implied_volatility(float(pe_px), spot, put_strike, tte, "PE", r)
            if pd.notna(pe_px) and pe_px
            else None
        )
        if ce_iv is not None and pe_iv is not None:
            out.append(round((ce_iv + pe_iv) / 2.0 * 100.0, 2))
        elif ce_iv is not None:
            out.append(round(ce_iv * 100.0, 2))
        elif pe_iv is not None:
            out.append(round(pe_iv * 100.0, 2))
        else:
            out.append(None)
    return out


def _daily_iv_history_for_rank(
    aligned: pd.DataFrame,
    iv_series: list[float | None],
) -> list[float]:
    """Collapse intraday IV to one sample per day (last valid) for IVR/IVP."""
    if aligned.empty or not iv_series:
        return []
    by_day: dict[date, float] = {}
    for ts, iv in zip(aligned.index, iv_series):
        if iv is None:
            continue
        d = ts.date() if hasattr(ts, "date") else date.today()
        by_day[d] = float(iv)
    return list(by_day.values())


def _nullable_list(series: pd.Series) -> list[float | None]:
    out: list[float | None] = []
    for v in series.tolist():
        if v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v):
            out.append(None)
        else:
            out.append(round(float(v), 4))
    return out


def build_straddle_watch_snapshot(
    underlying: str,
    expiry: str,
    call_strike: float,
    put_strike: float,
    *,
    range_key: str = "1D",
) -> dict[str, Any]:
    u = underlying.upper()
    if u not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS.keys())}")
    if not str(u).replace("_", "").isalnum():
        raise ValueError("Invalid underlying")

    rk = _parse_range(range_key)
    available = list_expiries(u)
    exp = str(expiry)[:10]
    if exp not in available:
        raise RuntimeError(f"Expiry {exp} not available for {u}")

    ce = find_option_leg(u, exp, float(call_strike), "CE")
    pe = find_option_leg(u, exp, float(put_strike), "PE")
    if not ce:
        raise RuntimeError(f"No CE leg for {u} {exp} strike {call_strike}")
    if not pe:
        raise RuntimeError(f"No PE leg for {u} {exp} strike {put_strike}")

    start, end = _range_bounds(u, rk)
    ce_df = _fetch_leg_history(int(ce["instrument_token"]), start, end)
    pe_df = _fetch_leg_history(int(pe["instrument_token"]), start, end)
    ce_df = _filter_session(ce_df, u)
    pe_df = _filter_session(pe_df, u)
    aligned = align_leg_frames(ce_df, pe_df)

    # Quotes for latest LTPs + full-chain OI (max pain / PCR)
    from options.oi_var import _quote_batches

    chain = get_chain(u, exp)
    quote_keys = _quote_keys_for_chain(chain)
    # Always include selected legs
    quote_keys.append(f"{ce['exchange']}:{ce['tradingsymbol']}")
    quote_keys.append(f"{pe['exchange']}:{pe['tradingsymbol']}")
    quotes = _quote_batches(list(dict.fromkeys(quote_keys)))
    chain_rows = _attach_quote_oi(chain, quotes)

    ce_q = quotes.get(f"{ce['exchange']}:{ce['tradingsymbol']}") or {}
    pe_q = quotes.get(f"{pe['exchange']}:{pe['tradingsymbol']}") or {}
    try:
        ce_ltp = float(ce_q.get("last_price") or 0) or None
    except (TypeError, ValueError):
        ce_ltp = None
    try:
        pe_ltp = float(pe_q.get("last_price") or 0) or None
    except (TypeError, ValueError):
        pe_ltp = None
    # Fall back to last bar
    if ce_ltp is None and not aligned.empty and pd.notna(aligned["call_price"].iloc[-1]):
        ce_ltp = float(aligned["call_price"].iloc[-1])
    if pe_ltp is None and not aligned.empty and pd.notna(aligned["put_price"].iloc[-1]):
        pe_ltp = float(aligned["put_price"].iloc[-1])

    pricing = _selected_iv_and_fair(
        underlying=u,
        expiry=exp,
        call_strike=float(call_strike),
        put_strike=float(put_strike),
        ce_ltp=ce_ltp,
        pe_ltp=pe_ltp,
    )
    iv_series = _iv_series_from_aligned(
        aligned,
        underlying=u,
        expiry=exp,
        call_strike=float(call_strike),
        put_strike=float(put_strike),
        spot=float(pricing["spot"]),
    )
    daily_iv = _daily_iv_history_for_rank(aligned, iv_series)
    # Prefer current IV in the history set
    if pricing["iv"] is not None:
        daily_iv = daily_iv + [float(pricing["iv"])]
    ivr, ivp = iv_rank_and_percentile(pricing["iv"], daily_iv)

    prices = _nullable_list(aligned["straddle_price"]) if not aligned.empty else []
    vols = _nullable_list(aligned["straddle_vol"]) if not aligned.empty else []
    vwap = straddle_vwap_series(prices, vols)

    t_iso: list[str] = []
    if not aligned.empty:
        for ts in aligned.index:
            if hasattr(ts, "isoformat"):
                t_iso.append(ts.isoformat(sep=" "))
            else:
                t_iso.append(str(ts))

    fut = _fut_quote_summary(u)
    fair = _spot_fair_summary(u)
    lot = int(ce.get("lot_size") or INDEX_OPTIONS[u].get("lot_size") or 1)
    mp = max_pain_strike(chain_rows)
    pcr = _chain_pcr(chain_rows)

    step = int(INDEX_OPTIONS[u]["strike_step"])
    try:
        atm = atm_strike(float(pricing["spot"]), step)
    except Exception:
        atm = float(call_strike)

    return {
        "ok": True,
        "mode": "latest",
        "underlying": u,
        "expiry": exp,
        "call_strike": float(call_strike),
        "put_strike": float(put_strike),
        "atm_strike": float(atm),
        "range": rk,
        "ce": {
            "tradingsymbol": ce["tradingsymbol"],
            "exchange": ce["exchange"],
            "instrument_token": int(ce["instrument_token"]),
            "ltp": ce_ltp,
        },
        "pe": {
            "tradingsymbol": pe["tradingsymbol"],
            "exchange": pe["exchange"],
            "instrument_token": int(pe["instrument_token"]),
            "ltp": pe_ltp,
        },
        "summary": {
            **fut,
            **fair,
            "lot_size": lot,
            "iv": pricing["iv"],
            "ivr": ivr,
            "ivp": ivp,
            "max_pain": mp,
            "pcr": pcr,
            "straddle_ltp": (
                round(ce_ltp + pe_ltp, 2) if ce_ltp is not None and pe_ltp is not None else None
            ),
            "straddle_bs": pricing.get("straddle_bs"),
            "spot": pricing["spot"],
        },
        "series": {
            "t": t_iso,
            "call_price": _nullable_list(aligned["call_price"]) if not aligned.empty else [],
            "put_price": _nullable_list(aligned["put_price"]) if not aligned.empty else [],
            "straddle_price": prices,
            "straddle_vwap": vwap,
            "call_oi": _nullable_list(aligned["call_oi"]) if not aligned.empty else [],
            "put_oi": _nullable_list(aligned["put_oi"]) if not aligned.empty else [],
            "iv": iv_series,
        },
        "updated_at": _now_ist().isoformat(),
    }
