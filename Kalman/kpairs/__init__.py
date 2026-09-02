"""Adaptive Kalman-filter pairs trading on NSE indices, across timeframes.

Modules
-------
indices    the index universe, split by whether a listed future exists
bars       5-minute Kite pull + session-anchored resampling to 15m/30m/60m/2h/4h
kalman     the [alpha, beta] random-walk filter, written out in plain numpy
stats      cointegration, half-life, Hurst, Benjamini-Hochberg FDR
signals    the entry/exit/stop state machine over a causal z-score
backtest   two-leg P&L, an NSE cost model, and frequency-aware metrics
tfstudy    the walk-forward timeframe sweep and the model-fit scan
"""

__all__ = ["indices", "bars", "kalman", "stats", "signals", "backtest", "tfstudy"]
