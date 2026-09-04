"""Rolling ATM straddle runner — independent CE/PE legs on 3ST signals."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

import pandas as pd

from backtest_engine import _in_window, _level, _parse_hm
from broker.base import Broker
from broker.kite_broker import KiteBroker
from broker.paper_broker import PaperBroker, get_paper_broker
from config import INDEX_OPTIONS
from execution.algo_ownership import leg_is_algo_managed
from execution.arming import get_arm_state
from execution.order_executor import order_tag, place_leg_to_target
from kite_errors import friendly_kite_message
from execution.rolling_straddle_store import (
    append_log,
    clear_spot_state_for_underlying,
    get_config,
    get_state,
    order_quantity_from_config,
    reset_daily_state_if_needed,
    save_config,
    save_state,
)
from kite_client import fetch_historical_by_token, session_status
from config import INDEX_OPTIONS
from options.chain import atm_strike, get_index_spot, get_index_spot_detail, tradingsymbol_matches_underlying
from options.legs import build_atm_leg
from strategy_3st import compute_signals
from utils.logging import get_logger, log_event

_log = get_logger(__name__)

_paper = get_paper_broker()
_kite = KiteBroker()

LegKey = Literal["ce", "pe"]
PositionSide = Literal["long", "short"]

# Generous LTP bands — detect NIFTY spot cached under CRUDEOIL (and vice versa).
_SPOT_BANDS: dict[str, tuple[float, float]] = {
    "NIFTY": (10_000, 50_000),
    "BANKNIFTY": (20_000, 80_000),
    "SENSEX": (30_000, 120_000),
    "CRUDEOIL": (2_000, 20_000),
    "CRUDEOILM": (2_000, 20_000),
    "NATURALGAS": (50, 2_000),
}


def _spot_plausible_for_underlying(underlying: str, spot: float | None) -> bool:
    if spot is None:
        return True
    band = _SPOT_BANDS.get(str(underlying).upper())
    if not band:
        return True
    lo, hi = band
    s = float(spot)
    return lo <= s <= hi


def _leg_position_side(leg: dict[str, Any]) -> PositionSide:
    """Broker position side — BUY = long, SELL = short (not 3ST zone label)."""
    es = str(leg.get("entry_side") or "BUY").upper()
    return "short" if es == "SELL" else "long"


def _zone_exit_bar_triggered(signals: dict[str, Any], side: PositionSide) -> bool:
    return bool(signals["long_zone_exit"] if side == "long" else signals["short_zone_exit"])


def _bar_ts_key(ts: Any) -> str:
    """Normalize signal bar timestamp for per-bar guards."""
    if ts is None:
        return ""
    if isinstance(ts, pd.Timestamp):
        return ts.strftime("%Y-%m-%d %H:%M:%S")
    return str(ts).replace("T", " ")[:19]


def _exit_on_bar_close_only(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("exit_on_bar_close_only", True))


def _reentry_allowed_after_exit(leg: dict[str, Any], signals: dict[str, Any]) -> tuple[bool, str]:
    """After exit, wait until a newer closed bar before zone_active re-entry."""
    last_exit = leg.get("last_exit_bar_ts")
    if not last_exit:
        return True, ""
    current = _bar_ts_key(signals.get("ts"))
    if not current or current <= str(last_exit):
        return False, "reentry_cooldown"
    return True, ""


def _same_bar_action_blocked(leg: dict[str, Any], signals: dict[str, Any]) -> bool:
    last = leg.get("last_action_bar_ts")
    if not last:
        return False
    return _bar_ts_key(signals.get("ts")) == str(last)


def _broker() -> Broker:
    return _kite if get_arm_state().get("mode") == "live" else _paper


def _broker_positions_detail(
    broker: Broker,
    *,
    timeout_sec: float = 8.0,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """``exchange:tradingsymbol`` -> {qty, average_price, last_price}. False when read failed."""
    import concurrent.futures

    out: dict[str, dict[str, Any]] = {}
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(broker.positions)
            rows = fut.result(timeout=timeout_sec)
    except concurrent.futures.TimeoutError:
        append_log("broker_timeout", f"Broker positions timed out after {timeout_sec}s")
        return out, False
    except Exception:
        return out, False
    if not isinstance(rows, list):
        return out, False
    for row in rows:
        sym = str(row.get("tradingsymbol") or "").strip()
        exch = str(row.get("exchange") or "").strip().upper()
        qty = int(row.get("quantity") or 0)
        if not (sym and exch and qty != 0):
            continue
        avg_raw = row.get("average_price")
        if avg_raw is None:
            avg_raw = row.get("avg_price")
        try:
            avg = float(avg_raw) if avg_raw is not None else None
        except (TypeError, ValueError):
            avg = None
        if avg is not None and avg <= 0:
            avg = None
        ltp_raw = row.get("last_price")
        try:
            ltp = float(ltp_raw) if ltp_raw is not None else None
        except (TypeError, ValueError):
            ltp = None
        out[f"{exch}:{sym}"] = {
            "qty": qty,
            "average_price": avg,
            "last_price": ltp,
        }
    return out, True


def _broker_positions_map(broker: Broker, *, timeout_sec: float = 8.0) -> tuple[dict[str, int], bool]:
    """``exchange:tradingsymbol`` -> signed net qty. Second value is False when the read failed."""
    detail, ok = _broker_positions_detail(broker, timeout_sec=timeout_sec)
    return {k: int(v["qty"]) for k, v in detail.items()}, ok


def _broker_avg_price(
    broker: Broker,
    exchange: str,
    tradingsymbol: str,
    *,
    detail: dict[str, dict[str, Any]] | None = None,
) -> float | None:
    """Kite position average_price for a symbol (used when local entry_price is missing)."""
    if detail is None:
        detail, ok = _broker_positions_detail(broker)
        if not ok:
            return None
    key = f"{str(exchange).strip().upper()}:{str(tradingsymbol).strip()}"
    row = detail.get(key) or {}
    avg = row.get("average_price")
    try:
        return float(avg) if avg is not None else None
    except (TypeError, ValueError):
        return None


#: Kite's ``place_order`` response carries an order id and nothing else — a
#: MARKET order's true average fill has to be read back from the orderbook.
#: Poll briefly: an entry is normally COMPLETE within a second, and a slow fill
#: must not stall the tick (reconcile upgrades an ``ltp_proxy`` price later).
ENTRY_FILL_POLL_ATTEMPTS = 3
ENTRY_FILL_POLL_SLEEP_SEC = 0.4

#: Where a leg's ``entry_price`` came from, best first. Only ``ltp_proxy`` is a
#: guess — it is a snapshot taken just after submit, not a fill — so it is the
#: one source reconcile is allowed to overwrite.
EntryPriceSource = Literal["order_fill", "order_avg", "kite_avg", "ltp_proxy"]


#: Suffix shown next to the entry-exit level so the operator can see whether the
#: breakeven stop is measured against a real fill.
_ENTRY_SOURCE_NOTES: dict[str, str] = {
    "order_avg": "",
    "order_fill": "",
    "fill": "",
    "kite_avg": " (Kite avg)",
    "ltp_proxy": " (LTP proxy — awaiting fill)",
}


def _entry_price_is_proxy(leg: dict[str, Any]) -> bool:
    """True when ``entry_price`` is the post-submit LTP snapshot, not a fill."""
    return str(leg.get("entry_price_source") or "") == "ltp_proxy"


def _completed_order_average(
    broker: Broker,
    order_id: str,
    *,
    exchange: str,
    tradingsymbol: str,
) -> float | None:
    """Average fill of a COMPLETE order; None while it is still working.

    Prefers Kite's ``average_price``. PaperBroker rows carry ``price`` instead,
    and for paper that *is* the fill, so both keys are real fills here.
    """
    oid = str(order_id).strip()
    if not oid:
        return None
    sym_u = tradingsymbol.strip().upper()
    exch_u = exchange.strip().upper()
    for row in broker.orders():
        if str(row.get("order_id") or "").strip() != oid:
            continue
        if str(row.get("tradingsymbol") or "").strip().upper() != sym_u:
            continue
        if str(row.get("exchange") or "").strip().upper() != exch_u:
            continue
        if str(row.get("status") or "").upper() != "COMPLETE":
            return None
        for key in ("average_price", "price"):
            try:
                px = float(row[key])
            except (KeyError, TypeError, ValueError):
                continue
            if px > 0:
                return px
        return None
    return None


def _entry_fill_price(
    broker: Broker,
    order_id: str | None,
    *,
    exchange: str,
    tradingsymbol: str,
) -> float | None:
    """Poll the orderbook briefly for this entry's real average fill."""
    if not order_id:
        return None
    for attempt in range(ENTRY_FILL_POLL_ATTEMPTS):
        try:
            px = _completed_order_average(
                broker, order_id, exchange=exchange, tradingsymbol=tradingsymbol
            )
        except Exception:
            return None
        if px is not None:
            return px
        if attempt < ENTRY_FILL_POLL_ATTEMPTS - 1:
            time.sleep(ENTRY_FILL_POLL_SLEEP_SEC)
    return None


def _effective_entry_price(leg: dict[str, Any]) -> float | None:
    """Prefer a real fill; an ``ltp_proxy`` entry yields to Kite's average.

    An ``ltp_proxy`` ``entry_price`` was sampled just *after* the market order
    was submitted, so it is not the fill — once the broker reports an
    ``average_price`` for the position, that is the better number for the
    entry-exit / SL ladder. Legs with no ``entry_price_source`` (state written
    before this field existed) keep the historical precedence.
    """
    keys = ("entry_price", "broker_average_price")
    if _entry_price_is_proxy(leg):
        keys = ("broker_average_price", "entry_price")
    for key in keys:
        raw = leg.get(key)
        if raw is None:
            continue
        try:
            px = float(raw)
        except (TypeError, ValueError):
            continue
        if px > 0:
            return px
    return None


def _flat_leg_from_broker_sync(leg: dict[str, Any], *, reason: str = "broker_sync_flat") -> dict[str, Any]:
    """Mark an algo leg flat when Kite no longer holds the position."""
    return {
        **leg,
        "status": "flat",
        "last_action": reason,
        "broker_qty": None,
        "tradingsymbol": None,
        "exchange": None,
        "strike": None,
        "entry_price": None,
        "entry_price_source": None,
        "broker_average_price": None,
        "entry_at": None,
        "entry_order_id": None,
        "managed_by": None,
        "ltp": None,
    }


def _find_broker_leg_position(
    broker: Broker,
    cfg: dict[str, Any],
    leg_key: LegKey,
    *,
    tradingsymbol: str | None = None,
    exchange: str | None = None,
    leg: dict[str, Any] | None = None,
) -> tuple[str, str, int] | None:
    """Resolve open broker qty for an algo-managed leg — exact symbol only (never fuzzy-match manual Kite lots)."""
    if leg is not None and not leg_is_algo_managed(leg):
        return None
    if not tradingsymbol or not exchange:
        return None
    pos_map, _ = _broker_positions_map(broker)
    key = f"{exchange.strip().upper()}:{tradingsymbol.strip()}"
    qty = pos_map.get(key)
    if qty:
        return exchange.strip().upper(), tradingsymbol.strip(), int(qty)
    return None


def _close_transaction_for_qty(qty: int) -> str | None:
    if qty > 0:
        return "SELL"
    if qty < 0:
        return "BUY"
    return None


def _position_side_from_qty(qty: int) -> PositionSide:
    return "short" if qty < 0 else "long"


def _strike_from_option_symbol(tradingsymbol: str) -> float | None:
    m = re.search(r"(\d{4,5})(CE|PE)$", str(tradingsymbol or "").upper())
    if m:
        return float(m.group(1))
    return None


def _leg_algo_history(leg: dict[str, Any]) -> bool:
    """True when this leg was opened by Rolling Straddle (may be flat after purge/restart)."""
    if str(leg.get("managed_by") or "") != "algo":
        return False
    if leg.get("entry_order_id"):
        return True
    if int(leg.get("entries_today") or 0) > 0:
        return True
    last = str(leg.get("last_action") or "")
    return last in {
        "long_entry",
        "short_entry",
        "long_reentry",
        "short_reentry",
        "purged_foreign_symbol",
    }


def _scan_broker_for_rolling_leg(
    broker: Broker,
    cfg: dict[str, Any],
    leg_key: LegKey,
    *,
    detail: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str, int, float | None] | None:
    """Find a net broker position for this leg's underlying + CE/PE suffix.

    Returns ``(exchange, tradingsymbol, qty, average_price)``. average_price is
    Kite's position average when available (used as entry when local fill is missing).
    """
    suffix = "CE" if leg_key == "ce" else "PE"
    underlying = str(cfg.get("underlying") or "NIFTY").upper()
    if detail is None:
        detail, positions_ok = _broker_positions_detail(broker)
        if not positions_ok:
            return None
    for key, row in detail.items():
        qty = int(row.get("qty") or 0)
        if qty == 0:
            continue
        exch, sym = key.split(":", 1)
        if not sym.upper().endswith(suffix):
            continue
        if not tradingsymbol_matches_underlying(sym, underlying):
            continue
        avg = row.get("average_price")
        try:
            avg_f = float(avg) if avg is not None else None
        except (TypeError, ValueError):
            avg_f = None
        if avg_f is not None and avg_f <= 0:
            avg_f = None
        return exch, sym, qty, avg_f
    return None


def _has_3st_entry_order(broker: Broker, tradingsymbol: str, exchange: str, leg_key: LegKey) -> bool:
    """True when a completed 3ST entry order exists for this symbol today."""
    prefix = f"3ST-{leg_key.upper()}-"
    sym_u = tradingsymbol.strip().upper()
    exch_u = exchange.strip().upper()
    try:
        for order in broker.orders():
            if str(order.get("tradingsymbol") or "").strip().upper() != sym_u:
                continue
            if str(order.get("exchange") or "").strip().upper() != exch_u:
                continue
            tag = str(order.get("tag") or "").upper()
            if not tag.startswith(prefix):
                continue
            if str(order.get("status") or "").upper() != "COMPLETE":
                continue
            if "ENTR" in tag:
                return True
    except Exception:
        pass
    return False


def _resolve_live_broker_leg(
    broker: Broker,
    cfg: dict[str, Any],
    leg_key: LegKey,
    leg: dict[str, Any],
    *,
    pos_map: dict[str, int] | None = None,
    detail: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, str, int, float | None] | None:
    """Match broker net qty for an algo leg — by stored symbol or 3ST-tagged scan.

    Fourth value is always Kite ``average_price`` (for filling missing local entry),
    never the local fill — callers keep ``leg.entry_price`` when already set.
    """
    if detail is None:
        detail, ok = _broker_positions_detail(broker)
        if not ok:
            return None
        pos_map = {k: int(v["qty"]) for k, v in detail.items()}
    elif pos_map is None:
        pos_map = {k: int(v["qty"]) for k, v in detail.items()}

    if leg.get("status") == "open" and leg_is_algo_managed(leg):
        if leg.get("tradingsymbol") and leg.get("exchange"):
            key = f"{str(leg['exchange']).strip().upper()}:{str(leg['tradingsymbol']).strip()}"
            qty = pos_map.get(key)
            if qty:
                exch = str(leg["exchange"]).strip().upper()
                sym = str(leg["tradingsymbol"]).strip()
                return exch, sym, int(qty), _broker_avg_price(broker, exch, sym, detail=detail)
        return None

    if leg.get("tradingsymbol") and leg.get("exchange"):
        key = f"{str(leg['exchange']).strip().upper()}:{str(leg['tradingsymbol']).strip()}"
        qty = pos_map.get(key)
        if qty:
            exch = str(leg["exchange"]).strip().upper()
            sym = str(leg["tradingsymbol"]).strip()
            return exch, sym, int(qty), _broker_avg_price(broker, exch, sym, detail=detail)

    scanned = _scan_broker_for_rolling_leg(broker, cfg, leg_key, detail=detail)
    if not scanned:
        return None
    exch, sym, qty, entry_px = scanned
    if not _has_3st_entry_order(broker, sym, exch, leg_key):
        return None
    if entry_px is None:
        entry_px = _broker_avg_price(broker, exch, sym, detail=detail)
    return exch, sym, qty, entry_px


def _leg_patch_from_broker(
    leg: dict[str, Any],
    *,
    exch: str,
    sym: str,
    qty: int,
    entry_px: float | None,
    last_action: str,
) -> dict[str, Any]:
    side = _position_side_from_qty(qty)
    patch: dict[str, Any] = {
        "status": "open",
        "tradingsymbol": sym,
        "exchange": exch,
        "broker_qty": qty,
        "entry_side": "SELL" if qty < 0 else "BUY",
        "position_side": side,
        "managed_by": "algo",
        "last_action": last_action,
    }
    strike = _strike_from_option_symbol(sym)
    if strike is not None:
        patch["strike"] = strike
    if entry_px is not None:
        try:
            avg = float(entry_px)
        except (TypeError, ValueError):
            avg = None
        if avg is not None and avg > 0:
            patch["broker_average_price"] = avg
            # Keep a real fill. Adopt Kite's average when we have no entry at
            # all, or when the stored one is only the post-submit LTP proxy.
            if leg.get("entry_price") is None or _entry_price_is_proxy(leg):
                patch["entry_price"] = avg
                patch["entry_price_source"] = "kite_avg"
    return {**leg, **patch}


def _collect_leg_orphans(
    cfg: dict[str, Any],
    state: dict[str, Any],
    broker: Broker,
    patches: dict[str, Any],
) -> list[dict[str, Any]]:
    """Broker positions for this underlying+leg with no local algo-managed open leg."""
    orphans: list[dict[str, Any]] = []
    for leg_key in ("ce", "pe"):
        leg = dict(state.get(leg_key) or {})
        patched = patches.get(leg_key) or leg
        if patched.get("status") == "open" and leg_is_algo_managed(patched):
            continue
        scanned = _scan_broker_for_rolling_leg(broker, cfg, leg_key)  # type: ignore[arg-type]
        if not scanned:
            continue
        exch, sym, qty, avg_px = scanned
        has_3st = _has_3st_entry_order(broker, sym, exch, leg_key)  # type: ignore[arg-type]
        orphans.append(
            {
                "leg_key": leg_key,
                "exchange": exch,
                "tradingsymbol": sym,
                "quantity": qty,
                "average_price": avg_px,
                "has_3st_order": has_3st,
                "managed": False,
            }
        )
    return orphans


def _reconcile_broker_legs(cfg: dict[str, Any], state: dict[str, Any], broker: Broker) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    """Refresh algo leg qty from Kite; sync flat when broker closed the position.

    Also fills missing ``entry_price`` from Kite ``average_price`` so entry-exit / SL work.
    """
    if get_arm_state().get("mode") != "live":
        return {}, [], []

    detail, positions_ok = _broker_positions_detail(broker)
    pos_map = {k: int(v["qty"]) for k, v in detail.items()} if positions_ok else {}
    patches: dict[str, Any] = {}
    mismatches: list[str] = []

    for leg_key in ("ce", "pe"):
        leg = dict(state.get(leg_key) or {})
        if leg.get("status") == "open" and not leg_is_algo_managed(leg):
            if leg.get("broker_qty"):
                patches[leg_key] = {**leg, "broker_qty": None}
            continue

        resolved = _resolve_live_broker_leg(
            broker, cfg, leg_key, leg, pos_map=pos_map, detail=detail if positions_ok else None
        )  # type: ignore[arg-type]
        broker_qty = int(resolved[2]) if resolved else 0
        state_open = leg.get("status") == "open"

        if broker_qty != 0:
            exch, sym, qty, entry_px = resolved  # type: ignore[misc]
            action = leg.get("last_action") or "reconcile_restored"
            if not state_open:
                append_log(
                    f"{leg_key}_reconcile_restore",
                    f"Restored from broker qty {qty}",
                    {"symbol": sym, "exchange": exch, "average_price": entry_px},
                )
            elif entry_px is not None and (
                leg.get("entry_price") is None or _entry_price_is_proxy(leg)
            ):
                was_proxy = _entry_price_is_proxy(leg)
                append_log(
                    f"{leg_key}_entry_from_avg",
                    (
                        f"entry_price {leg.get('entry_price')} (LTP proxy) replaced by "
                        f"Kite average_price={entry_px}"
                        if was_proxy
                        else f"entry_price set from Kite average_price={entry_px}"
                    ),
                    {"symbol": sym, "was_proxy": was_proxy},
                )
                action = f"Entry from Kite avg · {leg_key.upper()}"
            patched = _leg_patch_from_broker(
                leg,
                exch=exch,
                sym=sym,
                qty=qty,
                entry_px=entry_px,
                last_action=action if state_open else "reconcile_restored",
            )
            # Keep LTP fresh from Kite position row when available
            row = detail.get(f"{exch}:{sym}") or {}
            ltp = row.get("last_price")
            if ltp is not None:
                try:
                    patched = {**patched, "ltp": float(ltp)}
                except (TypeError, ValueError):
                    pass
            patches[leg_key] = patched
        elif state_open and leg_is_algo_managed(leg):
            if not positions_ok:
                mismatches.append(f"{leg_key}: broker positions unavailable")
                continue
            sym = leg.get("tradingsymbol")
            append_log(
                f"{leg_key}_broker_sync",
                "Closed locally — broker flat",
                {"symbol": sym, "exchange": leg.get("exchange")},
            )
            mismatches.append(f"{leg_key}: synced flat (broker closed)")
            patches[leg_key] = _flat_leg_from_broker_sync(leg)
        elif leg.get("broker_qty"):
            patches[leg_key] = {**leg, "broker_qty": None}

    orphans = _collect_leg_orphans(cfg, state, broker, patches)
    return patches, mismatches, orphans


def _paper_broker_qty_patches(cfg: dict[str, Any], state: dict[str, Any], broker: Broker) -> dict[str, Any]:
    """Refresh displayed broker qty from paper positions; flatten when symbol is gone."""
    if get_arm_state().get("mode") == "live":
        return {}
    patches: dict[str, Any] = {}
    for leg_key in ("ce", "pe"):
        leg = dict(state.get(leg_key) or {})
        if not leg_is_algo_managed(leg):
            continue
        resolved = _find_broker_leg_position(
            broker,
            cfg,
            leg_key,  # type: ignore[arg-type]
            tradingsymbol=leg.get("tradingsymbol"),
            exchange=leg.get("exchange"),
            leg=leg,
        )
        qty = int(resolved[2]) if resolved else 0
        if qty != 0:
            if leg.get("broker_qty") != qty:
                patches[leg_key] = {**leg, "broker_qty": qty}
        elif leg.get("status") == "open":
            patches[leg_key] = _flat_leg_from_broker_sync(leg)
        elif leg.get("broker_qty"):
            patches[leg_key] = {**leg, "broker_qty": None}
    return patches


def _order_leg_from_atm(
    cfg: dict[str, Any],
    leg_key: LegKey,
    atm: float,
    spot: float,
) -> dict[str, Any]:
    opt = "CE" if leg_key == "ce" else "PE"
    expiry = cfg.get("expiry") or ""
    if not expiry:
        raise RuntimeError("Expiry not configured")
    qty = order_quantity_from_config(cfg)
    built = build_atm_leg(
        cfg["underlying"],
        expiry,
        opt,  # type: ignore[arg-type]
        spot=spot,
        strike=atm,
        quantity=qty,
    )
    return built


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
    end = datetime.now()
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
        from kite_client import fetch_ltp_batch

        key = f"{exch}:{sym}"
        data = fetch_ltp_batch([key])
        return float(data[key]["last_price"])
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
        from kite_client import fetch_ltp_batch

        sym = f"{exchange}:{tradingsymbol}"
        px = float(fetch_ltp_batch([sym])[sym]["last_price"])
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
        st1_only=True,
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
        "high": float(row["high"]) if pd.notna(row.get("high")) else float(row["close"]),
        "low": float(row["low"]) if pd.notna(row.get("low")) else float(row["close"]),
        "st1": st1,
        "st2": st2,
        "st3": st3,
        "st1_upper": float(row["st1_upper"]) if pd.notna(row.get("st1_upper")) else None,
        "st1_lower": float(row["st1_lower"]) if pd.notna(row.get("st1_lower")) else None,
        "exit_line": exit_line,
        "exit_label": exit_label,
        "long_entry": bool(row["long_entry"]),
        "short_entry": bool(row["short_entry"]),
        "long_reentry": bool(row.get("long_reentry", False)),
        "short_reentry": bool(row.get("short_reentry", False)),
        "long_ready": bool(row["long_ready"]),
        "short_ready": bool(row["short_ready"]),
        "long_zone_exit": bool(row["long_zone_exit"]),
        "short_zone_exit": bool(row["short_zone_exit"]),
        "dir1": int(row["dir1"]) if pd.notna(row.get("dir1")) else None,
        "atr1": float(row["atr1"]) if pd.notna(row.get("atr1")) else None,
        "factor1": float(cfg.get("factor1") or 1.0),
    }


def _live_ref_price(signals: dict[str, Any], ltp: float | None) -> float | None:
    """Current live price for TF bar — prefer LTP as running close, else last TF close."""
    if ltp is not None:
        try:
            return float(ltp)
        except (TypeError, ValueError):
            pass
    close = signals.get("close")
    if close is None:
        return None
    try:
        return float(close)
    except (TypeError, ValueError):
        return None


#: Trailing-stop modes this runner honours. ``_level`` (backtest_engine) returns
#: None for "ATR", which is why ATR is a *trail* mode only and never an SL/TGT
#: mode here — see ``validate_stop_modes``.
TRAIL_MODES = ("ATR", "%", "Pts")


def _trail_band(mode: str, *, ref: float, atr1: float | None, value: float) -> float | None:
    """Distance from the live price to the trail, per mode.

    The ratchet is identical for all three; only the band differs. Returns None
    when the mode cannot be priced (unknown mode, or ATR with no ATR to hand).
    """
    if mode == "ATR":
        if atr1 is None or float(atr1) <= 0:
            return None
        return float(atr1) * float(value)
    if mode == "Pts":
        return abs(float(value))
    if mode == "%":
        return abs(float(ref)) * abs(float(value)) / 100.0
    return None


def _update_trail_live(
    leg: dict[str, Any],
    *,
    side: PositionSide,
    ref: float,
    band: float,
) -> tuple[float, float | None]:
    """
    Dynamic trail from live TF price (not entry).
    Long: ref − band, ratchets up; Short: ref + band, ratchets down.

    The persisted field names (``atr_trail`` / ``atr_extreme``) predate ``%``
    and ``Pts`` support and are shared with premium_book's state — kept as-is so
    existing state files and the UI keep working.
    """
    candidate = ref - band if side == "long" else ref + band
    prev = leg.get("atr_trail")
    extreme = leg.get("atr_extreme")
    try:
        extreme_f = float(extreme) if extreme is not None else ref
    except (TypeError, ValueError):
        extreme_f = ref
    if side == "long":
        extreme_f = max(extreme_f, ref)
        trail = candidate if prev is None else max(float(prev), candidate)
    else:
        extreme_f = min(extreme_f, ref)
        trail = candidate if prev is None else min(float(prev), candidate)
    return round(trail, 4), round(extreme_f, 4)


def _live_st1_exit_line(
    signals: dict[str, Any],
    ltp: float | None,
    *,
    side: PositionSide,
) -> tuple[float | None, bool]:
    """
    Dynamic ST1 exit from live LTP as running close (Pine-style band update).
    Short exit = upper band; Long exit = lower band.
    """
    bar_upper = signals.get("st1_upper")
    bar_lower = signals.get("st1_lower")
    st1 = signals.get("st1") or signals.get("exit_line")
    if bar_upper is None or bar_lower is None:
        try:
            return (float(st1) if st1 is not None else None), False
        except (TypeError, ValueError):
            return None, False

    atr = float(signals.get("atr1") or 0)
    factor1 = float(signals.get("factor1") or 1.0)
    if ltp is None or atr <= 0:
        el = float(bar_upper) if side == "short" else float(bar_lower)
        return round(el, 4), False

    atr_v = atr * factor1
    prev_upper = float(bar_upper)
    prev_lower = float(bar_lower)
    prev_close = float(signals.get("close") or ltp)
    high = max(float(signals.get("high") or ltp), float(ltp))
    low = min(float(signals.get("low") or ltp), float(ltp))
    src = (high + low) / 2.0
    lower_basic = src - atr_v
    upper_basic = src + atr_v
    lower = lower_basic if lower_basic > prev_lower or prev_close < prev_lower else prev_lower
    upper = upper_basic if upper_basic < prev_upper or prev_close > prev_upper else prev_upper
    el = upper if side == "short" else lower
    return round(el, 4), True


def _long_entry_reason(cfg: dict[str, Any], signals: dict[str, Any], leg: dict[str, Any], *, prefix: str = "") -> tuple[bool, str]:
    """Long 3ST entry — ST1 bull zone + ADX on this leg's chart only."""
    style = cfg.get("reentry_style", "zone_active")
    entries = int(leg.get("entries_today") or 0)
    if entries == 0:
        if signals["long_entry"]:
            return True, f"{prefix}long_entry"
        if signals["long_ready"]:
            return True, f"{prefix}long_ready"
        return False, "no long entry"
    if style == "zone_active" and signals["long_ready"] and not signals["long_entry"]:
        return True, f"{prefix}long_reentry"
    if signals["long_entry"]:
        return True, f"{prefix}long_entry"
    return False, "no long signal"


def _short_entry_reason(cfg: dict[str, Any], signals: dict[str, Any], leg: dict[str, Any], *, prefix: str = "") -> tuple[bool, str]:
    """Short 3ST entry — ST1 bear zone + ADX on this leg's chart only."""
    style = cfg.get("reentry_style", "zone_active")
    entries = int(leg.get("entries_today") or 0)
    if entries == 0:
        if signals["short_entry"]:
            return True, f"{prefix}short_entry"
        if signals["short_ready"]:
            return True, f"{prefix}short_ready"
        return False, "no short entry"
    if style == "zone_active" and signals["short_ready"] and not signals["short_entry"]:
        return True, f"{prefix}short_reentry"
    if signals["short_entry"]:
        return True, f"{prefix}short_entry"
    return False, "no short signal"


def _entry_txn_from_reason(reason: str) -> tuple[str, PositionSide]:
    r = reason.lower()
    if "short" in r:
        return "SELL", "short"
    return "BUY", "long"


def _can_enter_ce(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
) -> tuple[bool, str]:
    if leg.get("blocked"):
        return False, "CE blocked"
    if leg.get("status") == "open":
        return False, "CE already open"
    if _same_bar_action_blocked(leg, signals):
        return False, "skipped_same_bar"
    ok, reason = _reentry_allowed_after_exit(leg, signals)
    if not ok:
        return False, reason
    trade_mode = cfg.get("trade_mode", "Both")
    if trade_mode == "ShortOnly":
        return False, "trade_mode ShortOnly"
    max_re = int(cfg.get("max_reentries_ce") or 1)
    max_entries = max_re + 1
    if int(leg.get("entries_today") or 0) >= max_entries:
        return False, "CE max entries reached"
    if trade_mode != "ShortSignalsOnly":
        ok, reason = _long_entry_reason(cfg, signals, leg)
        if ok:
            return True, reason
    ok, reason = _short_entry_reason(cfg, signals, leg)
    if ok:
        return True, reason
    return False, "no CE signal"


def _can_enter_pe(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
) -> tuple[bool, str]:
    if leg.get("blocked"):
        return False, "PE blocked"
    if leg.get("status") == "open":
        return False, "PE already open"
    if _same_bar_action_blocked(leg, signals):
        return False, "skipped_same_bar"
    ok, reason = _reentry_allowed_after_exit(leg, signals)
    if not ok:
        return False, reason
    trade_mode = cfg.get("trade_mode", "Both")
    if trade_mode == "LongOnly":
        return False, "trade_mode LongOnly"
    max_re = int(cfg.get("max_reentries_pe") or 1)
    max_entries = max_re + 1
    if int(leg.get("entries_today") or 0) >= max_entries:
        return False, "PE max entries reached"
    if trade_mode != "ShortSignalsOnly":
        ok, reason = _long_entry_reason(cfg, signals, leg)
        if ok:
            return True, reason
    ok, reason = _short_entry_reason(cfg, signals, leg)
    if ok:
        return True, reason
    return False, "no PE signal"


def _entry_exit_enabled(cfg: dict[str, Any]) -> bool:
    """Exit #1: timeframe close against entry (default on)."""
    return bool(cfg.get("entry_exit_enabled", True))


def _live_exit_fields(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    *,
    side: PositionSide,
    ltp: float | None,
) -> dict[str, Any]:
    """Refresh dynamic ATR trail + live ST1 exit onto the leg each tick."""
    patch: dict[str, Any] = {}
    ref = _live_ref_price(signals, ltp)
    live_st, st_live = _live_st1_exit_line(signals, ltp, side=side)
    if live_st is not None:
        patch["zone_exit_level"] = live_st
        patch["signal_st1"] = live_st
        patch["st1_live"] = st_live
    tsl_mode = str(cfg.get("tsl_mode") or "Off")
    if tsl_mode in TRAIL_MODES and ref is not None:
        atr = signals.get("atr1")
        band = _trail_band(
            tsl_mode,
            ref=ref,
            atr1=float(atr) if atr is not None else None,
            value=float(cfg.get("tsl_value") or 1),
        )
        if band is not None and band > 0:
            trail, extreme = _update_trail_live(leg, side=side, ref=ref, band=band)
            patch["atr_trail"] = trail
            patch["atr_extreme"] = extreme
            patch["atr_live_ref"] = round(ref, 4)
            patch["trail_mode"] = tsl_mode
            patch["trail_band"] = round(band, 4)
            if atr is not None:
                patch["signal_atr1"] = float(atr)
    return patch


def _should_exit_leg(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    force: bool,
    *,
    leg_key: LegKey,
) -> tuple[bool, str]:
    """
    Exit ladder (first hit wins):
      1. Entry — Short: TF close above entry / Long: TF close below entry
      2. ATR  — dynamic trail from live TF price ± ATR1×mult (not entry)
      3. ST1  — dynamic live ST1: Short above / Long below
    Plus optional SL/TGT and session force_exit.
    """
    if leg.get("status") != "open" or not leg_is_algo_managed(leg):
        return False, ""
    if _same_bar_action_blocked(leg, signals):
        return False, "skipped_same_bar"
    if force:
        return True, "force_exit"

    side = _leg_position_side(leg)
    ltp = _leg_ltp(leg)
    bar_close_only = _exit_on_bar_close_only(cfg)
    bar_close = signals.get("close")
    # Prefer algo fill; fall back to Kite average_price adopted on restore.
    entry_px = _effective_entry_price(leg)
    exit_line = leg.get("zone_exit_level")
    if exit_line is None:
        exit_line = signals.get("exit_line")

    # 1) Entry — adverse close on the configured timeframe
    if _entry_exit_enabled(cfg) and entry_px is not None and bar_close is not None:
        try:
            ep = float(entry_px)
            bc = float(bar_close)
            if side == "long" and bc < ep:
                return True, "entry_exit"
            if side == "short" and bc > ep:
                return True, "entry_exit"
        except (TypeError, ValueError):
            pass

    # 2) Trailing stop — live, ratcheted, from current TF/live price.
    #    ATR / % / Pts all land on the same stored trail; only the band differs.
    tsl_mode = str(cfg.get("tsl_mode") or "Off")
    if tsl_mode in TRAIL_MODES:
        atr_trail = leg.get("atr_trail")
        ref = _live_ref_price(signals, ltp)
        try:
            if atr_trail is not None and ref is not None:
                trail = float(atr_trail)
                # Bar-close mode: fire on TF close through trail; else LTP
                px = float(bar_close) if bar_close_only and bar_close is not None else ref
                if side == "long" and px <= trail:
                    return True, "atr_exit"
                if side == "short" and px >= trail:
                    return True, "atr_exit"
        except (TypeError, ValueError):
            pass

    # 3) ST1 zone break (bar signal and/or live line)
    if side == "long":
        if signals["long_zone_exit"]:
            return True, "long_zone_exit"
        if exit_line is not None and ltp is not None and ltp < float(exit_line):
            if bar_close_only:
                return False, "exit_deferred_ltp"
            return True, "long_zone_exit_ltp"
    else:
        if signals["short_zone_exit"]:
            return True, "short_zone_exit"
        if exit_line is not None and ltp is not None and ltp > float(exit_line):
            if bar_close_only:
                return False, "exit_deferred_ltp"
            return True, "short_zone_exit_ltp"

    # Optional SL / Target (still supported)
    sl_mode = str(cfg.get("sl_mode") or "Off")
    tgt_mode = str(cfg.get("tgt_mode") or "Off")
    if entry_px and ltp is not None:
        try:
            ltp_f = float(ltp)
            ep = float(entry_px)
            if sl_mode != "Off":
                sl = _level(ep, sl_mode, float(cfg["sl_value"]), side, "sl")  # type: ignore[arg-type]
                if sl is not None:
                    if side == "long" and ltp_f <= sl:
                        return True, "sl"
                    if side == "short" and ltp_f >= sl:
                        return True, "sl"
            if tgt_mode != "Off":
                tgt = _level(ep, tgt_mode, float(cfg["tgt_value"]), side, "tgt")  # type: ignore[arg-type]
                if tgt is not None:
                    if side == "long" and ltp_f >= tgt:
                        return True, "target"
                    if side == "short" and ltp_f <= tgt:
                        return True, "target"
        except Exception:
            pass
    return False, ""


def _should_exit_ce(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    force: bool,
) -> tuple[bool, str]:
    return _should_exit_leg(cfg, signals, leg, force, leg_key="ce")


def _should_exit_pe(
    cfg: dict[str, Any],
    signals: dict[str, Any],
    leg: dict[str, Any],
    force: bool,
) -> tuple[bool, str]:
    return _should_exit_leg(cfg, signals, leg, force, leg_key="pe")


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
    built = _order_leg_from_atm(cfg, leg_key, atm, spot)
    broker = _broker()
    _seed_paper_ltp(broker, built["exchange"], built["tradingsymbol"])

    tag = order_tag(leg_key, "entry")
    _, pos_side = _entry_txn_from_reason(reason)
    target_qty = -int(built["quantity"]) if pos_side == "short" else int(built["quantity"])
    result = place_leg_to_target(
        broker,
        built,
        target_qty=target_qty,
        order_type=str(cfg.get("order_type") or "MARKET"),
        tag=tag,
        product=str(cfg.get("product") or "MIS"),
    )
    if not result["ok"]:
        msg = friendly_kite_message(result.get("message", ""))
        append_log(f"{leg_key}_entry_failed", msg, {"reason": reason})
        raise RuntimeError(msg or "Order failed")

    # Entry price must be the fill, not a price near it: exit ladder rule #1
    # ("entry_exit") is a breakeven stop measured against this number.
    fill_px: float | None = None
    fill_source: EntryPriceSource | None = None
    raw = result.get("raw") or {}
    if raw.get("price") is not None:
        # PaperBroker books the position at this price — it is the fill.
        fill_px = float(raw["price"])
        fill_source = "order_fill"
    else:
        fill_px = _entry_fill_price(
            broker,
            result.get("order_id"),
            exchange=built["exchange"],
            tradingsymbol=built["tradingsymbol"],
        )
        fill_source = "order_avg" if fill_px is not None else None

    if fill_px is None:
        # Order still working (or orderbook unreadable). Hold an LTP snapshot so
        # the ladder has *something*, flagged so reconcile replaces it.
        try:
            fill_px = broker.ltp(built["exchange"], built["tradingsymbol"])
            fill_source = "ltp_proxy"
        except Exception:
            fill_px = None
            fill_source = None
        append_log(
            f"{leg_key}_entry_fill_pending",
            "No completed fill yet — entry_price is an LTP proxy until reconcile",
            {"order_id": result.get("order_id"), "ltp_proxy": fill_px},
        )

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
        "entry_price_source": fill_source,
        # Stale average from the previous trade on this leg must not outrank a
        # fresh ltp_proxy in _effective_entry_price — reconcile refills it.
        "broker_average_price": None,
        "entry_at": datetime.now().isoformat(timespec="seconds"),
        "entry_order_id": result.get("order_id"),
        "entry_side": "SELL" if pos_side == "short" else "BUY",
        "position_side": pos_side,
        "managed_by": "algo",
        "broker_qty": int(built["quantity"]),
        "last_action": reason,
        "entries_today": entries,
        "reentries_used": reentries,
        "last_action_bar_ts": leg.get("signal_bar_ts"),
        "last_exit_bar_ts": None,
        "atr_trail": None,
        "atr_extreme": None,
        "atr_live_ref": None,
        "st1_live": False,
    }
    save_state({leg_key: leg_patch})
    append_log(
        f"{leg_key}_entry",
        reason,
        {"strike": built["strike"], "symbol": built["tradingsymbol"], "order_id": result.get("order_id")},
    )
    return leg_patch


def _flat_leg_exit_fields(leg: dict[str, Any], reason: str) -> dict[str, Any]:
    """State an exited leg is left in — the one definition of "flat after exit".

    Shared by both branches of ``_exit_leg`` **and** by ``decide()``, which has
    to model the exit locally: an exit stamps ``last_action_bar_ts`` /
    ``last_exit_bar_ts`` with this bar, and that is what makes the same-bar
    re-entry guard fire for an entry decided later in the same tick. If this
    drifts from what ``_exit_leg`` writes, the churn guards silently weaken —
    which is the 2026-07-14 whipsaw incident's failure mode.
    """
    return {
        "status": "flat",
        "last_action": reason,
        "entry_price": None,
        "entry_price_source": None,
        "broker_average_price": None,
        "entry_at": None,
        "entry_order_id": None,
        "broker_qty": None,
        "tradingsymbol": None,
        "exchange": None,
        "strike": None,
        "last_action_bar_ts": leg.get("signal_bar_ts"),
        "last_exit_bar_ts": leg.get("signal_bar_ts"),
        "atr_trail": None,
        "atr_extreme": None,
        "atr_live_ref": None,
        "st1_live": False,
    }


def _exit_leg(leg_key: LegKey, cfg: dict[str, Any], reason: str) -> dict[str, Any]:
    state = get_state()
    leg = state[leg_key]
    if not leg_is_algo_managed(leg):
        raise RuntimeError(
            f"{leg_key.upper()} leg is not algo-managed — manual Kite positions are never closed by 3ST."
        )
    broker = _broker()

    resolved = _find_broker_leg_position(
        broker,
        cfg,
        leg_key,
        tradingsymbol=leg.get("tradingsymbol"),
        exchange=leg.get("exchange"),
        leg=leg,
    )
    if not resolved:
        if leg.get("status") != "open":
            return leg
        flat = _flat_leg_exit_fields(leg, reason)
        save_state({leg_key: {**leg, **flat}})
        append_log(f"{leg_key}_exit_skipped", "Broker already flat", {"reason": reason})
        return {**leg, **flat}

    exch, sym, broker_qty = resolved
    close_tx = _close_transaction_for_qty(broker_qty)
    if close_tx is None:
        return leg

    close_leg = {
        "tradingsymbol": sym,
        "exchange": exch,
        "quantity": abs(broker_qty),
    }
    tag = order_tag(leg_key, "exit")
    result = place_leg_to_target(
        broker,
        close_leg,
        target_qty=0,
        order_type=str(cfg.get("order_type") or "MARKET"),
        tag=tag,
        product=str(cfg.get("product") or "MIS"),
    )
    if not result["ok"]:
        msg = friendly_kite_message(result.get("message", ""))
        append_log(f"{leg_key}_exit_failed", msg, {"reason": reason, "broker_qty": broker_qty})
        raise RuntimeError(msg or "Exit order failed")

    close_tx = (result.get("raw") or {}).get("transaction_type") or _close_transaction_for_qty(broker_qty)

    flat = {**_flat_leg_exit_fields(leg, reason), "managed_by": None}
    save_state({leg_key: {**leg, **flat}})
    append_log(
        f"{leg_key}_exit",
        reason,
        {"order_id": result.get("order_id"), "transaction_type": close_tx, "qty": abs(broker_qty)},
    )
    return {**leg, **flat}


def _format_tick_error(exc: Exception) -> str:
    msg = str(exc)
    lower = msg.lower()
    if "proxy" in lower or "staticip" in lower or "getaddrinfo failed" in lower:
        return (
            "Kite API unreachable via staticip proxy (DNS/network). "
            "If KITE_ALLOWED_EGRESS_IP is on this PC, 3ST binds that IPv6 directly — restart API. "
            "Otherwise fix STATICIP_HOST or set KITE_USE_STATICIP_PROXY=0."
        )
    if len(msg) > 220:
        return msg[:217] + "..."
    return msg


def _resolve_tick_spot(
    underlying: str, state: dict[str, Any], step: int
) -> tuple[float | None, str | None]:
    """Futures LTP for ATM — reject chain-mid glitches; fall back to last good spot."""
    from options.chain import get_index_spot_detail

    raw, spot_err = get_index_spot_detail(underlying)
    prev = state.get("last_spot")
    if prev is not None and not _spot_plausible_for_underlying(underlying, float(prev)):
        append_log(
            "spot_underlying_mismatch",
            f"Dropped last_spot {prev} — wrong scale for {underlying}",
            {"last": float(prev), "underlying": underlying},
        )
        prev = None
    if raw is not None and prev is not None:
        max_jump = float(step) * 5
        if abs(float(raw) - float(prev)) > max_jump:
            rejected = float(raw)
            append_log(
                "spot_sanity_reject",
                f"Ignored spot {rejected} (last {float(prev)}, max jump {max_jump})",
                {"raw": rejected, "last": float(prev), "underlying": underlying},
            )
            raw = None
            if spot_err is None:
                spot_err = f"Spot {rejected} rejected (implausible jump from {float(prev)})"
    if raw is None and prev is not None:
        append_log("spot_fallback", f"Using last_spot {prev}", {"underlying": underlying})
        return float(prev), None
    if raw is None:
        return None, spot_err
    return raw, None


def _spot_state_stale(cfg: dict[str, Any], state: dict[str, Any]) -> bool:
    """True when cached spot/ATM belongs to a different underlying (e.g. NIFTY -> CRUDEOIL)."""
    underlying = str(cfg.get("underlying") or "NIFTY")
    state_u = state.get("state_underlying")
    if state_u is not None and str(state_u) != underlying:
        return True
    prev = state.get("last_spot")
    if prev is not None and not _spot_plausible_for_underlying(underlying, float(prev)):
        return True
    # Never treat a glitchy live fetch as stale state — only wrong-scale cached spot.
    return False


def _strike_step_for(underlying: str) -> int:
    meta = INDEX_OPTIONS.get(str(underlying).upper(), {})
    return int(meta.get("strike_step") or 50)


def _sanitize_state_for_display(cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Hide cross-underlying spot/ATM in status; optionally show a live quote."""
    if not _spot_state_stale(cfg, state):
        return dict(state)
    out = dict(state)
    out["last_spot"] = None
    out["current_atm"] = None
    out["prev_atm"] = None
    out["spot_stale"] = True
    ce = dict(out.get("ce") or {})
    pe = dict(out.get("pe") or {})
    ce["signal_strike"] = None
    pe["signal_strike"] = None
    out["ce"] = ce
    out["pe"] = pe
    underlying = str(cfg.get("underlying") or "NIFTY")
    try:
        spot, _err = get_index_spot_detail(underlying)
        if spot is not None and _spot_plausible_for_underlying(underlying, float(spot)):
            step = _strike_step_for(underlying)
            out["last_spot"] = float(spot)
            out["current_atm"] = atm_strike(float(spot), step)
            out.pop("spot_stale", None)
            out["spot_live_only"] = True
    except Exception:
        pass
    return out


def _ensure_state_underlying(cfg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    underlying = str(cfg.get("underlying") or "NIFTY")
    if not _spot_state_stale(cfg, state):
        if state.get("state_underlying") is None:
            save_state({"state_underlying": underlying})
        return state
    old_u = state.get("state_underlying") or "(legacy)"
    clear_spot_state_for_underlying(
        underlying,
        reason=f"Stale spot state for {underlying} (was {old_u})",
        old_underlying=str(old_u) if old_u != "(legacy)" else None,
    )
    return get_state()


def _ensure_config_expiry(cfg: dict[str, Any]) -> dict[str, Any]:
    """Persist nearest valid option expiry when config has none or a stale date."""
    from options.chain import resolve_expiry

    underlying = str(cfg.get("underlying") or "NIFTY")
    old = str(cfg.get("expiry") or "")
    resolved = resolve_expiry(underlying, old or None)
    if resolved and resolved != old:
        return save_config({"expiry": resolved})
    return cfg


DecisionKind = Literal["purge", "exit", "enter", "note", "error"]

#: Entry gate hook. Called with the leg, its 3ST entry reason and its signal
#: row; returns ``(allow, note)``. ``decide()`` treats ``allow=False`` as "this
#: entry does not happen", logging ``{leg}_entry_gated`` with the note.
#:
#: This is the seam an external filter (e.g. GEX regime / flip distance) plugs
#: into. It can only ever *suppress* an entry the 3ST signal already produced —
#: it cannot create one, and it is never consulted for exits, so a bug in a gate
#: cannot leave a position unmanaged.
EntryGate = Callable[[str, str, dict[str, Any]], tuple[bool, str]]


@dataclass(frozen=True)
class Decision:
    """One thing the tick has resolved to do (or to record).

    ``decide()`` returns these in application order — purges, then exits, then
    entries — and ``tick()`` is the only place that performs the side effect.
    That split is what makes the ladder testable without a broker: everything
    below reads state and signals and returns data.
    """

    leg_key: LegKey
    kind: DecisionKind
    reason: str
    log_event: str | None = None
    log_detail: str = ""
    log_extra: dict[str, Any] = field(default_factory=dict)
    patch: dict[str, Any] = field(default_factory=dict)


def _purge_decision(leg_key: LegKey, leg: dict[str, Any], underlying: str) -> Decision | None:
    """Flatten a leg holding a symbol from another underlying.

    Happens when e.g. a watchlist CRUDEOIL position is synced into the CE leg.
    """
    sym = str(leg.get("tradingsymbol") or "").upper()
    if leg.get("status") != "open" or not sym:
        return None
    if tradingsymbol_matches_underlying(sym, underlying):
        return None
    return Decision(
        leg_key=leg_key,
        kind="purge",
        reason="purged_foreign_symbol",
        patch={
            "status": "flat",
            "tradingsymbol": None,
            "exchange": None,
            "strike": None,
            "entry_price": None,
            "entry_price_source": None,
            "broker_average_price": None,
            "entry_at": None,
            "entry_order_id": None,
            "last_action": "purged_foreign_symbol",
        },
    )


def _exit_decision(
    leg_key: LegKey,
    cfg: dict[str, Any],
    leg: dict[str, Any],
    signals: dict[str, Any],
    *,
    force: bool,
    arm: dict[str, Any],
) -> Decision | None:
    exit_fn = _should_exit_ce if leg_key == "ce" else _should_exit_pe
    try:
        should_x, x_reason = exit_fn(cfg, signals, leg, force)
    except Exception as exc:
        return Decision(leg_key, "error", f"{leg_key} exit: {exc}")

    if x_reason == "exit_deferred_ltp":
        return Decision(
            leg_key,
            "note",
            x_reason,
            log_event=f"{leg_key}_exit_deferred_ltp",
            log_detail="LTP crossed ST zone — waiting for bar close",
            log_extra={
                "ltp": leg.get("ltp"),
                "exit_line": signals.get("exit_line"),
                "signal_bar_ts": _bar_ts_key(signals.get("ts")),
            },
        )
    if x_reason == "skipped_same_bar":
        return Decision(
            leg_key,
            "note",
            x_reason,
            log_event=f"{leg_key}_skipped_same_bar",
            log_detail="Exit deferred — already acted this bar",
            log_extra={"signal_bar_ts": _bar_ts_key(signals.get("ts"))},
        )
    if not should_x:
        return None

    if arm.get("mode") == "live" and not arm.get("armed"):
        return Decision(
            leg_key,
            "note",
            x_reason,
            log_event=f"{leg_key}_exit_blocked_disarm",
            log_detail="Exit signal active — ARM required to send Kite order",
            log_extra={
                "reason": x_reason,
                "signal_bar_ts": _bar_ts_key(signals.get("ts")),
                "ltp": leg.get("ltp"),
            },
        )
    return Decision(leg_key, "exit", x_reason)


def _entry_decision(
    leg_key: LegKey,
    cfg: dict[str, Any],
    leg: dict[str, Any],
    signals: dict[str, Any],
    *,
    atm: float,
    gate: EntryGate | None,
) -> Decision | None:
    enter_fn = _can_enter_ce if leg_key == "ce" else _can_enter_pe
    try:
        ok, reason = enter_fn(cfg, signals, leg)
    except Exception as exc:
        return Decision(leg_key, "error", f"{leg_key} entry: {exc}")

    if not ok and reason == "reentry_cooldown":
        return Decision(
            leg_key,
            "note",
            reason,
            log_event=f"{leg_key}_reentry_cooldown",
            log_detail="Re-entry blocked until next bar closes",
            log_extra={
                "last_exit_bar_ts": leg.get("last_exit_bar_ts"),
                "signal_bar_ts": _bar_ts_key(signals.get("ts")),
            },
        )
    if not ok and reason == "skipped_same_bar":
        return Decision(
            leg_key,
            "note",
            reason,
            log_event=f"{leg_key}_skipped_same_bar",
            log_detail="Entry deferred — already acted this bar",
            log_extra={"signal_bar_ts": _bar_ts_key(signals.get("ts"))},
        )
    if not ok:
        return None

    signal_log = {
        "log_event": f"{leg_key}_entry_signal",
        "log_detail": reason,
        "log_extra": {
            "atm": atm,
            "signal_bar_ts": _bar_ts_key(signals.get("ts")),
            "close": signals.get("close"),
        },
    }

    if gate is not None:
        try:
            allow, note = gate(leg_key, reason, signals)
        except Exception as exc:
            # A gate must never be able to place a trade by failing. Treat any
            # error as "no opinion" and let the 3ST signal stand.
            allow, note = True, f"gate error ignored: {exc}"
        if not allow:
            return Decision(
                leg_key,
                "note",
                reason,
                log_event=f"{leg_key}_entry_gated",
                log_detail=note or "entry suppressed by gate",
                log_extra={**signal_log["log_extra"], "gate_note": note},
            )

    if str(cfg.get("execution_mode") or "auto") == "confirm":
        # Operator ships it by hand from the taskbar; log the signal only.
        return Decision(leg_key, "note", reason, **signal_log)

    return Decision(leg_key, "enter", reason, **signal_log)


def decide(
    cfg: dict[str, Any],
    state: dict[str, Any],
    leg_signals: dict[str, dict[str, Any]],
    *,
    force: bool,
    atm: float,
    arm: dict[str, Any],
    gate: EntryGate | None = None,
) -> list[Decision]:
    """Resolve a tick into an ordered list of actions. Pure — no broker, no I/O.

    Order is load-bearing: purges flatten a foreign leg before exits are judged,
    and exits are applied to the working state before entries are judged, so an
    exit and a re-entry on the same bar cannot both happen.
    """
    out: list[Decision] = []
    working = {
        "ce": dict(state.get("ce") or {}),
        "pe": dict(state.get("pe") or {}),
    }

    underlying = str(cfg.get("underlying") or "NIFTY").upper()
    for leg_key in ("ce", "pe"):
        purge = _purge_decision(leg_key, working[leg_key], underlying)  # type: ignore[arg-type]
        if purge is not None:
            out.append(purge)
            working[leg_key] = {**working[leg_key], **purge.patch}

    for leg_key in ("ce", "pe"):
        decision = _exit_decision(
            leg_key,  # type: ignore[arg-type]
            cfg,
            working[leg_key],
            leg_signals[leg_key],
            force=force,
            arm=arm,
        )
        if decision is None:
            continue
        out.append(decision)
        if decision.kind == "exit":
            working[leg_key] = {
                **working[leg_key],
                **_flat_leg_exit_fields(working[leg_key], decision.reason),
                "managed_by": None,
            }

    if force:
        return out

    # The dual-open check reads the *pre-entry* snapshot, matching the loop this
    # replaced. See test_dual_open_pins_current_stale_read_behaviour — with
    # allow_dual_open False and both legs flat, both can still enter on one
    # tick. Pinned rather than endorsed; changing it is a live-behaviour call.
    entry_snapshot = {k: dict(v) for k, v in working.items()}
    allow_dual = bool(cfg.get("allow_dual_open", True))
    for leg_key in ("ce", "pe"):
        other = "pe" if leg_key == "ce" else "ce"
        if not allow_dual and entry_snapshot[other].get("status") == "open":
            continue
        decision = _entry_decision(
            leg_key,  # type: ignore[arg-type]
            cfg,
            working[leg_key],
            leg_signals[leg_key],
            atm=atm,
            gate=gate,
        )
        if decision is not None:
            out.append(decision)

    return out


def apply_decisions(
    decisions: list[Decision],
    cfg: dict[str, Any],
    *,
    atm: float,
    spot: float,
) -> list[str]:
    """Perform what ``decide()`` resolved. The only side-effecting half.

    Returns the error strings the tick reports, in the same
    ``"{leg} exit: {msg}"`` / ``"{leg} entry: {msg}"`` shape as before.
    """
    errors: list[str] = []
    #: ``decide()`` assumes an exit succeeds when it judges the entry that
    #: follows. If the order actually failed the leg is still open, so an entry
    #: for it must not proceed. The same-bar guard already blocks this (an exit
    #: stamps ``last_action_bar_ts``), but relying on that emergently is how the
    #: churn guards get weakened later — so refuse it explicitly.
    failed_exit: set[str] = set()

    for d in decisions:
        if d.kind == "error":
            errors.append(d.reason)
            continue

        if d.kind == "enter" and d.leg_key in failed_exit:
            append_log(
                f"{d.leg_key}_entry_skipped_after_failed_exit",
                "Exit order failed this tick — leg may still be open",
                {"reason": d.reason},
            )
            continue

        if d.log_event:
            append_log(d.log_event, d.log_detail, d.log_extra)

        if d.kind == "purge":
            save_state({d.leg_key: {**get_state()[d.leg_key], **d.patch}})
        elif d.kind == "exit":
            try:
                _exit_leg(d.leg_key, cfg, d.reason)
            except Exception as exc:
                errors.append(f"{d.leg_key} exit: {exc}")
                failed_exit.add(d.leg_key)
        elif d.kind == "enter":
            try:
                _enter_leg(d.leg_key, cfg, atm, spot, d.reason)
            except Exception as exc:
                errors.append(f"{d.leg_key} entry: {exc}")
                append_log("error", friendly_kite_message(str(exc)), {"leg": d.leg_key})
    return errors


def tick() -> dict[str, Any]:
    """Single scheduler tick — scan signals, roll ATM, enter/exit legs."""
    cfg = _ensure_config_expiry(get_config())
    state = reset_daily_state_if_needed(_today_str())
    state = _ensure_state_underlying(cfg, state)

    if state.get("runner") != "running":
        return {"ok": True, "skipped": True, "reason": "runner stopped"}

    if not session_status().get("authenticated"):
        append_log("error", "Kite session required")
        return {"ok": False, "error": "Kite session required"}

    broker = _broker()
    reconcile_patches, broker_mismatches, _orphans = _reconcile_broker_legs(cfg, state, broker)
    if reconcile_patches:
        save_state(reconcile_patches)
        state = get_state()
    paper_patches = _paper_broker_qty_patches(cfg, state, broker)
    if paper_patches:
        save_state(paper_patches)
        state = get_state()

    underlying = cfg.get("underlying") or "NIFTY"
    timeframe = cfg.get("timeframe") or "5min"
    entry_start = cfg.get("entry_start") or "09:20"
    session_end = cfg.get("session_end") or "15:40"
    force_exit = cfg.get("force_exit") or "15:20"
    system_mode = cfg.get("system_mode") or "Intraday"

    now = datetime.now()
    in_session = system_mode == "Positional" or _in_window(pd.Timestamp(now), cfg.get("session_start", "09:15"), session_end)
    force = system_mode == "Intraday" and _in_window(pd.Timestamp(now), force_exit, session_end)

    if not in_session and not force:
        save_state({"last_tick_at": now.isoformat(timespec="seconds")})
        return {"ok": True, "skipped": True, "reason": "outside session"}

    spot, spot_err = _resolve_tick_spot(underlying, state, INDEX_OPTIONS[underlying]["strike_step"])
    if spot is None:
        msg = spot_err or f"No spot for {underlying}"
        append_log("error", msg)
        return {"ok": False, "error": msg}

    step = INDEX_OPTIONS[underlying]["strike_step"]
    new_atm = atm_strike(spot, step)
    prev_atm = state.get("current_atm")
    roll_dir = None
    if prev_atm is not None and new_atm != prev_atm:
        atm_jump = abs(float(new_atm) - float(prev_atm))
        if atm_jump > float(step) * 5:
            append_log(
                "atm_sanity_reject",
                f"Blocked ATM jump {prev_atm} -> {new_atm} (spot {spot})",
                {"prev_atm": prev_atm, "new_atm": new_atm, "spot": spot},
            )
            new_atm = float(prev_atm)
        else:
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
    ce_open = state["ce"].get("status") == "open"
    pe_open = state["pe"].get("status") == "open"
    ce_side = _leg_position_side(state["ce"]) if ce_open else "long"
    pe_side = _leg_position_side(state["pe"]) if pe_open else "long"
    ce_live = (
        _live_exit_fields(cfg, ce_signals, state["ce"], side=ce_side, ltp=ce_ltp)
        if ce_open
        else {}
    )
    pe_live = (
        _live_exit_fields(cfg, pe_signals, state["pe"], side=pe_side, ltp=pe_ltp)
        if pe_open
        else {}
    )
    save_state({
        "ce": {
            **state["ce"],
            "signal_strike": ce_strike,
            "signal_bar_ts": _bar_ts_key(ce_signals["ts"]),
            "signal_close": ce_signals["close"],
            "signal_st1": ce_live.get("signal_st1", ce_signals.get("st1")),
            "signal_atr1": ce_live.get("signal_atr1", ce_signals.get("atr1")),
            "zone_exit_level": ce_live.get("zone_exit_level", ce_signals.get("exit_line")),
            "zone_exit_label": ce_signals.get("exit_label"),
            "zone_exit_triggered": ce_open and _zone_exit_bar_triggered(ce_signals, ce_side),
            "position_side": ce_side if ce_open else state["ce"].get("position_side"),
            "long_ready": ce_signals["long_ready"],
            "long_entry": ce_signals["long_entry"],
            "short_ready": ce_signals["short_ready"],
            "short_entry": ce_signals["short_entry"],
            "ltp": ce_ltp,
            **ce_live,
        },
        "pe": {
            **state["pe"],
            "signal_strike": pe_strike,
            "signal_bar_ts": _bar_ts_key(pe_signals["ts"]),
            "signal_close": pe_signals["close"],
            "signal_st1": pe_live.get("signal_st1", pe_signals.get("st1")),
            "signal_atr1": pe_live.get("signal_atr1", pe_signals.get("atr1")),
            "zone_exit_level": pe_live.get("zone_exit_level", pe_signals.get("exit_line")),
            "zone_exit_label": pe_signals.get("exit_label"),
            "zone_exit_triggered": pe_open and _zone_exit_bar_triggered(pe_signals, pe_side),
            "position_side": pe_side if pe_open else state["pe"].get("position_side"),
            "long_ready": pe_signals["long_ready"],
            "long_entry": pe_signals["long_entry"],
            "short_ready": pe_signals["short_ready"],
            "short_entry": pe_signals["short_entry"],
            "ltp": pe_ltp,
            **pe_live,
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

    decisions = decide(
        cfg,
        state,
        leg_signals,
        force=force,
        atm=new_atm,
        arm=get_arm_state(),
    )
    errors = apply_decisions(decisions, cfg, atm=new_atm, spot=spot)

    return {
        "ok": len(errors) == 0,
        "spot": spot,
        "atm": new_atm,
        "last_signal": last_sig,
        "broker_mismatches": broker_mismatches,
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


def price_stop_summary(cfg: dict[str, Any]) -> dict[str, Any]:
    """Which price-based stops are armed, and what is left if none are.

    Reported rather than enforced: whether to run without a hard stop is the
    operator's call, but it should never be a silent one. With all three off the
    only exits are ``entry_exit`` (breakeven), the ST1 zone break and the
    session force-exit — every one of them on bar close, so a fast move against
    an open leg is unmanaged for up to one timeframe bar with no price floor.
    """
    sl = str(cfg.get("sl_mode") or "Off")
    tgt = str(cfg.get("tgt_mode") or "Off")
    tsl = str(cfg.get("tsl_mode") or "Off")
    armed = [
        f"SL {sl} {cfg.get('sl_value')}" if sl != "Off" else None,
        f"TSL {tsl} {cfg.get('tsl_value')}" if tsl != "Off" else None,
        f"TGT {tgt} {cfg.get('tgt_value')}" if tgt != "Off" else None,
    ]
    armed_list = [a for a in armed if a]
    remaining = ["force_exit " + str(cfg.get("force_exit") or "")]
    if _entry_exit_enabled(cfg):
        remaining.insert(0, "entry_exit (breakeven, bar close)")
    remaining.insert(-1, "ST1 zone break (bar close)")
    return {
        "has_price_stop": bool(sl != "Off" or tsl != "Off"),
        "sl_mode": sl,
        "tgt_mode": tgt,
        "tsl_mode": tsl,
        "armed": armed_list,
        "remaining_exits": remaining,
        "bar_close_only": _exit_on_bar_close_only(cfg),
        "timeframe": str(cfg.get("timeframe") or "5min"),
    }


def start_runner() -> dict[str, Any]:
    cfg = _ensure_config_expiry(get_config())
    if not cfg.get("expiry"):
        raise RuntimeError("Set expiry before starting — no option expiries for underlying")

    # Never let "no hard stop" be a silent condition on an armed live start.
    stops = price_stop_summary(cfg)
    if not stops["has_price_stop"]:
        detail = (
            f"No price-based stop configured (sl_mode/tsl_mode both Off) — "
            f"exits are {', '.join(stops['remaining_exits'])}, all on "
            f"{stops['timeframe']} bar close"
        )
        append_log("no_price_stop", detail, stops)
        if get_arm_state().get("mode") == "live" and get_arm_state().get("armed"):
            log_event(_log, logging.WARNING, "rolling_straddle_no_price_stop", **stops)

    reset_daily_state_if_needed(_today_str())
    save_state({"runner": "running", "scheduler_running": True})
    if session_status().get("authenticated") and get_arm_state().get("mode") == "live":
        state = get_state()
        broker = _broker()
        try:
            patches, mismatches, orphans = _reconcile_broker_legs(cfg, state, broker)
            if patches:
                save_state(patches)
            if mismatches:
                append_log("reconcile_on_start", "; ".join(mismatches))
            if orphans:
                append_log(
                    "orphans_on_start",
                    f"{len(orphans)} unlinked broker leg(s)",
                    {"orphans": [o.get("tradingsymbol") for o in orphans]},
                )
        except Exception as exc:
            append_log("reconcile_on_start", f"Skipped: {_format_tick_error(exc)}")
    append_log("runner_start", "Rolling straddle started")
    return {"ok": True, "runner": "running", "config": get_config(), "state": get_state()}


def stop_runner() -> dict[str, Any]:
    save_state({"runner": "stopped", "scheduler_running": False})
    append_log("runner_stop", "Rolling straddle stopped")
    return {"ok": True, "runner": "stopped", "state": get_state()}


def close_leg(leg: LegKey) -> dict[str, Any]:
    cfg = get_config()
    return _exit_leg(leg, cfg, "manual_close")


def unlink_leg(leg_key: LegKey) -> dict[str, Any]:
    """Stop 3ST monitoring without placing a Kite exit order."""
    state = get_state()
    leg = dict(state.get(leg_key) or {})
    if leg.get("status") != "open":
        return {"ok": True, "skipped": True, "reason": "leg already flat", "state": get_state()}
    sym = leg.get("tradingsymbol")
    flat = _flat_leg_from_broker_sync(leg, reason="unlinked")
    flat["entries_today"] = leg.get("entries_today")
    flat["reentries_used"] = leg.get("reentries_used")
    save_state({leg_key: flat})
    append_log(
        f"{leg_key}_unlinked",
        "Stopped 3ST monitoring — Kite position unchanged",
        {"symbol": sym, "exchange": leg.get("exchange")},
    )
    return {"ok": True, "state": get_state()}


def dismiss_leg_signal(leg_key: LegKey) -> dict[str, Any]:
    """Clear pending entry signal without placing an order."""
    state = get_state()
    leg = dict(state.get(leg_key) or {})
    save_state(
        {
            leg_key: {
                **leg,
                "blocked": True,
                "long_ready": False,
                "short_ready": False,
                "long_entry": False,
                "short_entry": False,
            }
        }
    )
    append_log(f"{leg_key}_dismissed", "Pending entry dismissed")
    return {"ok": True, "state": get_state()}


def ship_leg_entry(leg_key: LegKey) -> dict[str, Any]:
    """Place a pending Rolling Straddle entry (confirm mode)."""
    cfg = get_config()
    state = get_state()
    leg = dict(state.get(leg_key) or {})
    if leg.get("status") == "open":
        return {"ok": True, "skipped": True, "reason": "leg already open", "state": get_state()}
    if leg.get("blocked"):
        raise RuntimeError(f"{leg_key.upper()} entry dismissed — unblock by restarting runner or next session")

    signals = {
        "long_entry": bool(leg.get("long_entry")),
        "short_entry": bool(leg.get("short_entry")),
        "long_ready": bool(leg.get("long_ready")),
        "short_ready": bool(leg.get("short_ready")),
    }
    enter_fn = _can_enter_ce if leg_key == "ce" else _can_enter_pe
    ok, reason = enter_fn(cfg, signals, leg)
    if not ok:
        raise RuntimeError(f"No entry signal ready for {leg_key.upper()}: {reason}")

    spot = float(state.get("last_spot") or 0)
    atm = float(state.get("current_atm") or spot)
    if not atm:
        raise RuntimeError("ATM not available — wait for next tick")
    _enter_leg(leg_key, cfg, atm, spot, reason)  # type: ignore[arg-type]
    append_log(f"{leg_key}_shipped", "Manual ship from execution queue", {"reason": reason})
    return {"ok": True, "state": get_state()}


def adopt_leg(leg_key: LegKey) -> dict[str, Any]:
    """Link an open broker leg to Rolling Straddle exit monitoring."""
    if get_arm_state().get("mode") != "live":
        raise RuntimeError("Switch to Live mode before adopting Kite positions")
    if not session_status().get("authenticated"):
        raise RuntimeError("Kite session required")

    cfg = get_config()
    broker = _broker()
    state = get_state()
    leg = dict(state.get(leg_key) or {})
    if leg.get("status") == "open" and leg_is_algo_managed(leg):
        return {"ok": True, "message": "Leg already managed", "state": get_state()}

    pos_map, positions_ok = _broker_positions_map(broker)
    if not positions_ok:
        raise RuntimeError("Could not read Kite positions")

    detail, _ = _broker_positions_detail(broker)
    resolved = _resolve_live_broker_leg(
        broker, cfg, leg_key, leg, pos_map=pos_map, detail=detail
    )
    if not resolved:
        scanned = _scan_broker_for_rolling_leg(broker, cfg, leg_key, detail=detail)
        if not scanned:
            raise RuntimeError(f"No open broker position found for {leg_key.upper()} leg")
        resolved = scanned

    exch, sym, qty, entry_px = resolved
    if entry_px is None:
        entry_px = _broker_avg_price(broker, exch, sym, detail=detail)
    patch = _leg_patch_from_broker(
        leg,
        exch=exch,
        sym=sym,
        qty=qty,
        entry_px=entry_px,
        last_action="adopted",
    )
    if not patch.get("entry_at"):
        patch["entry_at"] = datetime.now().isoformat(timespec="seconds")
    save_state({leg_key: patch})
    append_log(
        f"{leg_key}_adopted",
        "Linked for 3ST exit monitoring",
        {"symbol": sym, "qty": qty, "exchange": exch},
    )
    return {"ok": True, "state": get_state()}


def close_all() -> dict[str, Any]:
    cfg = get_config()
    out: dict[str, Any] = {"ok": True, "closed": []}
    state = get_state()
    for leg_key in ("ce", "pe"):
        leg = state[leg_key]
        if leg.get("status") == "open" and leg_is_algo_managed(leg):
            try:
                _exit_leg(leg_key, cfg, "close_all")  # type: ignore[arg-type]
                out["closed"].append(leg_key)
            except Exception as e:
                out["ok"] = False
                out.setdefault("errors", []).append(f"{leg_key}: {e}")
    append_log("close_all", str(out.get("closed", [])))
    return out


def _leg_exit_params(
    leg_key: LegKey,
    cfg: dict[str, Any],
    leg: dict[str, Any],
) -> dict[str, Any]:
    """Exit ladder for UI — always list Entry → ATR → ST1 when applicable."""
    if leg.get("status") != "open":
        return {}

    side = _leg_position_side(leg)
    ltp = leg.get("ltp")
    entry = _effective_entry_price(leg)
    entry_source = leg.get("entry_price_source")
    if _entry_price_is_proxy(leg) and leg.get("broker_average_price") is not None:
        # _effective_entry_price took the broker average over the proxy.
        entry_source = "kite_avg"
    if entry_source is None:
        # Legacy state, written before entry_price_source existed.
        entry_source = (
            "fill"
            if leg.get("entry_price") is not None
            else ("kite_avg" if leg.get("broker_average_price") is not None else None)
        )
    exit_line = leg.get("zone_exit_level")
    exit_label = leg.get("zone_exit_label") or "ST1"
    force_hhmm = str(cfg.get("force_exit") or "")
    atr1 = leg.get("signal_atr1")
    signal_close = leg.get("signal_close")
    tf = str(cfg.get("timeframe") or "5min")

    params: dict[str, Any] = {
        "position_side": side,
        "trade_side_label": "Long" if side == "long" else "Short",
        "zone_exit_label": exit_label,
        "zone_exit_level": exit_line,
        "zone_exit_triggered": bool(leg.get("zone_exit_triggered")),
        "st1": leg.get("signal_st1"),
        "signal_close": signal_close,
        "force_exit": cfg.get("force_exit"),
        "session_end": cfg.get("session_end"),
        "timeframe": tf,
        "st_method": cfg.get("st_method"),
        "entry_exit_enabled": _entry_exit_enabled(cfg),
        "atr_tsl_enabled": str(cfg.get("tsl_mode") or "Off") == "ATR",
        "trail_enabled": str(cfg.get("tsl_mode") or "Off") in TRAIL_MODES,
        "trail_mode": str(cfg.get("tsl_mode") or "Off"),
        "force_exit_due": _time_reached(force_hhmm) if force_hhmm else False,
        "entry_source": entry_source,
        "exit_levels": [],
    }

    levels: list[dict[str, Any]] = []

    # 1) Entry (algo fill or Kite average_price fallback)
    if _entry_exit_enabled(cfg):
        if entry is not None:
            ep = float(entry)
            src_note = _ENTRY_SOURCE_NOTES.get(str(entry_source), "")
            if side == "long":
                rule = f"Long: {tf} close below entry {ep:.2f}{src_note}"
                hit = signal_close is not None and float(signal_close) < ep
                dist = round(float(signal_close) - ep, 2) if signal_close is not None else None
            else:
                rule = f"Short: {tf} close above entry {ep:.2f}{src_note}"
                hit = signal_close is not None and float(signal_close) > ep
                dist = round(ep - float(signal_close), 2) if signal_close is not None else None
            row: dict[str, Any] = {
                "order": 1,
                "category": "Entry",
                "price": round(ep, 2),
                "triggered": hit,
                "rule": rule,
                "enabled": True,
                "source": entry_source or "fill",
            }
            if dist is not None:
                row["distance"] = abs(dist)
            levels.append(row)
        else:
            levels.append(
                {
                    "order": 1,
                    "category": "Entry",
                    "price": None,
                    "triggered": False,
                    "rule": (
                        f"{'Short: TF close above entry' if side == 'short' else 'Long: TF close below entry'}"
                        " — waiting for Kite average_price"
                    ),
                    "enabled": True,
                    "missing": True,
                }
            )

    # 2) Trailing stop — live trail from current TF/live price (dynamic)
    tsl_mode = str(cfg.get("tsl_mode") or "Off")
    if tsl_mode in TRAIL_MODES:
        atr_trail = leg.get("atr_trail")
        live_ref = leg.get("atr_live_ref")
        # ATR needs an ATR to describe itself; % and Pts do not.
        priceable = tsl_mode != "ATR" or (atr1 is not None and float(atr1) > 0)
        if atr_trail is not None and priceable:
            atr_px = round(float(atr_trail), 2)
            hit = ltp is not None and (float(ltp) <= atr_px if side == "long" else float(ltp) >= atr_px)
            dist = round(abs(float(ltp) - atr_px), 2) if ltp is not None else None
            ref_txt = f"{float(live_ref):.2f}" if live_ref is not None else "live"
            if tsl_mode == "ATR":
                band_txt = f"ATR1×{cfg.get('tsl_value')} ({float(atr1):.2f})"
            elif tsl_mode == "%":
                band_txt = f"{cfg.get('tsl_value')}%"
            else:
                band_txt = f"{cfg.get('tsl_value')} pts"
            row = {
                "order": 2,
                "category": "ATR" if tsl_mode == "ATR" else f"Trail ({tsl_mode})",
                "price": atr_px,
                "triggered": hit,
                "rule": (
                    f"{'Short' if side == 'short' else 'Long'} live: "
                    f"{ref_txt} {'+' if side == 'short' else '−'} {band_txt}"
                    f" → trail {atr_px:.2f} (ratchets with {tf})"
                ),
                "enabled": True,
                "dynamic": True,
            }
            if dist is not None:
                row["distance"] = dist
            levels.append(row)
        elif atr1 is not None and float(atr1) > 0:
            # Preview before first open-leg tick trail is stored
            ref = float(ltp) if ltp is not None else (float(signal_close) if signal_close is not None else None)
            if ref is not None:
                band = float(atr1) * float(cfg.get("tsl_value") or 1)
                atr_px = round(ref - band, 2) if side == "long" else round(ref + band, 2)
                levels.append(
                    {
                        "order": 2,
                        "category": "ATR",
                        "price": atr_px,
                        "triggered": False,
                        "rule": (
                            f"Live preview from {ref:.2f} ± ATR1×{cfg.get('tsl_value')} "
                            f"— updates each tick"
                        ),
                        "enabled": True,
                        "dynamic": True,
                    }
                )
            else:
                levels.append(
                    {
                        "order": 2,
                        "category": "ATR",
                        "price": None,
                        "triggered": False,
                        "rule": "ATR TSL on — waiting for live LTP / TF close",
                        "enabled": True,
                        "missing": True,
                    }
                )
        else:
            levels.append(
                {
                    "order": 2,
                    "category": "ATR",
                    "price": None,
                    "triggered": False,
                    "rule": "ATR TSL on — needs live ST1 ATR",
                    "enabled": True,
                    "missing": True,
                }
            )
    else:
        levels.append(
            {
                "order": 2,
                "category": "ATR",
                "price": None,
                "triggered": False,
                "rule": "ATR TSL off — enable ATR TSL checkbox",
                "enabled": False,
                "missing": True,
            }
        )

    # 3) ST1 — dynamic live band
    if exit_line is not None:
        el = float(exit_line)
        triggered = bool(leg.get("zone_exit_triggered"))
        if signal_close is not None:
            sc = float(signal_close)
            if side == "long":
                triggered = triggered or sc < el
            else:
                triggered = triggered or sc > el
        live_tag = " live" if leg.get("st1_live") else ""
        if ltp is not None:
            lp = float(ltp)
            if side == "long":
                rule = f"Long: {tf} close / LTP below ST1{live_tag} {el:.2f}"
                distance = round(lp - el, 2)
            else:
                rule = f"Short: {tf} close / LTP above ST1{live_tag} {el:.2f}"
                distance = round(el - lp, 2)
        else:
            rule = f"ST1{live_tag} @ {el:.2f}"
            distance = None
        row = {
            "order": 3,
            "category": "ST1",
            "price": el,
            "triggered": triggered,
            "rule": rule,
            "enabled": True,
            "dynamic": True,
        }
        if distance is not None:
            row["distance"] = abs(distance)
        levels.append(row)
    else:
        levels.append(
            {
                "order": 3,
                "category": "ST1",
                "price": None,
                "triggered": False,
                "rule": "ST1 level unavailable",
                "enabled": True,
                "missing": True,
            }
        )

    # Optional SL / Target (appended after the core 3)
    if entry is not None:
        ep = float(entry)
        sl_mode = str(cfg.get("sl_mode") or "Off")
        tgt_mode = str(cfg.get("tgt_mode") or "Off")
        if sl_mode != "Off":
            sl = _level(ep, sl_mode, float(cfg.get("sl_value") or 1), side, "sl")  # type: ignore[arg-type]
            if sl is not None:
                hit = ltp is not None and (float(ltp) <= sl if side == "long" else float(ltp) >= sl)
                levels.append(
                    {
                        "order": 4,
                        "category": "SL",
                        "price": round(sl, 2),
                        "triggered": hit,
                        "rule": f"Stop ({sl_mode}) from entry {ep:.2f}",
                        "enabled": True,
                    }
                )
        if tgt_mode != "Off":
            tgt = _level(ep, tgt_mode, float(cfg.get("tgt_value") or 1), side, "tgt")  # type: ignore[arg-type]
            if tgt is not None:
                hit = ltp is not None and (float(ltp) >= tgt if side == "long" else float(ltp) <= tgt)
                levels.append(
                    {
                        "order": 5,
                        "category": "Target",
                        "price": round(tgt, 2),
                        "triggered": hit,
                        "rule": f"Target ({tgt_mode}) from entry {ep:.2f}",
                        "enabled": True,
                    }
                )

    if ltp is not None and exit_line is not None:
        el, lp = float(exit_line), float(ltp)
        if side == "long":
            params["zone_exit_at_ltp"] = lp < el
            params["zone_exit_ltp_distance"] = round(lp - el, 2)
            params["in_hold_zone"] = lp >= el
        else:
            params["zone_exit_at_ltp"] = lp > el
            params["zone_exit_ltp_distance"] = round(el - lp, 2)
            params["in_hold_zone"] = lp <= el

    params["exit_levels"] = levels
    priced = [lv for lv in levels if lv.get("price") is not None]
    triggered_levels = [lv for lv in priced if lv.get("triggered")]
    if triggered_levels:
        params["next_exit"] = triggered_levels[0]
    elif priced and ltp is not None:
        lp = float(ltp)
        nearest = min(
            priced,
            key=lambda lv: abs(float(lv["price"]) - lp),
        )
        params["next_exit"] = {**nearest, "triggered": False, "distance": round(abs(float(nearest["price"]) - lp), 2)}

    return params


def status_bundle(*, sync_broker: bool = True) -> dict[str, Any]:
    cfg = get_config()
    state = get_state()
    underlying = str(cfg.get("underlying") or "NIFTY")
    if state.get("state_underlying") is None:
        save_state({"state_underlying": underlying})
        state = get_state()
    elif _spot_state_stale(cfg, state):
        state = _ensure_state_underlying(cfg, state)
    broker_mismatches: list[str] = []
    orphans: list[dict[str, Any]] = []
    if sync_broker:
        try:
            broker = _broker()
            reconcile_patches, broker_mismatches, orphans = _reconcile_broker_legs(cfg, state, broker)
            if reconcile_patches:
                save_state(reconcile_patches)
                state = get_state()
            paper_patches = _paper_broker_qty_patches(cfg, state, broker)
            if paper_patches:
                save_state(paper_patches)
                state = get_state()
        except Exception as exc:
            broker_mismatches = [f"broker sync skipped: {_format_tick_error(exc)}"]
    display_state = _sanitize_state_for_display(cfg, state)
    ce = dict(display_state.get("ce") or {})
    pe = dict(display_state.get("pe") or {})
    ce["exit_params"] = _leg_exit_params("ce", cfg, ce)
    pe["exit_params"] = _leg_exit_params("pe", cfg, pe)
    return {
        "config": cfg,
        "state": {**display_state, "ce": ce, "pe": pe},
        "price_stops": price_stop_summary(cfg),
        "arm": get_arm_state(),
        "kite_authenticated": session_status().get("authenticated"),
        "broker_mismatches": broker_mismatches,
        "orphans": orphans,
        "order_quantity": order_quantity_from_config(cfg),
    }
