"""Wave strategy runner — limit-order pairs with order polling."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from execution.arming import get_arm_state
from execution.kite_strategy_adapter import KiteStrategyAdapter
from execution.trading_algo_path import ensure_trading_algo_path
from execution.wave_store import append_log, get_config, get_state, save_state, validate_quantities
from kite_auth import session_status

_engine = None
_order_tracker = None
_last_check_ts = 0.0


def _wave_config(cfg: dict[str, Any]) -> dict[str, Any]:
    skip = {"auto_start_on_boot", "check_interval_sec"}
    return {k: v for k, v in cfg.items() if k not in skip}


def _get_engine(force_new: bool = False):
    global _engine, _order_tracker
    if _engine is not None and not force_new:
        return _engine, _order_tracker

    ensure_trading_algo_path()
    from orders import OrderTracker
    from strategy.wave import WaveStrategy

    cfg = get_config()
    broker = KiteStrategyAdapter()
    _order_tracker = OrderTracker()
    _engine = WaveStrategy(_wave_config(cfg), broker, _order_tracker)
    return _engine, _order_tracker


def _poll_order_updates(engine) -> None:
    broker = engine.broker
    tag = engine.tag
    sym = engine.symbol_name.split(":")[-1]
    for row in broker.get_orderbook():
        order_tag = str(row.get("tag") or "")
        tradingsymbol = str(row.get("tradingsymbol") or "")
        if tag not in order_tag:
            continue
        if sym not in tradingsymbol:
            continue
        engine.handle_order_update(row)


def start_runner() -> dict[str, Any]:
    if not session_status().get("authenticated"):
        raise RuntimeError("Kite session required")
    validate_quantities(get_config())
    global _engine
    _engine = None
    engine, _ = _get_engine(force_new=True)
    save_state({"runner": "running", "initialized": True, "last_error": None})
    append_log("start", f"Wave runner started for {engine.symbol_name}")
    return get_state()


def stop_runner() -> dict[str, Any]:
    global _engine
    _engine = None
    save_state({"runner": "stopped"})
    append_log("stop", "Wave runner stopped")
    return get_state()


def tick() -> dict[str, Any]:
    global _last_check_ts
    state = get_state()
    if state.get("runner") != "running":
        return state

    cfg = get_config()
    interval = max(30, int(cfg.get("check_interval_sec") or 60))
    now = datetime.now().timestamp()
    periodic = (now - _last_check_ts) >= interval

    try:
        engine, _ = _get_engine()
        _poll_order_updates(engine)

        active = 0
        try:
            active = len(engine.orders) if engine.check_is_any_order_active() else 0
        except Exception:
            active = len(getattr(engine, "orders", {}) or {})

        if periodic:
            _last_check_ts = now
            try:
                engine.check_and_enforce_restrictions_on_active_orders()
            except Exception as e:
                append_log("restrictions_error", str(e))

            if not engine.check_is_any_order_active():
                engine.place_wave_order()
                append_log("wave_cycle", "Placed new wave order pair")

        quote = engine.broker.get_quote(engine.symbol_name)
        save_state(
            {
                "last_check_at": datetime.now().isoformat(timespec="seconds"),
                "last_spot": float(quote.last_price),
                "active_orders": active,
                "last_error": None,
            }
        )
    except Exception as e:
        append_log("error", str(e))
        save_state({"last_error": str(e)})
        raise
    return get_state()


def status_bundle() -> dict[str, Any]:
    return {
        "config": get_config(),
        "state": get_state(),
        "arm": get_arm_state(),
        "kite_authenticated": session_status().get("authenticated"),
    }
