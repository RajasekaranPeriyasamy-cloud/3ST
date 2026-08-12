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
        # Equity derivatives close 15:40 IST under NSE Closing Auction Session (CAS).
        "session_end": "15:40",
        "force_exit": "15:20",
    },
    "SENSEX": {
        "label": "SENSEX",
        "exchange": "BSE",
        "trading_symbol": "SENSEX",
        "yahoo_symbol": "^BSESN",
        "session_start": "09:15",
        "session_end": "15:40",
        "force_exit": "15:20",
    },
    "BANKNIFTY50": {
        "label": "NIFTY BANK",
        "exchange": "NSE",
        "trading_symbol": "BANKNIFTY",
        "yahoo_symbol": "^NSEBANK",
        "session_start": "09:15",
        "session_end": "15:40",
        "force_exit": "15:20",
    },
}

# UI label -> Firstock interval code (kept for optional Firstock path)
TIMEFRAMES = {
    "1min": "1mi",
    "3min": "3mi",
    "5min": "5mi",
    "15min": "15mi",
    "30min": "30mi",
    "60min": "60mi",
}

# UI label -> Kite Connect historical interval
KITE_INTERVALS = {
    "1min": "minute",
    "3min": "3minute",
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
    "1min": 7,
    "3min": 59,
    "5min": 59,
    "15min": 59,
    "30min": 59,
    "60min": 59,
}

# Kite Connect historical candle limits. These are two different things, and were
# previously one dict (KITE_MAX_DAYS) doing both jobs — which silently truncated
# any request reaching further back than the default range.
#
# How far back candles actually exist. Measured 2026-08-10 against a live session:
# NIFTY index, BANKNIFTY index and RELIANCE cash each served 1-minute bars 1825
# days back, and every coarser interval did too. The old values here (60/100/400)
# match Kite's documented *per-request* caps, which is almost certainly where they
# came from — but that limit is handled separately by chunking (see
# kite_client._fetch_historical_chunks), not by refusing the range.
#
# Futures and options are bounded by their own listing date instead: a near-dated
# future returns ~3 months of history however generous this ceiling is, and an
# illiquid strike returns nothing at all. So this is a ceiling, not a promise.
KITE_MAX_LOOKBACK_DAYS = {
    "1min": 1825,
    "3min": 1825,
    "5min": 1825,
    "15min": 1825,
    "30min": 1825,
    "60min": 1825,
}

# What "use max" asks for when the caller gives no explicit range. Deliberately
# far short of the ceiling above: 1825 days of 1-minute bars is ~470k rows over
# ~31 chunked requests. Raising the ceiling must not silently make every default
# backtest 30x heavier, so these keep the values the old shared constant had.
# Callers wanting older data now pass an explicit start and get it.
KITE_DEFAULT_RANGE_DAYS = {
    "1min": 60,
    "3min": 100,
    "5min": 100,
    "15min": 400,
    "30min": 400,
    "60min": 400,
}

# MCX market hours are fixed. Entry start / force exit remain user-editable.
MCX_SESSION = {
    "session_start": "09:00",
    "session_end": "23:30",
    "force_exit": "23:20",
    "entry_start": "09:20",
}

# Index options metadata for spread builder (lot sizes approximate — Kite dump is source of truth)
INDEX_OPTIONS = {
    "NIFTY": {
        "exchange": "NFO",
        "strike_step": 50,
        "lot_size": 65,
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
    "CRUDEOIL": {
        "exchange": "MCX",
        "strike_step": 50,
        "lot_size": 1,
        "spot_source": "future",
        "label": "Crude Oil",
        "session": MCX_SESSION,
        "default_product": "NRML",
    },
    "CRUDEOILM": {
        "exchange": "MCX",
        "strike_step": 50,
        "lot_size": 1,
        "spot_source": "future",
        "label": "Crude Oil Mini",
        "session": MCX_SESSION,
        "default_product": "NRML",
    },
    "NATURALGAS": {
        "exchange": "MCX",
        "strike_step": 5,
        "lot_size": 1,
        "spot_source": "future",
        "label": "Natural Gas",
        "session": MCX_SESSION,
        "default_product": "NRML",
    },
}

MCX_OPTION_UNDERLYINGS = ("CRUDEOIL", "CRUDEOILM", "NATURALGAS")

# Cash + major MCX underlyings sampled continuously in the background so
# session-history charts (Gamma Density, OI VAR) have data from market open
# even when no one has the desk page open. Filtered against INDEX_OPTIONS at
# call time, so a name not yet wired up (e.g. GOLD/SILVER) is silently
# skipped rather than raising. Shared by options/gamma_density.py and
# options/oi_var.py — keep both desks sampling the same underlying set.
ANALYTICS_HISTORY_SAMPLE_UNDERLYINGS = (
    "NIFTY",
    "BANKNIFTY",
    "SENSEX",
    "CRUDEOIL",
    "NATURALGAS",
    "GOLD",
    "SILVER",
)


def is_mcx_underlying(underlying: str | None) -> bool:
    u = str(underlying or "").upper()
    meta = INDEX_OPTIONS.get(u) or {}
    return str(meta.get("exchange") or "").upper() == "MCX"


def lock_mcx_market_session(cfg: dict) -> dict:
    """Force MCX market session to 09:00–23:30. Does not touch entry_start / force_exit."""
    if not is_mcx_underlying(cfg.get("underlying")):
        return cfg
    cfg["session_start"] = MCX_SESSION["session_start"]
    cfg["session_end"] = MCX_SESSION["session_end"]
    return cfg


# Cash / index-options market hours. From 2026-08-03, NSE Closing Auction Session
# (CAS) keeps equity derivatives open until 15:40 IST (cash CAS 15:15–15:35).
# See https://www.nseindia.com/static/products-services/closing-auction-session
DEFAULT_SESSION = {
    "session_start": "09:15",
    "session_end": "15:40",
    "force_exit": "15:20",
}

# SuperTrend params (matches Pine)
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
    "change_board_top_n": 5,
    "change_board_interval_min": 15,
}

OI_VAR_DEFAULTS = {
    "top_n": 10,
    "refresh_seconds": 60,
    # ΔVAR mode: oi_mark = ΔOI×LTP; true = VAR_now − VAR_base (base OI×LTP or stored EOD LTP)
    "dvar_mode": "oi_mark",
    "strike_window": 0,  # 0 = full chain; >0 = ATM ± N strikes for profile
    "min_oi": 0,
    "max_mid_spread_pct": 0.15,
    "multi_expiry_count": 2,
    "history_max_points": 120,
    "alert_dvar_burst_cr": 25.0,  # ΔVAR burst threshold vs prior tick
    "session_open_after": "09:20",  # IST — first snapshot after this becomes session open
}

# Gamma Density desk (Γ×OI density, dealer GEX, gamma-flip, expected-move bands)
GAMMA_DENSITY_DEFAULTS = {
    "risk_free_rate": 0.065,
    "dividend_yield": 0.012,  # align with GREEKS_ENGINE_DEFAULTS
    "refresh_seconds": 60,
    # Strikes to show on each side of ATM in the density curve
    "strike_window": 20,
    # Prefer bid/ask mid when spread ≤ this fraction of mid
    "max_mid_spread_pct": 0.12,
    "min_oi": 50,
    # naive = CE+ / PE− dealers; customer = inverted; oi_delta = sign from ΔOI vs EOD
    "sign_mode": "naive",
    "hedge_moves_pts": (50, 100),
    "multi_expiry_count": 2,
    "gex_profile_steps": 80,
    "history_max_points": 120,
    # Pin candidate when top-1 |GEX| share ≥ this (else wall midpoint / ATM)
    "pin_share_threshold": 0.18,
}

# Higher-order Greeks desk (1st/2nd order BS + GEX/VEX integration).
# European index options; r ≈ MIBOR/RBI proxy; q = index dividend yield.
GREEKS_ENGINE_DEFAULTS = {
    "risk_free_rate": 0.065,  # MIBOR / RBI reference proxy
    "dividend_yield": 0.012,  # Nifty/BankNifty approx index yield
    "refresh_seconds": 60,
    "strike_window": 20,
    # calendar = 365-day theta; trading_hours = 252 business-day theta
    "theta_mode": "calendar",
    "trading_days_per_year": 252,
    "nse_session_hours": 6.42,  # 09:15–15:40 IST (CAS / F&O close)
    "recommendations": {
        "max_ideas": 6,
        "disclaimer": (
            "Higher-order greek model — not advice; does not arm or place orders. "
            "Size premiums on Pricing Engine."
        ),
    },
}

# Unified trade-suggestions desk (GEX + VEX + higher-order greeks).
TRADE_SUGGESTIONS_DEFAULTS = {
    "index_underlyings": ("NIFTY", "BANKNIFTY", "SENSEX"),
    "refresh_seconds": 60,
    "strike_window": 20,
    "max_ideas": 8,
    "weekend_theta_boost_days": 2,  # Fri→Mon calendar bleed emphasis
    "disclaimer": (
        "Analytics suggestion — not advice; verify bid/ask and margins. "
        "Does not arm or place orders."
    ),
}

# Vanna Exposure desk (VEX raw + ₹, Vanna Line, IV shocks). Isolated from 3ST.
VANNA_EXPOSURE_DEFAULTS = {
    "risk_free_rate": 0.065,
    "refresh_seconds": 60,
    "strike_window": 20,
    "iv_shock_vol_points": (1, 2),
    # Trade ideas (dealer VEX / vol-up flow). Read-only — never arms/orders.
    "recommendations": {
        "max_ideas": 3,
        "disclaimer": (
            "Dealer-flow model — not advice; does not arm or place orders. "
            "Size premiums on Pricing Engine."
        ),
    },
}

# Volatility surface desk (IV across strikes × expiries)
VOL_SURFACE_DEFAULTS = {
    "strike_count": 15,
    "max_expiries": 6,
    "refresh_seconds": 120,
    "risk_free_rate": 0.065,
}

# IV Smile desk (single-expiry CE/PE IV curve)
IV_SMILE_DEFAULTS = {
    "index_underlyings": ("NIFTY", "BANKNIFTY", "SENSEX"),
    "strike_count": 25,
    "refresh_seconds": 60,
    "risk_free_rate": 0.065,
}

# IV Skew desk (delta-space skew: 25d risk reversal + butterfly, per expiry).
# Forward-based Black-76 — see options/skew_metrics.py for why spot is not used.
IV_SKEW_DEFAULTS = {
    "underlyings": ("NIFTY", "BANKNIFTY", "SENSEX", "CRUDEOIL", "CRUDEOILM", "NATURALGAS"),
    "max_expiries": 3,
    "target_delta": 0.25,
    "refresh_seconds": 60,
    "risk_free_rate": 0.065,
    # The strike window is sized so this |delta| is reachable on both wings,
    # deliberately deeper than target_delta so 25d is interpolated rather than
    # extrapolated. A fixed strike count cannot do that: BANKNIFTY at 48 DTE
    # had still not reached 25d at +/-20 strikes (measured 2026-08-12).
    "wing_delta": 0.10,
    "min_half_width": 6,
    "max_half_width": 40,
    # Quote-quality gates. MCX wings are thin enough to quote a 5-paisa option
    # with no two-sided market, which solves to a meaningless IV.
    "max_relative_spread": 0.5,
    "parity_gap_warn": 0.005,  # 0.5 vol points
    "forward_spread_warn_bps": 25.0,
    # Interpolating 25d across more than this much delta means the wing was too
    # sparse to have measured it; dropping more than this share of the quoted
    # legs means the chain was too thin to read at all.
    "max_bracket_gap": 0.15,
    "max_drop_ratio": 0.4,
}

# Complementary pricing engine (BS IV/Greeks + optional Heston-COS).
# Isolated from Rolling Straddle / 3ST signal execution.
PRICING_ENGINE_DEFAULTS = {
    "index_underlyings": ("NIFTY", "BANKNIFTY", "SENSEX"),
    "strike_count": 15,
    "refresh_seconds": 30,
    "risk_free_rate": 0.065,
    "heston": {
        "v0": 0.04,
        "kappa": 2.0,
        "theta": 0.04,
        "sigma": 0.5,
        "rho": -0.7,
        "q": 0.0,
    },
    # Trade ideas on Live desk (BS edge). Read-only — never arms/orders.
    "recommendations": {
        "atm_window_steps": 2,
        "min_ltp": 5.0,
        "max_ideas": 3,
        "disclaimer": (
            "Model suggestion — not advice; verify bid/ask before order. "
            "Does not arm or place orders."
        ),
    },
}

# Calendar futures spread arbitrage scanner
CALENDAR_ARBITRAGE_DEFAULTS = {
    "default_exchanges": ("NFO", "MCX"),
    "refresh_seconds": 300,
    "quote_refresh_seconds": 8,
}

# OI Profile desk (futures candles + OI-by-price butterfly + daily OI change)
OI_PROFILE_DEFAULTS = {
    # Intraday intervals offered for the candle/OI panel (UI label -> KITE_INTERVALS key)
    "intervals": ("1min", "5min", "15min"),
    "default_interval": "5min",
    "default_days": 5,
    "max_days": 30,
    # Number of horizontal price buckets for the OI-by-price butterfly profile
    "price_buckets": 24,
    "refresh_seconds": 60,
    # Index underlyings that have monthly futures with historical OI
    "underlyings": ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"),
}

# Relative Rotation Graph (RRG-Lite parity — weekly RS ratio / momentum)
RRG_BENCHMARKS = {
    "NIFTY50": {"label": "NIFTY 50", "instrument_key": "NIFTY50"},
    "BANKNIFTY50": {"label": "NIFTY BANK", "instrument_key": "BANKNIFTY50"},
    "SENSEX": {"label": "SENSEX", "instrument_key": "SENSEX"},
}

RRG_DEFAULTS = {
    "window": 14,
    "period": 52,
    "tail": 4,
    "lookback_days": 900,
}

# Expiry-cycle analogue fan (historical path matching). Isolated from 3ST execution.
ANALOGUE_DEFAULTS = {
    "underlyings": ("NIFTY", "BANKNIFTY", "SENSEX"),
    "max_lookback_days": 2500,
    "default_cycle_kind": "monthly",
    "default_similarity_band_pct": 4.0,
    "similarity_band_min": 0.5,
    "similarity_band_max": 15.0,
    "max_analogue_paths": 80,
    "refresh_seconds": 300,
}

# NSE sectoral indices for regime RRG (Kite NSE-INDICES tradingsymbols)
RRG_SECTOR_INDICES: dict[str, dict[str, object]] = {
    "NIFTY_AUTO": {"label": "Nifty Auto", "tradingsymbol": "NIFTY AUTO"},
    "NIFTY_BANK": {"label": "Nifty Bank", "tradingsymbol": "NIFTY BANK"},
    "NIFTY_FIN_SERVICE": {
        "label": "Nifty Financial Services",
        "tradingsymbol": "NIFTY FIN SERVICE",
    },
    "NIFTY_FMCG": {"label": "Nifty FMCG", "tradingsymbol": "NIFTY FMCG"},
    "NIFTY_HEALTHCARE": {
        "label": "Nifty Healthcare",
        "tradingsymbol": "NIFTY HEALTHCARE",
        "fallbacks": ["NIFTY HEALTH CARE"],
    },
    "NIFTY_IT": {"label": "Nifty IT", "tradingsymbol": "NIFTY IT"},
    "NIFTY_MEDIA": {"label": "Nifty Media", "tradingsymbol": "NIFTY MEDIA"},
    "NIFTY_METAL": {"label": "Nifty Metal", "tradingsymbol": "NIFTY METAL"},
    "NIFTY_PHARMA": {"label": "Nifty Pharma", "tradingsymbol": "NIFTY PHARMA"},
    "NIFTY_PVT_BANK": {
        "label": "Nifty Private Bank",
        "tradingsymbol": "NIFTY PVT BANK",
        "fallbacks": ["NIFTY PRIVATE BANK"],
    },
    "NIFTY_PSU_BANK": {"label": "Nifty PSU Bank", "tradingsymbol": "NIFTY PSU BANK"},
    "NIFTY_REALTY": {"label": "Nifty Realty", "tradingsymbol": "NIFTY REALTY"},
    "NIFTY_CONSUMER_DURABLES": {
        "label": "Nifty Consumer Durables",
        "tradingsymbol": "NIFTY CONSR DURBL",
        "fallbacks": ["NIFTY CONSUMER DURABLES"],
    },
    "NIFTY_OIL_GAS": {
        "label": "Nifty Oil & Gas",
        "tradingsymbol": "NIFTY OIL AND GAS",
        "fallbacks": ["NIFTY ENERGY"],
    },
    "NIFTY_CHEMICALS": {"label": "Nifty Chemicals", "tradingsymbol": "NIFTY CHEMICALS"},
}

# NSDL BSE sector name → RRG sector index id (equity FPI overlay)
FPI_SECTOR_TO_RRG: dict[str, str] = {
    "Automobile and Auto Components": "NIFTY_AUTO",
    "Fast Moving Consumer Goods": "NIFTY_FMCG",
    "Information Technology": "NIFTY_IT",
    "Healthcare": "NIFTY_HEALTHCARE",
    "Media, Entertainment & Publication": "NIFTY_MEDIA",
    "Metals & Mining": "NIFTY_METAL",
    "Realty": "NIFTY_REALTY",
    "Consumer Durables": "NIFTY_CONSUMER_DURABLES",
    "Oil, Gas & Consumable Fuels": "NIFTY_OIL_GAS",
    "Chemicals": "NIFTY_CHEMICALS",
    "Financial Services": "NIFTY_FIN_SERVICE",
}

# RRG sector ids that inherit FPI from another mapped sector (NSDL uses broader buckets)
FPI_RRG_ALIASES: dict[str, str] = {
    "NIFTY_BANK": "NIFTY_FIN_SERVICE",
    "NIFTY_PVT_BANK": "NIFTY_FIN_SERVICE",
    "NIFTY_PSU_BANK": "NIFTY_FIN_SERVICE",
    "NIFTY_PHARMA": "NIFTY_HEALTHCARE",
}

FPI_DEFAULTS = {
    "report_url": (
        "https://www.fpi.nsdl.co.in/web/StaticReports/"
        "Fortnightly_Sector_wise_FII_Investment_Data/FIIInvestSector_June302026.html"
    ),
    "cache_hours": 24,
    "default_period": "period2",
}

RRG_PRESETS: dict[str, dict[str, object]] = {
    "sector_rotation": {
        "label": "Sector rotation (vs NIFTY 50)",
        "benchmark": "NIFTY50",
        "symbols": list(RRG_SECTOR_INDICES.keys()),
    },
    "nifty_sample": {
        "label": "Nifty 50 sample",
        "benchmark": "NIFTY50",
        "symbols": ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "BHARTIARTL", "ITC", "SBIN"],
    },
    "bank_sample": {
        "label": "Bank Nifty sample",
        "benchmark": "BANKNIFTY50",
        "symbols": ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK", "FEDERALBNK", "BANDHANBNK"],
    },
}

# Legacy Streamlit UI only
DEFAULT_CAPITAL = 300_000.0
DEFAULT_QTY = 1
