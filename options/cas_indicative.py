"""Closing Auction Session (CAS) desk payload — official + pre-close forecast.

Display-only helper for NIFTY / BANKNIFTY / SENSEX. Does not feed GEX, OI, or
spot math — callers must keep using continuous LTP (`get_index_spot` /
`require_index_spot`) for calculations.

**Objective 1:** desk ``estimate`` is a pre-15:15 close forecast (Synth F +
Fut LTP + VWAP proxy) — available outside the CAS window, not official
equilibrium. Official Kite index ``indicative_*`` is accepted only when within
3% of continuous spot (see ``sanitize_official_indicative``).

Batch API shape: ``{"items": [<CasIndicative>, ...]}``.
Single item shape (frontend contract)::

    {
      "underlying": "NIFTY",
      "in_cas_window": true,
      "spot": 24750.0,
      "indicative": 24772.0,
      "official_indicative": 24772.0,
      "estimate": 24765.0,
      "estimate_components": { "synth_f": ..., "fut_ltp": ..., "ref_vwap": ..., "ref_vwap_window": "running_1500" },
      "estimate_method": "proxy_v1",
      "reference_limit_price": 24700.0,
      "upper_circuit_limit": null,
      "lower_circuit_limit": null,
      "total_imbalance": null,
      "source": "kite_quote",
      "asof": "2026-08-06T15:20:00+05:30",
      "last": { "indicative": 24772.0, "...": "prior in-window tick or null" },
      "session_poc": { "poc": 24750.0, "fut_symbol": "...", "...": "or null" },
      "synthetic_future": { "F": 24780.0, "atm_strike": 24750.0, "...": "or null" }
    }
"""

from __future__ import annotations

import math
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from config import INDEX_OPTIONS
from instruments import resolve_instrument
from kite_client import fetch_quote_batch
from options.cas_estimate import sanitize_official_indicative
from options.chain import get_index_spot

IST = ZoneInfo("Asia/Kolkata")

CAS_UNDERLYINGS = ("NIFTY", "BANKNIFTY", "SENSEX")
CAS_WINDOW_START = time(15, 15)
CAS_WINDOW_END = time(15, 35)

# In-process last good in-window tick (for outside-window display on /cas-indicative).
_LAST_TICK: dict[str, dict[str, Any]] = {}

# Prefer indicative_close_price; aliases kept for spike discoveries.
_INDICATIVE_KEYS = (
    "indicative_close_price",
    "indicative_close",
    "indicative_equilibrium_price",
    "indicative_index_value",
)

_AUCTION_LOOKING_KEYS = frozenset(
    {
        *_INDICATIVE_KEYS,
        "reference_limit_price",
        "upper_circuit_limit",
        "lower_circuit_limit",
        "total_imbalance",
        "buy_quantity",
        "sell_quantity",
        "auction_number",
        "auction_quantity",
    }
)


def _now_ist(when: datetime | None = None) -> datetime:
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def in_cas_window(when: datetime | None = None) -> bool:
    """True during cash Closing Auction Session 15:15–15:35 IST (inclusive)."""
    now = _now_ist(when)
    t = now.timetz().replace(tzinfo=None)
    return CAS_WINDOW_START <= t <= CAS_WINDOW_END


def _asof_iso(when: datetime | None = None) -> str:
    return _now_ist(when).isoformat(timespec="seconds")


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_intish(value: Any) -> int | float | None:
    """Preserve large imbalance integers when possible; else float/null."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        f = float(value)
        if f.is_integer() and abs(f) < 2**53:
            return int(f)
        return f
    except (TypeError, ValueError):
        return None


def parse_indicative_from_quote(quote: dict[str, Any] | None) -> float | None:
    """Prefer ``indicative_close_price`` (and known aliases) from a Kite quote."""
    if not quote:
        return None
    for key in _INDICATIVE_KEYS:
        if key in quote:
            px = _optional_number(quote.get(key))
            if px is not None:
                return px
    return None


def parse_cas_fields(quote: dict[str, Any] | None) -> dict[str, Any]:
    """Pull CAS-related scalars from a quote dict (missing → null)."""
    q = quote or {}
    return {
        "indicative": parse_indicative_from_quote(q),
        "reference_limit_price": _optional_number(q.get("reference_limit_price")),
        "upper_circuit_limit": _optional_number(q.get("upper_circuit_limit")),
        "lower_circuit_limit": _optional_number(q.get("lower_circuit_limit")),
        "total_imbalance": _optional_intish(q.get("total_imbalance")),
    }


def resolve_index_quote_key(underlying: str) -> str:
    """``exchange:tradingsymbol`` for Kite quote (via INDEX_OPTIONS index_token_key)."""
    u = str(underlying or "").upper()
    if u not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")
    meta = INDEX_OPTIONS[u]
    token_key = meta.get("index_token_key")
    if not token_key:
        raise ValueError(f"No index_token_key for {u} (CAS indices only)")
    resolved = resolve_instrument(str(token_key))
    return f"{resolved['exchange']}:{resolved['tradingsymbol']}"


def _empty_payload(
    underlying: str,
    *,
    in_window: bool,
    spot: float | None,
    source: str,
    when: datetime | None = None,
) -> dict[str, Any]:
    return {
        "underlying": underlying,
        "in_cas_window": in_window,
        "spot": spot,
        "indicative": None,
        "official_indicative": None,
        "estimate": None,
        "estimate_components": None,
        "estimate_method": None,
        "reference_limit_price": None,
        "upper_circuit_limit": None,
        "lower_circuit_limit": None,
        "total_imbalance": None,
        "source": source,
        "asof": _asof_iso(when),
        "last": None,
        "session_poc": None,
        "synthetic_future": None,
    }


def _attach_reference_levels(
    payload: dict[str, Any],
    *,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Attach Fut POC + ATM synth F. Failures stay null — never block CAS."""
    u = str(payload.get("underlying") or "").upper()
    spot = payload.get("spot")
    # Prefer sanitized official; never pass garbage into basis-vs-CAS.
    indicative = payload.get("official_indicative")
    if indicative is None:
        indicative = payload.get("indicative")

    try:
        from options.session_poc import compute_session_poc

        payload["session_poc"] = compute_session_poc(u, when=when)
    except Exception:
        payload["session_poc"] = None

    try:
        from options.synthetic_future import compute_synthetic_future

        ind_f = None
        if indicative is not None:
            try:
                ind_f = float(indicative)
            except (TypeError, ValueError):
                ind_f = None
        payload["synthetic_future"] = compute_synthetic_future(
            u,
            when=when,
            spot=float(spot) if spot is not None else None,
            indicative=ind_f,
        )
    except Exception:
        payload["synthetic_future"] = None

    return payload


def _attach_estimate(
    payload: dict[str, Any],
    *,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Attach desk pre-close forecast (proxy_v1 / future constituent_v1). Null-safe.

    Not gated on ``in_cas_window`` — Objective 1 is a meaningful estimate before 15:15.
    """
    u = str(payload.get("underlying") or "").upper()
    synth = payload.get("synthetic_future") if isinstance(payload.get("synthetic_future"), dict) else None
    poc = payload.get("session_poc") if isinstance(payload.get("session_poc"), dict) else None
    synth_f = None
    if synth and synth.get("F") is not None:
        try:
            synth_f = float(synth["F"])
        except (TypeError, ValueError):
            synth_f = None
    fut_poc = None
    if poc and poc.get("poc") is not None:
        try:
            fut_poc = float(poc["poc"])
        except (TypeError, ValueError):
            fut_poc = None

    try:
        from options.cas_estimate import compute_cas_estimate

        est = compute_cas_estimate(
            u,
            when=when,
            synth_f=synth_f,
            fut_poc=fut_poc,
            fetch_missing=True,
        )
        payload["estimate"] = est.get("estimate")
        payload["estimate_components"] = est.get("estimate_components")
        payload["estimate_method"] = est.get("estimate_method")
    except Exception:
        payload["estimate"] = None
        payload["estimate_components"] = None
        payload["estimate_method"] = None
    return payload


def _last_tick_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "indicative": payload.get("indicative"),
        "spot": payload.get("spot"),
        "reference_limit_price": payload.get("reference_limit_price"),
        "upper_circuit_limit": payload.get("upper_circuit_limit"),
        "lower_circuit_limit": payload.get("lower_circuit_limit"),
        "total_imbalance": payload.get("total_imbalance"),
        "source": payload.get("source"),
        "asof": payload.get("asof"),
    }


def _store_last_tick(payload: dict[str, Any]) -> None:
    """Remember last good in-window indicative for outside-window UI."""
    if not payload.get("in_cas_window"):
        return
    ind = payload.get("indicative")
    if ind is None or isinstance(ind, bool):
        return
    try:
        if not math.isfinite(float(ind)):
            return
    except (TypeError, ValueError):
        return
    u = str(payload.get("underlying") or "").upper()
    if u:
        _LAST_TICK[u] = _last_tick_snapshot(payload)


def _attach_last(payload: dict[str, Any]) -> dict[str, Any]:
    u = str(payload.get("underlying") or "").upper()
    last = _LAST_TICK.get(u)
    payload["last"] = dict(last) if last else None
    return payload


def clear_last_ticks() -> None:
    """Test helper — wipe in-process last-tick cache."""
    _LAST_TICK.clear()


def fetch_cas_indicative(
    underlying: str,
    *,
    now: datetime | None = None,
    quote: dict[str, Any] | None = None,
    include_reference_levels: bool = True,
) -> dict[str, Any]:
    """Fetch CAS indicative payload for one index underlying.

    Outside the CAS window ``indicative`` is forced null (other quote fields may
    still be filled when a quote is available). Pass ``quote`` to skip the network
    (tests / batch path).

    When a prior in-window indicative was seen this process, ``last`` carries that
    snapshot for desk display outside the window.

    ``include_reference_levels`` attaches all-day Fut POC + ATM synthetic future
    (null-safe). Snapshot enrichment for Gamma/OI sets this False to avoid
    duplicate work / nested latency.
    """
    u = str(underlying or "").upper()
    if u not in CAS_UNDERLYINGS:
        raise ValueError(f"CAS indicative supports {list(CAS_UNDERLYINGS)}; got '{underlying}'")

    when = _now_ist(now)
    in_window = in_cas_window(when)
    spot = get_index_spot(u)

    q = quote
    source = "unavailable"
    if q is None:
        try:
            key = resolve_index_quote_key(u)
            batch = fetch_quote_batch([key])
            q = batch.get(key) or next(iter(batch.values()), None) if batch else None
            if q is not None:
                source = "kite_quote"
        except Exception:
            q = None
            source = "unavailable"
    else:
        source = "kite_quote"

    spot_f = float(spot) if spot is not None else None

    if q is None:
        payload = _attach_last(
            _empty_payload(u, in_window=in_window, spot=spot_f, source=source, when=when)
        )
    else:
        fields = parse_cas_fields(q)
        raw_indicative = fields["indicative"] if in_window else None
        # Sanitize before storing / exposing — garbage must not poison Δ / basis.
        indicative = (
            sanitize_official_indicative(raw_indicative, spot_f) if in_window else None
        )
        payload = {
            "underlying": u,
            "in_cas_window": in_window,
            "spot": spot_f,
            "indicative": indicative,
            "official_indicative": indicative,
            "estimate": None,
            "estimate_components": None,
            "estimate_method": None,
            "reference_limit_price": fields["reference_limit_price"],
            "upper_circuit_limit": fields["upper_circuit_limit"],
            "lower_circuit_limit": fields["lower_circuit_limit"],
            "total_imbalance": fields["total_imbalance"],
            "source": source,
            "asof": _asof_iso(when),
            "last": None,
            "session_poc": None,
            "synthetic_future": None,
        }
        _store_last_tick(payload)
        payload = _attach_last(payload)

    if include_reference_levels:
        _attach_reference_levels(payload, when=when)
        _attach_estimate(payload, when=when)
    return payload


def _quote_for_key(quotes: dict[str, dict[str, Any]], key: str) -> dict[str, Any] | None:
    if not key or not quotes:
        return None
    if key in quotes:
        return quotes[key]
    suffix = key.split(":", 1)[-1]
    for k, v in quotes.items():
        if str(k) == key or str(k).endswith(suffix):
            return v
    return None


def fetch_cas_indicative_batch(
    underlyings: tuple[str, ...] | list[str] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Batch CAS payloads. Returns ``{\"items\": [...]}`` in request / default order."""
    wanted = tuple(str(u).upper() for u in (underlyings or CAS_UNDERLYINGS))
    for u in wanted:
        if u not in CAS_UNDERLYINGS:
            raise ValueError(f"CAS indicative supports {list(CAS_UNDERLYINGS)}; got '{u}'")

    when = _now_ist(now)
    key_by_u: dict[str, str] = {}
    keys: list[str] = []
    for u in wanted:
        try:
            key = resolve_index_quote_key(u)
            key_by_u[u] = key
            keys.append(key)
        except Exception:
            key_by_u[u] = ""

    quotes: dict[str, dict[str, Any]] = {}
    if keys:
        try:
            quotes = fetch_quote_batch(keys)
        except Exception:
            quotes = {}

    items: list[dict[str, Any]] = []
    for u in wanted:
        key = key_by_u.get(u) or ""
        q = _quote_for_key(quotes, key)
        if q is not None:
            items.append(fetch_cas_indicative(u, now=when, quote=q, include_reference_levels=True))
        else:
            # Resolve/fetch failed — do not re-hit the network via fetch_cas_indicative.
            empty = _attach_last(
                _empty_payload(
                    u,
                    in_window=in_cas_window(when),
                    spot=get_index_spot(u),
                    source="unavailable",
                    when=when,
                )
            )
            empty = _attach_reference_levels(empty, when=when)
            items.append(_attach_estimate(empty, when=when))
    return {"items": items}


def cas_for_snapshot(underlying: str) -> dict[str, Any] | None:
    """Optional ``cas`` block for gamma / oi-movers snapshots. Null if not CAS-eligible.

    Outside the CAS window returns a cheap null-indicative payload (no quote poll) so
    desk snapshots stay display-only and tests/CI are not forced onto Kite.
    """
    u = str(underlying or "").upper()
    if u not in CAS_UNDERLYINGS:
        return None
    try:
        when = _now_ist()
        if not in_cas_window(when):
            return _empty_payload(
                u,
                in_window=False,
                spot=None,
                source="unavailable",
                when=when,
            )
        # No Fut POC / synth here — Gamma/OI attach session_poc at snapshot top-level.
        return fetch_cas_indicative(u, now=when, include_reference_levels=False)
    except Exception:
        return None


def debug_quote_dump(underlying: str) -> dict[str, Any]:
    """Phase 0 spike: top-level quote keys + auction-looking nested values (no secrets)."""
    u = str(underlying or "").upper()
    if u not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")
    meta = INDEX_OPTIONS[u]
    token_key = meta.get("index_token_key")
    if not token_key:
        raise ValueError(f"No index_token_key for {u}")

    resolved = resolve_instrument(str(token_key))
    quote_key = f"{resolved['exchange']}:{resolved['tradingsymbol']}"
    batch = fetch_quote_batch([quote_key])
    quote = batch.get(quote_key) or (next(iter(batch.values())) if batch else None)

    top_level_keys: list[str] = sorted(quote.keys()) if isinstance(quote, dict) else []
    auction: dict[str, Any] = {}
    nested_auction: dict[str, Any] = {}

    def _walk(obj: Any, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                sk = str(k)
                child = f"{path}.{sk}" if path else sk
                if sk.lower() in {a.lower() for a in _AUCTION_LOOKING_KEYS} or any(
                    tok in sk.lower()
                    for tok in ("indicative", "imbalance", "reference_limit", "circuit", "auction")
                ):
                    if path:
                        nested_auction[child] = v if not isinstance(v, (dict, list)) else _summarize(v)
                    else:
                        auction[sk] = v if not isinstance(v, (dict, list)) else _summarize(v)
                if isinstance(v, dict):
                    _walk(v, child)
                elif isinstance(v, list) and v and isinstance(v[0], dict):
                    _walk(v[0], f"{child}[0]")

    def _summarize(v: Any) -> Any:
        if isinstance(v, dict):
            out: dict[str, Any] = {}
            for k, x in v.items():
                if isinstance(x, dict):
                    out[str(k)] = "<dict>"
                elif isinstance(x, list):
                    out[str(k)] = "<list>"
                else:
                    out[str(k)] = x
            return out
        if isinstance(v, list):
            return f"<list len={len(v)}>"
        return v

    if isinstance(quote, dict):
        _walk(quote)

    return {
        "underlying": u,
        "index_token_key": token_key,
        "quote_key": quote_key,
        "resolved": {
            "exchange": resolved.get("exchange"),
            "tradingsymbol": resolved.get("tradingsymbol"),
            "instrument_token": resolved.get("instrument_token"),
            "name": resolved.get("name"),
        },
        "in_cas_window": in_cas_window(),
        "top_level_keys": top_level_keys,
        "auction_fields": auction,
        "nested_auction_fields": nested_auction,
        "asof": _asof_iso(),
    }
