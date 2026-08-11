"""Highest OI Increase / Decrease desk — change boards with open / prior-day OI baseline."""

from __future__ import annotations

import json
import logging
import time as time_mod
from datetime import date, datetime, timedelta, time
from typing import Any
from zoneinfo import ZoneInfo

from config import INDEX_OPTIONS, MCX_SESSION, OI_TRACKER_DEFAULTS, is_mcx_underlying
from kite_client import fetch_historical_by_token
from options.oi_tracker import build_snapshot, tracker_config
from settings import data_dir

IST = ZoneInfo("Asia/Kolkata")
SESSION_FILE = data_dir() / "oi_movers_session_open.json"
PREV_DAY_CACHE = data_dir() / "oi_movers_prev_day_oi.json"
HISTORY_FILE = data_dir() / "oi_movers_history.json"

# Background sampler (scheduler) — keep CE/PE/PCR lines from ~09:20 without UI open.
OI_MOVERS_SAMPLE_INTERVAL_SEC = 30
OI_MOVERS_SAMPLE_FAIL_BACKOFF_SEC = 20
OI_MOVERS_SAMPLE_BUDGET_SEC = 45.0
_oi_sample_last_ok: dict[str, float] = {}


def movers_config() -> dict[str, Any]:
    cfg = tracker_config()
    return {
        "underlyings": cfg["underlyings"],
        "options_count": cfg["options_count"],
        "intervals_min": cfg["intervals_min"],
        "refresh_seconds": cfg["refresh_seconds"],
        "change_board_top_n": cfg.get("change_board_top_n", 5),
        "change_board_interval_min": cfg.get("change_board_interval_min", 15),
        "session_open_after": MCX_SESSION.get("entry_start", "09:20"),
        "mcx_underlyings": [u for u in cfg["underlyings"] if is_mcx_underlying(u)],
    }


def _today() -> str:
    return datetime.now(tz=IST).date().isoformat()


def _load_json(path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_json(path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _session_key(underlying: str, expiry: str) -> str:
    return f"{underlying.upper()}|{expiry}|{_today()}"


def _row_side(row: dict[str, Any]) -> str | None:
    """CE/PE from option_type or key suffix (atm_ce / otm1_pe)."""
    ot = str(row.get("option_type") or "").upper()
    if ot in ("CE", "PE"):
        return ot
    key = str(row.get("key") or "").lower()
    if key.endswith("_ce"):
        return "CE"
    if key.endswith("_pe"):
        return "PE"
    return None


def _side_totals_from_by_token(by_token: dict[str, Any]) -> tuple[int | None, int | None]:
    """Sum open OI by side when tokens carry a side tag."""
    ce = 0
    pe = 0
    saw_ce = False
    saw_pe = False
    for val in by_token.values():
        if not isinstance(val, dict):
            continue
        side = str(val.get("side") or "").upper()
        if side not in ("CE", "PE"):
            continue
        try:
            oi = int(val.get("oi"))
        except (TypeError, ValueError):
            continue
        if side == "CE":
            ce += oi
            saw_ce = True
        else:
            pe += oi
            saw_pe = True
    return (ce if saw_ce else None, pe if saw_pe else None)


def ensure_session_open_oi(
    underlying: str,
    expiry: str,
    rows: list[dict[str, Any]],
    *,
    after_hhmm: str = "09:20",
) -> dict[str, int]:
    """Persist first post-open OI per instrument token (once per day). Returns token→oi.

    Capture gate is wall-clock ``after_hhmm`` (cash default 09:20 IST) — the first
    successful poll after that gate, not the exchange's 09:15 opening tick.
    """
    data = _load_json(SESSION_FILE)
    entries = data.setdefault("entries", {})
    key = _session_key(underlying, expiry)
    existing = entries.get(key)
    if isinstance(existing, dict) and existing.get("by_token"):
        out: dict[str, int] = {}
        for tok, val in existing["by_token"].items():
            try:
                out[str(tok)] = int(val.get("oi") if isinstance(val, dict) else val)
            except (TypeError, ValueError):
                continue
        return out

    now = datetime.now(tz=IST)
    try:
        hh, mm = after_hhmm.split(":")
        gate = time(int(hh), int(mm))
    except Exception:
        gate = time(9, 20)
    if now.time() < gate:
        return {}

    by_token: dict[str, dict[str, Any]] = {}
    for row in rows:
        token = row.get("instrument_token")
        oi = row.get("latest_oi")
        if token is None or oi is None:
            continue
        try:
            rec: dict[str, Any] = {"oi": int(oi)}
        except (TypeError, ValueError):
            continue
        side = _row_side(row)
        if side:
            rec["side"] = side
        by_token[str(token)] = rec
    if not by_token:
        return {}

    # Drop stale days for this underlying|expiry
    prefix = f"{underlying.upper()}|{expiry}|"
    for k in list(entries.keys()):
        if k.startswith(prefix) and k != key:
            del entries[k]

    entry: dict[str, Any] = {
        "underlying": underlying.upper(),
        "expiry": expiry,
        "session_date": _today(),
        "captured_at": now.isoformat(timespec="seconds"),
        "by_token": by_token,
    }
    ce_tot, pe_tot = _side_totals_from_by_token(by_token)
    if ce_tot is not None and pe_tot is not None:
        entry["ce_base_oi"] = int(ce_tot)
        entry["pe_base_oi"] = int(pe_tot)
        entry["base_source"] = "open"
        entry["aggregates_locked_at"] = now.isoformat(timespec="seconds")
        entry["aggregates_lock_reason"] = "capture_sides"
    entries[key] = entry
    _save_json(SESSION_FILE, data)
    # Seed chart history at the open capture so CE/PE/PCR paint from ~09:20,
    # even if the OI Movers page is not open yet (other desks may trigger capture).
    try:
        ensure_history_anchor_at_open(underlying, expiry)
    except Exception:
        pass
    return {tok: int(v["oi"]) for tok, v in by_token.items()}


def ensure_locked_side_base_oi(
    underlying: str,
    expiry: str,
    *,
    ce_base_oi: int | None,
    pe_base_oi: int | None,
    base_source: str | None,
) -> tuple[int | None, int | None, str | None]:
    """Lock chart CE/PE Open aggregates once per session so ATM rolls cannot move them.

    Per-token open OI in ``by_token`` stays fixed, but the ATM±N window used for
    chart totals rolls with spot — without this lock, ``sum_side_oi`` over the
    live window makes dotted Open lines drift mid-session.

    Prefer: existing lock → first history tick → current open sum (once).
    Previous-day (PD) sums are not locked so a later open capture can replace them.
    """
    data = _load_json(SESSION_FILE)
    entries = data.setdefault("entries", {})
    key = _session_key(underlying, expiry)
    entry = entries.get(key)
    if not isinstance(entry, dict):
        entry = {
            "underlying": underlying.upper(),
            "expiry": expiry,
            "session_date": _today(),
            "by_token": {},
        }
        entries[key] = entry

    raw_ce, raw_pe = entry.get("ce_base_oi"), entry.get("pe_base_oi")
    if raw_ce is not None and raw_pe is not None:
        try:
            src = entry.get("base_source")
            return int(raw_ce), int(raw_pe), str(src) if src else base_source
        except (TypeError, ValueError):
            pass

    # Side-tagged capture can supply totals without a prior lock field.
    ce_tot, pe_tot = _side_totals_from_by_token(entry.get("by_token") or {})
    if ce_tot is not None and pe_tot is not None:
        entry["ce_base_oi"] = int(ce_tot)
        entry["pe_base_oi"] = int(pe_tot)
        entry["base_source"] = "open"
        entry["aggregates_locked_at"] = datetime.now(tz=IST).isoformat(timespec="seconds")
        entry["aggregates_lock_reason"] = "by_token_sides"
        _save_json(SESSION_FILE, data)
        return int(ce_tot), int(pe_tot), "open"

    hist = get_history(underlying, expiry)
    if hist:
        h0 = hist[0]
        hce, hpe = h0.get("ce_base_oi"), h0.get("pe_base_oi")
        if hce is not None and hpe is not None:
            try:
                frozen_ce, frozen_pe = int(hce), int(hpe)
            except (TypeError, ValueError):
                frozen_ce = frozen_pe = None  # type: ignore[assignment]
            if frozen_ce is not None and frozen_pe is not None:
                entry["ce_base_oi"] = frozen_ce
                entry["pe_base_oi"] = frozen_pe
                entry["base_source"] = h0.get("base_source") or base_source or "open"
                entry["aggregates_locked_at"] = datetime.now(tz=IST).isoformat(
                    timespec="seconds"
                )
                entry["aggregates_lock_reason"] = "history_first"
                _save_json(SESSION_FILE, data)
                return frozen_ce, frozen_pe, str(entry["base_source"])

    if ce_base_oi is None or pe_base_oi is None:
        return ce_base_oi, pe_base_oi, base_source
    # Only freeze when session-open baselines dominate — PD may still upgrade to open.
    if base_source != "open":
        return ce_base_oi, pe_base_oi, base_source

    entry["ce_base_oi"] = int(ce_base_oi)
    entry["pe_base_oi"] = int(pe_base_oi)
    entry["base_source"] = "open"
    entry["aggregates_locked_at"] = datetime.now(tz=IST).isoformat(timespec="seconds")
    entry["aggregates_lock_reason"] = "first_open_sum"
    _save_json(SESSION_FILE, data)
    return int(ce_base_oi), int(pe_base_oi), "open"

def _fetch_prev_day_oi(token: int) -> int | None:
    """Last completed daily candle OI before today."""
    end = date.today()
    start = end - timedelta(days=12)
    try:
        # day interval is not in KITE_INTERVALS — call historical via minute path's kite client
        from kite_client import _kite_direct_client

        kite = _kite_direct_client()
        raw = kite.historical_data(
            instrument_token=int(token),
            from_date=start,
            to_date=end,
            interval="day",
            continuous=False,
            oi=True,
        )
    except Exception:
        # Fallback: try 60min bars over recent days if day interval fails
        try:
            df = fetch_historical_by_token(
                int(token),
                "60min",
                start,
                end,
                oi=True,
            )
            if df is None or df.empty or "oi" not in df.columns:
                return None
            # last bar from a prior calendar day
            for ts, row in reversed(list(df.iterrows())):
                d = ts.date() if hasattr(ts, "date") else None
                if d is None or d >= end:
                    continue
                oi = row.get("oi")
                if oi is None:
                    continue
                try:
                    return int(oi)
                except (TypeError, ValueError):
                    continue
            return None
        except Exception:
            return None

    if not raw:
        return None
    today = end
    for candle in reversed(raw):
        if isinstance(candle, dict):
            ts = candle.get("date")
            oi = candle.get("oi")
        else:
            ts = candle[0] if candle else None
            oi = candle[6] if len(candle) > 6 else None
        if ts is None or oi is None:
            continue
        if isinstance(ts, datetime):
            d = ts.date()
        elif isinstance(ts, date):
            d = ts
        else:
            try:
                d = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
            except ValueError:
                continue
        if d >= today:
            continue
        try:
            return int(oi)
        except (TypeError, ValueError):
            continue
    return None


def get_prev_day_oi_map(tokens: list[int]) -> dict[str, int]:
    """Cached prior-day closing OI per instrument token (refreshed once per calendar day)."""
    cache = _load_json(PREV_DAY_CACHE)
    if cache.get("session_date") != _today():
        cache = {"session_date": _today(), "by_token": {}}
    by_token: dict[str, Any] = dict(cache.get("by_token") or {})
    changed = False
    out: dict[str, int] = {}
    for token in tokens:
        key = str(token)
        if key in by_token and by_token[key] is not None:
            try:
                out[key] = int(by_token[key])
                continue
            except (TypeError, ValueError):
                pass
        oi = _fetch_prev_day_oi(int(token))
        by_token[key] = oi
        changed = True
        if oi is not None:
            out[key] = oi
    if changed:
        cache["by_token"] = by_token
        _save_json(PREV_DAY_CACHE, cache)
    return out


def pick_baseline_oi(
    open_oi: int | None,
    prev_close_oi: int | None,
) -> tuple[int | None, str | None]:
    """Prefer session open OI; fall back to previous-day closing OI."""
    if open_oi is not None:
        return int(open_oi), "open"
    if prev_close_oi is not None:
        return int(prev_close_oi), "prev_close"
    return None, None


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


def _history_key(underlying: str, expiry: str) -> str:
    return f"{underlying.upper()}|{expiry}|{_today()}"


def sum_side_oi(
    rows: list[dict[str, Any]],
    baselines: dict[str, dict[str, Any]],
) -> tuple[int, int | None, str | None]:
    """Return ``(curr_oi_total, base_oi_total, dominant_base_source)``.

    ``base_oi_total`` is None when no row has an Open/PD baseline yet.
    Dominant source is ``open`` if any baseline used session open, else ``prev_close``.
    """
    curr_total = 0
    base_total = 0
    base_n = 0
    saw_open = False
    saw_prev = False
    for row in rows:
        try:
            curr_total += int(row.get("latest_oi") or 0)
        except (TypeError, ValueError):
            pass
        key = str(row.get("key") or "")
        base = baselines.get(key) or {}
        oi = base.get("oi")
        if oi is None:
            continue
        try:
            base_total += int(oi)
            base_n += 1
        except (TypeError, ValueError):
            continue
        src = base.get("source")
        if src == "open":
            saw_open = True
        elif src == "prev_close":
            saw_prev = True
    if base_n == 0:
        return curr_total, None, None
    source = "open" if saw_open else ("prev_close" if saw_prev else None)
    return curr_total, base_total, source


def filter_history_to_session(
    underlying: str,
    points: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep only ticks inside today's market window."""
    from options.gamma_density_history import session_window

    start, end = session_window(underlying)
    today = datetime.now(tz=IST).date()
    out: list[dict[str, Any]] = []
    for p in points:
        ts = _parse_ts(p.get("t"))
        if ts is None or ts.date() != today:
            continue
        tt = ts.timetz().replace(tzinfo=None)
        if start <= tt <= end:
            row = dict(p)
            row["ts_ms"] = int(ts.timestamp() * 1000)
            out.append(row)
    out.sort(key=lambda r: r.get("ts_ms") or 0)
    return out


def _persist_history_series(
    underlying: str,
    expiry: str,
    series: list[dict[str, Any]],
    *,
    max_points: int = 240,
) -> None:
    data = _load_json(HISTORY_FILE)
    entries = data.setdefault("entries", {})
    key = _history_key(underlying, expiry)
    trimmed = list(series)
    if len(trimmed) > max_points:
        trimmed = trimmed[-max_points:]
    prefix = f"{underlying.upper()}|{expiry}|"
    for k in list(entries.keys()):
        if k.startswith(prefix) and k != key:
            del entries[k]
    entries[key] = trimmed
    _save_json(HISTORY_FILE, data)


def append_history_point(
    underlying: str,
    expiry: str,
    point: dict[str, Any],
    *,
    max_points: int = 240,
    min_interval_sec: int = 45,
) -> list[dict[str, Any]]:
    """Append one in-session OI aggregate tick (chart-only; boards unchanged)."""
    from options.gamma_density_history import in_session

    data = _load_json(HISTORY_FILE)
    entries = data.setdefault("entries", {})
    key = _history_key(underlying, expiry)
    series: list[dict[str, Any]] = list(entries.get(key) or [])
    now = datetime.now(tz=IST)

    if in_session(underlying, now):
        too_soon = False
        if series:
            last_t = _parse_ts(series[-1].get("t"))
            too_soon = (
                last_t is not None
                and (now - last_t).total_seconds() < min_interval_sec
            )
        if not too_soon:
            pt = dict(point)
            pt.setdefault("t", now.isoformat(timespec="seconds"))
            series.append(pt)
            _persist_history_series(underlying, expiry, series, max_points=max_points)

    return filter_history_to_session(underlying, series)


def ensure_history_anchor_at_open(underlying: str, expiry: str) -> list[dict[str, Any]]:
    """Ensure a chart history tick exists at session-open capture (~09:20).

    Spot candles always cover the full session; CE/PE/PCR only exist where we
    recorded samples. If the first live sample lands late (page not open), insert
    an anchor at ``captured_at`` using locked Open CE/PE totals so forward-fill
    paints solid lines from the open gate.
    """
    sess = _load_json(SESSION_FILE)
    entry = (sess.get("entries") or {}).get(_session_key(underlying, expiry))
    if not isinstance(entry, dict):
        return get_history(underlying, expiry)

    ce = entry.get("ce_base_oi")
    pe = entry.get("pe_base_oi")
    if ce is None or pe is None:
        ce, pe = _side_totals_from_by_token(entry.get("by_token") or {})
    if ce is None or pe is None:
        return get_history(underlying, expiry)

    try:
        ce_i, pe_i = int(ce), int(pe)
    except (TypeError, ValueError):
        return get_history(underlying, expiry)

    ts = _parse_ts(entry.get("captured_at") or entry.get("aggregates_locked_at"))
    if ts is None:
        return get_history(underlying, expiry)

    data = _load_json(HISTORY_FILE)
    entries = data.setdefault("entries", {})
    key = _history_key(underlying, expiry)
    series: list[dict[str, Any]] = list(entries.get(key) or [])

    earliest: datetime | None = None
    for p in series:
        pt = _parse_ts(p.get("t"))
        if pt is None:
            continue
        if abs((pt - ts).total_seconds()) <= 90:
            return filter_history_to_session(underlying, series)
        if earliest is None or pt < earliest:
            earliest = pt

    # Earliest live sample already at/before open capture (+skew).
    if earliest is not None and earliest <= ts + timedelta(seconds=90):
        return filter_history_to_session(underlying, series)

    pcr = round(pe_i / ce_i, 4) if ce_i > 0 else None
    anchor: dict[str, Any] = {
        "t": ts.isoformat(timespec="seconds"),
        "ce_oi": ce_i,
        "pe_oi": pe_i,
        "ce_base_oi": ce_i,
        "pe_base_oi": pe_i,
        "pcr": pcr,
        "base_source": entry.get("base_source") or "open",
        "source": "open_anchor",
    }
    # Prefer spot from first later tick if present (display-only).
    for p in series:
        if p.get("spot") is not None:
            try:
                anchor["spot"] = float(p["spot"])
            except (TypeError, ValueError):
                pass
            break

    series.append(anchor)
    series.sort(key=lambda r: (_parse_ts(r.get("t")) or datetime.min.replace(tzinfo=IST)))
    _persist_history_series(underlying, expiry, series)
    return filter_history_to_session(underlying, series)


def get_history(underlying: str, expiry: str) -> list[dict[str, Any]]:
    data = _load_json(HISTORY_FILE)
    raw = list((data.get("entries") or {}).get(_history_key(underlying, expiry)) or [])
    return filter_history_to_session(underlying, raw)


def default_oi_movers_sample_underlyings() -> list[str]:
    """Cash majors first; MCX names included when listed in tracker config."""
    names = list(movers_config().get("underlyings") or [])
    cash = [u for u in names if u in INDEX_OPTIONS and not is_mcx_underlying(u)]
    mcx = [u for u in names if u in INDEX_OPTIONS and is_mcx_underlying(u)]
    # Prefer NIFTY/BANKNIFTY/SENSEX order for the budget window.
    preferred = ["NIFTY", "BANKNIFTY", "SENSEX"]
    ordered = [u for u in preferred if u in cash]
    for u in cash:
        if u not in ordered:
            ordered.append(u)
    ordered.extend(mcx)
    return ordered


def maybe_sample_oi_movers_history_periodic() -> bool:
    """Scheduler hook: persist OI Movers chart ticks without the UI open.

    Spot candles always cover the session; CE/PE/PCR lines only exist where we
    recorded samples. UI polls cover the open desk; this hook covers the rest
    from the post-09:20 open gate onward. Never raises.
    """
    from options.gamma_density_history import in_session

    now = datetime.now(tz=IST)
    if now.weekday() >= 5:
        return False

    names = default_oi_movers_sample_underlyings()
    if not names:
        return False

    now_ts = time_mod.time()
    due = [
        u
        for u in names
        if in_session(u, now)
        and (now_ts - _oi_sample_last_ok.get(u, 0.0)) >= OI_MOVERS_SAMPLE_INTERVAL_SEC
    ]
    if not due:
        return False

    deadline = now_ts + OI_MOVERS_SAMPLE_BUDGET_SEC
    any_ok = False
    sampled = 0
    try:
        from utils.logging import get_logger, log_event

        _log = get_logger("oi_movers")
    except Exception:
        _log = None

    for underlying in due:
        if sampled > 0 and time_mod.time() >= deadline:
            if _log is not None:
                log_event(
                    _log,
                    logging.WARNING,
                    "oi_movers_history_sample_budget",
                    sampled=sampled,
                    remaining=",".join(due[due.index(underlying) :]),
                )
            break
        try:
            # Persist history (+ open anchor); skip heavy chart candle merge cost
            # by still calling full snapshot — boards stay consistent with UI.
            build_movers_snapshot(underlying)
            _oi_sample_last_ok[underlying] = time_mod.time()
            any_ok = True
            sampled += 1
        except Exception as exc:
            _oi_sample_last_ok[underlying] = (
                time_mod.time()
                - OI_MOVERS_SAMPLE_INTERVAL_SEC
                + OI_MOVERS_SAMPLE_FAIL_BACKOFF_SEC
            )
            if _log is not None:
                log_event(
                    _log,
                    logging.WARNING,
                    "oi_movers_history_sample_failed",
                    underlying=underlying,
                    error=str(exc),
                )
    return any_ok


def _candle_close_and_time(candle: dict[str, Any]) -> tuple[datetime | None, float | None]:
    ts = _parse_ts(candle.get("date") or candle.get("datetime") or candle.get("time"))
    close = candle.get("close")
    if close is None:
        return ts, None
    try:
        return ts, float(close)
    except (TypeError, ValueError):
        return ts, None


_OI_FFILL_KEYS = (
    "ce_oi",
    "pe_oi",
    "ce_base_oi",
    "pe_base_oi",
    "pcr",
    "base_source",
)


def _latest_hist_at_or_before(
    hist_timed: list[tuple[datetime, dict[str, Any]]],
    ts: datetime,
    *,
    cursor: list[int],
) -> dict[str, Any] | None:
    """Advance ``cursor`` and return the last history tick with ``hts <= ts``."""
    i = cursor[0]
    while i + 1 < len(hist_timed) and hist_timed[i + 1][0] <= ts:
        i += 1
    cursor[0] = i
    if not hist_timed or hist_timed[i][0] > ts:
        return None
    return hist_timed[i][1]


def build_chart_series(
    underlying: str,
    history: list[dict[str, Any]],
    spot_candles: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge minute spot path with sparse OI aggregate ticks for Plotly.

    Chart-only: does not affect change-board rankings or baselines.
    Spot is dense from candles; CE/PE/PCR/base are forward-filled from the
    latest history tick at or before each minute (so solid lines span the session).
    """
    from options.gamma_density_history import session_window

    start, end = session_window(underlying)
    today = datetime.now(tz=IST).date()
    hist_timed: list[tuple[datetime, dict[str, Any]]] = []
    for p in history:
        ts = _parse_ts(p.get("t"))
        if ts is None or ts.date() != today:
            continue
        tt = ts.timetz().replace(tzinfo=None)
        if start <= tt <= end:
            hist_timed.append((ts, p))
    hist_timed.sort(key=lambda x: x[0])

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
            rows.append(
                {
                    "t": ts.isoformat(timespec="seconds"),
                    "ts_ms": int(ts.timestamp() * 1000),
                    "spot": round(close, 2),
                    "source": "candle",
                }
            )

    if not rows:
        for ts, p in hist_timed:
            rows.append(
                {
                    "t": ts.isoformat(timespec="seconds"),
                    "ts_ms": int(ts.timestamp() * 1000),
                    "spot": p.get("spot"),
                    "ce_oi": p.get("ce_oi"),
                    "pe_oi": p.get("pe_oi"),
                    "ce_base_oi": p.get("ce_base_oi"),
                    "pe_base_oi": p.get("pe_base_oi"),
                    "pcr": p.get("pcr"),
                    "base_source": p.get("base_source"),
                    "source": "oi",
                }
            )
        rows.sort(key=lambda r: r["ts_ms"])
        return rows

    # Live OI ticks often land after the last *closed* minute candle. Clamp those
    # ticks to the last candle time so forward-fill still paints the right edge.
    last_ts = _parse_ts(rows[-1].get("t"))
    hist_for_fill = hist_timed
    if last_ts is not None and hist_timed:
        hist_for_fill = [
            (ts if ts <= last_ts else last_ts, p) for ts, p in hist_timed
        ]
        hist_for_fill.sort(key=lambda x: x[0])

    cursor = [0]
    for row in rows:
        ts = _parse_ts(row.get("t"))
        if ts is None:
            continue
        hit = _latest_hist_at_or_before(hist_for_fill, ts, cursor=cursor)
        if hit is None:
            continue
        for k in _OI_FFILL_KEYS:
            if hit.get(k) is not None:
                row[k] = hit.get(k)

    rows.sort(key=lambda r: r["ts_ms"])
    return rows


def build_baselines(
    underlying: str,
    expiry: str,
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    *,
    after_hhmm: str = "09:20",
) -> dict[str, dict[str, Any]]:
    """Map option row key → {oi, source, open_oi, prev_close_oi}."""
    rows = list(calls) + list(puts)
    open_map = ensure_session_open_oi(underlying, expiry, rows, after_hhmm=after_hhmm)
    tokens = [
        int(r["instrument_token"])
        for r in rows
        if r.get("instrument_token") is not None
    ]
    prev_map = get_prev_day_oi_map(tokens) if tokens else {}

    baselines: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("key")
        if not key:
            continue
        tok = row.get("instrument_token")
        tok_s = str(tok) if tok is not None else ""
        open_oi = open_map.get(tok_s)
        prev_close = prev_map.get(tok_s)
        oi, source = pick_baseline_oi(open_oi, prev_close)
        baselines[str(key)] = {
            "oi": oi,
            "source": source,
            "open_oi": open_oi,
            "prev_close_oi": prev_close,
        }
    return baselines


def apply_baselines_to_boards(
    boards: dict[str, dict[str, list[dict[str, Any]]]],
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    baselines: dict[str, dict[str, Any]],
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Legacy helper: stamp Open/PD onto interval boards (Change still interval-based)."""
    by_strike_side: dict[tuple[Any, str], str] = {}
    for row in list(calls) + list(puts):
        strike = row.get("strike")
        side = "CE" if str(row.get("key", "")).endswith("_ce") else "PE"
        if row.get("key"):
            by_strike_side[(strike, side)] = str(row["key"])

    out: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for interval, sets in boards.items():
        out[interval] = {}
        for bucket, entries in sets.items():
            remapped: list[dict[str, Any]] = []
            for e in entries:
                key = by_strike_side.get((e.get("strike"), e.get("option_type")))
                base = baselines.get(key or "", {})
                oi = base.get("oi")
                source = base.get("source")
                remapped.append(
                    {
                        **e,
                        "prev_oi": int(oi) if oi is not None else None,
                        "prev_oi_source": source,
                        "open_oi": base.get("open_oi"),
                        "prev_close_oi": base.get("prev_close_oi"),
                    }
                )
            out[interval][bucket] = remapped
    return out


def build_session_change_boards(
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    *,
    expiry: str,
    baselines: dict[str, dict[str, Any]],
    top_n: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    """Rank CE/PE by OI change from Open/PD baseline → current.

    Change formulas:
      abs_chg = curr_oi − baseline_oi
      pct_chg = abs_chg / baseline_oi × 100
    where baseline_oi is session open OI, else previous-day closing OI.
    """
    from options.oi_tracker import _contract_label, _format_expiry_short

    entries: list[dict[str, Any]] = []
    for side, option_type in ((calls, "CE"), (puts, "PE")):
        for row in side:
            key = str(row.get("key") or "")
            curr_oi = row.get("latest_oi")
            base = baselines.get(key) or {}
            prev = base.get("oi")
            if curr_oi is None or prev is None:
                continue
            try:
                curr_i = int(curr_oi)
                prev_i = int(prev)
            except (TypeError, ValueError):
                continue
            abs_chg = curr_i - prev_i
            pct_chg = (abs_chg / prev_i * 100.0) if prev_i != 0 else None
            strike = row.get("strike")
            entries.append(
                {
                    "contract": _contract_label(strike, option_type, expiry),
                    "strike": strike,
                    "option_type": option_type,
                    "expiry_label": _format_expiry_short(expiry),
                    "prev_oi": prev_i,
                    "prev_oi_source": base.get("source"),
                    "open_oi": base.get("open_oi"),
                    "prev_close_oi": base.get("prev_close_oi"),
                    "curr_oi": curr_i,
                    "abs_chg": abs_chg,
                    "pct_chg": round(pct_chg, 2) if pct_chg is not None else None,
                }
            )

    def _with_bars(ranked: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
        sliced = ranked[: max(0, int(top_n))]
        magnitudes = [abs(float(e[metric])) for e in sliced if e.get(metric) is not None]
        peak = max(magnitudes) if magnitudes else 0.0
        out: list[dict[str, Any]] = []
        for e in sliced:
            val = e.get(metric)
            bar = 0.0
            if peak > 0 and val is not None:
                bar = round(abs(float(val)) / peak * 100.0, 1)
            out.append({**e, "bar_pct": bar})
        return out

    def _side_board(option_type: str, *, metric: str, positive: bool) -> list[dict[str, Any]]:
        side_rows = [e for e in entries if e.get("option_type") == option_type]
        if metric == "abs_chg":
            if positive:
                ranked = sorted(
                    [e for e in side_rows if e.get("abs_chg") is not None and e["abs_chg"] > 0],
                    key=lambda e: e["abs_chg"],
                    reverse=True,
                )
            else:
                ranked = sorted(
                    [e for e in side_rows if e.get("abs_chg") is not None and e["abs_chg"] < 0],
                    key=lambda e: e["abs_chg"],
                )
        else:
            if positive:
                ranked = sorted(
                    [e for e in side_rows if e.get("pct_chg") is not None and e["pct_chg"] > 0],
                    key=lambda e: e["pct_chg"],
                    reverse=True,
                )
            else:
                ranked = sorted(
                    [e for e in side_rows if e.get("pct_chg") is not None and e["pct_chg"] < 0],
                    key=lambda e: e["pct_chg"],
                )
        return _with_bars(ranked, metric)

    return {
        "increase_abs": _side_board("CE", metric="abs_chg", positive=True)
        + _side_board("PE", metric="abs_chg", positive=True),
        "increase_pct": _side_board("CE", metric="pct_chg", positive=True)
        + _side_board("PE", metric="pct_chg", positive=True),
        "decrease_abs": _side_board("CE", metric="abs_chg", positive=False)
        + _side_board("PE", metric="abs_chg", positive=False),
        "decrease_pct": _side_board("CE", metric="pct_chg", positive=False)
        + _side_board("PE", metric="pct_chg", positive=False),
    }


def build_movers_snapshot(
    underlying: str,
    expiry: str | None = None,
    options_count: int | None = None,
) -> dict[str, Any]:
    """Snapshot focused on Highest OI Increase / Decrease boards."""
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    snap = build_snapshot(underlying, expiry=expiry, options_count=options_count)
    calls = snap.get("calls") or []
    puts = snap.get("puts") or []
    exp = str(snap.get("expiry") or expiry or "")

    defaults = OI_TRACKER_DEFAULTS
    baselines = build_baselines(
        underlying,
        exp,
        calls,
        puts,
        after_hhmm=(
            str(MCX_SESSION.get("entry_start") or "09:20")
            if is_mcx_underlying(underlying)
            else "09:20"
        ),
    )

    board_top_n = int(defaults.get("change_board_top_n", 5))
    session_boards = build_session_change_boards(
        calls,
        puts,
        expiry=exp,
        baselines=baselines,
        top_n=board_top_n,
    )
    # Single board keyed as "session" (Change = Curr − Open/PD)
    boards = {"session": session_boards}

    open_count = sum(1 for b in baselines.values() if b.get("source") == "open")
    prev_count = sum(1 for b in baselines.values() if b.get("source") == "prev_close")

    ce_oi, ce_base_oi, ce_base_src = sum_side_oi(calls, baselines)
    pe_oi, pe_base_oi, pe_base_src = sum_side_oi(puts, baselines)
    pcr_payload = snap.get("pcr") or {}
    pcr_val = pcr_payload.get("chain_oi")
    if pcr_val is None and ce_oi > 0:
        pcr_val = round(pe_oi / ce_oi, 4)
    base_source = ce_base_src or pe_base_src
    # Freeze chart Open totals once — live ATM window sums otherwise drift mid-session.
    ce_base_oi, pe_base_oi, base_source = ensure_locked_side_base_oi(
        underlying,
        exp,
        ce_base_oi=ce_base_oi,
        pe_base_oi=pe_base_oi,
        base_source=base_source,
    )

    history: list[dict[str, Any]] = []
    chart_series: list[dict[str, Any]] = []
    try:
        from kite_client import fetch_index_minute_spot
        from options.gamma_density_history import minutes_since_session_open

        tick: dict[str, Any] = {
            "spot": round(float(snap["spot"]), 2),
            "ce_oi": int(ce_oi),
            "pe_oi": int(pe_oi),
            "pcr": float(pcr_val) if pcr_val is not None else None,
            "base_source": base_source,
        }
        if ce_base_oi is not None:
            tick["ce_base_oi"] = int(ce_base_oi)
        if pe_base_oi is not None:
            tick["pe_base_oi"] = int(pe_base_oi)
        # Backfill open-gate tick when first live sample is late (page/API lag).
        ensure_history_anchor_at_open(underlying, exp)
        history = append_history_point(underlying, exp, tick)
        if not history:
            history = get_history(underlying, exp)

        spot_candles: list[dict[str, Any]] = []
        try:
            lookback = minutes_since_session_open(underlying)
            spot_candles = fetch_index_minute_spot(underlying, minutes=max(int(lookback), 40))
        except Exception:
            spot_candles = []
        chart_series = build_chart_series(underlying, history, spot_candles)
    except Exception as exc:
        try:
            from utils.logging import get_logger, log_event

            log_event(
                get_logger("oi_movers"),
                "warning",
                "oi_movers_chart_history_failed",
                underlying=underlying,
                expiry=exp,
                error=str(exc),
            )
        except Exception:
            pass
        try:
            history = get_history(underlying, exp)
            chart_series = build_chart_series(underlying, history, None)
        except Exception:
            history = []
            chart_series = []

    cas_block = None
    try:
        from options.cas_indicative import cas_for_snapshot

        cas_block = cas_for_snapshot(underlying)
    except Exception:
        cas_block = None

    session_poc_block = None
    try:
        from options.session_poc import compute_session_poc

        session_poc_block = compute_session_poc(underlying)
    except Exception:
        session_poc_block = None

    return {
        "underlying": snap["underlying"],
        "expiry": snap["expiry"],
        "spot": snap["spot"],
        "atm_strike": snap["atm_strike"],
        "spot_warning": snap.get("spot_warning"),
        "updated_at": snap["updated_at"],
        "intervals_min": ["session"],
        "options_count": snap.get("options_count"),
        "change_boards": boards,
        "change_board_top_n": board_top_n,
        "change_board_interval_min": "session",
        "change_basis": "open_or_prev_close",
        "pcr": snap.get("pcr"),
        "ce_oi": int(ce_oi),
        "pe_oi": int(pe_oi),
        "ce_base_oi": int(ce_base_oi) if ce_base_oi is not None else None,
        "pe_base_oi": int(pe_base_oi) if pe_base_oi is not None else None,
        "base_source": base_source,
        "history": history,
        "chart_series": chart_series,
        "baseline": {
            "prefer": "open_then_prev_close",
            "open_count": open_count,
            "prev_close_count": prev_count,
            "total": len(baselines),
        },
        "cas": cas_block,
        "session_poc": session_poc_block,
    }
