"""3ST strategy adapter for the live runner."""

from __future__ import annotations

import pandas as pd

from strategies.base import Signal
from strategy_3st import StMethod, compute_signals


class ThreeSTStrategy:
    name = "3ST_ADX"

    def __init__(
        self,
        atr1: int = 21,
        factor1: float = 1.0,
        atr2: int = 14,
        factor2: float = 2.0,
        atr3: int = 7,
        factor3: float = 3.0,
        st1_enabled: bool = True,
        st2_enabled: bool = True,
        st3_enabled: bool = True,
        adx_enabled: bool = True,
        adx_period: int = 14,
        adx_threshold: float = 20.0,
        st_method: StMethod = "heikin_ashi",
    ) -> None:
        self.kwargs = dict(
            atr1=atr1,
            factor1=factor1,
            atr2=atr2,
            factor2=factor2,
            atr3=atr3,
            factor3=factor3,
            st1_enabled=st1_enabled,
            st2_enabled=st2_enabled,
            st3_enabled=st3_enabled,
            adx_enabled=adx_enabled,
            adx_period=adx_period,
            adx_threshold=adx_threshold,
            st_method=st_method,
        )
        self._pos = 0

    def on_bar(self, df: pd.DataFrame) -> Signal | None:
        if df is None or len(df) < 50:
            return None
        sig = compute_signals(df, **self.kwargs)
        row = sig.iloc[-1]
        px = float(row["close"])

        if bool(row.get("go_long", row["long_entry"])):
            self._pos = 1
            label = "re-entry" if bool(row.get("long_reentry")) else "entry"
            return Signal("enter_long", f"3ST long {label} (ST zone + ADX)", px)
        if bool(row.get("go_short", row["short_entry"])):
            self._pos = -1
            label = "re-entry" if bool(row.get("short_reentry")) else "entry"
            return Signal("enter_short", f"3ST short {label} (ST zone + ADX)", px)

        if self._pos == 1 and bool(row["long_zone_exit"]):
            self._pos = 0
            return Signal("exit", "3ST long exit — close below ST1", px)
        if self._pos == -1 and bool(row["short_zone_exit"]):
            self._pos = 0
            return Signal("exit", "3ST short exit — close above ST1", px)
        return None
