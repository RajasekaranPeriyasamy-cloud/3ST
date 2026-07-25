"""FastAPI backend for Lovable UI + Kite Connect algo platform."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, model_validator

from backtest_engine import BacktestParams, run_backtest, trades_to_df
from backtest_rolling_atm import RollingAtmParams, run_rolling_atm_backtest
from broker.kite_broker import KiteBroker
from broker.paper_broker import get_paper_broker, sync_paper_from_rolling_straddle
from config import (
    DEFAULT_ADX,
    DEFAULT_RISK,
    DEFAULT_SESSION,
    DEFAULT_ST,
    DEFAULT_ST_METHOD,
    INDEX_OPTIONS,
    INSTRUMENTS,
    KITE_MAX_DAYS,
    OI_PROFILE_DEFAULTS,
    TIMEFRAMES,
    YAHOO_MAX_DAYS,
)
from execution.arming import arm, disarm, get_arm_state, set_mode
from execution.rolling_straddle import close_all, close_leg, adopt_leg, unlink_leg, start_runner, status_bundle as rs_status_bundle, stop_runner, tick
from execution.rolling_straddle_store import get_config as rs_get_config
from execution.rolling_straddle_store import get_log as rs_get_log
from execution.rolling_straddle_store import save_config as rs_save_config
from execution.survivor_runner import start_runner as survivor_start_runner
from execution.survivor_runner import status_bundle as survivor_status_bundle
from execution.survivor_runner import stop_runner as survivor_stop_runner
from execution.survivor_runner import tick as survivor_tick
from execution.survivor_store import get_config as survivor_get_config
from execution.survivor_store import get_log as survivor_get_log
from execution.survivor_store import save_config as survivor_save_config
from execution.wave_runner import start_runner as wave_start_runner
from execution.wave_runner import status_bundle as wave_status_bundle
from execution.wave_runner import stop_runner as wave_stop_runner
from execution.wave_runner import tick as wave_tick
from execution.wave_store import get_config as wave_get_config
from execution.wave_store import get_log as wave_get_log
from execution.wave_store import save_config as wave_save_config
from execution.premium_book_runner import (
    close_all as premium_book_close_all,
    preview_current as premium_book_preview,
    revoke_buy_hold as premium_book_revoke_buy_hold,
    start_runner as premium_book_start_runner,
    status_bundle as premium_book_status_bundle,
    stop_runner as premium_book_stop_runner,
    tick as premium_book_tick,
)
from execution.premium_book_store import get_config as premium_book_get_config
from execution.premium_book_store import get_log as premium_book_get_log
from execution.premium_book_store import save_config as premium_book_save_config
from execution.scheduler import scheduler_status, start_scheduler, stop_scheduler
from execution.desk_trades import adopt_open_positions, build_active_trades_view, sync_active_trade_entry
from execution.execution_queue import build_execution_queue, queue_action
from execution.positions_view import build_positions_view
from execution.live_workflow import get_workflow_status, validate_live_execution
from execution.watchlist_activation import activate_watchlist_item, manual_enter_watchlist_item, trigger_manual_side
from execution.watchlist_close import close_watchlist_trade
from execution.watchlist_exit_runner import exit_status_for_item, scan_watchlist_exits
from execution.watchlist_runner import scan_watchlist
from instruments import (
    list_resolved,
    refresh_instruments,
    resolve_by_token,
    resolve_instrument,
    search_instruments,
    warm_instruments_cache,
)
from kite_errors import friendly_kite_message
from kite_auth import (
    clear_session,
    exchange_request_token,
    login_url,
    session_status,
)
from kite_client import (
    default_kite_date_range,
    fetch_historical_for_selection,
    kite_max_lookback_days,
    margins,
    preview_order_margins,
    status_bundle,
)
from options.chain import get_chain, list_expiries
from options.oi_tracker import build_snapshot, tracker_config
from options.oi_tracker_store import append_log, get_log as oi_get_log
from options.oi_movers import build_movers_snapshot, movers_config
from options.oi_var import build_var_snapshot, var_config
from options.gamma_density import build_gamma_snapshot, gamma_config
from options.gamma_density_provider import get_gamma_density_provider
from options.vanna_exposure import build_vanna_snapshot, vanna_config
from options.greeks_desk import build_greeks_snapshot, desk_config as greeks_desk_config
from options.trade_suggestions import build_trade_suggestions, suggestions_config
from pricing.desk import build_pricing_desk, price_single, pricing_config
from options.oi_profile import oi_profile_config, oi_profile_snapshot
from options.vol_surface import build_vol_surface, vol_surface_config
from options.iv_smile import build_iv_smile, iv_smile_config
from options.calendar_arbitrage import (
    build_arbitrage_snapshot,
    build_arbitrage_universe,
    calendar_arbitrage_config,
)
from execution.latency_log import get_stats as latency_get_stats, read_recent as latency_read_recent
from analysis.fpi_sectors import fpi_status, load_fpi_sectors
from analysis.rrg import build_rrg_snapshot, clear_rrg_daily_cache, rrg_config
from analysis.analogue_cycles import analogue_config, build_analogue_snapshot
from options.spreads import SPREAD_TEMPLATES, build_direction_spreads, preview_spread
from risk.limits import get_limits, update_limits
from selection_store import clear_selection, get_selection, save_selection
from watchlist_store import add_item as watchlist_add
from watchlist_store import get_item as watchlist_get
from watchlist_store import list_items as watchlist_list
from watchlist_store import mark_closed as watchlist_close
from watchlist_store import remove_item as watchlist_remove
from settings import desk_ui_url, kite_credentials, kite_ready
from yahoo_client import default_date_range, fetch_candles as yahoo_fetch, max_lookback_days as yahoo_max_days


_APP_STARTED_AT: float | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    import threading
    import time

    global _APP_STARTED_AT
    _APP_STARTED_AT = time.time()

    from execution.ltp_cache import start_ltp_feed, stop_ltp_feed
    from kite_auth import kite_egress_plan
    from settings import apply_kite_proxy_env

    _proxies, _bind_ipv6, mode = kite_egress_plan()
    if mode == "staticip_proxy":
        apply_kite_proxy_env()

    sync_paper_from_rolling_straddle()
    from execution.arming import load_persisted_state

    load_persisted_state()
    start_scheduler()
    threading.Thread(target=warm_instruments_cache, name="instruments-warm", daemon=True).start()
    await start_ltp_feed()
    try:
        from execution.reconcile import reconcile_live_desk

        reconcile_live_desk(apply_changes=True)
    except Exception:
        pass
    cfg = rs_get_config()
    if cfg.get("auto_start_on_boot"):
        try:
            start_runner()
        except Exception:
            pass
    if survivor_get_config().get("auto_start_on_boot"):
        try:
            survivor_start_runner()
        except Exception:
            pass
    if wave_get_config().get("auto_start_on_boot"):
        try:
            wave_start_runner()
        except Exception:
            pass
    if premium_book_get_config().get("auto_start_on_boot"):
        try:
            premium_book_start_runner()
        except Exception:
            pass
    yield
    await stop_ltp_feed()
    stop_scheduler()


app = FastAPI(title="3ST Kite Algo API", version="0.2.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "http://localhost:8081",
        "http://127.0.0.1:8081",
        "https://*.lovable.app",
        "https://*.lovable.dev",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _no_store_api_json(request: Request, call_next):
    """Prevent browsers caching SPA HTML under API paths (stale HTML → false JSON errors)."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/assets/") or path in ("/", "/favicon.ico"):
        return response
    # API JSON and auth — never cache
    if not path.startswith("/assets"):
        ctype = response.headers.get("content-type", "")
        if "application/json" in ctype or path.startswith(
            (
                "/vanna-exposure",
                "/pricing",
                "/gamma-density",
                "/greeks",
                "/trade-suggestions",
                "/oi-",
                "/vol-surface",
                "/iv-",
                "/auth",
                "/live",
                "/health",
                "/options",
                "/market",
            )
        ):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response.headers["Pragma"] = "no-cache"
    return response


_paper = get_paper_broker()
_kite_broker = KiteBroker()

SpreadTemplate = Literal[
    "bull_call",
    "bear_put",
    "bear_call",
    "bull_put",
    "iron_condor",
    "short_straddle",
    "short_strangle",
]


class SessionIn(BaseModel):
    request_token: str


class ArmIn(BaseModel):
    confirm: bool = False
    mode: Literal["paper", "live"] | None = None


class ModeIn(BaseModel):
    mode: Literal["paper", "live"]


class MarginPreviewIn(BaseModel):
    exchange: str
    tradingsymbol: str
    transaction_type: Literal["BUY", "SELL"]
    quantity: int
    product: Literal["MIS", "NRML", "CNC"] = "NRML"
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    price: float | None = None


class PanicIn(BaseModel):
    confirm: bool = False
    cancel_orders: bool = True
    close_positions: bool = True


class ReconcileIn(BaseModel):
    adopt_orphans: bool = False


class ManualEnterIn(BaseModel):
    side: Literal["buy", "sell"] | None = None
    signal: Literal["long", "short"] | None = None

    @model_validator(mode="after")
    def _require_side(self) -> ManualEnterIn:
        if self.side is None and self.signal is None:
            raise ValueError("Provide side ('buy' or 'sell')")
        return self


class RiskIn(BaseModel):
    max_qty: float | None = None
    max_open_positions: int | None = None
    max_daily_loss: float | None = None
    max_orders_per_minute: int | None = None


class SpreadLegOverride(BaseModel):
    strike: float | None = None
    option_type: Literal["CE", "PE"] | None = None
    side: Literal["BUY", "SELL"] | None = None
    quantity: int | None = None


class SpreadPreviewIn(BaseModel):
    underlying: str
    expiry: str
    template: SpreadTemplate
    width_steps: int = 1
    otm_offset: int = 0
    spot: float | None = None
    legs: list[SpreadLegOverride] | None = None


class SpreadConfigIn(BaseModel):
    underlying: str
    expiry: str
    long_template: SpreadTemplate = "bull_call"
    short_template: SpreadTemplate = "bear_call"
    width_steps: int = 1
    otm_offset: int = 0
    legs_long: list[dict[str, Any]] | None = None
    legs_short: list[dict[str, Any]] | None = None


class PremiumBookConfigIn(BaseModel):
    underlying: Literal["NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "CRUDEOILM"] = "NIFTY"
    expiry: str = ""
    trade_bias: Literal["sell_premium", "buy_hold"] = "sell_premium"
    book_side: Literal["sell", "buy"] | None = None  # legacy alias
    structure: Literal[
        "bull_put",
        "bear_call",
        "long_call",
        "long_put",
        "bull_call",
        "bear_put",
        "long_strangle",
    ] = "bull_put"
    otm_offset: int = 1
    width_steps: int = 1
    timeframe: str = "5min"
    entry_start: str = "09:20"
    session_start: str = DEFAULT_SESSION["session_start"]
    session_end: str = DEFAULT_SESSION["session_end"]
    force_exit: str = DEFAULT_SESSION["force_exit"]
    system_mode: Literal["Intraday", "Positional"] = "Intraday"
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    product: Literal["MIS", "NRML"] = "MIS"
    tick_interval_sec: int = 60
    convert_sl_to_spread: bool = True
    auto_structure: bool = True
    auto_start_on_boot: bool = False
    size_mode: Literal["lots", "qty"] = "lots"
    size_value: int = 1
    st_method: Literal["heikin_ashi", "regular", "hybrid"] = DEFAULT_ST_METHOD
    atr1: int = DEFAULT_ST["atr1"]
    factor1: float = DEFAULT_ST["factor1"]
    atr2: int = DEFAULT_ST["atr2"]
    factor2: float = DEFAULT_ST["factor2"]
    atr3: int = DEFAULT_ST["atr3"]
    factor3: float = DEFAULT_ST["factor3"]
    st1_enabled: bool = True
    st2_enabled: bool = True
    st3_enabled: bool = True
    entry_require_st1_st2: bool = True
    adx_enabled: bool = DEFAULT_ADX["enabled"]
    adx_period: int = DEFAULT_ADX["period"]
    adx_threshold: float = DEFAULT_ADX["threshold"]
    sl_mode: Literal["Off", "%", "Pts"] = DEFAULT_RISK["sl_mode"]  # type: ignore
    sl_value: float = DEFAULT_RISK["sl_value"]
    tgt_mode: Literal["Off", "%", "Pts"] = DEFAULT_RISK["tgt_mode"]  # type: ignore
    tgt_value: float = DEFAULT_RISK["tgt_value"]
    tsl_mode: Literal["Off", "%", "Pts", "ATR"] = "ATR"
    tsl_value: float = 1.2
    entry_exit_enabled: bool = False
    exit_on_bar_close_only: bool = True

    @model_validator(mode="after")
    def _require_enabled_st(self) -> PremiumBookConfigIn:
        if not (self.st1_enabled or self.st2_enabled or self.st3_enabled):
            raise ValueError("At least one SuperTrend (ST1, ST2, or ST3) must be enabled")
        return self


class SelectionIn(BaseModel):
    instrument_token: int | None = None
    exchange: str | None = None
    tradingsymbol: str | None = None
    name: str | None = None
    segment: Literal["equity", "future", "option"] = "equity"
    lot_size: int = 0
    timeframe: str = "15min"
    product: Literal["underlying", "options_spread"] = "underlying"
    spread: SpreadConfigIn | None = None
    st_method: Literal["heikin_ashi", "regular", "hybrid"] = DEFAULT_ST_METHOD
    system_mode: Literal["Intraday", "Positional"] = "Intraday"
    session_start: str = DEFAULT_SESSION["session_start"]
    session_end: str = DEFAULT_SESSION["session_end"]
    force_exit: str = DEFAULT_SESSION["force_exit"]
    atr1: int = DEFAULT_ST["atr1"]
    factor1: float = DEFAULT_ST["factor1"]
    atr2: int = DEFAULT_ST["atr2"]
    factor2: float = DEFAULT_ST["factor2"]
    atr3: int = DEFAULT_ST["atr3"]
    factor3: float = DEFAULT_ST["factor3"]
    st1_enabled: bool = DEFAULT_ST["st1_enabled"]
    st2_enabled: bool = DEFAULT_ST["st2_enabled"]
    st3_enabled: bool = DEFAULT_ST["st3_enabled"]
    adx_enabled: bool = DEFAULT_ADX["enabled"]
    adx_period: int = DEFAULT_ADX["period"]
    adx_threshold: float = DEFAULT_ADX["threshold"]
    sl_mode: Literal["Off", "%", "Pts"] = DEFAULT_RISK["sl_mode"]  # type: ignore
    sl_value: float = DEFAULT_RISK["sl_value"]
    tgt_mode: Literal["Off", "%", "Pts"] = DEFAULT_RISK["tgt_mode"]  # type: ignore
    tgt_value: float = DEFAULT_RISK["tgt_value"]
    tsl_mode: Literal["Off", "%", "Pts", "ATR"] = DEFAULT_RISK["tsl_mode"]  # type: ignore
    tsl_value: float = DEFAULT_RISK["tsl_value"]
    product_type: Literal["MIS", "NRML"] = "MIS"
    entry_mode: Literal["manual", "signal"] = "manual"

    @model_validator(mode="after")
    def _require_enabled_st(self) -> SelectionIn:
        if not (self.st1_enabled or self.st2_enabled or self.st3_enabled):
            raise ValueError("At least one SuperTrend (ST1, ST2, or ST3) must be enabled")
        return self


class BacktestIn(BaseModel):
    instrument: str | None = "NIFTY50"
    instrument_token: int | None = None
    exchange: str | None = None
    tradingsymbol: str | None = None
    timeframe: str = "5min"
    source: Literal["yahoo", "kite"] = "yahoo"
    start: date | None = None
    end: date | None = None
    use_max: bool = True
    use_selection: bool = False
    atr1: int = DEFAULT_ST["atr1"]
    factor1: float = DEFAULT_ST["factor1"]
    atr2: int = DEFAULT_ST["atr2"]
    factor2: float = DEFAULT_ST["factor2"]
    atr3: int = DEFAULT_ST["atr3"]
    factor3: float = DEFAULT_ST["factor3"]
    st1_enabled: bool = DEFAULT_ST["st1_enabled"]
    st2_enabled: bool = DEFAULT_ST["st2_enabled"]
    st3_enabled: bool = DEFAULT_ST["st3_enabled"]
    adx_enabled: bool = DEFAULT_ADX["enabled"]
    adx_period: int = DEFAULT_ADX["period"]
    adx_threshold: float = DEFAULT_ADX["threshold"]
    st_method: Literal["heikin_ashi", "regular", "hybrid"] = DEFAULT_ST_METHOD
    trade_mode: Literal["Both", "LongOnly", "ShortOnly"] = "Both"
    system_mode: Literal["Intraday", "Positional"] = "Intraday"
    session_start: str = DEFAULT_SESSION["session_start"]
    session_end: str = DEFAULT_SESSION["session_end"]
    force_exit: str = DEFAULT_SESSION["force_exit"]
    tgt_mode: Literal["Off", "%", "Pts"] = DEFAULT_RISK["tgt_mode"]  # type: ignore
    tgt_value: float = DEFAULT_RISK["tgt_value"]
    sl_mode: Literal["Off", "%", "Pts"] = DEFAULT_RISK["sl_mode"]  # type: ignore
    sl_value: float = DEFAULT_RISK["sl_value"]
    tsl_mode: Literal["Off", "%", "Pts", "ATR"] = DEFAULT_RISK["tsl_mode"]  # type: ignore
    tsl_value: float = DEFAULT_RISK["tsl_value"]
    lot_size: int = 0
    segment: Literal["equity", "future", "option"] = "equity"

    @model_validator(mode="after")
    def _require_enabled_st(self) -> BacktestIn:
        if not (self.st1_enabled or self.st2_enabled or self.st3_enabled):
            raise ValueError("At least one SuperTrend (ST1, ST2, or ST3) must be enabled")
        return self


class RollingStraddleConfigIn(BaseModel):
    underlying: Literal["NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "CRUDEOILM", "NATURALGAS"] = "NIFTY"
    expiry: str = ""
    timeframe: str = "5min"
    entry_start: str = "09:20"
    session_start: str = DEFAULT_SESSION["session_start"]
    session_end: str = DEFAULT_SESSION["session_end"]
    force_exit: str = DEFAULT_SESSION["force_exit"]
    system_mode: Literal["Intraday", "Positional"] = "Intraday"
    order_type: Literal["MARKET", "LIMIT"] = "MARKET"
    product: Literal["MIS", "NRML"] = "MIS"
    tick_interval_sec: int = 60
    trade_mode: Literal["Both", "LongOnly", "ShortOnly", "ShortSignalsOnly"] = "Both"
    max_reentries_ce: int = 1
    max_reentries_pe: int = 1
    reentry_style: Literal["zone_active", "edge_only"] = "zone_active"
    allow_dual_open: bool = True
    auto_start_on_boot: bool = False
    size_mode: Literal["lots", "qty"] = "lots"
    size_value: int = 1
    st_method: Literal["heikin_ashi", "regular", "hybrid"] = DEFAULT_ST_METHOD
    atr1: int = DEFAULT_ST["atr1"]
    factor1: float = DEFAULT_ST["factor1"]
    atr2: int = DEFAULT_ST["atr2"]
    factor2: float = DEFAULT_ST["factor2"]
    atr3: int = DEFAULT_ST["atr3"]
    factor3: float = DEFAULT_ST["factor3"]
    st1_enabled: bool = DEFAULT_ST["st1_enabled"]
    st2_enabled: bool = DEFAULT_ST["st2_enabled"]
    st3_enabled: bool = DEFAULT_ST["st3_enabled"]
    adx_enabled: bool = DEFAULT_ADX["enabled"]
    adx_period: int = DEFAULT_ADX["period"]
    adx_threshold: float = DEFAULT_ADX["threshold"]
    sl_mode: Literal["Off", "%", "Pts"] = DEFAULT_RISK["sl_mode"]  # type: ignore
    sl_value: float = DEFAULT_RISK["sl_value"]
    tgt_mode: Literal["Off", "%", "Pts"] = DEFAULT_RISK["tgt_mode"]  # type: ignore
    tgt_value: float = DEFAULT_RISK["tgt_value"]
    tsl_mode: Literal["Off", "%", "Pts", "ATR"] = DEFAULT_RISK["tsl_mode"]  # type: ignore
    tsl_value: float = DEFAULT_RISK["tsl_value"]
    entry_exit_enabled: bool = True
    execution_mode: Literal["auto", "confirm"] = "auto"
    exit_on_bar_close_only: bool = True

    @model_validator(mode="after")
    def _require_enabled_st(self) -> RollingStraddleConfigIn:
        if not (self.st1_enabled or self.st2_enabled or self.st3_enabled):
            raise ValueError("At least one SuperTrend (ST1, ST2, or ST3) must be enabled")
        return self


class CloseLegIn(BaseModel):
    leg: Literal["ce", "pe"]


class QueueActionIn(BaseModel):
    action: Literal["adopt", "unlink", "close", "ship", "execute", "dismiss"]


class SurvivorConfigIn(BaseModel):
    underlying: Literal["NIFTY", "BANKNIFTY", "SENSEX"] = "NIFTY"
    expiry: str = ""
    symbol_initials: str = ""
    index_symbol: str = "NSE:NIFTY 50"
    pe_gap: int = 20
    ce_gap: int = 20
    pe_quantity: int = 65
    ce_quantity: int = 65
    pe_symbol_gap: int = 200
    ce_symbol_gap: int = 200
    min_price_to_sell: float = 15
    sell_multiplier_threshold: int = 5
    pe_reset_gap: int = 30
    ce_reset_gap: int = 30
    pe_start_point: float = 0
    ce_start_point: float = 0
    exchange: str = "NFO"
    product_type: Literal["NRML", "MIS"] = "NRML"
    tag: str = "Survivor"
    tick_interval_sec: int = 15
    auto_start_on_boot: bool = False


class WaveConfigIn(BaseModel):
    symbol_name: str = "NIFTY26JULFUT"
    exchange: str = "NFO"
    buy_gap: float = 25
    sell_gap: float = 25
    buy_quantity: int = 65
    sell_quantity: int = 65
    lot_size: int = 65
    cool_off_time: int = 10
    product_type: Literal["NRML", "MIS"] = "NRML"
    order_type: Literal["LIMIT", "MARKET"] = "LIMIT"
    tag: str = "WaveScraper"
    min_nifty_delta: float = -100
    max_nifty_delta: float = 100
    min_bank_nifty_delta: float = -100
    max_bank_nifty_delta: float = 100
    interest_rate: float = 10.0
    todays_volatility: float = 20.0
    delta_calculation_days: int = 10
    check_interval_sec: int = 60
    auto_start_on_boot: bool = False


class RollingAtmBacktestIn(BacktestIn):
    underlying: Literal["NIFTY", "BANKNIFTY", "SENSEX"] = "NIFTY"
    max_reentries_ce: int = 1
    max_reentries_pe: int = 1
    reentry_style: Literal["zone_active", "edge_only"] = "zone_active"
    allow_dual_open: bool = True
    entry_start: str = "09:20"


class PricingCalculateIn(BaseModel):
    spot: float
    strike: float
    option_type: Literal["CE", "PE"] = "CE"
    market_price: float | None = None
    iv: float | None = None
    tte_years: float | None = None
    expiry: str | None = None
    risk_free_rate: float = 0.065
    include_heston: bool = False
    heston_overrides: dict[str, float] | None = None


def _err(e: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=friendly_kite_message(str(e)))


def _require_kite_session() -> None:
    if not session_status().get("authenticated"):
        raise HTTPException(
            status_code=401,
            detail="Kite session required. Complete login at /auth/login-url",
        )


def _session_meta(body: BacktestIn) -> dict[str, str]:
    if body.instrument and body.instrument in INSTRUMENTS:
        meta = INSTRUMENTS[body.instrument]
        return {
            "session_start": meta["session_start"],
            "session_end": meta["session_end"],
            "force_exit": body.force_exit or meta["force_exit"],
        }
    return {
        "session_start": body.session_start,
        "session_end": body.session_end,
        "force_exit": body.force_exit,
    }


def _apply_selection_to_backtest(body: BacktestIn, sel: dict[str, Any]) -> BacktestIn:
    """Merge saved stock-selection strategy fields into backtest request."""
    data = body.model_dump()
    for key in (
        "timeframe",
        "st_method",
        "system_mode",
        "session_start",
        "session_end",
        "force_exit",
        "atr1",
        "factor1",
        "atr2",
        "factor2",
        "atr3",
        "factor3",
        "st1_enabled",
        "st2_enabled",
        "st3_enabled",
        "adx_enabled",
        "adx_period",
        "adx_threshold",
        "sl_mode",
        "sl_value",
        "tgt_mode",
        "tgt_value",
        "tsl_mode",
        "tsl_value",
        "segment",
        "lot_size",
    ):
        if key in sel and sel[key] is not None:
            data[key] = sel[key]
    return BacktestIn(**data)


def _yahoo_key_for_target(target: dict[str, Any]) -> str | None:
    key = target.get("instrument_key")
    if key and key in INSTRUMENTS:
        return key
    sym = (target.get("tradingsymbol") or "").upper()
    if sym in ("NIFTY 50", "NIFTY50", "NIFTY"):
        return "NIFTY50"
    if sym == "SENSEX":
        return "SENSEX"
    token = target.get("instrument_token")
    if token == 256265:
        return "NIFTY50"
    return None


def _resolve_backtest_target(body: BacktestIn) -> dict[str, Any]:
    if body.use_selection:
        sel = get_selection()
        body = _apply_selection_to_backtest(body, sel)
        if sel.get("instrument_token"):
            lot = int(sel.get("lot_size") or 0)
            seg = sel.get("segment") or "equity"
            yahoo_key = _yahoo_key_for_target(
                {"tradingsymbol": sel.get("tradingsymbol"), "instrument_token": sel.get("instrument_token")}
            )
            return {
                "instrument_key": yahoo_key,
                "instrument_token": int(sel["instrument_token"]),
                "tradingsymbol": sel.get("tradingsymbol"),
                "exchange": sel.get("exchange"),
                "timeframe": body.timeframe,
                "label": sel.get("tradingsymbol") or str(sel["instrument_token"]),
                "segment": seg,
                "lot_size": lot,
                "body": body,
            }

    if body.instrument_token is not None:
        resolved = resolve_by_token(body.instrument_token)
        lot = int(body.lot_size or resolved.get("lot_size") or 0)
        return {
            "instrument_key": None,
            "instrument_token": body.instrument_token,
            "tradingsymbol": body.tradingsymbol or resolved["tradingsymbol"],
            "exchange": body.exchange or resolved["exchange"],
            "timeframe": body.timeframe,
            "label": body.tradingsymbol or resolved["tradingsymbol"],
            "segment": body.segment,
            "lot_size": lot,
            "body": body,
        }

    if body.instrument and body.instrument in INSTRUMENTS:
        return {
            "instrument_key": body.instrument,
            "instrument_token": None,
            "tradingsymbol": INSTRUMENTS[body.instrument]["trading_symbol"],
            "exchange": INSTRUMENTS[body.instrument]["exchange"],
            "timeframe": body.timeframe,
            "label": body.instrument,
            "segment": "equity",
            "lot_size": 0,
            "body": body,
        }

    raise RuntimeError(
        "Provide instrument (NIFTY50/SENSEX), instrument_token, or use_selection=true"
    )


@app.get("/health")
def health() -> dict[str, Any]:
    import time

    from instruments import cache_status
    from kite_auth import kite_egress_status
    from settings import env, kite_allowed_egress_ip, kite_use_staticip_proxy, proxy_ready

    egress = kite_egress_status()
    proxy_on = egress["mode"] == "staticip_proxy"
    allowed = kite_allowed_egress_ip()
    if egress["mode"] == "local_bind":
        hint = (
            f"Live orders bound to local IPv6 {egress['bind_ipv6']} "
            f"(whitelisted on developers.kite.trade)"
        )
    elif proxy_on:
        hint = (
            f"Live orders pinned to staticip.in — Kite whitelist must include {allowed or 'your static egress IP'}"
        )
    else:
        hint = "Orders use direct connection — whitelist your public IP on developers.kite.trade"
    uptime = round(time.time() - _APP_STARTED_AT, 1) if _APP_STARTED_AT else None
    return {
        "ok": True,
        "uptime_sec": uptime,
        **cache_status(),
        "kite_configured": kite_ready(),
        "kite_authenticated": session_status().get("authenticated", False),
        "kite_proxy_enabled": proxy_on,
        "kite_proxy_host": env("STATICIP_HOST") if proxy_on else None,
        "kite_allowed_egress_ip": allowed or None,
        "kite_egress_mode": egress["mode"],
        "kite_bind_ipv6": egress.get("bind_ipv6"),
        "kite_local_bind_available": egress.get("local_bind_available"),
        "kite_proxy_hint": hint,
        "instruments": list(INSTRUMENTS.keys()),
        "index_options": list(INDEX_OPTIONS.keys()),
        "timeframes": list(TIMEFRAMES.keys()),
        "spread_templates": list(SPREAD_TEMPLATES.keys()),
        "st_methods": ["heikin_ashi", "regular", "hybrid"],
    }


@app.get("/auth/login-url")
def auth_login_url() -> dict[str, str]:
    try:
        return {
            "login_url": login_url(),
            "redirect_url": kite_credentials()["redirect_url"],
            "hint": f"Open {desk_ui_url()}/login in the desk UI",
        }
    except Exception as e:
        raise _err(e) from e


@app.get("/auth/login", response_class=HTMLResponse)
def auth_login_page(request: Request) -> HTMLResponse:
    """One-click Kite login — no Swagger token paste needed."""
    try:
        url = login_url()
        base = str(request.base_url).rstrip("/")
        redirect = f"{base}/auth/callback"
    except Exception as e:
        return HTMLResponse(f"<h1>Kite not configured</h1><p>{e}</p>", status_code=400)
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>3ST Kite Login</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 520px; margin: 48px auto; padding: 0 16px; }}
  a.btn {{ display: inline-block; background: #387ed1; color: #fff; padding: 12px 24px;
    border-radius: 6px; text-decoration: none; font-weight: 600; }}
  code {{ background: #f4f4f5; padding: 2px 6px; border-radius: 4px; }}
  .warn {{ background: #fef3c7; padding: 12px; border-radius: 6px; margin: 16px 0; }}
</style></head><body>
  <h1>3ST — Zerodha Login</h1>
  <p>API port: <code>{request.url.port or 80}</code></p>
  <p>Redirect URL (set this in Kite developer app):</p>
  <p><code>{redirect}</code></p>
  <p><a class="btn" href="{url}">Login with Zerodha</a></p>
  <p>After login you return here, then go to the desk:</p>
  <p><a href="{desk_ui_url()}/login">{desk_ui_url()}/login</a></p>
  <div class="warn"><strong>Kite app redirect URL</strong> must be exactly:
  <code>{redirect}</code></div>
  <p><a href="/docs">API docs</a></p>
</body></html>"""
    return HTMLResponse(html)


@app.get("/auth/callback", response_class=HTMLResponse)
async def auth_callback(
    request_token: str | None = None,
    status: str | None = None,
) -> HTMLResponse:
    """Kite OAuth redirect target — exchanges request_token automatically."""
    if not request_token:
        return HTMLResponse(
            "<h1>Missing request_token</h1><p>Start from <a href='/auth/login'>/auth/login</a></p>",
            status_code=400,
        )
    try:
        profile = exchange_request_token(request_token)
        from execution.ltp_cache import get_ltp_cache

        await get_ltp_cache().restart_if_authenticated()
        desk = desk_ui_url()
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Login OK</title>
<meta http-equiv="refresh" content="2;url={desk}/">
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 520px; margin: 48px auto; padding: 0 16px; }}
  .ok {{ color: #16a34a; font-weight: 600; }}
</style></head><body>
  <h1 class="ok">Logged in successfully</h1>
  <p>User: <strong>{profile.get('user_name') or profile.get('user_id')}</strong>
     ({profile.get('user_id')})</p>
  <p>Redirecting to the desk…</p>
  <p><a href="{desk}/">Open 3ST Algo Desk</a></p>
</body></html>"""
        return HTMLResponse(html)
    except Exception as e:
        from kite_errors import friendly_auth_error

        detail = friendly_auth_error(str(e))
        return HTMLResponse(
            f"<h1>Login failed</h1><p>{detail}</p>"
            f"<p><a href='{desk_ui_url()}/login'>Back to desk login</a> · "
            "<a href='/auth/login'>API login</a></p>",
            status_code=400,
        )


@app.post("/auth/session")
async def auth_session(body: SessionIn) -> dict[str, Any]:
    try:
        profile = exchange_request_token(body.request_token)
        from execution.ltp_cache import get_ltp_cache

        await get_ltp_cache().restart_if_authenticated()
        return {"ok": True, **profile, "session": session_status()}
    except Exception as e:
        raise _err(e) from e


@app.delete("/auth/session")
async def auth_logout() -> dict[str, Any]:
    from execution.ltp_cache import stop_ltp_feed

    clear_session()
    await stop_ltp_feed()
    return {"ok": True, "session": session_status()}


@app.get("/auth/me")
def auth_me() -> dict[str, Any]:
    """Fast session check (disk only). Does not call Kite on every poll."""
    try:
        return session_status()
    except Exception as e:
        raise _err(e) from e


@app.get("/auth/profile")
def auth_profile() -> dict[str, Any]:
    """Full session + live Kite profile (may be slow if api.kite.trade is unreachable)."""
    try:
        return status_bundle()
    except Exception as e:
        raise _err(e) from e


@app.get("/instruments")
def instruments(refresh: bool = False) -> dict[str, Any]:
    try:
        if refresh:
            _require_kite_session()
            refresh_instruments(force=True)
        return {"items": list_resolved()}
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/instruments/timeframes")
def instruments_timeframes() -> dict[str, Any]:
    return {"items": list(TIMEFRAMES.keys())}


@app.get("/instruments/search")
def instruments_search(
    q: str = Query("", min_length=0),
    segment: Literal["equity", "future", "option"] = "equity",
    limit: int = Query(25, ge=1, le=100),
    refresh: bool = False,
) -> dict[str, Any]:
    try:
        if refresh:
            _require_kite_session()
        items = search_instruments(q=q, segment=segment, limit=limit, force_refresh=refresh)
        return {"q": q, "segment": segment, "items": items}
    except RuntimeError as e:
        if "cache empty" in str(e).lower() or "log in" in str(e).lower():
            raise _err(e, 401) from e
        raise _err(e) from e
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/instruments/{instrument_token}")
def instrument_by_token(instrument_token: int) -> dict[str, Any]:
    try:
        return resolve_by_token(instrument_token)
    except Exception as e:
        raise _err(e) from e


@app.get("/options/expiries")
def options_expiries(underlying: str) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        return {"underlying": u, "expiries": list_expiries(u)}
    except Exception as e:
        raise _err(e) from e


@app.get("/options/chain")
def options_chain(underlying: str, expiry: str) -> dict[str, Any]:
    try:
        return get_chain(underlying, expiry)
    except Exception as e:
        raise _err(e) from e


@app.get("/options/templates")
def options_templates() -> dict[str, Any]:
    return {"items": [{"id": k, "label": v} for k, v in SPREAD_TEMPLATES.items()]}


@app.post("/options/spreads/preview")
def options_spread_preview(body: SpreadPreviewIn) -> dict[str, Any]:
    try:
        _require_kite_session()
        overrides = [leg.model_dump(exclude_none=True) for leg in body.legs] if body.legs else None
        return preview_spread(
            underlying=body.underlying,
            expiry=body.expiry,
            template=body.template,
            width_steps=body.width_steps,
            spot=body.spot,
            legs_override=overrides,
            ltp_fn=_kite_broker.ltp,
            otm_offset=body.otm_offset,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.post("/options/spreads/preview-directions")
def options_spread_preview_directions(body: SpreadConfigIn) -> dict[str, Any]:
    try:
        _require_kite_session()
        return build_direction_spreads(
            underlying=body.underlying,
            expiry=body.expiry,
            long_template=body.long_template,
            short_template=body.short_template,
            width_steps=body.width_steps,
            ltp_fn=_kite_broker.ltp,
            otm_offset=body.otm_offset,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/selection")
def selection_get() -> dict[str, Any]:
    return get_selection()


@app.post("/selection")
def selection_set(body: SelectionIn) -> dict[str, Any]:
    try:
        payload = body.model_dump(exclude_none=True)
        if body.spread:
            payload["spread"] = body.spread.model_dump()
        if body.instrument_token:
            resolve_by_token(body.instrument_token)
        saved = save_selection(payload)
        return {"ok": True, "selection": saved}
    except Exception as e:
        raise _err(e) from e


@app.delete("/selection")
def selection_delete() -> dict[str, Any]:
    return {"ok": True, "selection": clear_selection()}


@app.get("/watchlist")
def watchlist_get_all(
    status: str | None = Query(None, description="Filter: waiting, triggered, active, closed (comma-separated)"),
) -> dict[str, Any]:
    items = watchlist_list(status)
    return {"items": items, "count": len(items)}


@app.post("/watchlist")
def watchlist_add_item(body: SelectionIn) -> dict[str, Any]:
    try:
        payload = body.model_dump(exclude_none=True)
        if body.spread:
            payload["spread"] = body.spread.model_dump()
        if body.instrument_token:
            resolve_by_token(body.instrument_token)
        item = watchlist_add(payload)
        return {"ok": True, "item": item}
    except Exception as e:
        raise _err(e) from e


@app.delete("/watchlist/{item_id}")
def watchlist_delete_item(item_id: str) -> dict[str, Any]:
    try:
        return watchlist_remove(item_id)
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.post("/watchlist/{item_id}/activate")
def watchlist_activate_item(item_id: str) -> dict[str, Any]:
    try:
        item = watchlist_get(item_id)
        if not item:
            raise KeyError(item_id)
        if item.get("status") not in {"triggered", "waiting"}:
            raise RuntimeError(f"Cannot activate item in status '{item.get('status')}'")
        updated = activate_watchlist_item(item_id)
        return {"ok": True, "item": updated}
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.post("/watchlist/{item_id}/close")
def watchlist_close_item(item_id: str) -> dict[str, Any]:
    try:
        item = watchlist_get(item_id)
        if not item:
            raise KeyError(item_id)
        if item.get("status") == "active":
            updated = close_watchlist_trade(item_id, "manual_close")
        else:
            updated = watchlist_close(item_id)
        return {"ok": True, "item": updated}
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.get("/execution/queue")
def execution_queue_status() -> dict[str, Any]:
    try:
        return build_execution_queue()
    except Exception as e:
        raise _err(e) from e


@app.post("/execution/queue/{leg_id}/action")
def execution_queue_item_action(leg_id: str, body: QueueActionIn) -> dict[str, Any]:
    try:
        return queue_action(leg_id, body.action)
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.get("/live/workflow")
def live_workflow_status() -> dict[str, Any]:
    try:
        return get_workflow_status()
    except Exception as e:
        raise _err(e) from e


@app.get("/live/ltp-cache")
def live_ltp_cache_status() -> dict[str, Any]:
    """WebSocket LTP cache health (Aio-Trader KiteFeed + REST fallback)."""
    try:
        from execution.ltp_cache import get_ltp_cache

        return {"ok": True, **get_ltp_cache().status()}
    except Exception as e:
        raise _err(e) from e


@app.post("/live/ltp-cache/restart")
async def live_ltp_cache_restart() -> dict[str, Any]:
    try:
        from execution.ltp_cache import get_ltp_cache

        await get_ltp_cache().restart_if_authenticated()
        return {"ok": True, **get_ltp_cache().status()}
    except Exception as e:
        raise _err(e) from e


@app.get("/market/health")
def market_health_status() -> dict[str, Any]:
    """Market-data feed health + trade-management safety gate for the Live Desk badge."""
    try:
        from execution.ltp_cache import is_trade_management_safe, market_health

        health = market_health()
        safe, reason = is_trade_management_safe()
        return {"ok": True, **health, "trade_management_safe": safe, "trade_management_reason": reason}
    except Exception as e:
        raise _err(e) from e


@app.websocket("/ws/ltp")
async def ws_ltp(ws: WebSocket) -> None:
    """Push cached LTPs + feed health to the Live Desk every second (real-time, no polling)."""
    import asyncio
    import time as _time

    from execution.ltp_cache import get_ltp_cache, is_trade_management_safe, market_health

    await ws.accept()
    cache = get_ltp_cache()
    try:
        while True:
            safe, reason = is_trade_management_safe()
            await ws.send_json(
                {
                    "type": "ltp",
                    "ts": _time.time(),
                    "prices": cache.snapshot(),
                    "health": {
                        **market_health(),
                        "trade_management_safe": safe,
                        "trade_management_reason": reason,
                    },
                }
            )
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
    except Exception:
        try:
            await ws.close()
        except Exception:
            pass
        return


@app.post("/watchlist/{item_id}/execute-live")
def watchlist_execute_live(item_id: str, body: ManualEnterIn) -> dict[str, Any]:
    """Manual BUY/SELL on Kite — requires LIVE + ARMED."""
    try:
        validate_live_execution()
        updated = trigger_manual_side(
            item_id,
            side=body.side,
            signal=body.signal,
            require_exchange=True,
        )
        return {"ok": True, "item": updated, "note": "Exchange order placed — 3ST exit monitor active"}
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.post("/watchlist/{item_id}/trigger")
def watchlist_trigger_side(item_id: str, body: ManualEnterIn) -> dict[str, Any]:
    """Manual BUY or SELL — works on waiting, triggered, or active (no open leg) rows."""
    try:
        _require_kite_session()
        updated = trigger_manual_side(item_id, side=body.side, signal=body.signal)
        return {"ok": True, "item": updated}
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.post("/watchlist/{item_id}/enter")
def watchlist_manual_enter(item_id: str, body: ManualEnterIn) -> dict[str, Any]:
    try:
        _require_kite_session()
        updated = manual_enter_watchlist_item(item_id, side=body.side, signal=body.signal)
        return {"ok": True, "item": updated}
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.post("/watchlist/scan-exits")
def watchlist_scan_exits(auto_close: bool = Query(True)) -> dict[str, Any]:
    try:
        _require_kite_session()
        return scan_watchlist_exits(auto_close=auto_close)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/watchlist/{item_id}/exit-status")
def watchlist_exit_status(item_id: str) -> dict[str, Any]:
    try:
        _require_kite_session()
        item = watchlist_get(item_id)
        if not item:
            raise KeyError(item_id)
        return {"ok": True, "status": exit_status_for_item(item)}
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.post("/watchlist/scan")
def watchlist_scan(require_armed: bool = Query(False)) -> dict[str, Any]:
    try:
        _require_kite_session()
        return scan_watchlist(require_armed=require_armed)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/margins")
def get_margins() -> dict[str, Any]:
    try:
        return margins()
    except Exception as e:
        raise _err(e) from e


@app.get("/live/arm")
def live_arm_status() -> dict[str, Any]:
    return get_arm_state()


@app.post("/live/mode")
def live_mode(body: ModeIn) -> dict[str, Any]:
    try:
        return set_mode(body.mode)
    except Exception as e:
        raise _err(e) from e


@app.post("/live/arm")
def live_arm(body: ArmIn) -> dict[str, Any]:
    import time

    from execution.rolling_straddle_store import append_log

    t0 = time.perf_counter()
    try:
        if body.mode:
            set_mode(body.mode)
        result = arm(confirm=body.confirm)
        ms = round((time.perf_counter() - t0) * 1000, 1)
        append_log("arm", f"{'ARMED' if result.get('armed') else 'arm call'} in {ms}ms", {"ms": ms, "mode": result.get("mode")})
        return {**result, "server_ms": ms}
    except Exception as e:
        raise _err(e) from e


@app.post("/live/disarm")
def live_disarm() -> dict[str, Any]:
    import time

    from execution.rolling_straddle_store import append_log

    t0 = time.perf_counter()
    result = disarm()
    ms = round((time.perf_counter() - t0) * 1000, 1)
    append_log("disarm", f"DISARMED in {ms}ms", {"ms": ms})
    return {**result, "server_ms": ms}


@app.post("/live/margin-preview")
def live_margin_preview(body: MarginPreviewIn) -> dict[str, Any]:
    """Estimate Kite margin before manual entry."""
    try:
        _require_kite_session()
        data = preview_order_margins(
            exchange=body.exchange,
            tradingsymbol=body.tradingsymbol,
            transaction_type=body.transaction_type,
            quantity=body.quantity,
            product=body.product,
            order_type=body.order_type,
            price=body.price,
        )
        return {"ok": True, "margin": data}
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.post("/live/panic")
def live_panic(body: PanicIn) -> dict[str, Any]:
    """
    Emergency stop: close active watchlist trades, cancel open 3ST orders, DISARM.
    Requires confirm=true.
    """
    try:
        if not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="Panic requires confirm=true — closes positions and cancels 3ST orders",
            )
        from execution.panic import run_panic

        return run_panic(
            cancel_orders=body.cancel_orders,
            close_positions=body.close_positions,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/live/reconcile")
def live_reconcile_report() -> dict[str, Any]:
    """Dry-run reconciliation report (broker vs local state) — no local mutations."""
    try:
        from execution.reconcile import reconcile_live_desk

        return reconcile_live_desk(apply_changes=False)
    except Exception as e:
        raise _err(e) from e


@app.post("/live/reconcile")
def live_reconcile_apply(body: ReconcileIn) -> dict[str, Any]:
    """Apply reconciliation: close stale local trades, refresh from broker, optionally adopt orphans."""
    try:
        from execution.reconcile import reconcile_live_desk

        return reconcile_live_desk(apply_changes=True, adopt_orphans=body.adopt_orphans)
    except Exception as e:
        raise _err(e) from e


@app.get("/risk/limits")
def risk_get() -> dict[str, Any]:
    out = get_limits()
    try:
        from execution.order_executor import _open_position_count
        from execution.positions_view import get_desk_broker

        broker, mode = get_desk_broker()
        out["open_positions"] = _open_position_count(broker)
        out["mode"] = mode
    except Exception:
        out["open_positions"] = 0
    return out


@app.post("/risk/limits")
def risk_set(body: RiskIn) -> dict[str, Any]:
    return update_limits(**body.model_dump(exclude_none=True))


@app.get("/live/positions")
def live_positions() -> dict[str, Any]:
    try:
        return build_positions_view()
    except Exception as e:
        raise _err(e) from e


@app.get("/live/active-trades")
def live_active_trades() -> dict[str, Any]:
    try:
        return build_active_trades_view()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/adopt-positions")
def live_adopt_positions() -> dict[str, Any]:
    """Link open Kite positions to watchlist for 3ST exit monitoring."""
    try:
        _require_kite_session()
        return adopt_open_positions()
    except Exception as e:
        raise _err(e) from e


@app.post("/watchlist/{item_id}/sync-entry")
def watchlist_sync_entry(item_id: str) -> dict[str, Any]:
    try:
        updated = sync_active_trade_entry(item_id)
        return {"ok": True, "item": updated}
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.get("/live/orders")
def live_orders() -> dict[str, Any]:
    state = get_arm_state()
    if state["mode"] != "live":
        sync_paper_from_rolling_straddle()
        broker = get_paper_broker()
    else:
        broker = _kite_broker
    try:
        return {"mode": state["mode"], "orders": broker.orders()}
    except Exception as e:
        raise _err(e) from e


@app.get("/live/rolling-straddle/config")
def rolling_straddle_get_config() -> dict[str, Any]:
    return rs_get_config()


@app.post("/live/rolling-straddle/config")
def rolling_straddle_set_config(body: RollingStraddleConfigIn) -> dict[str, Any]:
    try:
        saved = rs_save_config(body.model_dump())
        return {"ok": True, "config": saved}
    except Exception as e:
        raise _err(e) from e


@app.get("/live/rolling-straddle/status")
def rolling_straddle_status(light: bool = Query(False)) -> dict[str, Any]:
    try:
        bundle = rs_status_bundle(sync_broker=not light)
        bundle["scheduler"] = scheduler_status()
        return bundle
    except Exception as e:
        raise _err(e) from e


@app.get("/live/rolling-straddle/log")
def rolling_straddle_log(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    return {"items": rs_get_log(limit)}


@app.post("/live/rolling-straddle/start")
def rolling_straddle_start() -> dict[str, Any]:
    try:
        return start_runner()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/rolling-straddle/stop")
def rolling_straddle_stop() -> dict[str, Any]:
    try:
        return stop_runner()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/rolling-straddle/tick")
def rolling_straddle_tick_manual() -> dict[str, Any]:
    """Manual tick for testing."""
    try:
        return tick()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/rolling-straddle/close-all")
def rolling_straddle_close_all() -> dict[str, Any]:
    try:
        return close_all()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/rolling-straddle/close-leg")
def rolling_straddle_close_leg(body: CloseLegIn) -> dict[str, Any]:
    try:
        return close_leg(body.leg)
    except Exception as e:
        raise _err(e) from e


@app.post("/live/rolling-straddle/unlink-leg")
def rolling_straddle_unlink_leg(body: CloseLegIn) -> dict[str, Any]:
    try:
        return unlink_leg(body.leg)
    except Exception as e:
        raise _err(e) from e


@app.post("/live/rolling-straddle/adopt-leg")
def rolling_straddle_adopt_leg(body: CloseLegIn) -> dict[str, Any]:
    try:
        return adopt_leg(body.leg)
    except Exception as e:
        raise _err(e) from e


@app.get("/live/survivor/config")
def survivor_get_config_route() -> dict[str, Any]:
    return survivor_get_config()


@app.post("/live/survivor/config")
def survivor_set_config(body: SurvivorConfigIn) -> dict[str, Any]:
    try:
        _require_kite_session()
        saved = survivor_save_config(body.model_dump())
        return {"ok": True, "config": saved}
    except Exception as e:
        raise _err(e) from e


@app.get("/live/survivor/status")
def survivor_status() -> dict[str, Any]:
    try:
        bundle = survivor_status_bundle()
        bundle["scheduler"] = scheduler_status()
        return bundle
    except Exception as e:
        raise _err(e) from e


@app.get("/live/survivor/log")
def survivor_log(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    return {"items": survivor_get_log(limit)}


@app.post("/live/survivor/start")
def survivor_start() -> dict[str, Any]:
    try:
        _require_kite_session()
        return survivor_start_runner()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/survivor/stop")
def survivor_stop() -> dict[str, Any]:
    try:
        return survivor_stop_runner()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/survivor/tick")
def survivor_tick_manual() -> dict[str, Any]:
    try:
        _require_kite_session()
        return survivor_tick()
    except Exception as e:
        raise _err(e) from e


@app.get("/live/wave/config")
def wave_get_config_route() -> dict[str, Any]:
    return wave_get_config()


@app.post("/live/wave/config")
def wave_set_config(body: WaveConfigIn) -> dict[str, Any]:
    try:
        _require_kite_session()
        saved = wave_save_config(body.model_dump())
        return {"ok": True, "config": saved}
    except Exception as e:
        raise _err(e) from e


@app.get("/live/wave/status")
def wave_status() -> dict[str, Any]:
    try:
        bundle = wave_status_bundle()
        bundle["scheduler"] = scheduler_status()
        return bundle
    except Exception as e:
        raise _err(e) from e


@app.get("/live/wave/log")
def wave_log(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    return {"items": wave_get_log(limit)}


@app.post("/live/wave/start")
def wave_start() -> dict[str, Any]:
    try:
        _require_kite_session()
        return wave_start_runner()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/wave/stop")
def wave_stop() -> dict[str, Any]:
    try:
        return wave_stop_runner()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/wave/tick")
def wave_tick_manual() -> dict[str, Any]:
    try:
        _require_kite_session()
        return wave_tick()
    except Exception as e:
        raise _err(e) from e


@app.get("/live/premium-book/config")
def premium_book_get_config_route() -> dict[str, Any]:
    return premium_book_get_config()


@app.post("/live/premium-book/config")
def premium_book_set_config(body: PremiumBookConfigIn) -> dict[str, Any]:
    try:
        _require_kite_session()
        saved = premium_book_save_config(body.model_dump())
        return {"ok": True, "config": saved}
    except Exception as e:
        raise _err(e) from e


@app.get("/live/premium-book/status")
def premium_book_status() -> dict[str, Any]:
    try:
        bundle = premium_book_status_bundle()
        bundle["scheduler"] = scheduler_status()
        return bundle
    except Exception as e:
        raise _err(e) from e


@app.get("/live/premium-book/log")
def premium_book_log(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    return {"items": premium_book_get_log(limit)}


@app.post("/live/premium-book/start")
def premium_book_start() -> dict[str, Any]:
    try:
        _require_kite_session()
        return premium_book_start_runner()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/premium-book/stop")
def premium_book_stop() -> dict[str, Any]:
    try:
        return premium_book_stop_runner()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/premium-book/tick")
def premium_book_tick_manual() -> dict[str, Any]:
    try:
        _require_kite_session()
        return premium_book_tick()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/premium-book/close")
def premium_book_close() -> dict[str, Any]:
    try:
        _require_kite_session()
        return premium_book_close_all()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/premium-book/revoke-buy-hold")
def premium_book_revoke_buy_hold_route() -> dict[str, Any]:
    """Disable Buy & Hold (trade_bias → sell_premium) and flatten any open buy package."""
    try:
        _require_kite_session()
        return premium_book_revoke_buy_hold()
    except Exception as e:
        raise _err(e) from e


@app.post("/live/premium-book/preview")
def premium_book_preview_route() -> dict[str, Any]:
    try:
        _require_kite_session()
        return premium_book_preview()
    except Exception as e:
        raise _err(e) from e


@app.get("/backtest/limits")
def backtest_limits(
    source: Literal["yahoo", "kite"] = "kite",
    timeframe: str = "15min",
) -> dict[str, Any]:
    if timeframe not in TIMEFRAMES:
        raise _err(RuntimeError(f"Unknown timeframe {timeframe}"))
    if source == "kite":
        start, end = default_kite_date_range(timeframe)
        return {
            "source": "kite",
            "timeframe": timeframe,
            "max_days": kite_max_lookback_days(timeframe),
            "default_start": start.isoformat(),
            "default_end": end.isoformat(),
            "note": "Kite intraday history (up to ~400 days for 15/30/60min). Login required.",
        }
    start, end = default_date_range(timeframe)
    return {
        "source": "yahoo",
        "timeframe": timeframe,
        "max_days": yahoo_max_days(timeframe),
        "default_start": start.isoformat(),
        "default_end": end.isoformat(),
        "note": "Yahoo free data — ~60 days max for intraday. NIFTY50/SENSEX only.",
    }


@app.post("/backtest/run")
def backtest_run(body: BacktestIn) -> dict[str, Any]:
    try:
        target = _resolve_backtest_target(body)
        bt_body: BacktestIn = target.get("body") or body
        timeframe = target["timeframe"]
        if timeframe not in TIMEFRAMES:
            raise RuntimeError(f"Unknown timeframe {timeframe}")

        session = _session_meta(bt_body)
        segment = target.get("segment") or bt_body.segment
        lot_size = int(target.get("lot_size") or bt_body.lot_size or 0)
        use_lot = segment in ("future", "option") and lot_size > 0

        if body.source == "kite":
            from datetime import timedelta

            _require_kite_session()
            if body.use_max or body.start is None or body.end is None:
                start, end = default_kite_date_range(timeframe)
            else:
                start, end = body.start, body.end
                max_days = kite_max_lookback_days(timeframe)
                earliest = date.today() - timedelta(days=max_days)
                if start < earliest:
                    start = earliest
                if end > date.today():
                    end = date.today()

            if target["instrument_token"]:
                resolve_by_token(target["instrument_token"])
            elif target["instrument_key"]:
                resolve_instrument(target["instrument_key"])

            df = fetch_historical_for_selection(
                instrument_token=target["instrument_token"],
                instrument_key=target["instrument_key"],
                timeframe=timeframe,
                start=start,
                end=end,
            )
            source_meta = {
                "source": "kite",
                "start": str(start),
                "end": str(end),
                "bars": 0,
                "max_days": kite_max_lookback_days(timeframe),
                "instrument_token": target["instrument_token"],
                "tradingsymbol": target["tradingsymbol"],
            }
        else:
            yahoo_key = _yahoo_key_for_target(target)
            if yahoo_key is None:
                raise RuntimeError(
                    "Yahoo source only supports NIFTY50/SENSEX (search 'NIFTY 50'). "
                    "Use source=kite for other instruments."
                )
            df = yahoo_fetch(
                instrument=yahoo_key,
                timeframe=timeframe,
                start=body.start,
                end=body.end,
                use_max=body.use_max,
            )
            source_meta = {
                "source": "yahoo",
                "start": str(df.index.min().date()) if len(df) else None,
                "end": str(df.index.max().date()) if len(df) else None,
                "bars": len(df),
                "max_days": yahoo_max_days(timeframe),
            }

        if df.empty:
            raise RuntimeError("No candles returned")

        params = BacktestParams(
            atr1=bt_body.atr1,
            factor1=bt_body.factor1,
            atr2=bt_body.atr2,
            factor2=bt_body.factor2,
            atr3=bt_body.atr3,
            factor3=bt_body.factor3,
            st1_enabled=bt_body.st1_enabled,
            st2_enabled=bt_body.st2_enabled,
            st3_enabled=bt_body.st3_enabled,
            adx_enabled=bt_body.adx_enabled,
            adx_period=bt_body.adx_period,
            adx_threshold=bt_body.adx_threshold,
            st_method=bt_body.st_method,
            trade_mode=bt_body.trade_mode,
            system_mode=bt_body.system_mode,
            session_start=session["session_start"],
            session_end=session["session_end"],
            force_exit=session["force_exit"],
            tgt_mode=bt_body.tgt_mode,
            tgt_value=bt_body.tgt_value,
            sl_mode=bt_body.sl_mode,
            sl_value=bt_body.sl_value,
            tsl_mode=bt_body.tsl_mode,
            tsl_value=bt_body.tsl_value,
            lot_size=float(lot_size or 1),
            use_lot_multiplier=use_lot,
        )
        source_meta["bars"] = len(df)
        result = run_backtest(df, params)
        tdf = trades_to_df(result.trades)
        equity = result.equity
        eq_points = []
        if equity is not None and len(equity):
            step = max(1, len(equity) // 500)
            for ts, val in equity.iloc[::step].items():
                eq_points.append({"t": ts.isoformat(), "v": float(val)})

        metrics = dict(result.metrics)
        if metrics.get("net_points") is None:
            metrics["net_points"] = metrics.get("net_pnl", 0.0)

        candles_tail = df.tail(300).reset_index()
        first = candles_tail.columns[0]
        candles_tail = candles_tail.rename(columns={first: "datetime"})
        candles_tail["datetime"] = candles_tail["datetime"].astype(str)

        return {
            "ok": True,
            "meta": {
                **source_meta,
                "instrument": target["label"],
                "instrument_key": target["instrument_key"],
                "timeframe": timeframe,
                "bars": len(df),
            },
            "metrics": metrics,
            "trades": tdf.to_dict(orient="records") if not tdf.empty else [],
            "equity": eq_points,
            "candles": candles_tail.to_dict(orient="records"),
        }
    except Exception as e:
        raise _err(e) from e


@app.get("/oi-tracker/config")
def oi_tracker_config() -> dict[str, Any]:
    return tracker_config()


@app.get("/oi-tracker/log")
def oi_tracker_log(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
    return {"items": oi_get_log(limit)}


@app.get("/oi-tracker/snapshot")
def oi_tracker_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD; default nearest weekly"),
    options_count: int | None = Query(None, ge=1, le=15),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        _require_kite_session()
        return build_snapshot(u, expiry=expiry, options_count=options_count)
    except HTTPException:
        raise
    except Exception as e:
        append_log("error", str(e), {"underlying": underlying.upper(), "expiry": expiry})
        raise _err(e) from e


@app.get("/oi-movers/config")
def oi_movers_config() -> dict[str, Any]:
    return movers_config()


@app.get("/oi-movers/snapshot")
def oi_movers_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD; default nearest weekly"),
    options_count: int | None = Query(None, ge=1, le=15),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        _require_kite_session()
        return build_movers_snapshot(u, expiry=expiry, options_count=options_count)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/oi-var/config")
def oi_var_config() -> dict[str, Any]:
    return var_config()


@app.get("/oi-var/snapshot")
def oi_var_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD; default nearest weekly"),
    top_n: int | None = Query(None, ge=1, le=25),
    dvar_mode: str | None = Query(None, description="oi_mark | true"),
    multi_expiry: bool = Query(False, description="Include next-expiry VAR summary"),
    gamma_context: bool = Query(False, description="Attach Gamma walls/flip badges (slower)"),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        _require_kite_session()
        return build_var_snapshot(
            u,
            expiry=expiry,
            top_n=top_n,
            dvar_mode=dvar_mode,
            include_multi_expiry=multi_expiry,
            include_gamma_context=gamma_context,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/gamma-density/config")
def gamma_density_config() -> dict[str, Any]:
    return gamma_config()


@app.get("/gamma-density/snapshot")
def gamma_density_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD; default nearest weekly"),
    strike_window: int | None = Query(None, ge=1, le=60),
    sign_mode: str | None = Query(
        None,
        description="naive | customer | oi_delta — dealer gamma sign convention",
    ),
    multi_expiry: bool = Query(True, description="Include next-expiry GEX stack"),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        prov = get_gamma_density_provider()
        if prov.requires_session():
            _require_kite_session()
        return build_gamma_snapshot(
            u,
            expiry=expiry,
            strike_window=strike_window,
            sign_mode=sign_mode,
            include_multi_expiry=multi_expiry,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/vanna-exposure/config")
def vanna_exposure_config() -> dict[str, Any]:
    return vanna_config()


@app.get("/vanna-exposure/snapshot")
def vanna_exposure_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD; default nearest weekly"),
    strike_window: int | None = Query(None, ge=1, le=60),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        prov = get_gamma_density_provider()
        if prov.requires_session():
            _require_kite_session()
        return build_vanna_snapshot(u, expiry=expiry, strike_window=strike_window)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/greeks/config")
def greeks_engine_api_config() -> dict[str, Any]:
    return greeks_desk_config()


@app.get("/greeks/snapshot")
def greeks_engine_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD; default nearest weekly"),
    strike_window: int | None = Query(None, ge=1, le=60),
    theta_mode: str | None = Query(
        None, description="calendar | trading_hours — NSE theta decay mode"
    ),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        prov = get_gamma_density_provider()
        if prov.requires_session():
            _require_kite_session()
        return build_greeks_snapshot(
            u,
            expiry=expiry,
            strike_window=strike_window,
            theta_mode=theta_mode,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/trade-suggestions/config")
def trade_suggestions_config() -> dict[str, Any]:
    return suggestions_config()


@app.get("/trade-suggestions/snapshot")
def trade_suggestions_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD; default nearest weekly"),
    strike_window: int | None = Query(None, ge=1, le=60),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        prov = get_gamma_density_provider()
        if prov.requires_session():
            _require_kite_session()
        return build_trade_suggestions(u, expiry=expiry, strike_window=strike_window)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/pricing/config")
def pricing_engine_config() -> dict[str, Any]:
    return pricing_config()


@app.get("/pricing/desk")
def pricing_engine_desk(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str = Query(..., description="YYYY-MM-DD"),
    strike_count: int | None = Query(None, ge=3, le=41),
    include_heston: bool = Query(False),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        _require_kite_session()
        return build_pricing_desk(
            u,
            expiry,
            strike_count=strike_count,
            include_heston=include_heston,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.post("/pricing/calculate")
def pricing_engine_calculate(body: PricingCalculateIn) -> dict[str, Any]:
    try:
        return price_single(
            spot=body.spot,
            strike=body.strike,
            option_type=body.option_type,
            market_price=body.market_price,
            iv=body.iv,
            tte_years=body.tte_years,
            expiry=body.expiry,
            risk_free_rate=body.risk_free_rate,
            include_heston=body.include_heston,
            heston_overrides=body.heston_overrides,
        )
    except Exception as e:
        raise _err(e) from e


@app.get("/oi-profile/config")
def oi_profile_get_config() -> dict[str, Any]:
    return oi_profile_config()


@app.get("/oi-profile/snapshot")
def oi_profile_get_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | FINNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD future expiry; default front-month"),
    interval: str | None = Query(None, description="1min | 5min | 15min"),
    days: int | None = Query(None, ge=1, le=30),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        allowed = {s.upper() for s in OI_PROFILE_DEFAULTS["underlyings"]}
        if u not in allowed:
            raise RuntimeError(f"Unknown underlying. Use {sorted(allowed)}")
        _require_kite_session()
        return oi_profile_snapshot(u, expiry=expiry, interval=interval, days=days)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/latency/stats")
def latency_stats() -> dict[str, Any]:
    return latency_get_stats()


@app.get("/latency/recent")
def latency_recent(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    return {"items": latency_read_recent(limit)}


@app.get("/vol-surface/config")
def vol_surface_get_config() -> dict[str, Any]:
    return vol_surface_config()


@app.get("/vol-surface/snapshot")
def vol_surface_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiries: str | None = Query(None, description="Comma-separated YYYY-MM-DD; default nearest N"),
    strike_count: int | None = Query(None, ge=5, le=40),
    max_expiries: int | None = Query(None, ge=1, le=8),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        _require_kite_session()
        exp_list = [e.strip() for e in expiries.split(",") if e.strip()] if expiries else None
        return build_vol_surface(
            u,
            exp_list,
            strike_count=strike_count,
            max_expiries=max_expiries,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/iv-smile/config")
def iv_smile_get_config() -> dict[str, Any]:
    return iv_smile_config()


@app.get("/iv-smile/snapshot")
def iv_smile_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str = Query(..., description="YYYY-MM-DD"),
    strike_count: int | None = Query(None, ge=5, le=41),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        _require_kite_session()
        return build_iv_smile(u, expiry, strike_count=strike_count)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/arbitrage/config")
def arbitrage_get_config() -> dict[str, Any]:
    return calendar_arbitrage_config()


@app.get("/arbitrage/universe")
def arbitrage_universe(
    exchanges: str | None = Query(None, description="Comma-separated e.g. NFO,MCX"),
) -> dict[str, Any]:
    try:
        ex_list = [e.strip() for e in exchanges.split(",") if e.strip()] if exchanges else None
        return build_arbitrage_universe(ex_list)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/arbitrage/snapshot")
def arbitrage_snapshot(
    exchanges: str | None = Query(None, description="Comma-separated e.g. NFO,MCX"),
) -> dict[str, Any]:
    try:
        _require_kite_session()
        ex_list = [e.strip() for e in exchanges.split(",") if e.strip()] if exchanges else None
        return build_arbitrage_snapshot(ex_list)
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.get("/rrg/config")
def rrg_get_config() -> dict[str, Any]:
    return rrg_config()


@app.get("/rrg/fpi")
def rrg_fpi_status() -> dict[str, Any]:
    return fpi_status()


@app.get("/rrg/fpi/latest")
def rrg_fpi_latest(
    period: str = Query("period2", description="period1 | period2 | month_total"),
) -> dict[str, Any]:
    try:
        data = load_fpi_sectors()
        return {
            "ok": True,
            "period": period,
            "as_of": data.get("as_of"),
            "period1_label": data.get("period1_label"),
            "period2_label": data.get("period2_label"),
            "fetched_at": data.get("fetched_at"),
            "source_url": data.get("source_url"),
            "stale": bool(data.get("stale")),
            "sectors": data.get("sectors") or {},
        }
    except Exception as e:
        raise _err(e) from e


@app.post("/rrg/fpi/refresh")
def rrg_fpi_refresh() -> dict[str, Any]:
    try:
        data = load_fpi_sectors(force_refresh=True)
        return {
            "ok": True,
            "sector_count": len(data.get("sectors") or {}),
            "fetched_at": data.get("fetched_at"),
            "stale": bool(data.get("stale")),
        }
    except Exception as e:
        raise _err(e) from e


@app.get("/rrg/snapshot")
def rrg_snapshot(
    benchmark: str = Query("NIFTY50", description="NIFTY50 | BANKNIFTY50 | SENSEX"),
    symbols: str = Query(
        ...,
        min_length=1,
        description="Comma-separated equity symbols or sector ids (e.g. NIFTY_IT)",
    ),
    window: int = Query(14, ge=5, le=52),
    period: int = Query(52, ge=10, le=104),
    tail: int = Query(4, ge=2, le=12),
    base_date: str | None = Query(None, description="Optional ISO base date for RS momentum"),
    include_fpi: bool = Query(True, description="Attach NSDL FPI sector equity overlay"),
    fpi_period: str = Query("period2", description="period1 | period2 | month_total"),
) -> dict[str, Any]:
    try:
        _require_kite_session()
        sym_list = [s.strip() for s in symbols.split(",") if s.strip()]
        if not sym_list:
            raise HTTPException(status_code=400, detail="At least one symbol is required")
        if len(sym_list) > 50:
            raise HTTPException(status_code=400, detail="Maximum 50 symbols per request")
        return build_rrg_snapshot(
            benchmark=benchmark,
            symbols=sym_list,
            window=window,
            period=period,
            tail=tail,
            base_date=base_date,
            include_fpi=include_fpi,
            fpi_period=fpi_period,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.post("/rrg/cache/clear")
def rrg_clear_cache() -> dict[str, bool]:
    clear_rrg_daily_cache()
    return {"ok": True}


@app.get("/analogue/config")
def analogue_get_config() -> dict[str, Any]:
    return analogue_config()


@app.get("/analogue/snapshot")
def analogue_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    cycle_kind: str = Query("monthly", description="monthly | weekly"),
    similarity_band_pct: float | None = Query(None, ge=0.5, le=15.0),
    override_move_pct: float | None = Query(
        None,
        ge=-30.0,
        le=30.0,
        description="Optional what-if move %% at current day-in-cycle",
    ),
    lookback_days: int | None = Query(None, ge=120, le=2500),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        _require_kite_session()
        return build_analogue_snapshot(
            u,
            cycle_kind=cycle_kind,
            similarity_band_pct=similarity_band_pct,
            override_move_pct=override_move_pct,
            lookback_days=lookback_days,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise _err(e) from e


@app.post("/backtest/rolling-atm")
def backtest_rolling_atm(body: RollingAtmBacktestIn) -> dict[str, Any]:
    try:
        target = _resolve_backtest_target(body)
        bt_body: BacktestIn = target.get("body") or body
        timeframe = target["timeframe"]
        if timeframe not in TIMEFRAMES:
            raise RuntimeError(f"Unknown timeframe {timeframe}")

        if body.source == "kite":
            _require_kite_session()
            from datetime import timedelta

            if body.use_max or body.start is None or body.end is None:
                start, end = default_kite_date_range(timeframe)
            else:
                start, end = body.start, body.end
                max_days = kite_max_lookback_days(timeframe)
                earliest = date.today() - timedelta(days=max_days)
                if start < earliest:
                    start = earliest
            if target["instrument_token"]:
                resolve_by_token(target["instrument_token"])
            elif target["instrument_key"]:
                resolve_instrument(target["instrument_key"])
            df = fetch_historical_for_selection(
                instrument_token=target["instrument_token"],
                instrument_key=target["instrument_key"],
                timeframe=timeframe,
                start=start,
                end=end,
            )
        else:
            yahoo_key = _yahoo_key_for_target(target)
            if yahoo_key is None:
                raise RuntimeError("Yahoo source only supports NIFTY50/SENSEX")
            if body.use_max or body.start is None or body.end is None:
                start, end = default_date_range(timeframe)
            else:
                start, end = body.start, body.end
            df = yahoo_fetch(yahoo_key, timeframe, start, end)

        params = RollingAtmParams(
            atr1=body.atr1,
            factor1=body.factor1,
            atr2=body.atr2,
            factor2=body.factor2,
            atr3=body.atr3,
            factor3=body.factor3,
            st1_enabled=body.st1_enabled,
            st2_enabled=body.st2_enabled,
            st3_enabled=body.st3_enabled,
            adx_enabled=body.adx_enabled,
            adx_period=body.adx_period,
            adx_threshold=body.adx_threshold,
            st_method=body.st_method,
            trade_mode=body.trade_mode,
            system_mode=body.system_mode,
            session_start=body.session_start,
            session_end=body.session_end,
            force_exit=body.force_exit,
            sl_mode=body.sl_mode,
            sl_value=body.sl_value,
            tgt_mode=body.tgt_mode,
            tgt_value=body.tgt_value,
            tsl_mode=body.tsl_mode,
            tsl_value=body.tsl_value,
            max_reentries_ce=body.max_reentries_ce,
            max_reentries_pe=body.max_reentries_pe,
            reentry_style=body.reentry_style,
            allow_dual_open=body.allow_dual_open,
            entry_start=body.entry_start,
            underlying=body.underlying,
        )
        result = run_rolling_atm_backtest(df, params)
        return {
            "meta": {"bars": len(df), "timeframe": timeframe, "underlying": body.underlying},
            "metrics": result.metrics,
            "trades": [
                {
                    "leg": t.leg,
                    "side": t.side,
                    "entry_time": str(t.entry_time),
                    "exit_time": str(t.exit_time) if t.exit_time else None,
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "reason": t.reason,
                    "strike": t.strike,
                }
                for t in result.trades
            ],
        }
    except Exception as e:
        raise _err(e) from e


@app.get("/api/meta")
def api_meta() -> dict[str, str | bool]:
    from api.ui_static import ui_build_available

    return {
        "service": "3ST Kite Algo API",
        "docs": "/docs",
        "ui_bundled": ui_build_available(),
    }


from api.ui_static import mount_ui, ui_build_available

if not ui_build_available():
    @app.get("/", include_in_schema=False)
    def root_no_ui() -> dict[str, str]:
        return {
            "service": "3ST Kite Algo API",
            "docs": "/docs",
            "ui_hint": "Run: cd 'Pixel Perfect UI' && npm run build",
        }

mount_ui(app)
