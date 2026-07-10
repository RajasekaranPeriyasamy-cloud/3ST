"""Shared config for 3ST backtester."""

from __future__ import annotations

# Chart symbols available in the UI
INSTRUMENTS = {
    "NIFTY50": {
        "label": "NIFTY 50",
        "exchange": "NSE",
        "trading_symbol": "NIFTY",
        "yahoo_symbol": "^NSEI",
        "session_start": "09:15",
        "session_end": "15:30",
        "force_exit": "15:20",
    },
    "SENSEX": {
        "label": "SENSEX",
        "exchange": "BSE",
        "trading_symbol": "SENSEX",
        "yahoo_symbol": "^BSESN",
        "session_start": "09:15",
        "session_end": "15:30",
        "force_exit": "15:20",
    },
    "BANKNIFTY50": {
        "label": "NIFTY BANK",
        "exchange": "NSE",
        "trading_symbol": "BANKNIFTY",
        "yahoo_symbol": "^NSEBANK",
        "session_start": "09:15",
        "session_end": "15:30",
        "force_exit": "15:20",
    },
}

# UI label -> Firstock interval code (kept for optional Firstock path)
TIMEFRAMES = {
    "5min": "5mi",
    "15min": "15mi",
    "30min": "30mi",
    "60min": "60mi",
}

# UI label -> Kite Connect historical interval
KITE_INTERVALS = {
    "5min": "5minute",
    "15min": "15minute",
    "30min": "30minute",
    "60min": "60minute",
}

# Index name patterns used when resolving instrument tokens from Kite dump
KITE_INDEX_LOOKUP = {
    "NIFTY50": {"exchange": "NSE", "name": "NIFTY 50", "tradingsymbol": "NIFTY 50"},
    "SENSEX": {"exchange": "BSE", "name": "SENSEX", "tradingsymbol": "SENSEX"},
    "BANKNIFTY50": {"exchange": "NSE", "name": "NIFTY BANK", "tradingsymbol": "NIFTY BANK"},
}

# Yahoo Finance max lookback by timeframe (days)
YAHOO_MAX_DAYS = {
    "5min": 59,
    "15min": 59,
    "30min": 59,
    "60min": 59,
}

# Kite Connect historical candle lookback (days from today, per interval group)
KITE_MAX_DAYS = {
    "5min": 100,
    "15min": 400,
    "30min": 400,
    "60min": 400,
}

# Index options metadata for spread builder (lot sizes approximate — Kite dump is source of truth)
INDEX_OPTIONS = {
    "NIFTY": {
        "exchange": "NFO",
        "strike_step": 50,
        "lot_size": 75,
        "index_token_key": "NIFTY50",
    },
    "BANKNIFTY": {
        "exchange": "NFO",
        "strike_step": 100,
        "lot_size": 30,
        "index_token_key": "BANKNIFTY50",
    },
    "SENSEX": {
        "exchange": "BFO",
        "strike_step": 100,
        "lot_size": 20,
        "index_token_key": "SENSEX",
    },
}

DEFAULT_SESSION = {
    "session_start": "09:15",
    "session_end": "15:30",
    "force_exit": "15:20",
}

# Default SuperTrend params (matches Pine)
DEFAULT_ST = {
    "atr1": 21,
    "factor1": 1.0,
    "atr2": 14,
    "factor2": 2.0,
    "atr3": 7,
    "factor3": 3.0,
    "st1_enabled": True,
    "st2_enabled": True,
    "st3_enabled": True,
}

DEFAULT_ADX = {
    "enabled": True,
    "period": 14,
    "threshold": 20.0,
}

DEFAULT_RISK = {
    "tgt_mode": "Off",
    "tgt_value": 1.0,
    "sl_mode": "Off",
    "sl_value": 1.0,
    "tsl_mode": "Off",
    "tsl_value": 1.5,
}

DEFAULT_ST_METHOD = "heikin_ashi"

# OI Tracker page defaults (ported from oi_tracker_share.py)
OI_TRACKER_DEFAULTS = {
    "options_count": 5,
    "historical_minutes": 40,
    "intervals_min": (5, 10, 15, 30),
    "refresh_seconds": 60,
    "pct_thresholds": {5: 8.0, 10: 10.0, 15: 15.0, 30: 25.0},
    "alert_breach_ratio": 0.5,
    "risk_free_rate": 0.065,
    "bias_interval_min": 15,
    "bias_sideways_threshold": 0.55,
}

OI_VAR_DEFAULTS = {
    "top_n": 10,
    "refresh_seconds": 60,
}

# Legacy Streamlit UI only
DEFAULT_CAPITAL = 300_000.0
DEFAULT_QTY = 1
