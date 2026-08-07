"""
Relative Rotation Graph (RRG) — daily OHLC → weekly RS ratio / momentum.

Formulas aligned with BennyThadikaran/RRG-Lite (RS ratio & momentum wiki).

Broker-configurable: the market-data source is abstracted behind
:class:`RrgDataProvider`. The default provider uses Kite Connect, but any broker
(Firstock, a REST feed, an offline Parquet catalog, a test stub, …) can supply
data by implementing the three provider methods and calling
:func:`set_rrg_data_provider` (or passing ``provider=`` to
:func:`build_rrg_snapshot`).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from typing import Any, Literal

import pandas as pd

from config import FPI_DEFAULTS, INSTRUMENTS, RRG_BENCHMARKS, RRG_DEFAULTS, RRG_PRESETS, RRG_SECTOR_INDICES
from instruments import resolve_by_symbol, resolve_instrument, resolve_nse_index

Quadrant = Literal["leading", "weakening", "lagging", "improving"]

# In-memory daily close cache: (provider, token, lookback_days) -> (cache_date, series)
_daily_close_cache: dict[tuple[str, int, int], tuple[date, pd.Series]] = {}


# ======================================================================
# Broker-configurable data provider
# ======================================================================


class RrgDataProvider(ABC):
    """Pluggable market-data source for the RRG engine.

    Implement this for any broker/data feed. All three methods must be provided:

    - :meth:`resolve_symbol` — turn a user token (``RELIANCE``, ``NSE:INFY``,
      ``NIFTY_IT``, ``INDEX:NIFTY 50``) into an instrument descriptor.
    - :meth:`resolve_benchmark` — turn a benchmark id (``NIFTY50``) into a
      descriptor.
    - :meth:`daily_closes` — return a daily close ``pd.Series`` indexed by date.
    """

    #: Short, stable id used to namespace the daily-close cache per provider.
    name: str = "base"

    @abstractmethod
    def resolve_symbol(self, raw: str) -> dict[str, Any]:
        """Return ``{meta, label, symbol, exchange, kind}`` for a symbol token.

        ``meta`` must contain at least ``instrument_token``.
        """

    @abstractmethod
    def resolve_benchmark(self, benchmark_id: str) -> dict[str, Any]:
        """Return ``{id, label, instrument_token, exchange, tradingsymbol}``."""

    @abstractmethod
    def daily_closes(self, instrument_token: int, lookback_days: int) -> pd.Series:
        """Return a daily close series (date index) for the instrument."""


class KiteRrgDataProvider(RrgDataProvider):
    """Default provider — Kite Connect historical daily candles."""

    name = "kite"

    def resolve_symbol(self, raw: str) -> dict[str, Any]:
        exchange, sym, short_label = _parse_symbol(raw)
        sector_id = _sector_id_from_symbol(sym)
        if sector_id:
            cfg = RRG_SECTOR_INDICES[sector_id]
            meta = resolve_nse_index(
                str(cfg["tradingsymbol"]),
                name=str(cfg.get("label") or ""),
                fallbacks=[str(x) for x in (cfg.get("fallbacks") or [])],
            )
            return {
                "meta": meta,
                "label": short_label or str(cfg["label"]),
                "symbol": sector_id,
                "exchange": "NSE",
                "kind": "index",
            }

        if exchange == "INDEX":
            meta = resolve_nse_index(sym)
            return {
                "meta": meta,
                "label": short_label or sym,
                "symbol": str(meta["tradingsymbol"]),
                "exchange": "NSE",
                "kind": "index",
            }

        meta = resolve_by_symbol(exchange, sym)
        return {
            "meta": meta,
            "label": short_label or sym,
            "symbol": sym,
            "exchange": exchange,
            "kind": "equity",
        }

    def resolve_benchmark(self, benchmark_id: str) -> dict[str, Any]:
        bid = benchmark_id.upper().replace(" ", "")
        if bid in RRG_BENCHMARKS:
            key = str(RRG_BENCHMARKS[bid]["instrument_key"])
        elif benchmark_id in INSTRUMENTS:
            key = benchmark_id
        else:
            raise ValueError(f"Unknown benchmark '{benchmark_id}'. Use {list(RRG_BENCHMARKS)}")
        meta = resolve_instrument(key)
        bench_id = bid if bid in RRG_BENCHMARKS else benchmark_id
        return {
            "id": bench_id,
            "label": INSTRUMENTS[key]["label"],
            "instrument_token": int(meta["instrument_token"]),
            "exchange": meta["exchange"],
            "tradingsymbol": meta["tradingsymbol"],
        }

    def daily_closes(self, instrument_token: int, lookback_days: int) -> pd.Series:
        from kite_auth import get_kite_client

        end = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0)
        start = end - timedelta(days=int(lookback_days))
        kite = get_kite_client()
        frames: list[pd.DataFrame] = []
        cursor = start
        chunk_days = 365
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=chunk_days), end)
            raw = kite.historical_data(
                instrument_token=instrument_token,
                from_date=cursor,
                to_date=chunk_end,
                interval="day",
                continuous=False,
                oi=False,
            )
            if raw:
                rows = []
                for r in raw:
                    if isinstance(r, dict):
                        rows.append({"date": r["date"], "close": float(r["close"])})
                    else:
                        rows.append({"date": r[0], "close": float(r[4])})
                part = pd.DataFrame(rows).set_index("date")
                part.index = pd.to_datetime(part.index)
                frames.append(part)
            cursor = chunk_end + timedelta(seconds=1)
        if not frames:
            return pd.Series(dtype=float)
        df = pd.concat(frames).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        return _process_series(df["close"])


# Active provider (swappable). Defaults to Kite.
_provider: RrgDataProvider = KiteRrgDataProvider()


def set_rrg_data_provider(provider: RrgDataProvider) -> None:
    """Install a broker/data-source provider for RRG (e.g. Firstock, offline)."""
    global _provider
    _provider = provider
    clear_rrg_daily_cache()


def get_rrg_data_provider() -> RrgDataProvider:
    return _provider


# ======================================================================
# Config surface (UI)
# ======================================================================


def rrg_config() -> dict[str, Any]:
    return {
        "defaults": dict(RRG_DEFAULTS),
        "benchmarks": [
            {"id": k, "label": v["label"]} for k, v in RRG_BENCHMARKS.items()
        ],
        "sectors": [
            {
                "id": k,
                "label": str(v["label"]),
                "tradingsymbol": str(v["tradingsymbol"]),
            }
            for k, v in RRG_SECTOR_INDICES.items()
        ],
        "presets": [
            {
                "id": k,
                "label": str(v.get("label") or k),
                "benchmark": v.get("benchmark"),
                "symbols": list(v.get("symbols") or []),
            }
            for k, v in RRG_PRESETS.items()
        ],
        "fpi": {
            "default_period": str(FPI_DEFAULTS.get("default_period") or "period2"),
            "periods": [
                {"id": "period1", "label": "Fortnight 1 (equity net)"},
                {"id": "period2", "label": "Fortnight 2 (equity net)"},
                {"id": "month_total", "label": "Month total (equity net)"},
            ],
        },
        "provider": _provider.name,
    }


# ======================================================================
# Series helpers
# ======================================================================


def _process_series(ser: pd.Series) -> pd.Series:
    if ser.index.has_duplicates:
        ser = ser.loc[~ser.index.duplicated()]
    if not ser.index.is_monotonic_increasing:
        ser = ser.sort_index(ascending=True)
    return ser


def _daily_closes_cached(
    provider: RrgDataProvider, instrument_token: int, lookback_days: int
) -> pd.Series:
    key = (provider.name, int(instrument_token), int(lookback_days))
    today = date.today()
    cached = _daily_close_cache.get(key)
    if cached and cached[0] == today:
        return cached[1].copy()
    series = provider.daily_closes(int(instrument_token), int(lookback_days))
    _daily_close_cache[key] = (today, series.copy())
    return series


def clear_rrg_daily_cache() -> None:
    _daily_close_cache.clear()


def _to_weekly(close: pd.Series) -> pd.Series:
    close = _process_series(close)
    if close.empty:
        return close
    weekly = close.resample("W-SUN").last().dropna()
    return _process_series(weekly)


def _minimum_bars(window: int, period: int, tail: int) -> int:
    return window * 2 + max(window, period) + max(2, tail)


# ======================================================================
# RS ratio / momentum math (broker-agnostic)
# ======================================================================


def calculate_rs_ratio(
    stock: pd.Series,
    benchmark: pd.Series,
    *,
    window: int,
) -> pd.Series:
    aligned = pd.concat([stock, benchmark], axis=1, join="inner").dropna()
    if aligned.empty or len(aligned) < window:
        return pd.Series(dtype=float)
    stock_cl = aligned.iloc[:, 0]
    bm_cl = aligned.iloc[:, 1]
    rs = (stock_cl / bm_cl) * 100.0
    rs_sma = rs.rolling(window=window)
    std = rs_sma.std(ddof=1)
    out = ((rs - rs_sma.mean()) / std).dropna() + 100.0
    return out.replace([float("inf"), float("-inf")], pd.NA).dropna()


def calculate_rs_momentum(
    rs_ratio: pd.Series,
    *,
    window: int,
    period: int,
    base_date: str | None = None,
) -> pd.Series:
    if rs_ratio.empty:
        return pd.Series(dtype=float)
    if base_date:
        ts = pd.Timestamp(base_date)
        if ts not in rs_ratio.index:
            nearest = rs_ratio.index[rs_ratio.index.get_indexer([ts], method="nearest")[0]]
            base_rs = float(rs_ratio.loc[nearest])
        else:
            base_rs = float(rs_ratio.at[ts])
    else:
        if len(rs_ratio) <= period:
            return pd.Series(dtype=float)
        base_rs = float(rs_ratio.iloc[-period])
    if base_rs == 0:
        return pd.Series(dtype=float)
    rs_roc = ((rs_ratio / base_rs) - 1.0) * 100.0
    roc_sma = rs_roc.rolling(window=window)
    std = roc_sma.std(ddof=1)
    out = ((rs_roc - roc_sma.mean()) / std).dropna() + 100.0
    return out.replace([float("inf"), float("-inf")], pd.NA).dropna()


def quadrant_for(rs: float, mom: float) -> Quadrant:
    if rs >= 100 and mom >= 100:
        return "leading"
    if rs >= 100 and mom < 100:
        return "weakening"
    if rs < 100 and mom < 100:
        return "lagging"
    return "improving"


def color_for(rs: float, mom: float) -> str:
    if rs > 100:
        return "#008217" if mom > 100 else "#918000"
    return "#00749D" if mom > 100 else "#E0002B"


# ======================================================================
# Symbol parsing (shared by providers)
# ======================================================================


def _parse_symbol(raw: str) -> tuple[str, str, str | None]:
    """
    Parse RELIANCE, NSE:RELIANCE, NIFTY_AUTO, or RELIANCE,ShortLabel.
    Returns exchange, tradingsymbol, optional label.
    """
    token = raw.strip()
    label: str | None = None
    if "," in token:
        token, label = [p.strip() for p in token.split(",", 1)]
    if ":" in token:
        exch, sym = token.split(":", 1)
        return exch.strip().upper(), sym.strip().upper(), label
    return "NSE", token.upper(), label


def _sector_id_from_symbol(sym: str) -> str | None:
    key = sym.upper().replace(" ", "_")
    if key in RRG_SECTOR_INDICES:
        return key
    compact = sym.upper().replace(" ", "")
    for sector_id in RRG_SECTOR_INDICES:
        if sector_id.replace("_", "") == compact:
            return sector_id
    return None


def _regime_summary(plotted: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"leading": 0, "weakening": 0, "lagging": 0, "improving": 0}
    for row in plotted:
        quad = row.get("quadrant")
        if quad in counts:
            counts[str(quad)] += 1
    return counts


# ======================================================================
# Snapshot builder
# ======================================================================


def build_rrg_snapshot(
    *,
    benchmark: str,
    symbols: list[str],
    window: int | None = None,
    period: int | None = None,
    tail: int | None = None,
    base_date: str | None = None,
    lookback_days: int | None = None,
    include_fpi: bool = False,
    fpi_period: str | None = None,
    provider: RrgDataProvider | None = None,
) -> dict[str, Any]:
    prov = provider or _provider
    window = int(window if window is not None else RRG_DEFAULTS["window"])
    period = int(period if period is not None else RRG_DEFAULTS["period"])
    tail = max(2, int(tail if tail is not None else RRG_DEFAULTS["tail"]))
    lookback_days = int(
        lookback_days if lookback_days is not None else RRG_DEFAULTS["lookback_days"]
    )
    min_bars = _minimum_bars(window, period, tail)

    bm_meta = prov.resolve_benchmark(benchmark)
    bm_daily = _daily_closes_cached(prov, int(bm_meta["instrument_token"]), lookback_days)
    bm_weekly = _to_weekly(bm_daily)
    if len(bm_weekly) < min_bars:
        raise RuntimeError(
            f"Benchmark {bm_meta['label']} has insufficient weekly history "
            f"({len(bm_weekly)} bars, need {min_bars})."
        )

    as_of = bm_weekly.index[-1]
    plotted: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    x_vals: list[float] = []
    y_vals: list[float] = []

    for raw in symbols:
        raw = (raw or "").strip()
        if not raw:
            continue
        sym_key = raw
        try:
            resolved = prov.resolve_symbol(raw)
            meta = resolved["meta"]
            sym_key = str(resolved["symbol"])
            label = str(resolved["label"])
            exchange = str(resolved["exchange"])
            daily = _daily_closes_cached(prov, int(meta["instrument_token"]), lookback_days)
            weekly = _to_weekly(daily)
            if len(weekly) < min_bars:
                errors.append(
                    {
                        "symbol": sym_key,
                        "error": f"Insufficient weekly bars ({len(weekly)} < {min_bars})",
                    }
                )
                continue
            rsr = calculate_rs_ratio(weekly, bm_weekly, window=window)
            rsm = calculate_rs_momentum(
                rsr,
                window=window,
                period=period,
                base_date=base_date,
            )
            common = rsr.index.intersection(rsm.index)
            if len(common) < tail:
                errors.append({"symbol": sym_key, "error": "Insufficient RS/momentum overlap"})
                continue
            rsr = rsr.loc[common]
            rsm = rsm.loc[common]
            tail_rsr = rsr.iloc[-tail:]
            tail_rsm = rsm.iloc[-tail:]
            head_rs = float(rsr.iloc[-1])
            head_mom = float(rsm.iloc[-1])
            x_vals.extend(float(v) for v in tail_rsr.tolist())
            y_vals.extend(float(v) for v in tail_rsm.tolist())
            color = color_for(head_rs, head_mom)
            quad = quadrant_for(head_rs, head_mom)
            points = [
                {
                    "date": idx.strftime("%Y-%m-%d"),
                    "rs": round(float(tail_rsr.loc[idx]), 4),
                    "momentum": round(float(tail_rsm.loc[idx]), 4),
                }
                for idx in tail_rsr.index
            ]
            plotted.append(
                {
                    "symbol": sym_key,
                    "label": label,
                    "exchange": exchange,
                    "instrument_token": int(meta["instrument_token"]),
                    "kind": resolved.get("kind", "equity"),
                    "color": color,
                    "quadrant": quad,
                    "head": {
                        "rs": round(head_rs, 4),
                        "momentum": round(head_mom, 4),
                        "date": tail_rsr.index[-1].strftime("%Y-%m-%d"),
                    },
                    "tail": points,
                }
            )
        except Exception as exc:
            errors.append({"symbol": sym_key, "error": str(exc)})

    if not x_vals:
        x_min, x_max, y_min, y_max = 93.5, 106.5, 93.5, 106.5
    else:
        x_min = min(x_vals) - 0.3
        x_max = max(x_vals) + 0.3
        y_min = min(y_vals) - 0.3
        y_max = max(y_vals) + 0.3

    result = {
        "ok": True,
        "provider": prov.name,
        "benchmark": bm_meta,
        "as_of": as_of.strftime("%Y-%m-%d"),
        "params": {
            "window": window,
            "period": period,
            "tail": tail,
            "base_date": base_date,
            "lookback_days": lookback_days,
        },
        "bounds": {
            "x_min": round(x_min, 2),
            "x_max": round(x_max, 2),
            "y_min": round(y_min, 2),
            "y_max": round(y_max, 2),
        },
        "regime": _regime_summary(plotted),
        "symbols": plotted,
        "errors": errors,
    }
    if include_fpi:
        from analysis.fpi_sectors import attach_fpi_overlay

        period_key = fpi_period or str(FPI_DEFAULTS.get("default_period") or "period2")
        attach_fpi_overlay(result, period=period_key)
    return result
