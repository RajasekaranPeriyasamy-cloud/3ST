"""Intraday GEX / flip history for the Gamma Density desk (session-scoped JSON)."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from config import DEFAULT_SESSION, INDEX_OPTIONS, MCX_SESSION, is_mcx_underlying
from settings import data_dir

IST = ZoneInfo("Asia/Kolkata")
HISTORY_FILE = data_dir() / "gamma_density_history.json"


def _key(underlying: str, expiry: str) -> str:
    return f"{underlying.upper()}|{expiry}|{date.today().isoformat()}"


def _load() -> dict[str, Any]:
    if not HISTORY_FILE.exists():
        return {"series": {}}
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"series": {}}
    if not isinstance(data, dict):
        return {"series": {}}
    data.setdefault("series", {})
    return data


def _save(data: dict[str, Any]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def session_window(underlying: str) -> tuple[time, time]:
    """Cash / index-options 09:15–15:40 (CAS F&O close); MCX 09:00–23:30."""
    if is_mcx_underlying(underlying):
        start = MCX_SESSION.get("session_start", "09:00")
        end = MCX_SESSION.get("session_end", "23:30")
    else:
        meta = INDEX_OPTIONS.get(underlying.upper(), {})
        # Prefer instrument session when present; else cash default
        start = meta.get("session_start") or DEFAULT_SESSION.get("session_start", "09:15")
        end = meta.get("session_end") or DEFAULT_SESSION.get("session_end", "15:40")
        # INDEX_OPTIONS entries don't have session_start — use DEFAULT
        if not isinstance(start, str):
            start = "09:15"
        if not isinstance(end, str):
            end = "15:40"
    try:
        sh, sm = (int(x) for x in str(start).split(":")[:2])
        eh, em = (int(x) for x in str(end).split(":")[:2])
        return time(sh, sm), time(eh, em)
    except Exception:
        return time(9, 15), time(15, 40)


def in_session(underlying: str, when: datetime | None = None) -> bool:
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    start, end = session_window(underlying)
    t = now.timetz().replace(tzinfo=None)
    return start <= t <= end


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def append_history_point(
    underlying: str,
    expiry: str,
    *,
    spot: float,
    total_gex: float,
    flip_level: float | None,
    gamma_regime: str,
    max_points: int = 400,
    min_interval_sec: int = 45,
    hhi: float | None = None,
    conviction: float | int | None = None,
    pin_strike: float | None = None,
) -> list[dict[str, Any]]:
    """Append one in-session snapshot tick; skip after close / too-frequent polls."""
    data = _load()
    series = data.setdefault("series", {})
    key = _key(underlying, expiry)
    points: list[dict[str, Any]] = list(series.get(key) or [])
    now = datetime.now(tz=IST)

    if not in_session(underlying, now):
        return points

    if points:
        last_t = _parse_ts(points[-1].get("t"))
        if last_t is not None and (now - last_t).total_seconds() < min_interval_sec:
            return points

    tick: dict[str, Any] = {
        "t": now.isoformat(timespec="seconds"),
        "spot": round(float(spot), 2),
        "total_gex": round(float(total_gex), 2),
        "flip_level": round(float(flip_level), 2) if flip_level is not None else None,
        "gamma_regime": gamma_regime,
    }
    if hhi is not None:
        tick["hhi"] = round(float(hhi), 4)
    if conviction is not None:
        tick["conviction"] = float(conviction)
    if pin_strike is not None:
        tick["pin_strike"] = round(float(pin_strike), 2)
    points.append(tick)
    if len(points) > max_points:
        points = points[-max_points:]
    series[key] = points

    prefix = f"{underlying.upper()}|{expiry}|"
    today = date.today().isoformat()
    for k in list(series.keys()):
        if k.startswith(prefix) and not k.endswith(today):
            del series[k]

    _save(data)
    return points


def get_history(underlying: str, expiry: str) -> list[dict[str, Any]]:
    data = _load()
    return list(data.get("series", {}).get(_key(underlying, expiry)) or [])


def _candle_close_and_time(candle: dict[str, Any]) -> tuple[datetime | None, float | None]:
    ts = _parse_ts(candle.get("date") or candle.get("datetime") or candle.get("time"))
    close = candle.get("close")
    if close is None:
        return ts, None
    try:
        return ts, float(close)
    except (TypeError, ValueError):
        return ts, None


def build_chart_series(
    underlying: str,
    gex_points: list[dict[str, Any]],
    spot_candles: list[dict[str, Any]] | None,
    *,
    gex_match_sec: int = 120,
) -> list[dict[str, Any]]:
    """Merge day minute spot path with sparse GEX ticks for charting.

    - Spot is filled for every in-session minute candle (full day path).
    - GEX / flip are attached only near a stored GEX tick (else null) so gaps
      do not invent forward-looking flat GEX after the desk stopped polling.
    """
    start, end = session_window(underlying)
    today = date.today()

    gex_timed: list[tuple[datetime, dict[str, Any]]] = []
    for p in gex_points:
        ts = _parse_ts(p.get("t"))
        if ts is None:
            continue
        if ts.date() != today:
            continue
        tt = ts.timetz().replace(tzinfo=None)
        if not (start <= tt <= end):
            continue
        gex_timed.append((ts, p))
    gex_timed.sort(key=lambda x: x[0])

    rows: list[dict[str, Any]] = []
    if spot_candles:
        for c in spot_candles:
            ts, close = _candle_close_and_time(c)
            if ts is None or close is None or close <= 0:
                continue
            if ts.date() != today:
                continue
            tt = ts.timetz().replace(tzinfo=None)
            if not (start <= tt <= end):
                continue
            gex_val = None
            flip_val = None
            regime = None
            # nearest GEX tick within window
            best: tuple[float, dict[str, Any]] | None = None
            for gts, gp in gex_timed:
                dt = abs((gts - ts).total_seconds())
                if dt <= gex_match_sec and (best is None or dt < best[0]):
                    best = (dt, gp)
            if best is not None:
                gp = best[1]
                gex_val = gp.get("total_gex")
                flip_val = gp.get("flip_level")
                regime = gp.get("gamma_regime")
            rows.append(
                {
                    "t": ts.isoformat(timespec="seconds"),
                    "ts_ms": int(ts.timestamp() * 1000),
                    "spot": round(close, 2),
                    "total_gex": gex_val,
                    "flip_level": flip_val,
                    "gamma_regime": regime,
                    "source": "candle",
                }
            )

    # If no candles, fall back to GEX ticks alone (still session-filtered)
    if not rows:
        for ts, p in gex_timed:
            rows.append(
                {
                    "t": ts.isoformat(timespec="seconds"),
                    "ts_ms": int(ts.timestamp() * 1000),
                    "spot": p.get("spot"),
                    "total_gex": p.get("total_gex"),
                    "flip_level": p.get("flip_level"),
                    "gamma_regime": p.get("gamma_regime"),
                    "source": "gex",
                }
            )

    rows.sort(key=lambda r: r["ts_ms"])
    return rows


def detect_spot_reversals(
    series: list[dict[str, Any]],
    *,
    swing_bars: int = 4,
    min_move_pts: float = 40.0,
    confirm_bars: int = 6,
) -> list[dict[str, Any]]:
    """Detect intraday spot swing reversals (e.g. 10:10 AM V-bottom).

    Rules (bullish):
      - Local trough: spot[i] <= neighbors over ``swing_bars``
      - Within ``confirm_bars`` after trough, spot rises by ≥ ``min_move_pts``
    Bearish is the mirror.
    Optional ``gex_confirm`` when nearby GEX ticks change regime / flip side.
    """
    spots: list[tuple[int, float, str | None]] = []
    for i, row in enumerate(series):
        s = row.get("spot")
        if s is None:
            continue
        try:
            spots.append((i, float(s), row.get("t")))
        except (TypeError, ValueError):
            continue
    if len(spots) < swing_bars * 2 + confirm_bars + 1:
        return []

    out: list[dict[str, Any]] = []
    n = len(spots)
    for j in range(swing_bars, n - confirm_bars):
        idx, px, t = spots[j]
        window = [spots[k][1] for k in range(j - swing_bars, j + swing_bars + 1)]
        is_trough = px == min(window) and window.count(px) == 1
        is_peak = px == max(window) and window.count(px) == 1
        if not is_trough and not is_peak:
            continue

        future = [spots[k][1] for k in range(j + 1, min(n, j + 1 + confirm_bars))]
        if not future:
            continue

        if is_trough:
            move = max(future) - px
            if move < min_move_pts:
                continue
            side = "bullish"
        else:
            move = px - min(future)
            if move < min_move_pts:
                continue
            side = "bearish"

        # GEX context from series row at trough/peak
        row = series[idx]
        gex_confirm = False
        # Look ahead a few rows for regime flip near the reversal
        for k in range(idx, min(len(series), idx + confirm_bars + 2)):
            r = series[k]
            if r.get("gamma_regime") and row.get("gamma_regime"):
                if r["gamma_regime"] != row["gamma_regime"]:
                    gex_confirm = True
                    break
            if r.get("total_gex") is not None and row.get("total_gex") is not None:
                try:
                    if (float(r["total_gex"]) >= 0) != (float(row["total_gex"]) >= 0):
                        gex_confirm = True
                        break
                except (TypeError, ValueError):
                    pass

        out.append(
            {
                "t": t,
                "ts_ms": row.get("ts_ms"),
                "spot": px,
                "side": side,
                "move_pts": round(move, 1),
                "gex_confirm": gex_confirm,
                "label": (
                    f"{'Bullish' if side == 'bullish' else 'Bearish'} reversal"
                    + (f" (+{move:.0f} pts)" if side == "bullish" else f" (-{move:.0f} pts)")
                    + (" · GEX confirm" if gex_confirm else "")
                ),
            }
        )

    # De-dupe nearby reversals (keep strongest move)
    if not out:
        return []
    out.sort(key=lambda r: r.get("ts_ms") or 0)
    deduped: list[dict[str, Any]] = []
    for rev in out:
        if deduped and rev.get("ts_ms") and deduped[-1].get("ts_ms"):
            if abs(int(rev["ts_ms"]) - int(deduped[-1]["ts_ms"])) < 15 * 60 * 1000:
                if rev["move_pts"] > deduped[-1]["move_pts"]:
                    deduped[-1] = rev
                continue
        deduped.append(rev)
    return deduped


def minutes_since_session_open(underlying: str) -> int:
    """Minutes from session open to now (for candle lookback), capped."""
    now = datetime.now(tz=IST)
    start, _ = session_window(underlying)
    open_dt = datetime.combine(now.date(), start, tzinfo=IST)
    if now < open_dt:
        return 60
    mins = int((now - open_dt).total_seconds() // 60) + 15
    return max(60, min(mins, 600))
