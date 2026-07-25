"""
3ST · ADX Backtester
Streamlit UI — Yahoo Finance data (no broker login) · NIFTY50 / SENSEX · 5/15/30 min
EMA200 removed. ADX + Triple HA SuperTrend only.
"""

from __future__ import annotations

from datetime import date
import logging

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from backtest_engine import BacktestParams, run_backtest, trades_to_df
from config import DEFAULT_ADX, DEFAULT_CAPITAL, DEFAULT_QTY, DEFAULT_RISK, DEFAULT_ST, INSTRUMENTS, TIMEFRAMES
from yahoo_client import default_date_range, fetch_candles, max_lookback_days

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("3st.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("3st")

st.set_page_config(
    page_title="3ST · Yahoo Backtester",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("3ST · ADX Backtester")
st.caption("Yahoo Finance · NIFTY50 / SENSEX · 5min / 15min / 30min · No EMA200 · No Firstock login")


# ── Session state ─────────────────────────────────────────
if "candles" not in st.session_state:
    st.session_state.candles = None
if "result" not in st.session_state:
    st.session_state.result = None
if "meta" not in st.session_state:
    st.session_state.meta = None


# ── Sidebar: Data ─────────────────────────────────────────
with st.sidebar:
    st.header("Data (Yahoo Finance)")
    st.info(
        "Firstock login bypassed. Using Yahoo Finance for maximum available "
        "intraday history (~60 days for 5/15/30 min)."
    )

    instrument = st.selectbox(
        "Instrument",
        options=list(INSTRUMENTS.keys()),
        format_func=lambda k: f"{INSTRUMENTS[k]['label']} ({INSTRUMENTS[k]['yahoo_symbol']})",
    )
    timeframe = st.selectbox("Timeframe", options=list(TIMEFRAMES.keys()), index=0)

    max_days = max_lookback_days(timeframe)
    max_start, max_end = default_date_range(timeframe)
    use_max = st.checkbox(
        f"Use maximum Yahoo history (~{max_days} days)",
        value=True,
        help="Yahoo caps intraday history at about 60 days for 5m/15m/30m.",
    )

    if use_max:
        start_date, end_date = max_start, max_end
        st.caption(f"Range: **{start_date}** → **{end_date}**")
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            start_date = st.date_input("From", value=max_start, min_value=max_start, max_value=date.today())
        with col_b:
            end_date = st.date_input("To", value=max_end, min_value=max_start, max_value=date.today())

    if st.button("Fetch Candles", type="primary", use_container_width=True):
        meta = INSTRUMENTS[instrument]
        try:
            with st.spinner(f"Fetching {meta['label']} {timeframe} from Yahoo…"):
                df = fetch_candles(
                    instrument=instrument,
                    timeframe=timeframe,
                    start=start_date,
                    end=end_date,
                    use_max=use_max,
                )
            st.session_state.candles = df
            st.session_state.meta = {
                "instrument": instrument,
                "timeframe": timeframe,
                "source": "yahoo",
                "start": str(df.index.min().date()) if len(df) else "",
                "end": str(df.index.max().date()) if len(df) else "",
                **meta,
            }
            st.session_state.result = None
            st.success(f"{len(df):,} bars · {df.index.min()} → {df.index.max()}")
        except Exception as e:
            st.error(str(e))

    if st.session_state.candles is not None and not st.session_state.candles.empty:
        c = st.session_state.candles
        st.caption(f"Loaded **{len(c):,}** bars")


# ── Main: params + run ────────────────────────────────────
instrument = instrument if "instrument" in dir() else list(INSTRUMENTS.keys())[0]
meta = st.session_state.get("meta") or INSTRUMENTS[instrument]
candles: pd.DataFrame | None = st.session_state.candles

tab_run, tab_report, tab_trades, tab_data = st.tabs(["Backtest", "Report", "Trades", "Data"])

with tab_run:
    st.subheader("Strategy parameters")
    st.caption("Triple HA SuperTrend + ADX filter · EMA200 removed")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown("**ST1 Slow**")
        atr1 = st.number_input("ATR1", 1, 100, DEFAULT_ST["atr1"])
        factor1 = st.number_input("Factor1", 0.1, 20.0, float(DEFAULT_ST["factor1"]), 0.1)
    with c2:
        st.markdown("**ST2 Medium**")
        atr2 = st.number_input("ATR2", 1, 100, DEFAULT_ST["atr2"])
        factor2 = st.number_input("Factor2", 0.1, 20.0, float(DEFAULT_ST["factor2"]), 0.1)
    with c3:
        st.markdown("**ST3 Fast**")
        atr3 = st.number_input("ATR3", 1, 100, DEFAULT_ST["atr3"])
        factor3 = st.number_input("Factor3", 0.1, 20.0, float(DEFAULT_ST["factor3"]), 0.1)
    with c4:
        st.markdown("**ADX**")
        adx_enabled = st.checkbox("Enable ADX", value=DEFAULT_ADX["enabled"])
        adx_period = st.number_input("ADX Period", 5, 50, DEFAULT_ADX["period"])
        adx_threshold = st.number_input("Min ADX", 1.0, 60.0, float(DEFAULT_ADX["threshold"]), 1.0)

    c5, c6, c7 = st.columns(3)
    with c5:
        trade_mode = st.selectbox("Trade Mode", ["Both", "LongOnly", "ShortOnly"])
        system_mode = st.selectbox("System Mode", ["Intraday", "Positional"])
    with c6:
        tgt_mode = st.selectbox("Target", ["Off", "%", "Pts"], index=0)
        tgt_value = st.number_input("Target Value", 0.1, 10000.0, float(DEFAULT_RISK["tgt_value"]), 0.1)
        sl_mode = st.selectbox("Stoploss", ["Off", "%", "Pts"], index=0)
        sl_value = st.number_input("SL Value", 0.1, 10000.0, float(DEFAULT_RISK["sl_value"]), 0.1)
    with c7:
        tsl_mode = st.selectbox("Trail SL", ["Off", "%", "Pts"], index=0)
        tsl_value = st.number_input("TSL Value", 0.1, 10000.0, float(DEFAULT_RISK["tsl_value"]), 0.1)
        qty = st.number_input("Qty / Lots", 1.0, 10000.0, float(DEFAULT_QTY), 1.0)
        capital = st.number_input("Capital", 1000.0, 1e9, float(DEFAULT_CAPITAL), 1000.0)
        point_value = st.number_input(
            "Point Value",
            0.01,
            10000.0,
            1.0,
            0.01,
            help="PnL multiplier (e.g. lot size × index point)",
        )

    session_start = meta.get("session_start", "09:15")
    session_end = meta.get("session_end", "15:30")
    force_exit = meta.get("force_exit", "15:20")

    run_disabled = candles is None or candles.empty
    if st.button("Run Backtest", type="primary", disabled=run_disabled):
        params = BacktestParams(
            atr1=int(atr1),
            factor1=float(factor1),
            atr2=int(atr2),
            factor2=float(factor2),
            atr3=int(atr3),
            factor3=float(factor3),
            adx_enabled=bool(adx_enabled),
            adx_period=int(adx_period),
            adx_threshold=float(adx_threshold),
            trade_mode=trade_mode,
            system_mode=system_mode,
            session_start=session_start,
            session_end=session_end,
            force_exit=force_exit,
            tgt_mode=tgt_mode,
            tgt_value=float(tgt_value),
            sl_mode=sl_mode,
            sl_value=float(sl_value),
            tsl_mode=tsl_mode,
            tsl_value=float(tsl_value),
            lot_size=float(point_value),
            use_lot_multiplier=float(point_value) != 1.0,
        )
        with st.spinner("Running backtest…"):
            result = run_backtest(candles, params)
        st.session_state.result = result
        st.session_state.params = params
        st.success(f"Done · {result.metrics.get('trades', 0)} trades")

    if run_disabled:
        st.info("Fetch Candles from the sidebar, then Run Backtest.")


with tab_report:
    result = st.session_state.result
    if result is None:
        st.info("Run a backtest to see the report.")
    else:
        m = result.metrics
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        k1.metric("Net PnL", f"{m.get('net_pnl', 0):,.2f}")
        k2.metric("Return %", f"{m.get('return_pct', 0):.2f}%")
        k3.metric("Trades", f"{m.get('trades', 0)}")
        k4.metric("Win Rate", f"{m.get('win_rate', 0):.1f}%")
        k5.metric("Profit Factor", f"{m.get('profit_factor', 0):.2f}")
        k6.metric("Max DD %", f"{m.get('max_drawdown_pct', 0):.2f}%")

        k7, k8, k9, k10 = st.columns(4)
        k7.metric("Wins", m.get("wins", 0))
        k8.metric("Losses", m.get("losses", 0))
        k9.metric("Long / Short", f"{m.get('long_trades', 0)} / {m.get('short_trades', 0)}")
        k10.metric("Final Equity", f"{m.get('final_equity', 0):,.2f}")

        sig = result.signals
        eq = result.equity
        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=True,
            vertical_spacing=0.04,
            row_heights=[0.5, 0.25, 0.25],
            subplot_titles=("Price + SuperTrends", "ADX", "Equity"),
        )
        fig.add_trace(
            go.Candlestick(
                x=sig.index,
                open=sig["open"],
                high=sig["high"],
                low=sig["low"],
                close=sig["close"],
                name="OHLC",
            ),
            row=1,
            col=1,
        )
        for col, name, color in (
            ("st1", "ST1", "#2ecc71"),
            ("st2", "ST2", "#27ae60"),
            ("st3", "ST3", "#1abc9c"),
        ):
            fig.add_trace(
                go.Scatter(x=sig.index, y=sig[col], name=name, line=dict(width=1, color=color)),
                row=1,
                col=1,
            )

        tdf = trades_to_df(result.trades)
        if not tdf.empty:
            longs = tdf[tdf["side"] == "long"]
            shorts = tdf[tdf["side"] == "short"]
            if len(longs):
                fig.add_trace(
                    go.Scatter(
                        x=longs["entry_time"],
                        y=longs["entry_price"],
                        mode="markers",
                        name="Long Entry",
                        marker=dict(symbol="triangle-up", size=10, color="lime"),
                    ),
                    row=1,
                    col=1,
                )
            if len(shorts):
                fig.add_trace(
                    go.Scatter(
                        x=shorts["entry_time"],
                        y=shorts["entry_price"],
                        mode="markers",
                        name="Short Entry",
                        marker=dict(symbol="triangle-down", size=10, color="red"),
                    ),
                    row=1,
                    col=1,
                )

        fig.add_trace(
            go.Scatter(x=sig.index, y=sig["adx"], name="ADX", line=dict(color="#f39c12")),
            row=2,
            col=1,
        )
        params = st.session_state.get("params")
        if params:
            fig.add_hline(y=params.adx_threshold, line_dash="dot", line_color="gray", row=2, col=1)

        fig.add_trace(
            go.Scatter(x=eq.index, y=eq.values, name="Equity", fill="tozeroy", line=dict(color="#3498db")),
            row=3,
            col=1,
        )

        fig.update_layout(
            height=900,
            xaxis_rangeslider_visible=False,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            margin=dict(l=40, r=20, t=60, b=40),
        )
        st.plotly_chart(fig, use_container_width=True)

        label = meta.get("label", instrument)
        tf = st.session_state.get("meta", {}).get("timeframe", timeframe)
        st.caption(f"{label} · {tf} · Yahoo Finance · {meta.get('start', '')} → {meta.get('end', '')}")


with tab_trades:
    result = st.session_state.result
    if result is None:
        st.info("No trades yet.")
    else:
        tdf = trades_to_df(result.trades)
        st.dataframe(tdf, use_container_width=True, height=480)
        st.download_button(
            "Download trades CSV",
            tdf.to_csv(index=False).encode("utf-8"),
            file_name="3st_trades.csv",
            mime="text/csv",
        )


with tab_data:
    if candles is None or candles.empty:
        st.info("Fetch candles from the sidebar.")
    else:
        st.write(f"**{len(candles):,}** bars · source: Yahoo Finance")
        st.dataframe(candles.tail(500), use_container_width=True, height=420)
        st.download_button(
            "Download candles CSV",
            candles.to_csv().encode("utf-8"),
            file_name="candles_yahoo.csv",
            mime="text/csv",
        )
