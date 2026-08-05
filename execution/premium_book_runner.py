"""Premium Book runner — sell-premium and buy & hold structures on underlying ST1+ADX.

Order triggers follow Rolling Straddle's candle-bar gates:
  - ST1/ST2 structure entry and ST1 structure exit evaluate once per bar timestamp
    (``last_action_bar_ts`` / ``last_exit_bar_ts`` + re-entry cooldown).
  - Force exit, ATR trail, and fixed SL remain tick/LTP-responsive for risk control.
  - Config flag ``exit_on_bar_close_only`` (default True) enables the ST1 same-bar skip.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

import pandas as pd

from backtest_engine import _in_window, _parse_hm
from broker.base import Broker
from broker.execution_support import net_position_qty
from broker.kite_broker import KiteBroker
from broker.paper_broker import PaperBroker, get_paper_broker
from config import INDEX_OPTIONS
from execution.arming import get_arm_state
from execution.order_executor import order_tag, place_leg_to_target
from execution.premium_book_store import (
    append_log,
    flat_leg,
    flat_package,
    get_config,
    get_state,
    is_buy_structure,
    order_quantity_from_config,
    reset_daily_state_if_needed,
    save_config,
    save_state,
)
from instruments import resolve_future, resolve_underlying_index_token
from kite_client import fetch_historical_by_token, session_status
from kite_errors import friendly_kite_message
from options.chain import atm_strike, find_option_leg, get_index_spot, nearest_expiry
from options.spreads import (
    _max_loss_estimate,
    build_legs,
    hedge_wing_for_short_leg,
    preview_spread,
)
from strategy_3st import compute_signals

_paper = get_paper_broker()
_kite = KiteBroker()

LegKey = Literal["ce", "pe"]
Structure = Literal[
    "bull_put",
    "bear_call",
    "short_strangle",
    "short_straddle",
    "long_call",
    "long_put",
    "bull_call",
    "bear_put",
    "long_strangle",
]
SL_EXIT_REASONS = frozenset({"atr_exit", "sl_exit"})
BUY_PACKAGE_STRUCTURES = frozenset(
    {"long_call", "long_put", "bull_call", "bear_put", "long_strangle"}
)
SELL_VERTICALS = frozenset({"bull_put", "bear_call"})


def _broker() -> Broker:
    return _kite if get_arm_state().get("mode") == "live" else _paper


def _today_str() -> str:
    return date.today().isoformat()


def _time_reached(hhmm: str) -> bool:
    now = datetime.now()
    h, m = _parse_hm(hhmm)
    return (now.hour, now.minute) >= (h, m)


def _bar_ts_key(ts: Any) -> str:
    """Normalize signal bar timestamp for per-bar guards (matches Rolling Straddle)."""
    if ts is None:
        return ""
    if isinstance(ts, pd.Timestamp):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts).replace("T", " ")[:19]


def _exit_on_bar_close_only(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("exit_on_bar_close_only", True))


def _reentry_allowed_after_exit(
    holder: dict[str, Any],
    signals: dict[str, Any],
) -> tuple[bool, str]:
    """After exit, wait until a newer bar timestamp before re-entry (RS pattern)."""
    last_exit = holder.get("last_exit_bar_ts")
    if not last_exit:
        return True, ""
    current = _bar_ts_key(signals.get("ts"))
    if not current or current <= str(last_exit):
        return False, "reentry_cooldown"
    return True, ""


def _same_bar_action_blocked(holder: dict[str, Any], signals: dict[str, Any]) -> bool:
    """Block a second entry/exit on the same candle timestamp."""
    last = holder.get("last_action_bar_ts")
    if not last:
        return False
    return _bar_ts_key(signals.get("ts")) == str(last)


def _desk_bar_guard(state: dict[str, Any], signals: dict[str, Any]) -> dict[str, Any]:
    """Merge desk-wide + package/leg bar stamps so package↔legs cannot churn."""
    candidates = [state]
    pkg = state.get("package") or {}
    candidates.append(pkg)
    for key in ("ce", "pe"):
        candidates.append(state.get(key) or {})
    last_action = None
    last_exit = None
    for c in candidates:
        a = c.get("last_action_bar_ts")
        e = c.get("last_exit_bar_ts")
        if a and (last_action is None or str(a) > str(last_action)):
            last_action = a
        if e and (last_exit is None or str(e) > str(last_exit)):
            last_exit = e
    return {
        "last_action_bar_ts": last_action or state.get("last_action_bar_ts"),
        "last_exit_bar_ts": last_exit or state.get("last_exit_bar_ts"),
        "signal_bar_ts": _bar_ts_key(signals.get("ts")),
    }


def _stamp_bar_action(
    *,
    signals: dict[str, Any],
    is_exit: bool = False,
) -> dict[str, Any]:
    bar = _bar_ts_key(signals.get("ts"))
    patch: dict[str, Any] = {
        "signal_bar_ts": bar,
        "last_action_bar_ts": bar,
    }
    if is_exit:
        patch["last_exit_bar_ts"] = bar
    else:
        patch["last_exit_bar_ts"] = None
    return patch


def _morning_bar_ready(df: pd.DataFrame, entry_start: str) -> bool:
    if df.empty:
        return False
    last = df.index[-1]
    if not isinstance(last, pd.Timestamp):
        last = pd.to_datetime(last)
    h, m = _parse_hm(entry_start)
    if last.date() != date.today():
        return False
    return (last.hour * 60 + last.minute) >= (h * 60 + m)


def _seed_paper_ltp(broker: Broker, exchange: str, tradingsymbol: str) -> None:
    if not isinstance(broker, PaperBroker):
        return
    try:
        broker.ltp(exchange, tradingsymbol)
        return
    except RuntimeError:
        pass
    try:
        from kite_client import fetch_ltp_batch

        sym = f"{exchange}:{tradingsymbol}"
        px = float(fetch_ltp_batch([sym])[sym]["last_price"])
        broker.set_ltp(exchange, tradingsymbol, px)
    except Exception:
        broker.set_ltp(exchange, tradingsymbol, 1.0)


def _chart_token_for_underlying(underlying: str) -> int:
    """Index token for NSE/BSE; front-month future for MCX commodities (crude)."""
    meta = INDEX_OPTIONS.get(underlying) or {}
    spot_source = meta.get("spot_source") or ("index" if meta.get("index_token_key") else "future")
    if spot_source == "future":
        fut = resolve_future(underlying)
        return int(fut["instrument_token"])
    return resolve_underlying_index_token(underlying)


def _fetch_underlying_df(underlying: str, timeframe: str) -> pd.DataFrame:
    token = _chart_token_for_underlying(underlying)
    end = datetime.now()
    start = end - timedelta(days=10)
    return fetch_historical_by_token(token, timeframe, start, end)


def _compute_latest_signals(df: pd.DataFrame, cfg: dict[str, Any]) -> dict[str, Any]:
    """ST1 is the exit line; ST1+ST2 dirs feed entry when ``entry_require_st1_st2``."""
    require_st12 = bool(cfg.get("entry_require_st1_st2", True))
    st2_on = bool(cfg.get("st2_enabled", True))
    # Keep ST1 as signal/exit line; enable ST2 so dir2 is available for entry filter.
    # ST3 never gates Premium Book entry (even if checkbox on).
    sig = compute_signals(
        df,
        atr1=int(cfg["atr1"]),
        factor1=float(cfg["factor1"]),
        atr2=int(cfg["atr2"]),
        factor2=float(cfg["factor2"]),
        atr3=int(cfg["atr3"]),
        factor3=float(cfg["factor3"]),
        st1_enabled=True,
        st2_enabled=st2_on if require_st12 else False,
        st3_enabled=False,
        adx_enabled=bool(cfg["adx_enabled"]),
        adx_period=int(cfg["adx_period"]),
        adx_threshold=float(cfg["adx_threshold"]),
        st_method=cfg.get("st_method") or "heikin_ashi",
        st1_only=False,
    )
    row = sig.iloc[-1]
    ts = sig.index[-1]
    st1 = float(row["st1"]) if pd.notna(row.get("st1")) else None
    dir1 = int(row["dir1"]) if pd.notna(row.get("dir1")) else None
    dir2 = int(row["dir2"]) if pd.notna(row.get("dir2")) else None
    return {
        "ts": ts,
        "close": float(row["close"]),
        "high": float(row["high"]) if pd.notna(row.get("high")) else float(row["close"]),
        "low": float(row["low"]) if pd.notna(row.get("low")) else float(row["close"]),
        "st1": st1,
        "exit_line": st1,
        "exit_label": "ST1",
        "long_entry": bool(row["long_entry"]),
        "short_entry": bool(row["short_entry"]),
        "long_ready": bool(row["long_ready"]),
        "short_ready": bool(row["short_ready"]),
        "long_zone_exit": bool(row["long_zone_exit"]),
        "short_zone_exit": bool(row["short_zone_exit"]),
        "dir1": dir1,
        "dir2": dir2,
        "dir_all_bull": bool(row.get("dir_all_bull", False)),
        "dir_all_bear": bool(row.get("dir_all_bear", False)),
        "entry_require_st1_st2": require_st12 and st2_on,
        "atr1": float(row["atr1"]) if pd.notna(row.get("atr1")) else None,
        "adx": float(row["adx"]) if pd.notna(row.get("adx")) else None,
        "adx_ok": bool(row.get("adx_ok", True)),
    }


def _entry_long_ok(signals: dict[str, Any]) -> bool:
    """Long-side entry: ST1 zone (+ADX) and, when enabled, ST1+ST2 both bullish."""
    zone = bool(signals.get("long_ready") or signals.get("long_entry"))
    if not zone:
        return False
    if not signals.get("entry_require_st1_st2"):
        return True
    return signals.get("dir1") == 1 and signals.get("dir2") == 1


def _entry_short_ok(signals: dict[str, Any]) -> bool:
    """Short-side entry: ST1 zone (+ADX) and, when enabled, ST1+ST2 both bearish."""
    zone = bool(signals.get("short_ready") or signals.get("short_entry"))
    if not zone:
        return False
    if not signals.get("entry_require_st1_st2"):
        return True
    return signals.get("dir1") == -1 and signals.get("dir2") == -1


def _live_ref_price(signals: dict[str, Any], ltp: float | None) -> float | None:
    if ltp is not None:
        try:
            return float(ltp)
        except (TypeError, ValueError):
            pass
    close = signals.get("close")
    try:
        return float(close) if close is not None else None
    except (TypeError, ValueError):
        return None


def _update_atr_trail_short(leg: dict[str, Any], *, ref: float, atr1: float, mult: float) -> tuple[float, float]:
    """Short premium: trail above ref, ratchets down as premium falls."""
    band = float(atr1) * float(mult)
    candidate = ref + band
    prev = leg.get("atr_trail")
    extreme = leg.get("atr_extreme")
    try:
        extreme_f = float(extreme) if extreme is not None else ref
    except (TypeError, ValueError):
        extreme_f = ref
    extreme_f = min(extreme_f, ref)
    trail = candidate if prev is None else min(float(prev), candidate)
    return round(trail, 4), round(extreme_f, 4)


def _update_atr_trail_long(leg: dict[str, Any], *, ref: float, atr1: float, mult: float) -> tuple[float, float]:
    """Long premium: trail below ref, ratchets up as premium rises."""
    band = float(atr1) * float(mult)
    candidate = ref - band
    prev = leg.get("atr_trail")
    extreme = leg.get("atr_extreme")
    try:
        extreme_f = float(extreme) if extreme is not None else ref
    except (TypeError, ValueError):
        extreme_f = ref
    extreme_f = max(extreme_f, ref)
    trail = candidate if prev is None else max(float(prev), candidate)
    return round(trail, 4), round(extreme_f, 4)


def exit_ladder_reason(
    *,
    force: bool,
    atr_hit: bool,
    st1_hit: bool,
    entry_exit_hit: bool = False,
    entry_exit_enabled: bool = False,
    sl_hit: bool = False,
) -> str | None:
    """
    Locked exit order for Premium Book (sell and buy & hold):
      1. Force  2. optional entry-exit  3. ATR (or fixed SL)  4. ST1
    Entry-exit defaults OFF for both books (ATR + ST1 + force).
    """
    if force:
        return "force_exit"
    if entry_exit_enabled and entry_exit_hit:
        return "entry_exit"
    if atr_hit:
        return "atr_exit"
    if sl_hit:
        return "sl_exit"
    if st1_hit:
        return "st1_exit"
    return None


def should_convert_sl_to_spread(
    *,
    structure: str,
    convert_enabled: bool,
    exit_reason: str,
    leg_has_wing: bool,
) -> bool:
    """True when a naked short straddle/strangle leg should buy a hedge wing."""
    if not convert_enabled:
        return False
    if structure not in ("short_straddle", "short_strangle"):
        return False
    if leg_has_wing:
        return False
    return exit_reason in SL_EXIT_REASONS


def pick_auto_structure(
    signals: dict[str, Any],
    *,
    trade_bias: str = "sell_premium",
) -> tuple[str | None, str]:
    """
    Direction-driven structure (sell premium / buy hold).

    Sell book (credit verticals only):
      Above ST1+ST2 → bull_put
      Below ST1+ST2 → bear_call
      No signal / whipsaw → no entry (ST1/ST2 unfit for short strangle/straddle)

    Buy book mirror:
      Above → bull_call · Below → bear_put · Flat → long_strangle
    """
    long_ok = _entry_long_ok(signals)
    short_ok = _entry_short_ok(signals)
    bias = str(trade_bias or "sell_premium")

    if bias == "buy_hold":
        if long_ok and not short_ok:
            return "bull_call", "auto_above_st1_st2"
        if short_ok and not long_ok:
            return "bear_put", "auto_below_st1_st2"
        if not long_ok and not short_ok:
            return "long_strangle", "auto_flat_whipsaw"
        return None, "auto_conflict_st1_st2"

    # sell_premium — verticals only; sit out flat / conflict
    if long_ok and not short_ok:
        return "bull_put", "auto_above_st1_st2"
    if short_ok and not long_ok:
        return "bear_call", "auto_below_st1_st2"
    if not long_ok and not short_ok:
        return None, "auto_flat_no_entry"
    return None, "skip_whipsaw"


def structure_entry_ok(structure: str, signals: dict[str, Any]) -> tuple[bool, str]:
    """Entry: ST1 zone (+ADX) confirmed by ST1+ST2 direction. Exit uses ST1 only elsewhere."""
    need = "need_st1_st2" if signals.get("entry_require_st1_st2") else "need_st1_zone"

    if structure == "bear_call":
        if _entry_short_ok(signals):
            return True, "bear_call_st1_st2_short"
        return False, need
    if structure == "bull_put":
        if _entry_long_ok(signals):
            return True, "bull_put_st1_st2_long"
        return False, need
    if structure in ("long_call", "bull_call"):
        if _entry_long_ok(signals):
            return True, f"{structure}_st1_st2_long"
        return False, need
    if structure in ("long_put", "bear_put"):
        if _entry_short_ok(signals):
            return True, f"{structure}_st1_st2_short"
        return False, need
    if structure == "long_strangle":
        if _entry_long_ok(signals) or _entry_short_ok(signals):
            return True, "long_strangle_st1_st2"
        return False, need
    if structure in ("short_strangle", "short_straddle"):
        long_ok = _entry_long_ok(signals)
        short_ok = _entry_short_ok(signals)
        if structure == "short_strangle":
            # Sideways: no ST1+ST2 directional lock; or soft when ADX not trending.
            if not long_ok and not short_ok:
                return True, "strangle_flat_st1_st2"
            if signals.get("adx_ok") is False:
                return True, "strangle_sideways_adx"
            if long_ok and short_ok:
                return False, "conflicting_st1_st2"
            # One-sided ST1+ST2 still allows dual/otm short book
            return True, "strangle_st1_st2_soft"
        # short_straddle — directional short premium, one side
        if short_ok:
            return True, "straddle_st1_st2_short"
        if long_ok:
            return True, "straddle_st1_st2_long"
        return False, need
    return False, "unknown_structure"


def _ensure_expiry(cfg: dict[str, Any]) -> dict[str, Any]:
    underlying = str(cfg.get("underlying") or "NIFTY")
    expiry = str(cfg.get("expiry") or "")
    if not expiry:
        resolved = nearest_expiry(underlying)
        if resolved:
            return save_config({"expiry": resolved})
    return cfg


def _leg_ltp(exchange: str | None, tradingsymbol: str | None) -> float | None:
    if not exchange or not tradingsymbol:
        return None
    try:
        return float(_broker().ltp(str(exchange), str(tradingsymbol)))
    except Exception:
        pass
    try:
        from kite_client import fetch_ltp_batch

        key = f"{exchange}:{tradingsymbol}"
        return float(fetch_ltp_batch([key])[key]["last_price"])
    except Exception:
        return None


def _place_leg(
    broker: Broker,
    leg: dict[str, Any],
    *,
    target_qty: int,
    cfg: dict[str, Any],
    tag: str,
) -> dict[str, Any]:
    _seed_paper_ltp(broker, leg["exchange"], leg["tradingsymbol"])
    result = place_leg_to_target(
        broker,
        leg,
        target_qty=target_qty,
        order_type=str(cfg.get("order_type") or "MARKET"),
        tag=tag,
        product=str(cfg.get("product") or "MIS"),
    )
    if not result["ok"]:
        msg = friendly_kite_message(result.get("message", ""))
        raise RuntimeError(msg or "Order failed")
    return result


def _scale_legs(legs: list[dict[str, Any]], qty: int) -> list[dict[str, Any]]:
    out = []
    for leg in legs:
        out.append({**leg, "quantity": qty})
    return out


def _open_package(
    cfg: dict[str, Any],
    structure: str,
    spot: float,
    reason: str,
    *,
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expiry = cfg.get("expiry") or ""
    if not expiry:
        raise RuntimeError("Expiry not configured")
    legs = build_legs(
        underlying=str(cfg["underlying"]),
        expiry=expiry,
        template=structure,  # type: ignore[arg-type]
        width_steps=int(cfg.get("width_steps") or 1),
        spot=spot,
        otm_offset=int(cfg.get("otm_offset") or 0),
    )
    qty = order_quantity_from_config(cfg)
    legs = _scale_legs(legs, qty)
    broker = _broker()
    arm = get_arm_state()
    if arm.get("mode") == "live" and not arm.get("armed"):
        raise RuntimeError("ARM required for live orders")

    fill_credit = 0.0
    opened: list[dict[str, Any]] = []
    for i, leg in enumerate(legs):
        side = str(leg["side"]).upper()
        target = -int(leg["quantity"]) if side == "SELL" else int(leg["quantity"])
        tag = order_tag(f"pb{i}", "entry")
        result = _place_leg(broker, leg, target_qty=target, cfg=cfg, tag=tag)
        px = None
        raw = result.get("raw") or {}
        if raw.get("price") is not None:
            px = float(raw["price"])
        else:
            px = _leg_ltp(leg["exchange"], leg["tradingsymbol"])
        if px is not None:
            fill_credit += (-px if side == "SELL" else px)  # credit positive when short premium
        opened.append(
            {
                **leg,
                "entry_price": px,
                "entry_order_id": result.get("order_id"),
                "target_qty": target,
            }
        )

    # Recalculate: SELL adds credit, BUY adds debit
    credit = 0.0
    debit = 0.0
    for ol in opened:
        px = float(ol["entry_price"] or 0)
        if str(ol["side"]).upper() == "SELL":
            credit += px
        else:
            debit += px
    net = debit - credit  # >0 debit book, <0 credit book
    buy_book = is_buy_structure(structure)
    net_credit = max(0.0, -net) if not buy_book else None
    net_debit = max(0.0, net) if buy_book else None
    max_loss = _max_loss_estimate(
        structure,  # type: ignore[arg-type]
        int(cfg.get("width_steps") or 1),
        str(cfg["underlying"]),
        net if buy_book else -max(0.0, credit - debit),
    )
    if not buy_book and structure in SELL_VERTICALS and max_loss is None:
        step = float(INDEX_OPTIONS[cfg["underlying"]]["strike_step"])
        width = int(cfg.get("width_steps") or 1) * step
        max_loss = max(0.0, width - max(0.0, credit - debit))

    bar_stamp = _stamp_bar_action(signals=signals or {}, is_exit=False)
    pkg = {
        "status": "open",
        "structure": structure,
        "legs": opened,
        "net_credit": round(net_credit, 4) if net_credit is not None else None,
        "net_debit": round(net_debit, 4) if net_debit is not None else None,
        "max_loss": round(max_loss, 4) if max_loss is not None else None,
        "entry_at": datetime.now().isoformat(timespec="seconds"),
        "last_action": reason,
        "atr_trail": None,
        "atr_extreme": None,
        "atr_live_ref": None,
        **bar_stamp,
    }
    save_state(
        {
            "package": pkg,
            "ce": flat_leg(),
            "pe": flat_leg(),
            **bar_stamp,
        }
    )
    append_log(
        "package_entry",
        reason,
        {
            "structure": structure,
            "credit": net_credit,
            "debit": net_debit,
            "max_loss": max_loss,
            "signal_bar_ts": bar_stamp.get("signal_bar_ts"),
        },
    )
    return pkg


def _broker_symbols_flat(broker: Broker, legs: list[dict[str, Any]]) -> bool | None:
    """True if all legs are flat at broker (any product); None if unreadable."""
    try:
        rows = broker.positions()
        for leg in legs:
            sym = str(leg.get("tradingsymbol") or "")
            exch = str(leg.get("exchange") or "")
            if not sym or not exch:
                continue
            # Ignore product filter — MIS/NRML drift must not falsely clear a live leg.
            if net_position_qty(rows, tradingsymbol=sym, exchange=exch) != 0:
                return False
        return True
    except Exception:
        return None


def _close_package(
    cfg: dict[str, Any],
    reason: str,
    *,
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = get_state()
    pkg = state.get("package") or {}
    if pkg.get("status") != "open":
        return pkg
    broker = _broker()
    arm = get_arm_state()
    if arm.get("mode") == "live" and not arm.get("armed"):
        # Manual flatten on Kite leaves local package open — reconcile without ARM.
        flat_at_broker = _broker_symbols_flat(broker, list(pkg.get("legs") or []))
        if flat_at_broker is True:
            bar_stamp = _stamp_bar_action(
                signals=signals or {"ts": pkg.get("signal_bar_ts")},
                is_exit=True,
            )
            flat = flat_package()
            flat["last_action"] = f"reconcile_broker_flat:{reason}"
            flat.update(bar_stamp)
            save_state({"package": flat, **bar_stamp})
            append_log(
                "package_exit_reconciled_broker_flat",
                reason,
                {"structure": pkg.get("structure"), "signal_bar_ts": bar_stamp.get("signal_bar_ts")},
            )
            return flat
        append_log("package_exit_blocked_disarm", reason)
        raise RuntimeError("ARM required for live exit")

    for i, leg in enumerate(pkg.get("legs") or []):
        close_leg = {
            "tradingsymbol": leg["tradingsymbol"],
            "exchange": leg["exchange"],
            "quantity": abs(int(leg.get("quantity") or order_quantity_from_config(cfg))),
        }
        tag = order_tag(f"pb{i}", "exit")
        _place_leg(broker, close_leg, target_qty=0, cfg=cfg, tag=tag)

    bar_stamp = _stamp_bar_action(signals=signals or {"ts": pkg.get("signal_bar_ts")}, is_exit=True)
    flat = flat_package()
    flat["last_action"] = reason
    flat.update(bar_stamp)
    save_state({"package": flat, **bar_stamp})
    append_log(
        "package_exit",
        reason,
        {"structure": pkg.get("structure"), "signal_bar_ts": bar_stamp.get("signal_bar_ts")},
    )
    return flat


def _open_short_leg(
    leg_key: LegKey,
    cfg: dict[str, Any],
    spot: float,
    reason: str,
    *,
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    structure = str(cfg.get("structure") or "short_straddle")
    expiry = cfg.get("expiry") or ""
    if not expiry:
        raise RuntimeError("Expiry not configured")
    legs = build_legs(
        underlying=str(cfg["underlying"]),
        expiry=expiry,
        template=structure,  # type: ignore[arg-type]
        width_steps=int(cfg.get("width_steps") or 1),
        spot=spot,
        otm_offset=int(cfg.get("otm_offset") or 0),
    )
    opt = "CE" if leg_key == "ce" else "PE"
    match = next((lg for lg in legs if lg["option_type"] == opt), None)
    if not match:
        raise RuntimeError(f"No {opt} leg in {structure}")
    qty = order_quantity_from_config(cfg)
    match = {**match, "quantity": qty}
    broker = _broker()
    arm = get_arm_state()
    if arm.get("mode") == "live" and not arm.get("armed"):
        raise RuntimeError("ARM required for live orders")

    result = _place_leg(broker, match, target_qty=-qty, cfg=cfg, tag=order_tag(f"pb{leg_key}", "entry"))
    px = None
    raw = result.get("raw") or {}
    if raw.get("price") is not None:
        px = float(raw["price"])
    else:
        px = _leg_ltp(match["exchange"], match["tradingsymbol"])

    bar_stamp = _stamp_bar_action(signals=signals or {}, is_exit=False)
    patch = {
        "status": "open",
        "tradingsymbol": match["tradingsymbol"],
        "exchange": match["exchange"],
        "strike": match["strike"],
        "option_type": opt,
        "entry_price": px,
        "entry_at": datetime.now().isoformat(timespec="seconds"),
        "entry_order_id": result.get("order_id"),
        "entry_side": "SELL",
        "position_side": "short",
        "broker_qty": qty,
        "managed_by": "algo",
        "last_action": reason,
        "atr_trail": None,
        "atr_extreme": None,
        "wing": None,
        "converted_structure": None,
        **bar_stamp,
    }
    save_state({leg_key: patch, **bar_stamp})
    append_log(
        f"{leg_key}_entry",
        reason,
        {
            "strike": match["strike"],
            "symbol": match["tradingsymbol"],
            "signal_bar_ts": bar_stamp.get("signal_bar_ts"),
        },
    )
    return patch


def _close_short_leg(
    leg_key: LegKey,
    cfg: dict[str, Any],
    reason: str,
    *,
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = get_state()
    leg = state.get(leg_key) or {}
    if leg.get("status") != "open":
        return leg
    broker = _broker()
    arm = get_arm_state()
    wing = leg.get("wing")
    if arm.get("mode") == "live" and not arm.get("armed"):
        # Manual flatten on Kite leaves local CE/PE open — clear state, no order.
        check_legs: list[dict[str, Any]] = [leg]
        if isinstance(wing, dict) and wing.get("tradingsymbol"):
            check_legs.append(wing)
        flat_at_broker = _broker_symbols_flat(broker, check_legs)
        if flat_at_broker is True:
            bar_stamp = _stamp_bar_action(
                signals=signals or {"ts": leg.get("signal_bar_ts")},
                is_exit=True,
            )
            flat = flat_leg()
            flat["last_action"] = f"reconcile_broker_flat:{reason}"
            flat.update(bar_stamp)
            save_state({leg_key: flat, **bar_stamp})
            append_log(
                f"{leg_key}_exit_reconciled_broker_flat",
                reason,
                {
                    "symbol": leg.get("tradingsymbol"),
                    "signal_bar_ts": bar_stamp.get("signal_bar_ts"),
                },
            )
            return flat
        append_log(f"{leg_key}_exit_blocked_disarm", reason)
        raise RuntimeError("ARM required for live exit")

    qty = abs(int(leg.get("broker_qty") or order_quantity_from_config(cfg)))
    # Close wing first if converted package on this side
    if isinstance(wing, dict) and wing.get("tradingsymbol"):
        wleg = {
            "tradingsymbol": wing["tradingsymbol"],
            "exchange": wing["exchange"],
            "quantity": abs(int(wing.get("quantity") or qty)),
        }
        _place_leg(broker, wleg, target_qty=0, cfg=cfg, tag=order_tag(f"pb{leg_key}w", "exit"))

    close_leg = {
        "tradingsymbol": leg["tradingsymbol"],
        "exchange": leg["exchange"],
        "quantity": qty,
    }
    _place_leg(broker, close_leg, target_qty=0, cfg=cfg, tag=order_tag(f"pb{leg_key}", "exit"))
    bar_stamp = _stamp_bar_action(
        signals=signals or {"ts": leg.get("signal_bar_ts")},
        is_exit=True,
    )
    flat = flat_leg()
    flat["last_action"] = reason
    flat.update(bar_stamp)
    save_state({leg_key: flat, **bar_stamp})
    append_log(f"{leg_key}_exit", reason, {"signal_bar_ts": bar_stamp.get("signal_bar_ts")})
    return flat


def convert_leg_to_vertical(
    leg_key: LegKey,
    cfg: dict[str, Any],
    *,
    exit_reason: str,
) -> dict[str, Any]:
    """Buy hedge wing on SL — converts naked short into credit vertical."""
    state = get_state()
    leg = dict(state.get(leg_key) or {})
    if leg.get("status") != "open" or leg.get("wing"):
        return leg

    opt = str(leg.get("option_type") or ("CE" if leg_key == "ce" else "PE"))
    short_strike = float(leg["strike"])
    step = float(INDEX_OPTIONS[cfg["underlying"]]["strike_step"])
    width_steps = int(cfg.get("width_steps") or 1)
    decision = hedge_wing_for_short_leg(
        option_type=opt,
        short_strike=short_strike,
        width_steps=width_steps,
        strike_step=step,
    )
    expiry = cfg.get("expiry") or ""
    found = find_option_leg(str(cfg["underlying"]), expiry, float(decision["strike"]), opt)
    if not found:
        raise RuntimeError(f"No hedge wing {opt} @ {decision['strike']}")

    qty = abs(int(leg.get("broker_qty") or order_quantity_from_config(cfg)))
    wing_leg = {
        "tradingsymbol": found["tradingsymbol"],
        "exchange": found["exchange"],
        "instrument_token": found["instrument_token"],
        "side": "BUY",
        "quantity": qty,
        "strike": found["strike"],
        "option_type": opt,
    }
    broker = _broker()
    arm = get_arm_state()
    if arm.get("mode") == "live" and not arm.get("armed"):
        raise RuntimeError("ARM required for live convert")

    # Buy wing first (defined risk), then continue managing as package
    result = _place_leg(broker, wing_leg, target_qty=qty, cfg=cfg, tag=order_tag(f"pb{leg_key}w", "entry"))
    px = None
    raw = result.get("raw") or {}
    if raw.get("price") is not None:
        px = float(raw["price"])
    else:
        px = _leg_ltp(wing_leg["exchange"], wing_leg["tradingsymbol"])

    template = str(decision["template"])
    detail = (
        f"SL convert → {template} @ short {short_strike} / wing {decision['strike']} "
        f"(reason={exit_reason})"
    )
    wing_state = {
        **wing_leg,
        "entry_price": px,
        "entry_order_id": result.get("order_id"),
        "entry_at": datetime.now().isoformat(timespec="seconds"),
    }
    patch = {
        **leg,
        "wing": wing_state,
        "converted_structure": template,
        "last_action": f"sl_convert_{template}",
        "atr_trail": None,
        "atr_extreme": None,
    }
    save_state({leg_key: patch})
    append_log(
        "sl_convert",
        detail,
        {
            "leg": leg_key,
            "template": template,
            "short_strike": short_strike,
            "wing_strike": decision["strike"],
            "exit_reason": exit_reason,
        },
    )
    return patch


def _fixed_sl_hit(
    leg: dict[str, Any],
    cfg: dict[str, Any],
    ltp: float | None,
    *,
    long_premium: bool = False,
) -> bool:
    mode = str(cfg.get("sl_mode") or "Off")
    if mode == "Off" or ltp is None or leg.get("entry_price") is None:
        return False
    entry = float(leg["entry_price"])
    val = float(cfg.get("sl_value") or 0)
    if long_premium:
        # Long option: adverse = premium down
        if mode == "%":
            return ltp <= entry * (1.0 - val / 100.0)
        if mode == "Pts":
            return ltp <= entry - val
        return False
    # Short option: adverse = premium up
    if mode == "%":
        return ltp >= entry * (1.0 + val / 100.0)
    if mode == "Pts":
        return ltp >= entry + val
    return False


def _short_leg_adverse_zone(option_type: str, signals: dict[str, Any]) -> bool:
    """
    Underlying ST1 zone that hurts a naked short option.

    Short CE is hurt when price is above ST1 (``short_zone_exit``).
    Short PE is hurt when price is below ST1 (``long_zone_exit``).

    Using the opposite flags caused short-strangle CE churn: flat/whipsaw entry
    remains valid while price sits below ST1, so CE would ST1-exit and re-enter
    every tick/bar while PE held.
    """
    opt = str(option_type or "").upper()
    if opt == "CE":
        return bool(signals.get("short_zone_exit"))
    return bool(signals.get("long_zone_exit"))


def _st1_hit_short_leg(leg: dict[str, Any], signals: dict[str, Any]) -> bool:
    """ST1 structure exit for a short option leg (bar signal flags only)."""
    if leg.get("converted_structure") == "bear_call":
        return bool(signals.get("long_zone_exit") or signals.get("long_ready"))
    if leg.get("converted_structure") == "bull_put":
        return bool(signals.get("short_zone_exit") or signals.get("short_ready"))
    return _short_leg_adverse_zone(str(leg.get("option_type") or ""), signals)


def _st1_hit_package(structure: str, signals: dict[str, Any]) -> bool:
    """ST1 structure exit for a multi-leg package (bar signal flags only)."""
    if structure in ("bear_call", "long_put", "bear_put"):
        st1_hit = bool(signals.get("long_zone_exit") or signals.get("long_ready"))
    elif structure in ("bull_put", "long_call", "bull_call"):
        st1_hit = bool(signals.get("short_zone_exit") or signals.get("short_ready"))
    else:
        st1_hit = bool(signals.get("long_zone_exit") or signals.get("short_zone_exit"))

    if structure == "long_call":
        return bool(signals.get("long_zone_exit") or signals.get("short_ready"))
    if structure == "long_put":
        return bool(signals.get("short_zone_exit") or signals.get("long_ready"))
    if structure == "bull_call":
        return bool(signals.get("long_zone_exit") or signals.get("short_ready"))
    if structure == "bear_put":
        return bool(signals.get("short_zone_exit") or signals.get("long_ready"))
    return st1_hit


def _evaluate_short_leg_exit(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    force: bool,
    *,
    desk_guard: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """
    Return (exit_reason, atr_patch).

    Force / ATR / fixed SL stay LTP-responsive each tick.
    ST1 (and optional entry-exit) are candle-bar gated like Rolling Straddle when
    ``exit_on_bar_close_only`` is True — same-bar action is skipped.
    """
    if leg.get("status") != "open":
        return None, {}
    ltp = _leg_ltp(leg.get("exchange"), leg.get("tradingsymbol"))
    patch: dict[str, Any] = {
        "ltp": ltp,
        "signal_bar_ts": _bar_ts_key(signals.get("ts")),
    }
    atr_hit = False
    if str(cfg.get("tsl_mode") or "Off") == "ATR" and ltp is not None:
        atr = signals.get("atr1")
        if atr is not None and float(atr) > 0:
            opt_atr = max(1.0, float(atr) * 0.02)
            trail, extreme = _update_atr_trail_short(
                leg, ref=float(ltp), atr1=opt_atr, mult=float(cfg.get("tsl_value") or 1.2)
            )
            patch.update({"atr_trail": trail, "atr_extreme": extreme, "atr_live_ref": round(float(ltp), 4)})
            if float(ltp) >= trail:
                atr_hit = True

    sl_hit = _fixed_sl_hit(leg, cfg, ltp)
    if force:
        return "force_exit", patch
    # Risk exits remain tick/LTP responsive (not deferred to next bar).
    if atr_hit:
        return "atr_exit", patch
    if sl_hit:
        return "sl_exit", patch

    guard = desk_guard or leg
    if _exit_on_bar_close_only(cfg) and _same_bar_action_blocked(guard, signals):
        return "skipped_same_bar", patch

    st1_hit = _st1_hit_short_leg(leg, signals)
    entry_exit_hit = False
    if bool(cfg.get("entry_exit_enabled")) and ltp is not None and leg.get("entry_price") is not None:
        entry_exit_hit = float(ltp) > float(leg["entry_price"])

    reason = exit_ladder_reason(
        force=False,
        atr_hit=False,
        st1_hit=st1_hit,
        entry_exit_hit=entry_exit_hit,
        entry_exit_enabled=bool(cfg.get("entry_exit_enabled")),
        sl_hit=False,
    )
    return reason, patch


def _evaluate_package_exit(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    pkg: dict[str, Any],
    force: bool,
    *,
    desk_guard: dict[str, Any] | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """
    Package exit ladder.

    Force / ATR stay responsive; ST1 structure exits are bar-gated when
    ``exit_on_bar_close_only`` (default True).
    """
    if pkg.get("status") != "open":
        return None, {}
    structure = str(pkg.get("structure") or "")
    buy_book = is_buy_structure(structure)

    want_side = "BUY" if buy_book else "SELL"
    mtm: list[float] = []
    for leg in pkg.get("legs") or []:
        if str(leg.get("side")).upper() != want_side:
            continue
        px = _leg_ltp(leg.get("exchange"), leg.get("tradingsymbol"))
        if px is not None:
            mtm.append(float(px))
    mid = sum(mtm) / len(mtm) if mtm else None
    patch: dict[str, Any] = {"signal_bar_ts": _bar_ts_key(signals.get("ts"))}
    atr_hit = False
    if str(cfg.get("tsl_mode") or "Off") == "ATR" and mid is not None and signals.get("atr1"):
        opt_atr = max(1.0, float(signals["atr1"]) * 0.02)
        mult = float(cfg.get("tsl_value") or 1.2)
        if buy_book:
            trail, extreme = _update_atr_trail_long(pkg, ref=mid, atr1=opt_atr, mult=mult)
            atr_hit = mid <= trail
        else:
            trail, extreme = _update_atr_trail_short(pkg, ref=mid, atr1=opt_atr, mult=mult)
            atr_hit = mid >= trail
        patch.update({"atr_trail": trail, "atr_extreme": extreme, "atr_live_ref": round(mid, 4)})

    if force:
        return "force_exit", patch
    if atr_hit:
        return "atr_exit", patch

    guard = desk_guard or pkg
    if _exit_on_bar_close_only(cfg) and _same_bar_action_blocked(guard, signals):
        return "skipped_same_bar", patch

    st1_hit = _st1_hit_package(structure, signals)
    entry_exit_hit = False
    if buy_book and bool(cfg.get("entry_exit_enabled")) and mid is not None:
        entry_debit = pkg.get("net_debit")
        if entry_debit is not None:
            entry_exit_hit = mid < float(entry_debit) * 0.5

    reason = exit_ladder_reason(
        force=False,
        atr_hit=False,
        st1_hit=st1_hit,
        entry_exit_enabled=bool(cfg.get("entry_exit_enabled")),
        entry_exit_hit=entry_exit_hit,
        sl_hit=False,
    )
    return reason, patch


def _pick_straddle_leg(signals: dict[str, Any]) -> LegKey | None:
    if signals.get("short_ready") or signals.get("short_entry"):
        return "ce"
    if signals.get("long_ready") or signals.get("long_entry"):
        return "pe"
    return None


def preview_current(
    cfg: dict[str, Any] | None = None,
    spot: float | None = None,
    *,
    structure_override: str | None = None,
) -> dict[str, Any]:
    cfg = cfg or get_config()
    underlying = str(cfg.get("underlying") or "NIFTY")
    expiry = str(cfg.get("expiry") or "") or nearest_expiry(underlying) or ""
    if not expiry:
        raise RuntimeError(f"No expiry for {underlying}")
    if spot is None:
        spot = get_index_spot(underlying)
    structure = structure_override or str(cfg.get("structure") or "bull_put")

    def _ltp(exch: str, sym: str) -> float:
        try:
            return float(_broker().ltp(exch, sym))
        except Exception:
            return 0.0

    out = preview_spread(
        underlying=underlying,
        expiry=expiry,
        template=structure,  # type: ignore[arg-type]
        width_steps=int(cfg.get("width_steps") or 1),
        spot=spot,
        otm_offset=int(cfg.get("otm_offset") or 0),
        ltp_fn=_ltp,
    )
    out["structure"] = structure
    out["auto_structure"] = bool(cfg.get("auto_structure", True))
    return out


def start_runner() -> dict[str, Any]:
    if not session_status().get("authenticated"):
        raise RuntimeError("Kite session required")
    cfg = _ensure_expiry(get_config())
    from execution.premium_book_store import validate_config

    validate_config(cfg)
    save_state({"runner": "running", "last_error": None})
    append_log("start", "Premium Book runner started")
    return get_state()


def stop_runner() -> dict[str, Any]:
    save_state({"runner": "stopped"})
    append_log("stop", "Premium Book runner stopped")
    return get_state()


def close_all() -> dict[str, Any]:
    cfg = get_config()
    state = get_state()
    if (state.get("package") or {}).get("status") == "open":
        _close_package(cfg, "manual_close")
    for key in ("ce", "pe"):
        if (state.get(key) or {}).get("status") == "open":
            _close_short_leg(key, cfg, "manual_close")  # type: ignore[arg-type]
    append_log("close_all", "Force closed Premium Book positions")
    return get_state()


def revoke_buy_hold() -> dict[str, Any]:
    """
    Turn Buy & Hold off (back to sell_premium) and flatten any open buy-hold package.

    Sell-premium short legs are left alone — only buy-structure packages are closed.
    """
    from execution.premium_book_store import (
        DEFAULT_SELL_STRUCTURE,
        TRADE_BIAS_SELL,
        is_buy_structure as _is_buy,
    )

    cfg = get_config()
    state = get_state()
    pkg = state.get("package") or {}
    closed = False
    if pkg.get("status") == "open" and _is_buy(str(pkg.get("structure") or cfg.get("structure") or "")):
        _close_package(cfg, "revoke_buy_hold")
        closed = True
    elif pkg.get("status") == "open" and is_buy_structure(str(cfg.get("structure") or "")):
        _close_package(cfg, "revoke_buy_hold")
        closed = True

    saved = save_config(
        {
            "trade_bias": TRADE_BIAS_SELL,
            "structure": DEFAULT_SELL_STRUCTURE,
            "convert_sl_to_spread": True,
            "entry_exit_enabled": False,
        }
    )
    append_log(
        "revoke_buy_hold",
        "Buy & Hold revoked → sell_premium"
        + (" · flattened buy package" if closed else " · no open buy package"),
        {"closed_package": closed, "structure": saved.get("structure")},
    )
    return {
        "ok": True,
        "closed_package": closed,
        "config": saved,
        "state": get_state(),
    }


def tick() -> dict[str, Any]:
    cfg = _ensure_expiry(get_config())
    state = reset_daily_state_if_needed(_today_str())
    if state.get("runner") != "running":
        return {"ok": True, "skipped": True, "reason": "runner stopped"}

    if not session_status().get("authenticated"):
        append_log("error", "Kite session required")
        return {"ok": False, "error": "Kite session required"}

    underlying = str(cfg.get("underlying") or "NIFTY")
    timeframe = str(cfg.get("timeframe") or "5min")
    entry_start = str(cfg.get("entry_start") or "09:20")
    session_end = str(cfg.get("session_end") or "15:40")
    force_exit = str(cfg.get("force_exit") or "15:20")
    system_mode = str(cfg.get("system_mode") or "Intraday")
    structure = str(cfg.get("structure") or "bull_put")

    now = datetime.now()
    in_session = system_mode == "Positional" or _in_window(
        pd.Timestamp(now), cfg.get("session_start", "09:15"), session_end
    )
    force = system_mode == "Intraday" and _in_window(pd.Timestamp(now), force_exit, session_end)

    if not in_session and not force:
        save_state({"last_tick_at": now.isoformat(timespec="seconds")})
        return {"ok": True, "skipped": True, "reason": "outside session"}

    spot = get_index_spot(underlying)
    if spot is None:
        append_log("error", f"No spot for {underlying}")
        return {"ok": False, "error": f"No spot for {underlying}"}

    step = INDEX_OPTIONS[underlying]["strike_step"]
    atm = atm_strike(float(spot), step)

    try:
        df = _fetch_underlying_df(underlying, timeframe)
    except Exception as e:
        append_log("error", str(e))
        save_state({"last_error": str(e)})
        return {"ok": False, "error": str(e)}

    if df.empty or len(df) < 50:
        save_state(
            {
                "last_spot": spot,
                "current_atm": atm,
                "last_tick_at": now.isoformat(timespec="seconds"),
            }
        )
        return {"ok": True, "skipped": True, "reason": "insufficient bars"}

    morning_ready = _morning_bar_ready(df, entry_start) and _time_reached(entry_start)
    morning_seen = bool(state.get("morning_bar_seen"))
    if morning_ready and not morning_seen:
        morning_seen = True
        save_state({"morning_bar_seen": True, "morning_bar_at": now.isoformat(timespec="seconds")})
        append_log("morning_bar", f"Entry window open from {entry_start}")

    if not morning_seen and not force:
        save_state(
            {
                "last_spot": spot,
                "current_atm": atm,
                "last_tick_at": now.isoformat(timespec="seconds"),
            }
        )
        return {"ok": True, "skipped": True, "reason": "waiting for morning bar"}

    signals = _compute_latest_signals(df, cfg)
    bar_ts = _bar_ts_key(signals["ts"])
    save_state(
        {
            "last_spot": spot,
            "current_atm": atm,
            "last_signal": "short" if signals.get("short_ready") else ("long" if signals.get("long_ready") else None),
            "last_signal_at": bar_ts,
            "signal_bar_ts": bar_ts,
            "last_tick_at": now.isoformat(timespec="seconds"),
            "last_error": None,
        }
    )
    state = get_state()
    desk_guard = _desk_bar_guard(state, signals)
    errors: list[str] = []

    # --- Exits first ---
    pkg = state.get("package") or {}
    if pkg.get("status") == "open":
        reason, atr_patch = _evaluate_package_exit(
            cfg, signals, pkg, force, desk_guard=desk_guard
        )
        if atr_patch:
            save_state({"package": {**pkg, **atr_patch}})
        if reason == "skipped_same_bar":
            append_log(
                "package_skipped_same_bar",
                "ST1 exit deferred — already acted this bar",
                {"signal_bar_ts": bar_ts},
            )
        elif reason:
            try:
                _close_package(cfg, reason, signals=signals)
                desk_guard = _desk_bar_guard(get_state(), signals)
            except Exception as e:
                errors.append(f"package exit: {e}")

    state = get_state()
    for leg_key in ("ce", "pe"):
        leg = state.get(leg_key) or {}
        if leg.get("status") != "open":
            continue
        reason, atr_patch = _evaluate_short_leg_exit(
            cfg, signals, leg, force, desk_guard=desk_guard
        )
        if atr_patch:
            save_state({leg_key: {**leg, **atr_patch}})
            leg = {**leg, **atr_patch}
        if reason == "skipped_same_bar":
            append_log(
                f"{leg_key}_skipped_same_bar",
                "ST1 exit deferred — already acted this bar",
                {"signal_bar_ts": bar_ts},
            )
            continue
        if not reason:
            continue
        try:
            # Auto-structure may differ from the saved Structure dropdown.
            live_structure = str(
                (get_state().get("active_structure") or cfg.get("structure") or structure)
            )
            if should_convert_sl_to_spread(
                structure=live_structure,
                convert_enabled=bool(cfg.get("convert_sl_to_spread", True)),
                exit_reason=reason,
                leg_has_wing=bool(leg.get("wing")),
            ):
                convert_leg_to_vertical(leg_key, cfg, exit_reason=reason)  # type: ignore[arg-type]
                # Convert is an action on this bar — stamp so ST1 cannot churn same candle.
                stamp = _stamp_bar_action(signals=signals, is_exit=False)
                save_state({leg_key: {**(get_state().get(leg_key) or {}), **stamp}, **stamp})
                desk_guard = _desk_bar_guard(get_state(), signals)
            else:
                _close_short_leg(leg_key, cfg, reason, signals=signals)  # type: ignore[arg-type]
                desk_guard = _desk_bar_guard(get_state(), signals)
        except Exception as e:
            errors.append(f"{leg_key} exit/convert: {e}")

    if force:
        if errors:
            save_state({"last_error": "; ".join(errors)})
        return {"ok": not errors, "errors": errors, "state": get_state()}

    # --- Entries (auto structure from ST1+ST2 when enabled; one decision per closed bar) ---
    state = get_state()
    desk_guard = _desk_bar_guard(state, signals)
    if _same_bar_action_blocked(desk_guard, signals):
        append_log(
            "entry_skipped_same_bar",
            "Entry deferred — already acted this bar",
            {"signal_bar_ts": bar_ts},
        )
        return {
            "ok": True,
            "entry": "skipped_same_bar",
            "signal_bar_ts": bar_ts,
            "state": state,
        }
    ok_re, re_why = _reentry_allowed_after_exit(desk_guard, signals)
    if not ok_re:
        append_log(
            "entry_reentry_cooldown",
            "Re-entry blocked until next bar closes",
            {
                "last_exit_bar_ts": desk_guard.get("last_exit_bar_ts"),
                "signal_bar_ts": bar_ts,
            },
        )
        return {
            "ok": True,
            "entry": re_why,
            "signal_bar_ts": bar_ts,
            "state": state,
        }

    trade_bias = str(cfg.get("trade_bias") or "sell_premium")
    auto = bool(cfg.get("auto_structure", True))
    active_structure = structure
    auto_why = "manual_structure"
    if auto:
        picked, auto_why = pick_auto_structure(signals, trade_bias=trade_bias)
        if picked is None:
            save_state(
                {
                    "active_structure": None,
                    "auto_structure_reason": auto_why,
                    "last_tick_at": now.isoformat(timespec="seconds"),
                }
            )
            return {"ok": True, "entry": auto_why, "state": get_state()}
        active_structure = picked
        # Track live pick in state only — do not overwrite manual Structure dropdown prefs.
        save_state({"active_structure": active_structure, "auto_structure_reason": auto_why})

    # Retired sell templates: never open new short strangle/straddle legs.
    if active_structure in ("short_straddle", "short_strangle"):
        append_log(
            "entry_skipped_legacy_structure",
            "Short straddle/strangle retired from Premium Book — manage existing legs only",
            {"structure": active_structure, "signal_bar_ts": bar_ts},
        )
        save_state(
            {
                "active_structure": None,
                "auto_structure_reason": "legacy_short_no_new_entry",
                "last_tick_at": now.isoformat(timespec="seconds"),
            }
        )
        return {
            "ok": True,
            "entry": "legacy_short_no_new_entry",
            "state": get_state(),
        }

    ok, why = structure_entry_ok(active_structure, signals)
    if not ok:
        return {"ok": True, "entry": why, "auto": auto_why, "state": get_state()}
    why = f"{auto_why}:{why}" if auto else why

    is_package = active_structure in SELL_VERTICALS or active_structure in BUY_PACKAGE_STRUCTURES
    cfg_run = {**cfg, "structure": active_structure}

    try:
        if is_package:
            if (state.get("package") or {}).get("status") != "open":
                _open_package(cfg_run, active_structure, float(spot), why, signals=signals)
    except Exception as e:
        append_log("error", str(e))
        errors.append(str(e))
        save_state({"last_error": str(e)})

    out = {"ok": not errors, "errors": errors, "state": get_state(), "structure": active_structure}
    try:
        out["preview"] = preview_current(cfg, spot=float(spot), structure_override=active_structure)
        save_state({"preview": out["preview"]})
    except Exception:
        pass
    return out


def status_bundle() -> dict[str, Any]:
    cfg = get_config()
    state = get_state()
    preview = None
    try:
        if session_status().get("authenticated"):
            preview = state.get("preview") or preview_current(cfg)
    except Exception as e:
        preview = {"error": str(e)}
    return {
        "config": cfg,
        "state": state,
        "preview": preview,
        "arm": get_arm_state(),
        "kite_authenticated": session_status().get("authenticated"),
        "narrative": (
            (
                "Buy & hold · auto: above→bull call · below→bear put · flat→long strangle · ST1 exit"
                if (cfg.get("auto_structure", True) and is_buy_structure(str(cfg.get("structure") or "")))
                or (cfg.get("auto_structure", True) and str(cfg.get("trade_bias")) == "buy_hold")
                else "Buy & hold · ST1+ST2 entry · ST1 exit"
                if is_buy_structure(str(cfg.get("structure") or ""))
                else "Sell premium · auto: above→bull put · below→bear call · flat/whipsaw→sit out · ST1 exit"
                if cfg.get("auto_structure", True)
                else "Sell premium · ST1+ST2 entry · ST1 exit · credit verticals only"
            )
        ),
        # Live pick only — do not fall back to Config dropdown (that looked like "Bull ready").
        "active_structure": state.get("active_structure"),
        "auto_structure_reason": state.get("auto_structure_reason"),
        "config_structure": cfg.get("structure"),
    }
