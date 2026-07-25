"""Open Interest tracker — snapshot builder for OI Tracker page."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from config import INDEX_OPTIONS, OI_TRACKER_DEFAULTS
from kite_client import fetch_index_minute_spot, fetch_minute_oi, fetch_quote_batch
from options.chain import atm_strike, get_chain, get_index_spot, nearest_expiry
from options.iv import implied_volatility, time_to_expiry_years
from options.oi_signal import compute_interval_signals, compute_overall_bias
from options.oi_tracker_store import append_log


def _format_expiry_short(expiry: str) -> str:
    """Format ISO expiry (YYYY-MM-DD…) as ``21-Jul-26``."""
    try:
        d = date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return str(expiry)
    return d.strftime("%d-%b-%y")


def _contract_label(strike: Any, option_type: str, expiry: str) -> str:
    try:
        strike_s = f"{float(strike):,.0f}"
    except (TypeError, ValueError):
        strike_s = str(strike)
    return f"{strike_s} {option_type} {_format_expiry_short(expiry)}"


def _prev_oi(latest_oi: Any, abs_chg: Any) -> int | None:
    if latest_oi is None or abs_chg is None:
        return None
    try:
        return int(latest_oi) - int(abs_chg)
    except (TypeError, ValueError):
        return None


def build_oi_change_boards(
    calls: list[dict[str, Any]],
    puts: list[dict[str, Any]],
    *,
    expiry: str,
    intervals_min: tuple[int, ...] | list[int],
    top_n: int = 5,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Rank CE and PE separately into Increase/Decrease × Absolute/Percent boards.

    Each board list is ``CE top_n`` followed by ``PE top_n`` so the UI can split
    calls and puts inside the same Increase/Decrease card.
    """
    boards: dict[str, dict[str, list[dict[str, Any]]]] = {}

    for interval in intervals_min:
        key = str(interval)
        entries: list[dict[str, Any]] = []
        for side, option_type in ((calls, "CE"), (puts, "PE")):
            for row in side:
                abs_chg = (row.get("abs") or {}).get(key)
                pct_chg = (row.get("pct") or {}).get(key)
                curr_oi = row.get("latest_oi")
                if abs_chg is None and pct_chg is None:
                    continue
                if curr_oi is None:
                    continue
                prev = (row.get("prev_oi") or {}).get(key)
                if prev is None:
                    prev = _prev_oi(curr_oi, abs_chg)
                strike = row.get("strike")
                entries.append(
                    {
                        "contract": _contract_label(strike, option_type, expiry),
                        "strike": strike,
                        "option_type": option_type,
                        "expiry_label": _format_expiry_short(expiry),
                        "prev_oi": prev,
                        "curr_oi": int(curr_oi) if curr_oi is not None else None,
                        "abs_chg": int(abs_chg) if abs_chg is not None else None,
                        "pct_chg": float(pct_chg) if pct_chg is not None else None,
                    }
                )

        def _with_bars(
            ranked: list[dict[str, Any]],
            metric: str,
        ) -> list[dict[str, Any]]:
            sliced = ranked[: max(0, int(top_n))]
            magnitudes = [
                abs(float(e[metric]))
                for e in sliced
                if e.get(metric) is not None
            ]
            peak = max(magnitudes) if magnitudes else 0.0
            out: list[dict[str, Any]] = []
            for e in sliced:
                val = e.get(metric)
                bar = 0.0
                if peak > 0 and val is not None:
                    bar = round(abs(float(val)) / peak * 100.0, 1)
                out.append({**e, "bar_pct": bar})
            return out

        def _side_board(
            option_type: str,
            *,
            metric: str,
            positive: bool,
        ) -> list[dict[str, Any]]:
            side_rows = [e for e in entries if e.get("option_type") == option_type]
            if metric == "abs_chg":
                if positive:
                    ranked = sorted(
                        [e for e in side_rows if e.get("abs_chg") is not None and e["abs_chg"] > 0],
                        key=lambda e: e["abs_chg"],
                        reverse=True,
                    )
                else:
                    ranked = sorted(
                        [e for e in side_rows if e.get("abs_chg") is not None and e["abs_chg"] < 0],
                        key=lambda e: e["abs_chg"],  # most negative first
                    )
            else:
                if positive:
                    ranked = sorted(
                        [e for e in side_rows if e.get("pct_chg") is not None and e["pct_chg"] > 0],
                        key=lambda e: e["pct_chg"],
                        reverse=True,
                    )
                else:
                    ranked = sorted(
                        [e for e in side_rows if e.get("pct_chg") is not None and e["pct_chg"] < 0],
                        key=lambda e: e["pct_chg"],
                    )
            return _with_bars(ranked, metric)

        boards[key] = {
            "increase_abs": _side_board("CE", metric="abs_chg", positive=True)
            + _side_board("PE", metric="abs_chg", positive=True),
            "increase_pct": _side_board("CE", metric="pct_chg", positive=True)
            + _side_board("PE", metric="pct_chg", positive=True),
            "decrease_abs": _side_board("CE", metric="abs_chg", positive=False)
            + _side_board("PE", metric="abs_chg", positive=False),
            "decrease_pct": _side_board("CE", metric="pct_chg", positive=False)
            + _side_board("PE", metric="pct_chg", positive=False),
        }

    return boards


def _key_suffix(index_from_atm: int) -> str:
    if index_from_atm == 0:
        return "atm"
    if index_from_atm < 0:
        return f"itm{-index_from_atm}"
    return f"otm{index_from_atm}"


def get_relevant_options(
    underlying: str,
    expiry: str,
    atm_strike_val: float,
    options_count: int,
    strike_step: int,
) -> dict[str, dict[str, Any]]:
    """Map option keys (atm_ce, itm1_pe, ...) to contract details."""
    chain = get_chain(underlying, expiry)
    strikes_list = chain.get("strikes") or []
    by_strike: dict[float, dict[str, Any]] = {}
    for row in strikes_list:
        by_strike[float(row["strike"])] = row

    relevant: dict[str, dict[str, Any]] = {}
    for i in range(-options_count, options_count + 1):
        strike = atm_strike_val + (i * strike_step)
        row = by_strike.get(strike)
        if row is None:
            # tolerate float drift
            for k, v in by_strike.items():
                if abs(k - strike) < 0.01:
                    row = v
                    strike = k
                    break
        if not row:
            continue
        suffix = _key_suffix(i)
        if row.get("ce"):
            relevant[f"{suffix}_ce"] = {
                **row["ce"],
                "strike": strike,
                "position": i,
            }
        if row.get("pe"):
            relevant[f"{suffix}_pe"] = {
                **row["pe"],
                "strike": strike,
                "position": i,
            }
    return relevant


def fetch_historical_oi_batch(
    option_details: dict[str, dict[str, Any]],
    minutes: int,
) -> dict[str, list[dict[str, Any]]]:
    store: dict[str, list[dict[str, Any]]] = {}
    for key, details in option_details.items():
        token = details.get("instrument_token")
        if not token:
            store[key] = []
            continue
        try:
            store[key] = fetch_minute_oi(int(token), minutes=minutes)
        except Exception:
            store[key] = []
    return store


def _parse_candle_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return None


def find_oi_at_timestamp(
    historical_candles: list[dict[str, Any]],
    target_time: datetime,
    latest_oi_and_time: tuple[Any, datetime] | None = None,
) -> int | None:
    if not historical_candles:
        return None
    for candle in reversed(historical_candles):
        candle_time = _parse_candle_time(candle.get("date"))
        if candle_time is None:
            continue
        if candle_time <= target_time:
            if latest_oi_and_time and candle_time > latest_oi_and_time[1]:
                continue
            oi = candle.get("oi")
            return int(oi) if oi is not None else None
    return None


def find_close_at_timestamp(
    historical_candles: list[dict[str, Any]],
    target_time: datetime,
) -> float | None:
    if not historical_candles:
        return None
    for candle in reversed(historical_candles):
        candle_time = _parse_candle_time(candle.get("date"))
        if candle_time is None:
            continue
        if candle_time <= target_time:
            close = candle.get("close")
            if close is not None and float(close) > 0:
                return float(close)
            return None
    return None


def calculate_oi_differences(
    raw_historical: dict[str, list[dict[str, Any]]],
    intervals_min: tuple[int, ...],
) -> dict[str, dict[str, Any]]:
    report: dict[str, dict[str, Any]] = {}
    current_processing_time = datetime.now(timezone.utc)

    for option_key, candles_list in raw_historical.items():
        report[option_key] = {}
        latest_oi, latest_oi_timestamp = None, None
        if candles_list:
            latest_candle = candles_list[-1]
            latest_oi = latest_candle.get("oi")
            latest_oi_timestamp = _parse_candle_time(latest_candle.get("date"))

        report[option_key]["latest_oi"] = int(latest_oi) if latest_oi is not None else None
        report[option_key]["latest_oi_timestamp"] = latest_oi_timestamp

        if latest_oi is None:
            for interval in intervals_min:
                report[option_key][f"abs_diff_{interval}m"] = None
                report[option_key][f"pct_diff_{interval}m"] = None
            continue

        for interval in intervals_min:
            target_past_time = current_processing_time - timedelta(minutes=interval)
            past_oi = find_oi_at_timestamp(
                candles_list,
                target_past_time,
                latest_oi_and_time=(latest_oi, latest_oi_timestamp) if latest_oi_timestamp else None,
            )
            abs_oi_diff = None
            pct_oi_change = None
            if past_oi is not None:
                abs_oi_diff = int(latest_oi) - past_oi
                if past_oi != 0:
                    pct_oi_change = (abs_oi_diff / past_oi) * 100.0
            report[option_key][f"abs_diff_{interval}m"] = abs_oi_diff
            report[option_key][f"pct_diff_{interval}m"] = pct_oi_change

    return report


def calculate_iv_differences(
    raw_historical: dict[str, list[dict[str, Any]]],
    contracts: dict[str, dict[str, Any]],
    spot_candles: list[dict[str, Any]],
    live_quotes: dict[str, dict[str, Any]],
    expiry: str,
    spot_now: float,
    intervals_min: tuple[int, ...],
    risk_free_rate: float,
) -> dict[str, dict[str, Any]]:
    report: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc)

    for option_key, candles_list in raw_historical.items():
        contract = contracts.get(option_key, {})
        strike = contract.get("strike")
        option_type = "CE" if option_key.endswith("_ce") else "PE"
        exchange = contract.get("exchange", "NFO")
        symbol = contract.get("tradingsymbol", "")
        quote_key = f"{exchange}:{symbol}"

        quote = live_quotes.get(quote_key) or {}
        ltp = quote.get("last_price")
        if ltp is None and candles_list:
            ltp = candles_list[-1].get("close")
        ltp = float(ltp) if ltp is not None else None

        tte_now = time_to_expiry_years(expiry, now)
        iv_now = implied_volatility(
            ltp, spot_now, float(strike) if strike is not None else None,
            tte_now, option_type, risk_free_rate,
        )

        entry: dict[str, Any] = {
            "ltp": ltp,
            "iv": round(iv_now * 100.0, 2) if iv_now is not None else None,
        }

        for interval in intervals_min:
            entry[f"iv_abs_diff_{interval}m"] = None
            entry[f"iv_pct_diff_{interval}m"] = None

        if iv_now is None:
            report[option_key] = entry
            continue

        for interval in intervals_min:
            target_past_time = now - timedelta(minutes=interval)
            past_price = find_close_at_timestamp(candles_list, target_past_time)
            past_spot = find_close_at_timestamp(spot_candles, target_past_time)
            tte_past = time_to_expiry_years(expiry, target_past_time)
            iv_past = implied_volatility(
                past_price,
                past_spot,
                float(strike) if strike is not None else None,
                tte_past,
                option_type,
                risk_free_rate,
            )
            if iv_past is None:
                continue
            abs_diff = (iv_now - iv_past) * 100.0
            pct_diff = (abs_diff / (iv_past * 100.0)) * 100.0 if iv_past > 0 else None
            entry[f"iv_abs_diff_{interval}m"] = round(abs_diff, 2)
            entry[f"iv_pct_diff_{interval}m"] = round(pct_diff, 2) if pct_diff is not None else None

        report[option_key] = entry

    return report


def _build_side_rows(
    oi_report: dict[str, dict[str, Any]],
    iv_report: dict[str, dict[str, Any]],
    contracts: dict[str, dict[str, Any]],
    side: str,
    intervals_min: tuple[int, ...],
    thresholds: dict[int, float],
    raw_historical: dict[str, list[dict[str, Any]]] | None = None,
    pcr_now: float | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Build CE or PE rows sorted by strike. Returns rows, breach_count, cell_count."""
    suffix = "_ce" if side == "call" else "_pe"
    keys = sorted(
        [k for k in oi_report if k.endswith(suffix)],
        key=lambda k: contracts.get(k, {}).get("strike", 0),
    )
    signal_map: dict[str, dict[str, dict[str, str] | None]] = {}
    if raw_historical is not None and pcr_now is not None:
        signal_map = compute_interval_signals(
            side, keys, oi_report, iv_report, raw_historical, pcr_now, intervals_min
        )

    rows: list[dict[str, Any]] = []
    breach_count = 0
    cell_count = 0

    for key in keys:
        data = oi_report.get(key, {})
        iv_data = iv_report.get(key, {})
        contract = contracts.get(key, {})
        position = contract.get("position", 0)
        pct_map: dict[str, float | None] = {}
        abs_map: dict[str, int | None] = {}
        prev_map: dict[str, int | None] = {}
        breach_map: dict[str, bool] = {}
        iv_pct_map: dict[str, float | None] = {}
        iv_abs_map: dict[str, float | None] = {}
        signals_map: dict[str, dict[str, str] | None] = signal_map.get(key, {})
        latest_oi = data.get("latest_oi")

        for interval in intervals_min:
            pct = data.get(f"pct_diff_{interval}m")
            abs_chg = data.get(f"abs_diff_{interval}m")
            pct_map[str(interval)] = pct
            abs_map[str(interval)] = abs_chg
            prev_map[str(interval)] = _prev_oi(latest_oi, abs_chg)
            iv_pct_map[str(interval)] = iv_data.get(f"iv_pct_diff_{interval}m")
            iv_abs_map[str(interval)] = iv_data.get(f"iv_abs_diff_{interval}m")
            cell_count += 1
            breached = (
                pct is not None
                and interval in thresholds
                and abs(pct) > thresholds[interval]
            )
            breach_map[str(interval)] = bool(breached)
            if breached:
                breach_count += 1

        ts = data.get("latest_oi_timestamp")
        rows.append(
            {
                "key": key,
                "strike": contract.get("strike"),
                "symbol": contract.get("tradingsymbol", "N/A"),
                "instrument_token": contract.get("instrument_token"),
                "position": position,
                "latest_oi": latest_oi,
                "oi_time": ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else None),
                "ltp": iv_data.get("ltp"),
                "iv": iv_data.get("iv"),
                "pct": pct_map,
                "abs": abs_map,
                "prev_oi": prev_map,
                "iv_pct": iv_pct_map,
                "iv_abs": iv_abs_map,
                "signals": signals_map,
                "breach": breach_map,
            }
        )
    return rows, breach_count, cell_count


def tracker_config() -> dict[str, Any]:
    defaults = OI_TRACKER_DEFAULTS
    return {
        "underlyings": list(INDEX_OPTIONS.keys()),
        "options_count": defaults["options_count"],
        "historical_minutes": defaults["historical_minutes"],
        "intervals_min": list(defaults["intervals_min"]),
        "refresh_seconds": defaults["refresh_seconds"],
        "thresholds": {str(k): v for k, v in defaults["pct_thresholds"].items()},
        "alert_breach_ratio": defaults["alert_breach_ratio"],
        "risk_free_rate": defaults["risk_free_rate"],
        "bias_interval_min": defaults.get("bias_interval_min", 15),
        "bias_sideways_threshold": defaults.get("bias_sideways_threshold", 0.55),
        "change_board_top_n": defaults.get("change_board_top_n", 5),
        "change_board_interval_min": defaults.get("change_board_interval_min", 15),
    }


def build_snapshot(
    underlying: str,
    expiry: str | None = None,
    options_count: int | None = None,
) -> dict[str, Any]:
    if underlying not in INDEX_OPTIONS:
        raise ValueError(f"Unknown underlying '{underlying}'. Use {list(INDEX_OPTIONS)}")

    defaults = OI_TRACKER_DEFAULTS
    opt_count = options_count if options_count is not None else defaults["options_count"]
    intervals = defaults["intervals_min"]
    thresholds: dict[int, float] = defaults["pct_thresholds"]
    hist_minutes = defaults["historical_minutes"]
    alert_ratio = defaults["alert_breach_ratio"]
    risk_free_rate = float(defaults["risk_free_rate"])

    meta = INDEX_OPTIONS[underlying]
    strike_step = int(meta["strike_step"])

    exp = expiry or nearest_expiry(underlying)
    if not exp:
        raise RuntimeError(f"No expiries found for {underlying}")

    spot = get_index_spot(underlying)
    spot_warning: str | None = None
    if spot is None:
        spot_warning = "Index LTP unavailable; using chain midpoint for ATM"
        chain = get_chain(underlying, exp)
        strikes = chain.get("strikes") or []
        if not strikes:
            raise RuntimeError(f"No option chain for {underlying} expiry {exp}")
        spot = float(strikes[len(strikes) // 2]["strike"])

    atm = atm_strike(float(spot), strike_step)
    contracts = get_relevant_options(underlying, exp, atm, opt_count, strike_step)
    if not contracts:
        raise RuntimeError(f"No option contracts found around ATM {atm} for {underlying} {exp}")

    raw_oi = fetch_historical_oi_batch(contracts, minutes=hist_minutes)
    spot_candles: list[dict[str, Any]] = []
    try:
        spot_candles = fetch_index_minute_spot(underlying, minutes=hist_minutes)
    except Exception:
        spot_candles = []

    quote_keys = [
        f"{c.get('exchange', meta['exchange'])}:{c.get('tradingsymbol')}"
        for c in contracts.values()
        if c.get("tradingsymbol")
    ]
    live_quotes: dict[str, dict[str, Any]] = {}
    try:
        live_quotes = fetch_quote_batch(quote_keys)
    except Exception:
        live_quotes = {}

    oi_report = calculate_oi_differences(raw_oi, intervals)
    iv_report = calculate_iv_differences(
        raw_oi,
        contracts,
        spot_candles,
        live_quotes,
        exp,
        float(spot),
        intervals,
        risk_free_rate,
    )

    call_oi_total = sum(
        (v.get("latest_oi") or 0) for k, v in oi_report.items() if k.endswith("_ce")
    )
    put_oi_total = sum(
        (v.get("latest_oi") or 0) for k, v in oi_report.items() if k.endswith("_pe")
    )
    chain_pcr = round(put_oi_total / call_oi_total, 4) if call_oi_total > 0 else None

    calls, call_breach, call_cells = _build_side_rows(
        oi_report, iv_report, contracts, "call", intervals, thresholds, raw_oi, chain_pcr
    )
    puts, put_breach, put_cells = _build_side_rows(
        oi_report, iv_report, contracts, "put", intervals, thresholds, raw_oi, chain_pcr
    )

    call_ratio = (call_breach / call_cells) if call_cells else 0.0
    put_ratio = (put_breach / put_cells) if put_cells else 0.0
    alert_triggered = call_ratio > alert_ratio or put_ratio > alert_ratio

    atm_ce_iv = next((r.get("iv") for r in calls if r.get("position") == 0), None)
    atm_pe_iv = next((r.get("iv") for r in puts if r.get("position") == 0), None)

    overall_bias = compute_overall_bias(
        calls,
        puts,
        interval_min=int(defaults.get("bias_interval_min", 15)),
        sideways_threshold=float(defaults.get("bias_sideways_threshold", 0.55)),
    )

    board_top_n = int(defaults.get("change_board_top_n", 5))
    board_interval = int(defaults.get("change_board_interval_min", 15))
    change_boards = build_oi_change_boards(
        calls,
        puts,
        expiry=exp,
        intervals_min=intervals,
        top_n=board_top_n,
    )

    snapshot = {
        "underlying": underlying,
        "expiry": exp,
        "spot": float(spot),
        "atm_strike": float(atm),
        "spot_warning": spot_warning,
        "updated_at": datetime.now().astimezone().isoformat(),
        "intervals_min": list(intervals),
        "thresholds": {str(k): v for k, v in thresholds.items()},
        "options_count": opt_count,
        "calls": calls,
        "puts": puts,
        "change_boards": change_boards,
        "change_board_top_n": board_top_n,
        "change_board_interval_min": board_interval,
        "pcr": {
            "chain_oi": chain_pcr,
            "call_oi_total": call_oi_total,
            "put_oi_total": put_oi_total,
        },
        "overall_bias": overall_bias,
        "alert": {
            "triggered": alert_triggered,
            "call_breach_ratio": round(call_ratio, 4),
            "put_breach_ratio": round(put_ratio, 4),
            "breach_ratio_threshold": alert_ratio,
        },
    }

    call_pct = round(call_ratio * 100, 1)
    put_pct = round(put_ratio * 100, 1)
    pcr_str = f"{chain_pcr:.2f}" if chain_pcr is not None else "N/A"
    iv_parts: list[str] = []
    if atm_ce_iv is not None:
        iv_parts.append(f"ATM CE IV {atm_ce_iv:.1f}%")
    if atm_pe_iv is not None:
        iv_parts.append(f"PE {atm_pe_iv:.1f}%")
    iv_str = " · ".join(iv_parts)
    detail = (
        f"{underlying} {exp} · spot {float(spot):.2f} · ATM {atm} · PCR {pcr_str}"
        f" · calls {call_pct}% · puts {put_pct}% breached"
    )
    if iv_str:
        detail += f" · {iv_str}"

    append_log(
        "snapshot",
        detail,
        {
            "underlying": underlying,
            "expiry": exp,
            "spot": float(spot),
            "atm_strike": float(atm),
            "chain_pcr": chain_pcr,
            "call_oi_total": call_oi_total,
            "put_oi_total": put_oi_total,
            "atm_ce_iv": atm_ce_iv,
            "atm_pe_iv": atm_pe_iv,
            "call_breach_ratio": round(call_ratio, 4),
            "put_breach_ratio": round(put_ratio, 4),
            "alert_triggered": alert_triggered,
        },
    )
    if alert_triggered:
        breached_calls = [r for r in calls if any(r.get("breach", {}).values())]
        breached_puts = [r for r in puts if any(r.get("breach", {}).values())]
        append_log(
            "alert",
            f"Call {call_pct}% · Put {put_pct}% cells breached (threshold {int(alert_ratio * 100)}%)",
            {
                "underlying": underlying,
                "expiry": exp,
                "call_breach_ratio": round(call_ratio, 4),
                "put_breach_ratio": round(put_ratio, 4),
                "breached_call_strikes": [r.get("strike") for r in breached_calls[:5]],
                "breached_put_strikes": [r.get("strike") for r in breached_puts[:5]],
            },
        )

    return snapshot
