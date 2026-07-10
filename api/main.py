"""FastAPI backend for Lovable UI + Kite Connect algo platform."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
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
    TIMEFRAMES,
    YAHOO_MAX_DAYS,
)
from execution.arming import arm, disarm, get_arm_state, set_mode
from execution.rolling_straddle import close_all, close_leg, start_runner, status_bundle as rs_status_bundle, stop_runner, tick
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
from execution.scheduler import scheduler_status, start_scheduler, stop_scheduler
from execution.watchlist_runner import scan_watchlist
from instruments import (
    list_resolved,
    refresh_instruments,
    resolve_by_token,
    resolve_instrument,
    search_instruments,
)
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
    status_bundle,
)
from options.chain import get_chain, list_expiries
from options.oi_tracker import build_snapshot, tracker_config
from options.oi_tracker_store import append_log, get_log as oi_get_log
from options.oi_var import build_var_snapshot, var_config
from options.spreads import SPREAD_TEMPLATES, build_direction_spreads, preview_spread
from risk.limits import get_limits, update_limits
from selection_store import clear_selection, get_selection, save_selection
from watchlist_store import add_item as watchlist_add
from watchlist_store import get_item as watchlist_get
from watchlist_store import list_items as watchlist_list
from watchlist_store import mark_active as watchlist_activate
from watchlist_store import mark_closed as watchlist_close
from watchlist_store import remove_item as watchlist_remove
from settings import kite_credentials, kite_ready
from yahoo_client import default_date_range, fetch_candles as yahoo_fetch, max_lookback_days as yahoo_max_days


@asynccontextmanager
async def _lifespan(app: FastAPI):
    sync_paper_from_rolling_straddle()
    start_scheduler()
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
    yield
    stop_scheduler()


app = FastAPI(title="3ST Kite Algo API", version="0.2.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
        "https://*.lovable.app",
        "https://*.lovable.dev",
        "*",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_paper = get_paper_broker()
_kite_broker = KiteBroker()

SpreadTemplate = Literal[
    "bull_call",
    "bear_put",
    "bear_call",
    "bull_put",
    "iron_condor",
]


class SessionIn(BaseModel):
    request_token: str


class ArmIn(BaseModel):
    confirm: bool = False


class ModeIn(BaseModel):
    mode: Literal["paper", "live"]


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
    spot: float | None = None
    legs: list[SpreadLegOverride] | None = None


class SpreadConfigIn(BaseModel):
    underlying: str
    expiry: str
    long_template: SpreadTemplate = "bull_call"
    short_template: SpreadTemplate = "bear_call"
    width_steps: int = 1
    legs_long: list[dict[str, Any]] | None = None
    legs_short: list[dict[str, Any]] | None = None


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
    underlying: Literal["NIFTY", "BANKNIFTY", "SENSEX"] = "NIFTY"
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
    trade_mode: Literal["Both", "LongOnly", "ShortOnly"] = "Both"
    max_reentries_ce: int = 1
    max_reentries_pe: int = 1
    reentry_style: Literal["zone_active", "edge_only"] = "zone_active"
    allow_dual_open: bool = True
    auto_start_on_boot: bool = False
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

    @model_validator(mode="after")
    def _require_enabled_st(self) -> RollingStraddleConfigIn:
        if not (self.st1_enabled or self.st2_enabled or self.st3_enabled):
            raise ValueError("At least one SuperTrend (ST1, ST2, or ST3) must be enabled")
        return self


class CloseLegIn(BaseModel):
    leg: Literal["ce", "pe"]


class SurvivorConfigIn(BaseModel):
    underlying: Literal["NIFTY", "BANKNIFTY", "SENSEX"] = "NIFTY"
    expiry: str = ""
    symbol_initials: str = ""
    index_symbol: str = "NSE:NIFTY 50"
    pe_gap: int = 20
    ce_gap: int = 20
    pe_quantity: int = 75
    ce_quantity: int = 75
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
    symbol_name: str = "NIFTY25SEPFUT"
    exchange: str = "NFO"
    buy_gap: float = 25
    sell_gap: float = 25
    buy_quantity: int = 75
    sell_quantity: int = 75
    lot_size: int = 75
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


def _err(e: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(e))


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
    return {
        "ok": True,
        "kite_configured": kite_ready(),
        "kite_authenticated": session_status().get("authenticated", False),
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
            "hint": "Easiest: open http://127.0.0.1:8000/auth/login in browser",
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
  <p>After login, Zerodha redirects to the URL above. It must match your Kite app
  <strong>Redirect URL</strong> exactly.</p>
  <div class="warn"><strong>Use port 8001:</strong>
  <a href="http://127.0.0.1:8001/auth/login">http://127.0.0.1:8001/auth/login</a>
  if port 8000 gives Not Found after login.</div>
  <p><a href="/docs">API docs</a></p>
</body></html>"""
    return HTMLResponse(html)


@app.get("/auth/callback", response_class=HTMLResponse)
def auth_callback(
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
        sess = session_status()
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Login OK</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 520px; margin: 48px auto; padding: 0 16px; }}
  .ok {{ color: #16a34a; font-weight: 600; }}
</style></head><body>
  <h1 class="ok">Logged in successfully</h1>
  <p>User: <strong>{profile.get('user_name') or profile.get('user_id')}</strong>
     ({profile.get('user_id')})</p>
  <p>Status: {status or 'success'}</p>
  <ul>
    <li><a href="/auth/me">Check session JSON</a></li>
    <li><a href="/docs">Swagger API</a></li>
    <li><a href="/instruments?refresh=true">Refresh instruments</a></li>
  </ul>
</body></html>"""
        return HTMLResponse(html)
    except Exception as e:
        return HTMLResponse(
            f"<h1>Login failed</h1><p>{e}</p>"
            "<p>Get a fresh login from <a href='/auth/login'>/auth/login</a> "
            "(tokens are one-time).</p>",
            status_code=400,
        )


@app.post("/auth/session")
def auth_session(body: SessionIn) -> dict[str, Any]:
    try:
        profile = exchange_request_token(body.request_token)
        return {"ok": True, **profile, "session": session_status()}
    except Exception as e:
        raise _err(e) from e


@app.delete("/auth/session")
def auth_logout() -> dict[str, Any]:
    clear_session()
    return {"ok": True, "session": session_status()}


@app.get("/auth/me")
def auth_me() -> dict[str, Any]:
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
        if underlying not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        return {"underlying": underlying, "expiries": list_expiries(underlying)}
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
        updated = watchlist_activate(item_id)
        return {"ok": True, "item": updated}
    except KeyError as e:
        raise _err(e, 404) from e
    except Exception as e:
        raise _err(e) from e


@app.post("/watchlist/{item_id}/close")
def watchlist_close_item(item_id: str) -> dict[str, Any]:
    try:
        updated = watchlist_close(item_id)
        return {"ok": True, "item": updated}
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
    try:
        return arm(confirm=body.confirm)
    except Exception as e:
        raise _err(e) from e


@app.post("/live/disarm")
def live_disarm() -> dict[str, Any]:
    return disarm()


@app.get("/risk/limits")
def risk_get() -> dict[str, Any]:
    return get_limits()


@app.post("/risk/limits")
def risk_set(body: RiskIn) -> dict[str, Any]:
    return update_limits(**body.model_dump(exclude_none=True))


@app.get("/live/positions")
def live_positions() -> dict[str, Any]:
    state = get_arm_state()
    if state["mode"] != "live":
        sync_paper_from_rolling_straddle()
        broker = get_paper_broker()
    else:
        broker = _kite_broker
    try:
        return {"mode": state["mode"], "positions": broker.positions()}
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
def rolling_straddle_status() -> dict[str, Any]:
    try:
        bundle = rs_status_bundle()
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


@app.get("/oi-var/config")
def oi_var_config() -> dict[str, Any]:
    return var_config()


@app.get("/oi-var/snapshot")
def oi_var_snapshot(
    underlying: str = Query("NIFTY", description="NIFTY | BANKNIFTY | SENSEX"),
    expiry: str | None = Query(None, description="YYYY-MM-DD; default nearest weekly"),
    top_n: int | None = Query(None, ge=1, le=25),
) -> dict[str, Any]:
    try:
        u = underlying.upper()
        if u not in INDEX_OPTIONS:
            raise RuntimeError(f"Unknown underlying. Use {list(INDEX_OPTIONS.keys())}")
        _require_kite_session()
        return build_var_snapshot(u, expiry=expiry, top_n=top_n)
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


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "3ST Kite Algo API",
        "docs": "/docs",
        "lovable": "Point VITE_API_BASE_URL to this server",
    }
