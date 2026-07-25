"""Highest OI Increase / Decrease desk — change boards with open / prior-day OI baseline."""

from __future__ import annotations

import json
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
    return date.today().isoformat()


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


def ensure_session_open_oi(
    underlying: str,
    expiry: str,
    rows: list[dict[str, Any]],
    *,
    after_hhmm: str = "09:20",
) -> dict[str, int]:
    """Persist first post-open OI per instrument token (once per day). Returns token→oi."""
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

    by_token: dict[str, dict[str, int]] = {}
    for row in rows:
        token = row.get("instrument_token")
        oi = row.get("latest_oi")
        if token is None or oi is None:
            continue
        try:
            by_token[str(token)] = {"oi": int(oi)}
        except (TypeError, ValueError):
            continue
    if not by_token:
        return {}

    # Drop stale days for this underlying|expiry
    prefix = f"{underlying.upper()}|{expiry}|"
    for k in list(entries.keys()):
        if k.startswith(prefix) and k != key:
            del entries[k]

    entries[key] = {
        "underlying": underlying.upper(),
        "expiry": expiry,
        "session_date": _today(),
        "captured_at": now.isoformat(timespec="seconds"),
        "by_token": by_token,
    }
    _save_json(SESSION_FILE, data)
    return {tok: int(v["oi"]) for tok, v in by_token.items()}


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
        "baseline": {
            "prefer": "open_then_prev_close",
            "open_count": open_count,
            "prev_close_count": prev_count,
            "total": len(baselines),
        },
    }
