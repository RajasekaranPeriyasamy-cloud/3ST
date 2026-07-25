"""Implied volatility surface — IV across strikes × expiries for index options.

For each selected expiry we take ``strike_count`` strikes centred on the ATM and
solve IV from each option's live LTP (Black-Scholes). We use the **OTM** wing at
every strike (puts below spot, calls at/above spot) to build one clean smile, the
market-standard construction that avoids the parity-driven ITM noise.

Output is a regular grid (rows = expiries, cols = strikes offset from ATM) so the
frontend can render it three ways from the same payload: a 2D heatmap, per-expiry
skew smiles, an ATM term-structure line, and an interactive 3D surface.

Reuses ``options.chain`` (chain/expiries/spot), ``options.iv`` (IV + TTE) and the
batched quote fetch in ``options.oi_var`` so lot/instrument data tracks Kite.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from config import INDEX_OPTIONS, VOL_SURFACE_DEFAULTS
from options.chain import get_chain, list_expiries, require_index_spot
from options.iv import implied_volatility, time_to_expiry_years
from options.oi_var import _quote_batches


def vol_surface_config() -> dict[str, Any]:
    d = VOL_SURFACE_DEFAULTS
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "strike_count": d["strike_count"],
        "max_expiries": d["max_expiries"],
        "refresh_seconds": d["refresh_seconds"],
    }


def _select_expiries(underlying: str, expiries: list[str] | None, max_expiries: int) -> list[str]:
    available = list_expiries(underlying)
    if not available:
        return []
    today = date.today()
    if expiries:
        chosen = [e for e in expiries if e in available]
    else:
        chosen = [e for e in available if date.fromisoformat(e[:10]) >= today]
    return chosen[:max_expiries]


def build_vol_surface(
    underlying: str,
    expiries: list[str] | None = None,
    *,
    strike_count: int | None = None,
    max_expiries: int | None = None,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    defaults = VOL_SURFACE_DEFAULTS
    n_strikes = int(strike_count if strike_count is not None else defaults["strike_count"])
    n_strikes = max(5, min(n_strikes, 40))
    n_exp = int(max_expiries if max_expiries is not None else defaults["max_expiries"])

    step = int(INDEX_OPTIONS[underlying]["strike_step"])

    sel = _select_expiries(underlying, expiries, n_exp)
    if not sel:
        raise RuntimeError(f"No expiries available for {underlying}")

    spot = require_index_spot(underlying)

    atm = round(spot / step) * step
    half = n_strikes // 2
    x_strikes = [float(atm + i * step) for i in range(-half, half + 1)]

    # Collect OTM legs to quote across every expiry, then batch-fetch once.
    leg_map: dict[tuple[str, float], tuple[str, str]] = {}  # (expiry, strike) -> (otype, quote_key)
    for e in sel:
        chain = get_chain(underlying, e)
        smap = {float(r["strike"]): r for r in chain.get("strikes", [])}
        exch = chain.get("exchange", "NFO")
        for k in x_strikes:
            row = smap.get(k)
            if not row:
                continue
            otype = "CE" if k >= spot else "PE"
            leg = row.get(otype.lower())
            if not leg or not leg.get("tradingsymbol"):
                continue
            key = f"{leg.get('exchange', exch)}:{leg['tradingsymbol']}"
            leg_map[(e, k)] = (otype, key)

    quotes = _quote_batches([v[1] for v in leg_map.values()])

    z: list[list[float | None]] = []
    expiries_meta: list[dict[str, Any]] = []
    points: list[dict[str, Any]] = []
    resolved = 0
    today = date.today()

    for e in sel:
        tte = time_to_expiry_years(e)
        if tte is None:
            continue
        dte = (date.fromisoformat(e[:10]) - today).days
        row_iv: list[float | None] = []
        atm_iv: float | None = None
        for k in x_strikes:
            iv_pct: float | None = None
            entry = leg_map.get((e, k))
            if entry:
                otype, key = entry
                q = quotes.get(key)
                if q:
                    ltp = float(q.get("last_price") or 0)
                    iv = implied_volatility(ltp, spot, k, tte, otype)
                    if iv is not None:
                        iv_pct = round(iv * 100, 2)
                        resolved += 1
                        points.append(
                            {
                                "expiry": e,
                                "dte": dte,
                                "strike": k,
                                "moneyness": round(k / spot, 4),
                                "iv": iv_pct,
                                "option_type": otype,
                            }
                        )
            row_iv.append(iv_pct)
            if k == atm:
                atm_iv = iv_pct
        z.append(row_iv)
        expiries_meta.append(
            {
                "expiry": e,
                "dte": dte,
                "tte_years": round(tte, 6),
                "atm_iv": atm_iv,
            }
        )

    if not expiries_meta:
        raise RuntimeError(f"No resolvable expiries for {underlying}")

    return {
        "underlying": underlying,
        "spot": spot,
        "atm_strike": float(atm),
        "strike_step": step,
        "strike_count": n_strikes,
        "updated_at": datetime.now().astimezone().isoformat(),
        "strikes": x_strikes,
        "moneyness": [round(k / spot, 4) for k in x_strikes],
        "expiries": expiries_meta,
        "z": z,
        "points": points,
        "legs_resolved": resolved,
        "term_structure": [
            {"expiry": m["expiry"], "dte": m["dte"], "atm_iv": m["atm_iv"]}
            for m in expiries_meta
        ],
    }
