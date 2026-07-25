"""OI VAR desk — full-chain VAR in Crores with Top-N VAR / ↑ΔVAR / ↓ΔVAR boards.

P0–P3: renamed boards, dual ΔVAR modes, session-open, profile, flow tags,
Gamma context, multi-expiry, history + alerts.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import INDEX_OPTIONS, OI_VAR_DEFAULTS
from options.chain import get_chain, list_expiries, nearest_expiry, require_index_spot
from options.oi_var_store import CRORE, ensure_eod_baseline

QUOTE_BATCH = 500


def _cfg() -> dict[str, Any]:
    return dict(OI_VAR_DEFAULTS)


def var_config() -> dict[str, Any]:
    d = _cfg()
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "top_n": d["top_n"],
        "refresh_seconds": d["refresh_seconds"],
        "dvar_modes": ["oi_mark", "true"],
        "dvar_mode": d.get("dvar_mode", "oi_mark"),
        "strike_window": int(d.get("strike_window") or 0),
        "min_oi": int(d.get("min_oi") or 0),
        "multi_expiry_count": int(d.get("multi_expiry_count") or 2),
        "alert_dvar_burst_cr": float(d.get("alert_dvar_burst_cr") or 25),
    }


def var_crores(oi: int | float | None, price: float | None) -> float | None:
    if oi is None or price is None or price <= 0:
        return None
    return round((float(oi) * float(price)) / CRORE, 2)


def _arrow(value: float | int | None) -> str:
    if value is None:
        return "flat"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "flat"
    if v > 0:
        return "up"
    if v < 0:
        return "down"
    return "flat"


def _session_open_px(quote: dict[str, Any]) -> float | None:
    ohlc = quote.get("ohlc") or {}
    raw = ohlc.get("open")
    if raw is None:
        raw = quote.get("open")
    if raw is None:
        return None
    try:
        open_px = float(raw)
    except (TypeError, ValueError):
        return None
    return open_px if open_px > 0 else None


def _quote_price(quote: dict[str, Any], max_spread_pct: float) -> tuple[float | None, str]:
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

    if bid_f and ask_f and bid_f > 0 and ask_f >= bid_f:
        mid = 0.5 * (bid_f + ask_f)
        if mid > 0 and (ask_f - bid_f) / mid <= max_spread_pct:
            return mid, "mid"
    if ltp_f and ltp_f > 0:
        return ltp_f, "ltp"
    return None, "none"


def moneyness_label(side: str, strike: float, spot: float) -> str:
    if side.lower() in ("call", "ce"):
        return "ITMCE" if strike < spot else "OTMCE"
    return "ITMPE" if strike > spot else "OTMPE"


def flow_tag(delta_oi: int | None, ltp_chg: float | None) -> str:
    if delta_oi is None or ltp_chg is None:
        return "unknown"
    if delta_oi == 0 or ltp_chg == 0:
        return "flat"
    if delta_oi > 0 and ltp_chg > 0:
        return "long_build"
    if delta_oi > 0 and ltp_chg < 0:
        return "short_build"
    if delta_oi < 0 and ltp_chg > 0:
        return "short_cover"
    if delta_oi < 0 and ltp_chg < 0:
        return "long_unwind"
    return "flat"


# Long-leaning vs short-leaning for side regime (CE Short→Long / PE Long→Short)
_FLOW_LONG = {"long_build", "short_cover"}
_FLOW_SHORT = {"short_build", "long_unwind"}


def flow_polarity(tag: str | None) -> int:
    """+1 long-leaning, -1 short-leaning, 0 flat/unknown."""
    if tag in _FLOW_LONG:
        return 1
    if tag in _FLOW_SHORT:
        return -1
    return 0


def side_flow_regime(
    legs: list[dict[str, Any]],
    side: str,
    *,
    tag_key: str = "flow_tag_session",
) -> dict[str, Any]:
    """Aggregate CE or PE into long / short / mixed using |ΔOI|-weighted tags.

    Score > 0 → long-leaning (build longs / cover shorts).
    Score < 0 → short-leaning (build shorts / unwind longs).
    """
    want = "call" if side.lower() in ("call", "ce") else "put"
    long_w = 0.0
    short_w = 0.0
    counts: dict[str, int] = {
        "long_build": 0,
        "short_build": 0,
        "short_cover": 0,
        "long_unwind": 0,
        "flat": 0,
        "unknown": 0,
    }
    for leg in legs:
        if leg.get("side") != want:
            continue
        tag = str(leg.get(tag_key) or leg.get("flow_tag") or "unknown")
        counts[tag] = counts.get(tag, 0) + 1
        # Weight by absolute OI change (contracts); fall back to |ΔVAR|
        w = abs(float(leg.get("delta_oi_session") or leg.get("delta_oi") or 0))
        if w <= 0:
            w = abs(float(leg.get("var_chg_session") or leg.get("var_chg_cr") or 0))
        if w <= 0:
            w = 1.0
        pol = flow_polarity(tag)
        if pol > 0:
            long_w += w
        elif pol < 0:
            short_w += w

    score = round(long_w - short_w, 2)
    total = long_w + short_w
    # Require some mass so noise doesn't flip regime
    if total <= 0:
        regime = "mixed"
    elif abs(score) / total < 0.15:
        regime = "mixed"
    elif score > 0:
        regime = "long"
    else:
        regime = "short"

    return {
        "side": "CE" if want == "call" else "PE",
        "regime": regime,
        "score": score,
        "long_weight": round(long_w, 2),
        "short_weight": round(short_w, 2),
        "counts": counts,
    }


def detect_flow_shifts(
    history: list[dict[str, Any]],
    *,
    min_hold_ticks: int = 2,
) -> list[dict[str, Any]]:
    """Detect CE/PE regime flips across session history (e.g. CE short→long at ~10:10).

    A shift is recorded when regime changes between long↔short (mixed ignored as bridge)
    and the new regime holds for ``min_hold_ticks`` samples.
    """
    shifts: list[dict[str, Any]] = []
    if len(history) < min_hold_ticks + 1:
        return shifts

    for side_key, label in (("ce_flow_regime", "CE"), ("pe_flow_regime", "PE")):
        last_solid: str | None = None
        pending_from: str | None = None
        pending_to: str | None = None
        pending_t: str | None = None
        pending_spot: float | None = None
        hold = 0

        for pt in history:
            reg = pt.get(side_key)
            if reg not in ("long", "short"):
                continue
            if last_solid is None:
                last_solid = reg
                continue
            if reg == last_solid:
                pending_from = pending_to = pending_t = None
                hold = 0
                continue
            # Candidate flip
            if pending_to == reg:
                hold += 1
            else:
                pending_from = last_solid
                pending_to = reg
                pending_t = pt.get("t")
                try:
                    pending_spot = float(pt["spot"]) if pt.get("spot") is not None else None
                except (TypeError, ValueError):
                    pending_spot = None
                hold = 1
            if hold >= min_hold_ticks and pending_from and pending_to:
                shifts.append(
                    {
                        "side": label,
                        "from_regime": pending_from,
                        "to_regime": pending_to,
                        "t": pending_t,
                        "spot": pending_spot,
                        "label": f"{label} {pending_from} → {pending_to}",
                        "message": (
                            f"{label} flow shifted {pending_from} → {pending_to}"
                            + (f" near {pending_t}" if pending_t else "")
                        ),
                    }
                )
                last_solid = pending_to
                pending_from = pending_to = pending_t = None
                hold = 0

    return shifts



def _flatten_chain_legs(chain: dict[str, Any]) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    for row in chain.get("strikes") or []:
        strike = float(row["strike"])
        if row.get("ce"):
            legs.append({"side": "call", "strike": strike, **row["ce"]})
        if row.get("pe"):
            legs.append({"side": "put", "strike": strike, **row["pe"]})
    return legs


def _quote_batches(keys: list[str]) -> dict[str, dict[str, Any]]:
    from kite_client import fetch_quote_batch

    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(keys), QUOTE_BATCH):
        chunk = keys[i : i + QUOTE_BATCH]
        try:
            out.update(fetch_quote_batch(chunk))
        except Exception:
            continue
    return out


def _leg_from_quote(
    leg: dict[str, Any],
    quote: dict[str, Any] | None,
    spot: float,
    baseline_oi: int | None,
    *,
    dvar_mode: str,
    max_spread_pct: float,
    min_oi: int,
    session_token: dict[str, Any] | None = None,
    eod_ltp: float | None = None,
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

    side = leg["side"]
    strike = float(leg["strike"])
    var_cr = var_crores(oi, px)
    open_px = _session_open_px(quote)
    ltp_chg = round(px - open_px, 2) if open_px is not None else None

    delta_oi: int | None = None
    var_chg_cr: float | None = None
    var_chg_session: float | None = None

    if baseline_oi is not None:
        delta_oi = oi - baseline_oi
        if dvar_mode == "true" and eod_ltp is not None and eod_ltp > 0:
            base_var = var_crores(baseline_oi, eod_ltp)
            if base_var is not None and var_cr is not None:
                var_chg_cr = round(var_cr - base_var, 2)
        else:
            var_chg_cr = var_crores(abs(delta_oi), px)
            if delta_oi < 0 and var_chg_cr is not None:
                var_chg_cr = -var_chg_cr

    delta_oi_session: int | None = None
    ltp_chg_session: float | None = None
    if session_token is not None:
        try:
            s_oi = int(session_token.get("oi"))
            s_ltp = float(session_token.get("ltp"))
            s_var = var_crores(s_oi, s_ltp)
            if s_var is not None and var_cr is not None:
                var_chg_session = round(var_cr - s_var, 2)
            delta_oi_session = oi - s_oi
            ltp_chg_session = round(px - s_ltp, 2)
        except (TypeError, ValueError):
            pass

    var_vs_open = None
    if open_px and var_cr is not None:
        open_var = var_crores(oi, open_px)
        if open_var is not None:
            var_vs_open = round(var_cr - open_var, 2)

    return {
        "side": side,
        "strike": strike,
        "symbol": leg.get("tradingsymbol", ""),
        "instrument_token": leg.get("instrument_token"),
        "moneyness": moneyness_label(side, strike, spot),
        "oi": oi,
        "ltp": round(float(quote.get("last_price") or px), 2),
        "price": round(px, 2),
        "price_source": px_src,
        "open": round(open_px, 2) if open_px is not None else None,
        "ltp_chg": ltp_chg,
        "ltp_arrow": _arrow(ltp_chg),
        "delta_oi": delta_oi,
        "delta_oi_session": delta_oi_session,
        "var_cr": var_cr,
        "var_arrow": _arrow(var_vs_open),
        "var_chg_cr": var_chg_cr,
        "var_chg_arrow": _arrow(var_chg_cr),
        "var_chg_session": var_chg_session,
        "flow_tag": flow_tag(delta_oi, ltp_chg),
        "flow_tag_session": flow_tag(delta_oi_session, ltp_chg_session),
        "pct_side_var": None,
        "near_call_wall": False,
        "near_put_wall": False,
        "near_flip": False,
    }


def build_chain_legs(
    legs: list[dict[str, Any]],
    quotes: dict[str, dict[str, Any]],
    spot: float,
    oi_baseline: dict[str, int],
    *,
    dvar_mode: str = "oi_mark",
    max_spread_pct: float = 0.15,
    min_oi: int = 0,
    session_by_token: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    built: list[dict[str, Any]] = []
    for leg in legs:
        exchange = leg.get("exchange", "NFO")
        symbol = leg.get("tradingsymbol")
        if not symbol:
            continue
        key = f"{exchange}:{symbol}"
        token = leg.get("instrument_token")
        baseline = oi_baseline.get(str(token)) if token else None
        sess = session_by_token.get(str(token)) if session_by_token and token is not None else None
        row = _leg_from_quote(
            leg,
            quotes.get(key),
            spot,
            baseline,
            dvar_mode=dvar_mode,
            max_spread_pct=max_spread_pct,
            min_oi=min_oi,
            session_token=sess,
        )
        if row:
            built.append(row)
    return built


def _footer(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    def _sum(field: str) -> float | None:
        vals = [r[field] for r in rows if r.get(field) is not None]
        if not vals:
            return None
        return round(sum(float(v) for v in vals), 2)

    return {"var_cr_total": _sum("var_cr"), "var_chg_total": _sum("var_chg_cr")}


def _annotate_pct(rows: list[dict[str, Any]], side_total: float | None) -> None:
    if not side_total or side_total <= 0:
        for r in rows:
            r["pct_side_var"] = None
        return
    for r in rows:
        v = r.get("var_cr")
        r["pct_side_var"] = round(100.0 * float(v) / side_total, 2) if v is not None else None


def rank_tables(chain_legs: list[dict[str, Any]], top_n: int = 10) -> dict[str, Any]:
    ce = [l for l in chain_legs if l["side"] == "call"]
    pe = [l for l in chain_legs if l["side"] == "put"]
    ce_total = sum(float(r["var_cr"]) for r in ce if r.get("var_cr") is not None) or None
    pe_total = sum(float(r["var_cr"]) for r in pe if r.get("var_cr") is not None) or None
    _annotate_pct(ce, ce_total)
    _annotate_pct(pe, pe_total)

    def _top_var(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(rows, key=lambda r: r.get("var_cr") or 0, reverse=True)[:top_n]

    def _top_dvar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with_chg = [r for r in rows if r.get("var_chg_cr") is not None]
        return sorted(with_chg, key=lambda r: r["var_chg_cr"], reverse=True)[:top_n]

    def _bottom_dvar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with_chg = [r for r in rows if r.get("var_chg_cr") is not None]
        return sorted(with_chg, key=lambda r: r["var_chg_cr"])[:top_n]

    def _side(top, up, dn):
        return {
            "top_oi": top,
            "top_chg": up,
            "bottom_chg": dn,
            "top_var": top,
            "top_dvar_up": up,
            "top_dvar_dn": dn,
            "footer": {
                "top_oi": _footer(top),
                "top_chg": _footer(up),
                "bottom_chg": _footer(dn),
                "top_var": _footer(top),
                "top_dvar_up": _footer(up),
                "top_dvar_dn": _footer(dn),
            },
        }

    return {
        "calls": _side(_top_var(ce), _top_dvar(ce), _bottom_dvar(ce)),
        "puts": _side(_top_var(pe), _top_dvar(pe), _bottom_dvar(pe)),
        "ce_var_total": round(ce_total, 2) if ce_total is not None else None,
        "pe_var_total": round(pe_total, 2) if pe_total is not None else None,
    }


def _var_profile(chain_legs: list[dict[str, Any]], spot: float, window: int) -> list[dict[str, Any]]:
    by_k: dict[float, dict[str, float]] = {}
    for leg in chain_legs:
        k = float(leg["strike"])
        row = by_k.setdefault(k, {"strike": k, "ce_var": 0.0, "pe_var": 0.0, "net_dvar": 0.0})
        v = float(leg.get("var_cr") or 0)
        dv = float(leg.get("var_chg_cr") or 0)
        if leg["side"] == "call":
            row["ce_var"] += v
        else:
            row["pe_var"] += v
        row["net_dvar"] += dv

    rows = sorted(by_k.values(), key=lambda r: r["strike"])
    if window > 0 and rows:
        atm = min(rows, key=lambda r: abs(r["strike"] - spot))
        idx = rows.index(atm)
        rows = rows[max(0, idx - window) : min(len(rows), idx + window + 1)]
    for r in rows:
        r["ce_var"] = round(r["ce_var"], 2)
        r["pe_var"] = round(r["pe_var"], 2)
        r["net_dvar"] = round(r["net_dvar"], 2)
        r["total_var"] = round(r["ce_var"] + r["pe_var"], 2)
    return rows


def _attach_gamma_context(chain_legs: list[dict[str, Any]], underlying: str, expiry: str) -> dict[str, Any]:
    ctx: dict[str, Any] = {"call_wall": None, "put_wall": None, "flip_level": None, "available": False}
    try:
        from options.gamma_density import build_gamma_snapshot

        snap = build_gamma_snapshot(
            underlying,
            expiry=expiry,
            include_multi_expiry=False,
            include_history=False,
            include_vanna_strip=False,
        )
        ctx["call_wall"] = snap.get("call_wall")
        ctx["put_wall"] = snap.get("put_wall")
        ctx["flip_level"] = snap.get("flip_level")
        ctx["available"] = True
        cw, pw, flip = ctx["call_wall"], ctx["put_wall"], ctx["flip_level"]
        for leg in chain_legs:
            k = leg["strike"]
            if cw is not None and abs(k - float(cw)) < 1e-6:
                leg["near_call_wall"] = True
            if pw is not None and abs(k - float(pw)) < 1e-6:
                leg["near_put_wall"] = True
            if flip is not None and abs(k - float(flip)) <= max(abs(float(flip)) * 0.002, 25):
                leg["near_flip"] = True
    except Exception:
        pass
    return ctx


def _multi_expiry_summary(
    underlying: str,
    primary_expiry: str,
    spot: float,
    *,
    count: int,
    dvar_mode: str,
    max_spread_pct: float,
    min_oi: int,
) -> list[dict[str, Any]]:
    if count <= 1:
        return []
    try:
        exps = [e for e in list_expiries(underlying) if e != primary_expiry][: max(0, count - 1)]
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for exp in exps:
        try:
            chain = get_chain(underlying, exp)
            raw = _flatten_chain_legs(chain)
            if not raw:
                continue
            baseline_date, oi_baseline = ensure_eod_baseline(underlying, exp, raw)
            keys = [
                f"{leg.get('exchange', chain.get('exchange', 'NFO'))}:{leg['tradingsymbol']}"
                for leg in raw
                if leg.get("tradingsymbol")
            ]
            quotes = _quote_batches(keys)
            legs = build_chain_legs(
                raw, quotes, spot, oi_baseline,
                dvar_mode=dvar_mode, max_spread_pct=max_spread_pct, min_oi=min_oi,
            )
            ce = [l for l in legs if l["side"] == "call"]
            pe = [l for l in legs if l["side"] == "put"]
            top_ce = max(ce, key=lambda r: r.get("var_cr") or 0) if ce else None
            top_pe = max(pe, key=lambda r: r.get("var_cr") or 0) if pe else None
            ce_tot = round(sum(float(r["var_cr"]) for r in ce if r.get("var_cr")), 2) if ce else None
            pe_tot = round(sum(float(r["var_cr"]) for r in pe if r.get("var_cr")), 2) if pe else None
            out.append({
                "expiry": exp,
                "baseline_date": baseline_date,
                "ce_var_total": ce_tot,
                "pe_var_total": pe_tot,
                "top_ce_strike": top_ce["strike"] if top_ce else None,
                "top_ce_var": top_ce.get("var_cr") if top_ce else None,
                "top_pe_strike": top_pe["strike"] if top_pe else None,
                "top_pe_var": top_pe.get("var_cr") if top_pe else None,
                "legs": len(legs),
            })
        except Exception:
            continue
    return out


def _build_alerts(
    history: list[dict[str, Any]],
    *,
    top_ce: float | None,
    top_pe: float | None,
    net_dvar: float | None,
    burst_cr: float,
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if len(history) < 2:
        return alerts
    prev = history[-2]
    if top_ce is not None and prev.get("top_ce_strike") not in (None, top_ce):
        alerts.append({
            "type": "top_ce_migration",
            "message": f"CE Top-1 VAR moved {prev.get('top_ce_strike')} → {top_ce}",
            "severity": "warn",
        })
    if top_pe is not None and prev.get("top_pe_strike") not in (None, top_pe):
        alerts.append({
            "type": "top_pe_migration",
            "message": f"PE Top-1 VAR moved {prev.get('top_pe_strike')} → {top_pe}",
            "severity": "warn",
        })
    prev_net = prev.get("net_dvar")
    if net_dvar is not None and prev_net is not None:
        burst = abs(float(net_dvar) - float(prev_net))
        if burst >= burst_cr:
            alerts.append({
                "type": "dvar_burst",
                "message": f"Net ΔVAR burst {burst:.1f} Cr since last tick",
                "severity": "alert",
            })
    return alerts


def build_var_snapshot(
    underlying: str,
    expiry: str | None = None,
    *,
    top_n: int | None = None,
    dvar_mode: str | None = None,
    strike_window: int | None = None,
    min_oi: int | None = None,
    include_multi_expiry: bool = False,
    include_gamma_context: bool = False,
    include_history: bool = True,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    defaults = _cfg()
    n = top_n if top_n is not None else int(defaults["top_n"])
    mode = (dvar_mode or defaults.get("dvar_mode") or "oi_mark").lower()
    if mode not in ("oi_mark", "true"):
        mode = "oi_mark"
    window = int(strike_window if strike_window is not None else defaults.get("strike_window") or 0)
    min_oi_v = int(min_oi if min_oi is not None else defaults.get("min_oi") or 0)
    max_spread = float(defaults.get("max_mid_spread_pct") or 0.15)
    multi_n = int(defaults.get("multi_expiry_count") or 2)
    hist_max = int(defaults.get("history_max_points") or 120)
    burst_cr = float(defaults.get("alert_dvar_burst_cr") or 25)
    open_after = str(defaults.get("session_open_after") or "09:20")

    exp = expiry or nearest_expiry(underlying)
    if not exp:
        raise RuntimeError(f"No expiries found for {underlying}")

    spot = float(require_index_spot(underlying))
    chain = get_chain(underlying, exp)
    raw_legs = _flatten_chain_legs(chain)
    if not raw_legs:
        raise RuntimeError(f"No option legs for {underlying} expiry {exp}")

    baseline_date, oi_baseline = ensure_eod_baseline(underlying, exp, raw_legs)
    quote_keys = [
        f"{leg.get('exchange', chain.get('exchange', 'NFO'))}:{leg['tradingsymbol']}"
        for leg in raw_legs
        if leg.get("tradingsymbol")
    ]
    quotes = _quote_batches(quote_keys)

    prelim = build_chain_legs(
        raw_legs, quotes, spot, oi_baseline,
        dvar_mode=mode, max_spread_pct=max_spread, min_oi=min_oi_v,
    )

    session_entry = None
    session_by_token: dict[str, Any] = {}
    try:
        from options.oi_var_session import ensure_session_open

        session_entry = ensure_session_open(underlying, exp, prelim, after_hhmm=open_after)
        if session_entry:
            session_by_token = session_entry.get("by_token") or {}
    except Exception:
        session_entry = None

    chain_legs = (
        build_chain_legs(
            raw_legs, quotes, spot, oi_baseline,
            dvar_mode=mode, max_spread_pct=max_spread, min_oi=min_oi_v,
            session_by_token=session_by_token,
        )
        if session_by_token
        else prelim
    )

    gamma_ctx = (
        _attach_gamma_context(chain_legs, underlying, exp)
        if include_gamma_context
        else {"available": False, "call_wall": None, "put_wall": None, "flip_level": None}
    )

    ranked = rank_tables(chain_legs, top_n=n)
    ce_tot = ranked.pop("ce_var_total")
    pe_tot = ranked.pop("pe_var_total")
    pcr_var = round(pe_tot / ce_tot, 3) if ce_tot and pe_tot is not None and ce_tot > 0 else None

    ce_dvar = sum(float(r["var_chg_cr"]) for r in chain_legs if r["side"] == "call" and r.get("var_chg_cr") is not None)
    pe_dvar = sum(float(r["var_chg_cr"]) for r in chain_legs if r["side"] == "put" and r.get("var_chg_cr") is not None)
    net_dvar = round(ce_dvar + pe_dvar, 2)

    top_ce_rows = ranked["calls"]["top_var"]
    top_pe_rows = ranked["puts"]["top_var"]
    top_ce_strike = top_ce_rows[0]["strike"] if top_ce_rows else None
    top_pe_strike = top_pe_rows[0]["strike"] if top_pe_rows else None

    top10_ce_var = ranked["calls"]["footer"]["top_var"]["var_cr_total"]
    top10_pe_var = ranked["puts"]["footer"]["top_var"]["var_cr_total"]
    concentration = {
        "ce_top_share_pct": round(100.0 * top10_ce_var / ce_tot, 1) if ce_tot and top10_ce_var else None,
        "pe_top_share_pct": round(100.0 * top10_pe_var / pe_tot, 1) if pe_tot and top10_pe_var else None,
    }

    profile_window = window if window > 0 else 20
    var_profile = _var_profile(chain_legs, spot, profile_window)

    atm = None
    if chain_legs:
        strikes = sorted({float(r["strike"]) for r in chain_legs})
        atm = min(strikes, key=lambda k: abs(k - spot)) if strikes else None

    multi = (
        _multi_expiry_summary(
            underlying, exp, spot, count=multi_n, dvar_mode=mode,
            max_spread_pct=max_spread, min_oi=min_oi_v,
        )
        if include_multi_expiry
        else []
    )

    history: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    flow_shifts: list[dict[str, Any]] = []
    ce_flow = side_flow_regime(chain_legs, "call")
    pe_flow = side_flow_regime(chain_legs, "put")

    if include_history:
        try:
            from options.oi_var_session import append_history_point, get_history

            point = {
                "t": datetime.now().astimezone().isoformat(timespec="seconds"),
                "spot": spot,
                "ce_var_total": ce_tot,
                "pe_var_total": pe_tot,
                "net_dvar": net_dvar,
                "top_ce_strike": top_ce_strike,
                "top_pe_strike": top_pe_strike,
                "pcr_var": pcr_var,
                "ce_flow_regime": ce_flow.get("regime"),
                "pe_flow_regime": pe_flow.get("regime"),
                "ce_flow_score": ce_flow.get("score"),
                "pe_flow_score": pe_flow.get("score"),
            }
            history = append_history_point(underlying, exp, point, max_points=hist_max)
            if not history:
                history = get_history(underlying, exp)
            alerts = _build_alerts(
                history, top_ce=top_ce_strike, top_pe=top_pe_strike,
                net_dvar=net_dvar, burst_cr=burst_cr,
            )
            flow_shifts = detect_flow_shifts(history)
            for sh in flow_shifts:
                alerts.append({
                    "type": "flow_shift",
                    "message": sh.get("message") or sh.get("label"),
                    "severity": "alert",
                    "side": sh.get("side"),
                    "from_regime": sh.get("from_regime"),
                    "to_regime": sh.get("to_regime"),
                    "t": sh.get("t"),
                })
        except Exception:
            history = []
            alerts = []
            flow_shifts = []

    mid_n = sum(1 for r in chain_legs if r.get("price_source") == "mid")
    ltp_n = sum(1 for r in chain_legs if r.get("price_source") == "ltp")

    return {
        "underlying": underlying,
        "expiry": exp,
        "spot": spot,
        "atm_strike": atm,
        "updated_at": datetime.now().astimezone().isoformat(),
        "baseline_date": baseline_date,
        "dvar_mode": mode,
        "session_open_at": session_entry.get("captured_at") if session_entry else None,
        "chain_legs_quoted": len(chain_legs),
        "chain_legs_total": len(raw_legs),
        "top_n": n,
        "price_source_stats": {"mid": mid_n, "ltp": ltp_n},
        "flow_regime": {"ce": ce_flow, "pe": pe_flow},
        "flow_shifts": flow_shifts,
        "summary": {
            "ce_var_total": ce_tot,
            "pe_var_total": pe_tot,
            "pcr_var": pcr_var,
            "ce_dvar_total": round(ce_dvar, 2),
            "pe_dvar_total": round(pe_dvar, 2),
            "net_dvar": net_dvar,
            "concentration": concentration,
        },
        "gamma_context": gamma_ctx,
        "var_profile": var_profile,
        "multi_expiry": multi,
        "history": history,
        "alerts": alerts,
        **ranked,
    }
