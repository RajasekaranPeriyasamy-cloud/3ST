"""The indicator itself: settings, the altimeter window, and the full result.

This is the Python equivalent of the Pine script's last-bar block. Where Pine
can only evaluate on ``barstate.islast``, :func:`compute` takes an
``end_offset``, so you can walk the whole history and backtest the readings -
which is the main practical reason to port this at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bars import Bar, BarSeries
from .engines import FootprintRow, VolumeEngine
from .metrics import (
    ValueArea,
    balance_tilt,
    first_imbalances_from_poc,
    point_of_control,
    value_area,
)
from .profile import ProfileModel, build_profile, band_mass, sample_curves
from .rows import RowSplit, diagonal_imbalance, footprint_view_rows, gauss_view_rows

__all__ = ["Settings", "WindowColumn", "FootprintResult", "compute"]

MAX_LOOKBACK = 5000
"""Pine's historical buffer ceiling for computed series."""


@dataclass(slots=True)
class Settings:
    """Every input of the original indicator, with the original defaults."""

    engine: VolumeEngine = VolumeEngine.GEOMETRIC
    window_bars: int = 5            # columns in the footprint table
    view_ticks: int = 5             # one-tick rows above and below the axis
    bell_div: float = 3.0           # "Volume Concentration"
    profile_period: int = 23        # bars the profile covers
    profile_resolution: int = 100   # samples along the profile curve
    va_pct: float = 70.0            # value area share of volume
    imb_pct: float = 300.0          # diagonal imbalance threshold
    tilt_pct: float = 5.0           # balance-label dead zone
    residual_tol_ppm: float = 1.0   # DRIFT threshold
    ovl_balance_pct: float = 75.0   # BALANCED boundary


@dataclass(slots=True)
class WindowColumn:
    """One candle that earned a column in the footprint table."""

    offset: int          # bars back from the newest bar; 0 is the newest
    bar: Bar
    buy_total: float
    sell_total: float
    split: RowSplit

    @property
    def total(self) -> float:
        return self.buy_total + self.sell_total

    @property
    def delta(self) -> float:
        return self.buy_total - self.sell_total


@dataclass(slots=True)
class FootprintResult:
    """Everything the indicator knows at one point in time."""

    settings: Settings
    mintick: float
    axis_tick: int
    axis_price: float
    row_prices: list[float] = field(default_factory=list)
    columns: list[WindowColumn] = field(default_factory=list)

    # Window ("dashboard") domain, summed across the columns.
    row_sum_buy: list[float] = field(default_factory=list)
    row_sum_sell: list[float] = field(default_factory=list)
    window_poc_index: int = -1
    window_poc_price: float | None = None
    window_va: ValueArea | None = None
    window_vah_price: float | None = None
    window_val_price: float | None = None
    window_buy_imbalance: list[bool] = field(default_factory=list)
    window_sell_imbalance: list[bool] = field(default_factory=list)

    # Profile ("chart") domain.
    profile: ProfileModel | None = None
    profile_prices: list[float] = field(default_factory=list)
    profile_buy: list[float] = field(default_factory=list)
    profile_sell: list[float] = field(default_factory=list)
    chart_poc_price: float | None = None
    chart_poc_buy: float | None = None
    chart_poc_sell: float | None = None
    chart_vah_price: float | None = None
    chart_val_price: float | None = None
    chart_imb_up: tuple[float, bool] | None = None
    chart_imb_down: tuple[float, bool] | None = None

    @property
    def window_total_buy(self) -> float:
        return sum(self.row_sum_buy)

    @property
    def window_total_sell(self) -> float:
        return sum(self.row_sum_sell)

    @property
    def window_delta(self) -> float:
        return self.window_total_buy - self.window_total_sell

    @property
    def residual_ok(self) -> bool:
        if self.profile is None or self.profile.residual_ppm is None:
            return True
        return self.profile.residual_ppm <= self.settings.residual_tol_ppm

    @property
    def residual_label(self) -> str:
        return "EXACT" if self.residual_ok else "DRIFT"

    def balance_verdict(self) -> str:
        if self.profile is None:
            return "NO DATA"
        return self.profile.verdict(self.settings.tilt_pct)


def compute(
    series: BarSeries,
    settings: Settings | None = None,
    footprint_rows: list[list[FootprintRow] | None] | None = None,
    end_offset: int = 0,
) -> FootprintResult:
    """Evaluate the whole indicator as of ``end_offset`` bars back from the end.

    ``series`` must already carry buy/sell volume - run it through
    :func:`volume_footprint.engines.apply_engine` first.
    """
    st = settings or Settings()
    tick = series.mintick
    anchor = series.back(end_offset)
    axis_tick = series.tick_index(anchor.close)
    n_rows = 2 * st.view_ticks + 1
    k_min = axis_tick - st.view_ticks

    res = FootprintResult(
        settings=st,
        mintick=tick,
        axis_tick=axis_tick,
        axis_price=axis_tick * tick,
        row_prices=[(k_min + k) * tick for k in range(n_rows)],
    )

    # ---- Zone-memory window search -------------------------------------
    # Walk back from the anchor, newest first, and keep only candles that
    # carry engine data AND actually traded inside the visible frame. A candle
    # that never printed at these prices takes no column, so every column you
    # see is a candle that genuinely left volume here. The cheap overlap test
    # runs before the analytic split, so skipped candles cost almost nothing.
    found = 0
    last_idx = min(MAX_LOOKBACK - 1, len(series) - 1 - end_offset)
    for i in range(end_offset, last_idx + 1):
        if found >= st.window_bars:
            break
        bar = series.back(i)
        if bar.buy_volume is None or bar.sell_volume is None:
            continue
        if (bar.buy_volume + bar.sell_volume) <= 0.0:
            continue

        bar_lo_t = series.tick_index(bar.low)
        bar_hi_t = series.tick_index(bar.high)
        if not (bar_lo_t <= axis_tick + st.view_ticks and bar_hi_t >= axis_tick - st.view_ticks):
            continue

        if st.engine is VolumeEngine.FOOTPRINT:
            rows_i = (
                footprint_rows[len(series) - 1 - i]
                if footprint_rows is not None
                else None
            )
            if not rows_i:
                continue
            split = footprint_view_rows(rows_i, axis_tick, st.view_ticks, tick)
        else:
            split = gauss_view_rows(
                bar.buy_volume,
                bar.sell_volume,
                bar.high,
                bar.low,
                bar.close,
                axis_tick,
                st.view_ticks,
                tick,
                st.bell_div,
                st.imb_pct,
            )

        if not split.has_frame_data():
            continue

        found += 1
        res.columns.append(
            WindowColumn(
                offset=i,
                bar=bar,
                buy_total=bar.buy_volume,
                sell_total=bar.sell_volume,
                split=split,
            )
        )

    # ---- Window domain: row sums, POC, value area, imbalance ------------
    res.row_sum_buy = [0.0] * n_rows
    res.row_sum_sell = [0.0] * n_rows
    for col in res.columns:
        for k in range(n_rows):
            b = col.split.buy_rows[k]
            s = col.split.sell_rows[k]
            if b is not None:
                res.row_sum_buy[k] += b
            if s is not None:
                res.row_sum_sell[k] += s

    level_totals = [res.row_sum_buy[k] + res.row_sum_sell[k] for k in range(n_rows)]
    res.window_poc_index = point_of_control(level_totals)
    if res.window_poc_index >= 0:
        res.window_poc_price = (k_min + res.window_poc_index) * tick
        va = value_area(level_totals, res.window_poc_index, st.va_pct)
        res.window_va = va
        if va is not None:
            res.window_vah_price = (k_min + va.high_index) * tick
            res.window_val_price = (k_min + va.low_index) * tick

    res.window_buy_imbalance, res.window_sell_imbalance = diagonal_imbalance(
        res.row_sum_buy, res.row_sum_sell, st.imb_pct
    )

    # ---- Profile domain -------------------------------------------------
    model = build_profile(series, st.profile_period, st.bell_div, end_offset)
    res.profile = model
    if model.span > 0.0:
        prices, buys, sells = sample_curves(model, st.profile_resolution)
        res.profile_prices, res.profile_buy, res.profile_sell = prices, buys, sells

        totals = [buys[k] + sells[k] for k in range(len(prices))]
        poc_k = point_of_control(totals)
        if poc_k >= 0:
            res.chart_poc_price = prices[poc_k]
            half = tick / 2.0
            res.chart_poc_buy = band_mass(
                res.chart_poc_price - half, res.chart_poc_price + half, model.comps_buy
            )
            res.chart_poc_sell = band_mass(
                res.chart_poc_price - half, res.chart_poc_price + half, model.comps_sell
            )

            cva = value_area(totals, poc_k, st.va_pct)
            if cva is not None:
                res.chart_vah_price = prices[cva.high_index]
                res.chart_val_price = prices[cva.low_index]

            up, down = first_imbalances_from_poc(buys, sells, poc_k, st.imb_pct)
            if up is not None:
                res.chart_imb_up = (prices[up[0]], up[1])
            if down is not None:
                res.chart_imb_down = (prices[down[0]], down[1])

    return res
