"""Survivor strategy runner — polls index spot and drives trading-algo SurvivorStrategy."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from execution.arming import get_arm_state
from execution.kite_strategy_adapter import KiteStrategyAdapter
from execution.survivor_store import append_log, get_config, get_state, save_state, validate_quantities
from execution.trading_algo_path import ensure_trading_algo_path
from kite_auth import session_status
from options.chain import get_chain, nearest_expiry

_engine = None
_order_tracker = None


def _derive_symbol_initials(underlying: str, expiry: str) -> str:
    chain = get_chain(underlying, expiry)
    for row in chain.get("strikes") or []:
        ce = row.get("ce")
        if not ce:
            continue
        sym = str(ce["tradingsymbol"])
        strike = str(int(float(row["strike"])))
        if sym.endswith("CE") and strike in sym:
            return sym[: sym.index(strike)]
    raise RuntimeError(f"Could not derive symbol_initials for {underlying} {expiry}")


def _strategy_config(cfg: dict[str, Any]) -> dict[str, Any]:
    underlying = cfg.get("underlying", "NIFTY")
    expiry = cfg.get("expiry") or nearest_expiry(underlying)
    if not expiry:
        raise RuntimeError(f"No expiry for {underlying}")

    initials = cfg.get("symbol_initials") or _derive_symbol_initials(underlying, expiry)
    index_sym = cfg.get("index_symbol") or "NSE:NIFTY 50"
    if underlying == "BANKNIFTY":
        index_sym = "NSE:NIFTY BANK"
    elif underlying == "SENSEX":
        index_sym = "BSE:SENSEX"

    return {
        "index_symbol": index_sym,
        "symbol_initials": initials,
        "pe_gap": cfg["pe_gap"],
        "ce_gap": cfg["ce_gap"],
        "pe_quantity": cfg["pe_quantity"],
        "ce_quantity": cfg["ce_quantity"],
        "pe_symbol_gap": cfg["pe_symbol_gap"],
        "ce_symbol_gap": cfg["ce_symbol_gap"],
        "min_price_to_sell": cfg["min_price_to_sell"],
        "sell_multiplier_threshold": cfg["sell_multiplier_threshold"],
        "pe_reset_gap": cfg["pe_reset_gap"],
        "ce_reset_gap": cfg["ce_reset_gap"],
        "pe_start_point": cfg["pe_start_point"],
        "ce_start_point": cfg["ce_start_point"],
        "exchange": cfg.get("exchange", "NFO"),
        "order_type": cfg.get("order_type", "MARKET"),
        "product_type": cfg.get("product_type", "NRML"),
        "trans_type": "SELL",
        "tag": cfg.get("tag", "Survivor"),
    }


def _get_engine(force_new: bool = False):
    global _engine, _order_tracker
    if _engine is not None and not force_new:
        return _engine, _order_tracker

    ensure_trading_algo_path()
    from orders import OrderTracker
    from strategy.survivor import SurvivorStrategy

    cfg = get_config()
    strat_cfg = _strategy_config(cfg)
    broker = KiteStrategyAdapter()
    _order_tracker = OrderTracker()
    _engine = SurvivorStrategy(broker, strat_cfg, _order_tracker)

    state = get_state()
    if state.get("nifty_pe_last_value") is not None:
        _engine.nifty_pe_last_value = float(state["nifty_pe_last_value"])
    if state.get("nifty_ce_last_value") is not None:
        _engine.nifty_ce_last_value = float(state["nifty_ce_last_value"])
    _engine.pe_reset_gap_flag = int(state.get("pe_reset_gap_flag") or 0)
    _engine.ce_reset_gap_flag = int(state.get("ce_reset_gap_flag") or 0)
    return _engine, _order_tracker


def _persist_engine_refs(engine) -> None:
    save_state(
        {
            "nifty_pe_last_value": engine.nifty_pe_last_value,
            "nifty_ce_last_value": engine.nifty_ce_last_value,
            "pe_reset_gap_flag": engine.pe_reset_gap_flag,
            "ce_reset_gap_flag": engine.ce_reset_gap_flag,
            "initialized": True,
        }
    )


def start_runner() -> dict[str, Any]:
    if not session_status().get("authenticated"):
        raise RuntimeError("Kite session required")
    validate_quantities(get_config())
    global _engine
    _engine = None
    engine, _ = _get_engine(force_new=True)
    instruments = getattr(engine, "instruments", None)
    if instruments is None or instruments.shape[0] == 0:
        raise RuntimeError("Survivor: no instruments for symbol_initials — set expiry/series in config")
    save_state({"runner": "running", "last_error": None})
    append_log("start", "Survivor runner started")
    return get_state()


def stop_runner() -> dict[str, Any]:
    global _engine
    _engine = None
    save_state({"runner": "stopped"})
    append_log("stop", "Survivor runner stopped")
    return get_state()


def tick() -> dict[str, Any]:
    state = get_state()
    if state.get("runner") != "running":
        return state

    cfg = get_config()
    try:
        engine, _ = _get_engine()
        index_sym = _strategy_config(cfg)["index_symbol"]
        broker = engine.broker
        quote = broker.get_quote(index_sym)
        spot = float(quote.last_price)
        engine.on_ticks_update({"last_price": spot})
        _persist_engine_refs(engine)
        save_state(
            {
                "last_tick_at": datetime.now().isoformat(timespec="seconds"),
                "last_spot": spot,
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
