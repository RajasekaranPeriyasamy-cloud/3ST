"""OI Tracker position interpretation — PCR + OI + IV matrix."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

Tone = Literal["bull", "bear", "neutral"]
Arrow = Literal["up", "down", "flat"]


def _parse_candle_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def _find_oi_at_timestamp(
    historical_candles: list[dict[str, Any]],
    target_time: datetime,
) -> int | None:
    if not historical_candles:
        return None
    for candle in reversed(historical_candles):
        candle_time = _parse_candle_time(candle.get("date"))
        if candle_time is None:
            continue
        if candle_time <= target_time:
            oi = candle.get("oi")
            return int(oi) if oi is not None else None
    return None


def chain_pcr_at_time(
    raw_historical: dict[str, list[dict[str, Any]]],
    target_time: datetime,
) -> float | None:
    call_total = 0
    put_total = 0
    found = False
    for key, candles in raw_historical.items():
        oi = _find_oi_at_timestamp(candles, target_time)
        if oi is None:
            continue
        found = True
        if key.endswith("_ce"):
            call_total += oi
        elif key.endswith("_pe"):
            put_total += oi
    if not found or call_total <= 0:
        return None
    return put_total / call_total


def _trend_up(current: float | None, past: float | None) -> bool | None:
    if current is None or past is None:
        return None
    if current > past:
        return True
    if current < past:
        return False
    return None


def interpret_oi_signal(
    side: str,
    oi_increasing: bool | None,
    iv_increasing: bool | None,
    pcr_increasing: bool | None,
) -> dict[str, str] | None:
    """
    Map PCR + OI + IV trends to position labels (reference matrix).

    Core (OI up): fresh positions. Sub (OI down): positions closing.
    """
    if oi_increasing is None or iv_increasing is None or pcr_increasing is None:
        return None

    is_put = side.lower() == "put"

    if is_put:
        if oi_increasing:
            if pcr_increasing and iv_increasing:
                return {"label": "Long Puts", "tone": "bear", "arrow": "down"}
            if pcr_increasing and not iv_increasing:
                return {"label": "Short Puts", "tone": "bull", "arrow": "up"}
        else:
            if not pcr_increasing and not iv_increasing:
                return {"label": "Put Unwinding", "tone": "bull", "arrow": "up"}
            if not pcr_increasing and iv_increasing:
                return {"label": "Short Covering (Puts)", "tone": "bull", "arrow": "up"}
    else:
        if oi_increasing:
            if not pcr_increasing and iv_increasing:
                return {"label": "Long Calls", "tone": "bull", "arrow": "up"}
            if not pcr_increasing and not iv_increasing:
                return {"label": "Short Calls", "tone": "bear", "arrow": "down"}
        else:
            if pcr_increasing and not iv_increasing:
                return {"label": "Call Unwinding", "tone": "bear", "arrow": "down"}
            if pcr_increasing and iv_increasing:
                return {"label": "Short Covering (Calls)", "tone": "bear", "arrow": "down"}

    return None


def compute_interval_signals(
    side: str,
    option_keys: list[str],
    oi_report: dict[str, dict[str, Any]],
    iv_report: dict[str, dict[str, Any]],
    raw_historical: dict[str, list[dict[str, Any]]],
    pcr_now: float | None,
    intervals_min: tuple[int, ...],
) -> dict[str, dict[str, dict[str, str] | None]]:
    """Per option key, per interval signal map."""
    now = datetime.now(timezone.utc)
    pcr_trends: dict[int, bool | None] = {}
    for interval in intervals_min:
        pcr_past = chain_pcr_at_time(raw_historical, now - timedelta(minutes=interval))
        pcr_trends[interval] = _trend_up(pcr_now, pcr_past)

    out: dict[str, dict[str, dict[str, str] | None]] = {}
    for key in option_keys:
        out[key] = {}
        oi_data = oi_report.get(key, {})
        iv_data = iv_report.get(key, {})
        for interval in intervals_min:
            abs_oi = oi_data.get(f"abs_diff_{interval}m")
            iv_abs = iv_data.get(f"iv_abs_diff_{interval}m")
            if abs_oi is None or iv_abs is None or abs_oi == 0 or iv_abs == 0:
                out[key][str(interval)] = None
                continue
            oi_up = abs_oi > 0
            iv_up = iv_abs > 0
            pcr_up = pcr_trends.get(interval)
            out[key][str(interval)] = interpret_oi_signal(side, oi_up, iv_up, pcr_up)

    return out


def _view_from_signal(signal: dict[str, str] | None) -> str:
    if not signal:
        return "sideways"
    tone = signal.get("tone")
    if tone == "bull":
        return "long"
    if tone == "bear":
        return "short"
    return "sideways"


def _view_label(view: str) -> str:
    return {"long": "LONG", "short": "SHORT", "sideways": "SIDEWAYS"}.get(view, "SIDEWAYS")


def compute_overall_bias(
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    interval_min: int = 15,
    sideways_threshold: float = 0.55,
) -> dict[str, Any]:
    """
    Overall Long / Short / Sideways from ATM legs at one interval.

    Chain badge: >= sideways_threshold of ATM CE+PE signals agree on bull or bear tone.
    Calls / Puts badges: single ATM leg signal at interval.
    """
    key = str(interval_min)
    atm_call = next((r for r in calls if r.get("position") == 0), None)
    atm_put = next((r for r in puts if r.get("position") == 0), None)

    call_signal = (atm_call or {}).get("signals", {}).get(key)
    put_signal = (atm_put or {}).get("signals", {}).get(key)

    calls_view = _view_from_signal(call_signal)
    puts_view = _view_from_signal(put_signal)

    tones: list[str] = []
    for sig in (call_signal, put_signal):
        if sig and sig.get("tone") in ("bull", "bear"):
            tones.append(str(sig["tone"]))

    bull_pct = bear_pct = 0.0
    chain_view = "sideways"
    if tones:
        bull_count = sum(1 for t in tones if t == "bull")
        bear_count = sum(1 for t in tones if t == "bear")
        total = len(tones)
        bull_pct = bull_count / total
        bear_pct = bear_count / total
        if bull_pct >= sideways_threshold:
            chain_view = "long"
        elif bear_pct >= sideways_threshold:
            chain_view = "short"

    return {
        "interval_min": interval_min,
        "strike_scope": "atm",
        "sideways_threshold_pct": round(sideways_threshold * 100, 1),
        "chain": {
            "view": chain_view,
            "label": _view_label(chain_view),
            "bull_pct": round(bull_pct * 100, 1),
            "bear_pct": round(bear_pct * 100, 1),
            "samples": len(tones),
        },
        "calls": {
            "view": calls_view,
            "label": _view_label(calls_view),
            "signal": call_signal,
        },
        "puts": {
            "view": puts_view,
            "label": _view_label(puts_view),
            "signal": put_signal,
        },
    }
