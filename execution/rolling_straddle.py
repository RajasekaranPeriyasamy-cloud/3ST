"""Rolling ATM straddle runner — independent CE/PE legs on 3ST signals."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

import pandas as pd

from backtest_engine import _in_window, _level, _parse_hm
from broker.base import Broker
from broker.kite_broker import KiteBroker
from broker.paper_broker import PaperBroker, get_paper_broker
from config import INDEX_OPTIONS
from execution.arming import get_arm_state
from execution.order_executor import order_tag, place_leg_order
from execution.rolling_straddle_store import (
    append_log,
    get_config,
    get_state,
    reset_daily_state_if_needed,
    save_config,
    save_state,
)
from kite_client import fetch_historical_by_token, session_status
from options.chain import atm_strike, get_index_spot
from options.legs import build_atm_leg
from strategy_3st import compute_signals

_paper = get_paper_broker()
_kite = KiteBroker()

LegKey = Literal["ce", "pe"]


def _broker() -> Broker:
    return _kite if get_arm_state().get("mode") == "live" else _paper


def _today_str() -> str:
    return date.today().isoformat()


def _time_reached(hhmm: str) -> bool:
    now = datetime.now()
    h, m = _parse_hm(hhmm)
    return (now.hour, now.minute) >= (h, m)


def _morning_bar_ready(df: pd.DataFrame, entry_start: str) -> bool:
    """True once the latest closed bar is at or after today's entry_start."""
    if df.empty:
        return False
    last = df.index[-1]
    if not isinstance(last, pd.Timestamp):
        last = pd.to_datetime(last)
    h, m = _parse_hm(entry_start)
    bar_minutes = last.hour * 60 + last.minute
    target = h * 60 + m
    today = date.today()
    if last.date() != today:
        return False
    return bar_minutes >= target


def _fetch_candles_df(token: int, timeframe: str) -> pd.DataFrame:
    end = date.today()
    start = end - timedelta(days=10)
    return fetch_historical_by_token(token, timeframe, start, end)


def _fetch_leg_df(cfg: dict[str, Any], atm: float, option_type: str, timeframe: str) -> pd.DataFrame:
    built = build_atm_leg(
        cfg["underlying"],
        cfg["expiry"],
        option_type,  # type: ignore[arg-type]
        strike=atm,
    )
    return _fetch_candles_df(int(built["instrument_token"]), timeframe)


def _signal_strike_for_leg(leg: dict[str, Any], new_atm: float) -> float:
    """Open legs keep their entry strike for 3ST — ATM roll must not switch the signal chart."""
    if leg.get("status") == "open" and leg.get("strike") is not None:
        return float(leg["strike"])
    return new_atm


def _leg_ltp(leg: dict[str, Any]) -> float | None:
    if leg.get("status") != "open" or not leg.get("tradingsymbol") or not leg.get("exchange"):
        return None
    exch = str(leg["exchange"])
    sym = str(leg["tradingsymbol"])
    try:
        broker = _broker()
        return float(broker.ltp(exch, sym))
    except Exception:
        pass
    try:
        from kite_auth import get_kite_client

        kite = get_kite_client()
        key = f"{exch}:{sym}"
        return float(kite.ltp(key)[key]["last_price"])
    except Exception:
        return None


def _seed_paper_ltp(broker: Broker, exchange: str, tradingsymbol: str) -> None:
    if not isinstance(broker, PaperBroker):
        return
    try:
        broker.ltp(exchange, tradingsymbol)
        return
    except RuntimeError:
        pass
    try:
        from kite_auth import get_kite_client

        kite = get_kite_client()
        sym = f"{exchange}:{tradingsymbol}"
        px = float(kite.ltp(sym)[sym]["last_price"])
        broker.set_ltp(exchange, tradingsymbol, px)
    except Exception:
        broker.set_ltp(exchange, tradingsymbol, 1.0)


def _compute_latest_signals(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    sig = compute_signals(
        df,
        atr1=int(cfg["atr1"]),
        factor1=float(cfg["factor1"]),
        atr2=int(cfg["atr2"]),
        factor2=float(cfg["factor2"]),
        atr3=int(cfg["atr3"]),
        factor3=float(cfg["factor3"]),
        st1_enabled=bool(cfg["st1_enabled"]),
        st2_enabled=bool(cfg["st2_enabled"]),
        st3_enabled=bool(cfg["st3_enabled"]),
        adx_enabled=bool(cfg["adx_enabled"]),
        adx_period=int(cfg["adx_period"]),
        adx_threshold=float(cfg["adx_threshold"]),
        st_method=cfg.get("st_method") or "heikin_ashi",
    )
    row = sig.iloc[-1]
    ts = sig.index[-1]
    st1 = float(row["st1"]) if pd.notna(row.get("st1")) else None
    st2 = float(row["st2"]) if pd.notna(row.get("st2")) else None
    st3 = float(row["st3"]) if pd.notna(row.get("st3")) else None
    if cfg.get("st1_enabled") and st1 is not None:
        exit_line = st1
        exit_label = "ST1"
    elif cfg.get("st2_enabled") and st2 is not None:
        exit_line = st2
        exit_label = "ST2"
    elif cfg.get("st3_enabled") and st3 is not None:
        exit_line = st3
        exit_label = "ST3"
    else:
        exit_line = st1
        exit_label = "ST1"
    return {
        "ts": ts,
        "close": float(row["close"]),
        "st1": st1,
        "st2": st2,
        "st3": st3,
        "exit_line": exit_line,
        "exit_label": exit_label,
        "long_entry": bool(row["long_entry"]),
        "short_entry": bool(row["short_entry"]),
        "long_ready": bool(row["long_ready"]),
        "short_ready": bool(row["short_ready"]),
        "long_zone_exit": bool(row["long_zone_exit"]),
        "short_zone_exit": bool(row["short_zone_exit"]),
        "atr1": float(row["atr1"]) if pd.notna(row.get("atr1")) else None,
    }


def _short_entry_reason(cfg: dict[str, Any], signals: dict[str, Any], leg: dict[str, Any], *, prefix: str = "") -> tuple[bool, str]:
    """Shared short-zone entry rules for PE chart or CE chart (CE short → PE leg)."""
    style = cfg.get("reentry_style", "zone_active")
    entries = int(leg.get("entries_today") or 0)
    if entries == 0:
        if signals["short_entry"]:
            return True, f"{prefix}short_entry"
        if signals["short_ready"]:
            return True, f"{prefix}short_ready"
        return False, "no short entry"
    if style == "zone_active" and signals["short_ready"] and not signals["short_entry"]:
        return True, f"{prefix}pe_reentry"
    if signals["short_entry"]:
        return True, f"{prefix}short_entry"
    return False, "no short signal"


def _long_entry_reason(cfg: dict[str, Any], signals: dict[str, Any], leg: dict[str, Any], *, prefix: str = "") -> tuple[bool, str]:
    """Shared long-zone entry rules for CE chart or PE chart (PE long → CE leg)."""
    style = cfg.get("reentry_style", "zone_active")
    entries = int(leg.get("entries_today") or 0)
    if entries == 0:
        if signals["long_entry"]:
            return True, f"{prefix}long_entry"
        if signals["long_ready"]:
            return True, f"{prefix}long_ready"
        return False, "no long entry"
    if style == "zone_active" and signals["long_ready"] and not signals["long_entry"]:
        return True, f"{prefix}ce_reentry"
    if signals["long_entry"]:
        return True, f"{prefix}long_entry"
    return False, "no long signal"


def _can_enter_ce(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    pe_signals: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if leg.get("blocked"):
        return False, "CE blocked"
    if leg.get("status") == "open":
        return False, "CE already open"
    trade_mode = cfg.get("trade_mode", "Both")
    if trade_mode == "ShortOnly":
        return False, "trade_mode ShortOnly"
    max_re = int(cfg.get("max_reentries_ce") or 1)
    max_entries = max_re + 1
    if int(leg.get("entries_today") or 0) >= max_entries:
        return False, "CE max entries reached"
    ok, reason = _long_entry_reason(cfg, signals, leg)
    if ok:
        return True, reason
    if pe_signals is not None:
        ok, reason = _long_entry_reason(cfg, pe_signals, leg, prefix="pe_chart_")
        if ok:
            return True, reason
    return False, "no CE signal"


def _can_enter_pe(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    ce_signals: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if leg.get("blocked"):
        return False, "PE blocked"
    if leg.get("status") == "open":
        return False, "PE already open"
    trade_mode = cfg.get("trade_mode", "Both")
    if trade_mode == "LongOnly":
        return False, "trade_mode LongOnly"
    max_re = int(cfg.get("max_reentries_pe") or 1)
    max_entries = max_re + 1
    if int(leg.get("entries_today") or 0) >= max_entries:
        return False, "PE max entries reached"
    ok, reason = _short_entry_reason(cfg, signals, leg)
    if ok:
        return True, reason
    if ce_signals is not None:
        ok, reason = _short_entry_reason(cfg, ce_signals, leg, prefix="ce_chart_")
        if ok:
            return True, reason
    return False, "no PE signal"


def _should_exit_ce(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    force: bool,
    pe_signals: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if leg.get("status") != "open":
        return False, ""
    if force:
        return True, "force_exit"
    if signals["long_zone_exit"]:
        return True, "long_zone_exit"
    exit_line = signals.get("exit_line")
    if exit_line is not None:
        ltp = _leg_ltp(leg)
        if ltp is not None and ltp < float(exit_line):
            return True, "long_zone_exit_ltp"
    if pe_signals is not None:
        if pe_signals.get("short_entry"):
            return True, "pe_short_entry"
        if pe_signals.get("short_ready"):
            return True, "pe_short_zone"
    entry_px = leg.get("entry_price")
    if entry_px and cfg.get("sl_mode", "Off") != "Off":
        try:
            broker = _broker()
            ltp = broker.ltp(str(leg["exchange"]), str(leg["tradingsymbol"]))
            sl = _level(float(entry_px), cfg["sl_mode"], float(cfg["sl_value"]), "long", "sl")
            if sl is not None and ltp <= sl:
                return True, "sl"
            tgt = _level(float(entry_px), cfg["tgt_mode"], float(cfg["tgt_value"]), "long", "tgt")
            if cfg.get("tgt_mode", "Off") != "Off" and tgt is not None and ltp >= tgt:
                return True, "target"
        except Exception:
            pass
    return False, ""


def _should_exit_pe(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    force: bool,
    ce_signals: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if leg.get("status") != "open":
        return False, ""
    if force:
        return True, "force_exit"
    if signals["short_zone_exit"]:
        return True, "short_zone_exit"
    exit_line = signals.get("exit_line")
    if exit_line is not None:
        ltp = _leg_ltp(leg)
        if ltp is not None and ltp > float(exit_line):
            return True, "short_zone_exit_ltp"
    if ce_signals is not None:
        if ce_signals.get("long_entry"):
            return True, "ce_long_entry"
        if ce_signals.get("long_ready"):
            return True, "ce_long_zone"
    entry_px = leg.get("entry_price")
    if entry_px and cfg.get("sl_mode", "Off") != "Off":
        try:
            broker = _broker()
            ltp = broker.ltp(str(leg["exchange"]), str(leg["tradingsymbol"]))
            sl = _level(float(entry_px), cfg["sl_mode"], float(cfg["sl_value"]), "short", "sl")
            if sl is not None and ltp >= sl:
                return True, "sl"
            tgt = _level(float(entry_px), cfg["tgt_mode"], float(cfg["tgt_value"]), "short", "tgt")
            if cfg.get("tgt_mode", "Off") != "Off" and tgt is not None and ltp <= tgt:
                return True, "target"
        except Exception:
            pass
    return False, ""


def _enter_leg(
    leg_key: LegKey,
    cfg: dict[str, Any],
    atm: float,
    spot: float,
    reason: str,
) -> dict[str, Any]:
    opt = "CE" if leg_key == "ce" else "PE"
    expiry = cfg.get("expiry") or ""
    if not expiry:
        raise RuntimeError("Expiry not configured")
    built = build_atm_leg(
        cfg["underlying"],
        expiry,
        opt,  # type: ignore[arg-type]
        spot=spot,
        strike=atm,
    )
    broker = _broker()
    _seed_paper_ltp(broker, built["exchange"], built["tradingsymbol"])

    tag = order_tag(leg_key, "entry")
    result = place_leg_order(
        broker,
        built,
        transaction_type="BUY",
        order_type=str(cfg.get("order_type") or "MARKET"),
        tag=tag,
        product=str(cfg.get("product") or "MIS"),
    )
    if not result["ok"]:
        append_log(f"{leg_key}_entry_failed", result.get("message", ""), {"reason": reason})
        raise RuntimeError(result.get("message") or "Order failed")

    fill_px = None
    raw = result.get("raw") or {}
    if raw.get("price") is not None:
        fill_px = float(raw["price"])
    else:
        try:
            fill_px = broker.ltp(built["exchange"], built["tradingsymbol"])
        except Exception:
            fill_px = None

    state = get_state()
    leg = state[leg_key]
    entries = int(leg.get("entries_today") or 0) + 1
    reentries = int(leg.get("reentries_used") or 0)
    if entries > 1:
        reentries += 1

    leg_patch = {
        "status": "open",
        "tradingsymbol": built["tradingsymbol"],
        "exchange": built["exchange"],
        "strike": built["strike"],
        "entry_price": fill_px,
        "entry_at": datetime.now().isoformat(timespec="seconds"),
        "entry_order_id": result.get("order_id"),
        "last_action": reason,
        "entries_today": entries,
        "reentries_used": reentries,
    }
    save_state({leg_key: leg_patch})
    append_log(
        f"{leg_key}_entry",
        reason,
        {"strike": built["strike"], "symbol": built["tradingsymbol"], "order_id": result.get("order_id")},
    )
    return leg_patch


def _exit_leg(leg_key: LegKey, cfg: dict[str, Any], reason: str) -> dict[str, Any]:
    state = get_state()
    leg = state[leg_key]
    if leg.get("status") != "open":
        return leg

    broker = _broker()
    close_leg = {
        "tradingsymbol": leg["tradingsymbol"],
        "exchange": leg["exchange"],
        "quantity": build_atm_leg(
            cfg["underlying"],
            cfg["expiry"],
            "CE" if leg_key == "ce" else "PE",
            strike=float(leg["strike"]) if leg.get("strike") else None,
        )["quantity"],
    }
    tag = order_tag(leg_key, "exit")
    result = place_leg_order(
        broker,
        close_leg,
        transaction_type="SELL",
        order_type=str(cfg.get("order_type") or "MARKET"),
        tag=tag,
        product=str(cfg.get("product") or "MIS"),
    )
    if not result["ok"]:
        append_log(f"{leg_key}_exit_failed", result.get("message", ""), {"reason": reason})
        raise RuntimeError(result.get("message") or "Exit order failed")

    flat = {
        "status": "flat",
        "last_action": reason,
        "entry_price": None,
        "entry_at": None,
        "entry_order_id": None,
    }
    save_state({leg_key: {**leg, **flat}})
    append_log(f"{leg_key}_exit", reason, {"order_id": result.get("order_id")})
    return {**leg, **flat}


def _format_tick_error(exc: Exception) -> str:
    msg = str(exc)
    lower = msg.lower()
    if "proxy" in lower or "staticip" in lower or "getaddrinfo failed" in lower:
        return (
            "Kite API unreachable via staticip proxy (DNS/network). "
            "Kite now connects direct by default — restart API. "
            "If you need proxy: fix STATICIP_HOST or set KITE_USE_STATICIP_PROXY=0."
        )
    if len(msg) > 220:
        return msg[:217] + "..."
    return msg


def tick() -> dict[str, Any]:
    """Single scheduler tick — scan signals, roll ATM, enter/exit legs."""
    cfg = get_config()
    state = reset_daily_state_if_needed(_today_str())

    if state.get("runner") != "running":
        return {"ok": True, "skipped": True, "reason": "runner stopped"}

    if not session_status().get("authenticated"):
        append_log("error", "Kite session required")
        return {"ok": False, "error": "Kite session required"}

    underlying = cfg.get("underlying") or "NIFTY"
    timeframe = cfg.get("timeframe") or "5min"
    entry_start = cfg.get("entry_start") or "09:20"
    session_end = cfg.get("session_end") or "15:30"
    force_exit = cfg.get("force_exit") or "15:20"
    system_mode = cfg.get("system_mode") or "Intraday"

    now = datetime.now()
    in_session = system_mode == "Positional" or _in_window(pd.Timestamp(now), cfg.get("session_start", "09:15"), session_end)
    force = system_mode == "Intraday" and _in_window(pd.Timestamp(now), force_exit, session_end)

    if not in_session and not force:
        save_state({"last_tick_at": now.isoformat(timespec="seconds")})
        return {"ok": True, "skipped": True, "reason": "outside session"}

    spot = get_index_spot(underlying)
    if spot is None:
        append_log("error", f"No spot for {underlying}")
        return {"ok": False, "error": "No spot"}

    step = INDEX_OPTIONS[underlying]["strike_step"]
    new_atm = atm_strike(spot, step)
    prev_atm = state.get("current_atm")
    roll_dir = None
    if prev_atm is not None and new_atm != prev_atm:
        roll_dir = "up" if new_atm > prev_atm else "down"
        append_log("atm_roll", f"{prev_atm} -> {new_atm}", {"direction": roll_dir, "spot": spot})

    try:
        ce_strike = _signal_strike_for_leg(state["ce"], new_atm)
        pe_strike = _signal_strike_for_leg(state["pe"], new_atm)
        ce_df = _fetch_leg_df(cfg, ce_strike, "CE", timeframe)
        pe_df = _fetch_leg_df(cfg, pe_strike, "PE", timeframe)
    except Exception as e:
        detail = _format_tick_error(e)
        append_log("error", detail)
        return {"ok": False, "error": detail}

    if ce_df.empty or len(ce_df) < 50 or pe_df.empty or len(pe_df) < 50:
        save_state({"last_spot": spot, "current_atm": new_atm, "last_tick_at": now.isoformat(timespec="seconds")})
        return {"ok": True, "skipped": True, "reason": "insufficient option bars"}

    morning_ready = _morning_bar_ready(ce_df, entry_start) and _time_reached(entry_start)
    morning_seen = bool(state.get("morning_bar_seen"))
    if morning_ready and not morning_seen:
        morning_seen = True
        save_state({"morning_bar_seen": True, "morning_bar_at": now.isoformat(timespec="seconds")})
        append_log("morning_bar", f"Entry window open from {entry_start}")

    if not morning_seen:
        save_state({
            "last_spot": spot,
            "current_atm": new_atm,
            "prev_atm": prev_atm,
            "last_roll_direction": roll_dir,
            "last_tick_at": now.isoformat(timespec="seconds"),
        })
        return {"ok": True, "skipped": True, "reason": "waiting for 9:20 bar"}

    ce_signals = _compute_latest_signals(ce_df, cfg)
    pe_signals = _compute_latest_signals(pe_df, cfg)
    leg_signals = {"ce": ce_signals, "pe": pe_signals}

    ce_ltp = _leg_ltp(state["ce"])
    pe_ltp = _leg_ltp(state["pe"])
    save_state({
        "ce": {
            **state["ce"],
            "signal_strike": ce_strike,
            "signal_close": ce_signals["close"],
            "signal_st1": ce_signals.get("st1"),
            "zone_exit_level": ce_signals.get("exit_line"),
            "zone_exit_label": ce_signals.get("exit_label"),
            "zone_exit_triggered": ce_signals["long_zone_exit"],
            "short_ready": ce_signals["short_ready"],
            "short_entry": ce_signals["short_entry"],
            "ltp": ce_ltp,
        },
        "pe": {
            **state["pe"],
            "signal_strike": pe_strike,
            "signal_close": pe_signals["close"],
            "signal_st1": pe_signals.get("st1"),
            "zone_exit_level": pe_signals.get("exit_line"),
            "zone_exit_label": pe_signals.get("exit_label"),
            "zone_exit_triggered": pe_signals["short_zone_exit"],
            "ltp": pe_ltp,
        },
    })

    last_sig = None
    if ce_signals["long_entry"] or ce_signals["long_ready"]:
        last_sig = "long"
    elif pe_signals["short_entry"] or pe_signals["short_ready"]:
        last_sig = "short"
    elif ce_signals["short_entry"] or ce_signals["short_ready"]:
        last_sig = "short"

    save_state({
        "last_spot": spot,
        "current_atm": new_atm,
        "prev_atm": prev_atm,
        "last_roll_direction": roll_dir,
        "last_signal": last_sig,
        "last_signal_at": str(ce_signals["ts"]),
        "last_tick_at": now.isoformat(timespec="seconds"),
    })

    state = get_state()
    errors: list[str] = []

    for leg_key, exit_fn in (("ce", _should_exit_ce), ("pe", _should_exit_pe)):
        leg = state[leg_key]
        signals = leg_signals[leg_key]
        other = leg_signals["pe"] if leg_key == "ce" else leg_signals["ce"]
        try:
            if leg_key == "ce":
                should_x, x_reason = exit_fn(cfg, signals, leg, force, other)
            else:
                should_x, x_reason = exit_fn(cfg, signals, leg, force, other)
            if should_x:
                _exit_leg(leg_key, cfg, x_reason)  # type: ignore[arg-type]
                state = get_state()
        except Exception as e:
            errors.append(f"{leg_key} exit: {e}")

    if not force:
        state = get_state()
        for leg_key, enter_fn in (("ce", _can_enter_ce), ("pe", _can_enter_pe)):
            leg = state[leg_key]
            signals = leg_signals[leg_key]
            other = leg_signals["pe"] if leg_key == "ce" else leg_signals["ce"]
            other_key = "pe" if leg_key == "ce" else "ce"
            if not cfg.get("allow_dual_open", True) and state[other_key].get("status") == "open":
                continue
            try:
                if leg_key == "ce":
                    ok, reason = enter_fn(cfg, signals, leg, other)
                else:
                    ok, reason = enter_fn(cfg, signals, leg, other)
                if ok:
                    _enter_leg(leg_key, cfg, new_atm, spot, reason)  # type: ignore[arg-type]
            except Exception as e:
                errors.append(f"{leg_key} entry: {e}")
                append_log("error", str(e), {"leg": leg_key})

    return {
        "ok": len(errors) == 0,
        "spot": spot,
        "atm": new_atm,
        "last_signal": last_sig,
        "ce_signal": {
            "strike": ce_strike,
            "close": ce_signals["close"],
            "st1": ce_signals.get("st1"),
            "exit_line": ce_signals.get("exit_line"),
            "long_entry": ce_signals["long_entry"],
            "long_ready": ce_signals["long_ready"],
            "long_zone_exit": ce_signals["long_zone_exit"],
            "short_ready": ce_signals["short_ready"],
            "short_entry": ce_signals["short_entry"],
            "ltp": ce_ltp,
        },
        "pe_signal": {
            "strike": pe_strike,
            "close": pe_signals["close"],
            "st1": pe_signals.get("st1"),
            "exit_line": pe_signals.get("exit_line"),
            "short_entry": pe_signals["short_entry"],
            "short_ready": pe_signals["short_ready"],
            "short_zone_exit": pe_signals["short_zone_exit"],
            "ltp": pe_ltp,
        },
        "errors": errors,
        "state": get_state(),
    }


def start_runner() -> dict[str, Any]:
    cfg = get_config()
    if not cfg.get("expiry"):
        from options.chain import nearest_expiry

        exp = nearest_expiry(cfg.get("underlying") or "NIFTY")
        if exp:
            cfg = save_config({"expiry": exp})
        else:
            raise RuntimeError("Set expiry before starting")
    reset_daily_state_if_needed(_today_str())
    save_state({"runner": "running", "scheduler_running": True})
    append_log("runner_start", "Rolling straddle started")
    return {"ok": True, "runner": "running", "config": get_config(), "state": get_state()}


def stop_runner() -> dict[str, Any]:
    save_state({"runner": "stopped", "scheduler_running": False})
    append_log("runner_stop", "Rolling straddle stopped")
    return {"ok": True, "runner": "stopped", "state": get_state()}


def close_leg(leg: LegKey) -> dict[str, Any]:
    cfg = get_config()
    return _exit_leg(leg, cfg, "manual_close")


def close_all() -> dict[str, Any]:
    cfg = get_config()
    out: dict[str, Any] = {"ok": True, "closed": []}
    state = get_state()
    for leg_key in ("ce", "pe"):
        if state[leg_key].get("status") == "open":
            try:
                _exit_leg(leg_key, cfg, "close_all")  # type: ignore[arg-type]
                out["closed"].append(leg_key)
            except Exception as e:
                out["ok"] = False
                out.setdefault("errors", []).append(f"{leg_key}: {e}")
    append_log("close_all", str(out.get("closed", [])))
    return out


def status_bundle() -> dict[str, Any]:
    return {
        "config": get_config(),
        "state": get_state(),
        "arm": get_arm_state(),
        "kite_authenticated": session_status().get("authenticated"),
    }
