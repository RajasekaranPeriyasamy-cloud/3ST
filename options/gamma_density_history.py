"""Intraday GEX / flip history for the Gamma Density desk (session-scoped JSON)."""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, time
from typing import Any, Callable, TypeVar
from zoneinfo import ZoneInfo

from config import DEFAULT_SESSION, INDEX_OPTIONS, MCX_SESSION, is_mcx_underlying
from settings import data_dir
from utils.atomic_json import atomic_write_json

IST = ZoneInfo("Asia/Kolkata")
HISTORY_FILE = data_dir() / "gamma_density_history.json"

# Serialize load→mutate→save so concurrent snapshot refreshes / underlying switches
# cannot clobber other keys (classic lost-update + truncated-read wipe).
_STORE_LOCK = threading.RLock()

# Day-end (last in-session) HHI: keep a buffer beyond the 30-session percentile window.
DAILY_HHI_MAX_DAYS = 40
DAILY_HHI_PERCENTILE_N = 30

# Per-session pin sampling. The intraday ``series`` trail is pruned to today on
# every write, so nothing survives to answer "when the pin looked strong at
# midday, did it hold to close?". These bounded samples are the calibration set
# for that question; without them any pin-strength threshold is a prior, not a
# finding. Deliberately stores raw inputs and no verdict — held/strength are
# derived at read time so a threshold change does not invalidate history.
DAILY_PIN_MAX_DAYS = 60
DAILY_PIN_SAMPLE_MIN = 30
# Sample fields kept per checkpoint (bounded ~13/session on a cash underlying).
PIN_SAMPLE_FIELDS = (
    "t",
    "pin",
    "pin_source",
    "pin_share",
    "spot",
    "total_gex",
    "gamma_regime",
    "hhi",
    "flip_level",
    "sigma1_pts",
)

_T = TypeVar("_T")


def _today_ist() -> date:
    """Calendar day in IST — history keys must not depend on host local TZ."""
    return datetime.now(tz=IST).date()


def _key(underlying: str, expiry: str) -> str:
    return f"{underlying.upper()}|{expiry}|{_today_ist().isoformat()}"


def _daily_hhi_key(underlying: str) -> str:
    """Cross-session HHI is keyed by underlying (expiry rolls weekly)."""
    return underlying.upper()


def _reversals_key(underlying: str, expiry: str, tf: str | None = None) -> str:
    """Session key for frozen reversals: underlying|expiry|date|tf."""
    tf_key = normalize_reversal_tf(tf)
    return f"{underlying.upper()}|{expiry}|{_today_ist().isoformat()}|{tf_key}"


FROZEN_REVERSAL_FIELDS = (
    "t",
    "ts_ms",
    "confirmed_at",
    "confirmed_ts_ms",
    "side",
    "spot",
    "move_pts",
    "gex_confirm",
    "partial_ungated",
    # Live: price pivot shown before GEX/OI hard gate clears; promotes in-place.
    "provisional",
    "oi_align",
    "oi_gate_pass",
    "tf",
    "label",
)

# Hard OI gate: proximity to walls/pin (strike steps).
OI_GATE_NEAR_STEPS = 2
# Neighborhood for supportive / hostile ΔOI sums.
OI_DOI_NEAR_STEPS = 3


def _canonical_reversal(rev: dict[str, Any]) -> dict[str, Any]:
    return {k: rev.get(k) for k in FROZEN_REVERSAL_FIELDS}


def _empty_store() -> dict[str, Any]:
    return {"series": {}, "reversals": {}, "daily_hhi": {}, "daily_pin": {}}


def _normalize_store(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("series", {})
    data.setdefault("reversals", {})
    data.setdefault("daily_hhi", {})
    data.setdefault("daily_pin", {})
    return data


def _load(*, strict: bool = False) -> dict[str, Any]:
    """Load history JSON.

    ``strict=True`` (mutators): refuse to treat a non-empty corrupt/partial file as
    empty — callers must abort the write rather than wipe the desk history.
    """
    if not HISTORY_FILE.exists():
        return _empty_store()
    try:
        raw = HISTORY_FILE.read_text(encoding="utf-8")
    except OSError:
        if strict:
            raise
        return _empty_store()
    if not raw.strip():
        return _empty_store()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        if strict:
            raise
        return _empty_store()
    if not isinstance(data, dict):
        if strict:
            raise ValueError("gamma_density_history root must be an object")
        return _empty_store()
    return _normalize_store(data)


def _save(data: dict[str, Any]) -> None:
    """Atomic replace so concurrent readers never see a truncated JSON file."""
    atomic_write_json(HISTORY_FILE, data, indent=2)


def _update_store(mutator: Callable[[dict[str, Any]], _T]) -> _T:
    """Run ``mutator(data)`` under the store lock and persist (or abort on corrupt)."""
    with _STORE_LOCK:
        try:
            data = _load(strict=True)
        except (json.JSONDecodeError, OSError, ValueError):
            # Do not overwrite a non-empty unreadable file with {} — that is the wipe bug.
            raise
        result = mutator(data)
        _save(data)
        return result


def session_window(underlying: str) -> tuple[time, time]:
    """Equity derivatives 09:15–15:40; MCX 09:00–23:30.

    Non-CAS cash continuous still ends 15:30 exchange-side; desk charts/history
    follow the derivatives segment through CAS close.
    """
    if is_mcx_underlying(underlying):
        start = MCX_SESSION.get("session_start", "09:00")
        end = MCX_SESSION.get("session_end", "23:30")
    else:
        meta = INDEX_OPTIONS.get(underlying.upper(), {})
        # Prefer instrument session when present; else derivatives default
        start = meta.get("session_start") or DEFAULT_SESSION.get("session_start", "09:15")
        end = meta.get("session_end") or DEFAULT_SESSION.get("session_end", "15:40")
        # INDEX_OPTIONS entries don't have session_start — use DEFAULT
        if not isinstance(start, str):
            start = "09:15"
        if not isinstance(end, str):
            end = "15:40"
    try:
        sh, sm = (int(x) for x in str(start).split(":")[:2])
        eh, em = (int(x) for x in str(end).split(":")[:2])
        return time(sh, sm), time(eh, em)
    except Exception:
        return time(9, 15), time(15, 40)


def in_session(underlying: str, when: datetime | None = None) -> bool:
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    start, end = session_window(underlying)
    t = now.timetz().replace(tzinfo=None)
    return start <= t <= end


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def session_history_max_points(underlying: str, *, interval_sec: int = 60) -> int:
    """Retention cap so a full session at ``interval_sec`` is not truncated.

    Cash ~6.5h → ~400 ticks; MCX ~14.5h → ~900+ ticks. Floor keeps short
    sessions comfortable; ceiling avoids unbounded growth.
    """
    start, end = session_window(underlying)
    mins = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    if mins <= 0:
        mins = 400
    iv = max(30, int(interval_sec))
    return max(400, min(int(mins * 60 / iv) + 80, 1600))


def append_history_point(
    underlying: str,
    expiry: str,
    *,
    spot: float,
    total_gex: float,
    flip_level: float | None,
    gamma_regime: str,
    max_points: int | None = None,
    min_interval_sec: int = 45,
    hhi: float | None = None,
    conviction: float | int | None = None,
    pin_strike: float | None = None,
    atm_iv: float | None = None,
    pos_gex: float | None = None,
    neg_gex: float | None = None,
    call_wall: float | None = None,
    put_wall: float | None = None,
) -> list[dict[str, Any]]:
    """Append one in-session snapshot tick; skip after close / too-frequent polls.

    Only mutates this underlying|expiry|today series key (and prunes that pair's
    prior-day keys). Other underlyings' series / reversals / daily_hhi are kept.

    ``pos_gex`` / ``neg_gex`` are chart-only add-ons. Reversal / GEX-gate logic
    continues to key solely on ``total_gex`` (+ spot / OI) — do not wire
    component fields into ``detect_spot_reversals`` or ``_gex_sign``.

    ``call_wall`` / ``put_wall`` are recorded (added 2026-08-27) purely so a
    *past* session's walls can be recovered. Every other level on the tick had
    history; the walls were live-only, which meant no desk could draw them on an
    archived day and no study could ask whether they held. Like the collector's
    strike width this is forward-only — a wall not written down now is gone.
    They are read-only passengers here: nothing in this module keys on them.
    """
    key = _key(underlying, expiry)
    now = datetime.now(tz=IST)
    cap = int(max_points) if max_points is not None else session_history_max_points(
        underlying, interval_sec=max(min_interval_sec, 45)
    )

    # Read path (no write): out-of-session / too-soon must not touch the file.
    with _STORE_LOCK:
        data = _load(strict=False)
        series = data.setdefault("series", {})
        points: list[dict[str, Any]] = list(series.get(key) or [])

        if not in_session(underlying, now):
            return points

        if points:
            last_t = _parse_ts(points[-1].get("t"))
            if last_t is not None and (now - last_t).total_seconds() < min_interval_sec:
                return points

    tick: dict[str, Any] = {
        "t": now.isoformat(timespec="seconds"),
        "spot": round(float(spot), 2),
        "total_gex": round(float(total_gex), 2),
        "flip_level": round(float(flip_level), 2) if flip_level is not None else None,
        "gamma_regime": gamma_regime,
    }
    if pos_gex is not None:
        tick["pos_gex"] = round(float(pos_gex), 2)
    if neg_gex is not None:
        tick["neg_gex"] = round(float(neg_gex), 2)
    if hhi is not None:
        tick["hhi"] = round(float(hhi), 4)
    if conviction is not None:
        tick["conviction"] = float(conviction)
    if pin_strike is not None:
        tick["pin_strike"] = round(float(pin_strike), 2)
    if atm_iv is not None:
        tick["atm_iv"] = round(float(atm_iv), 2)
    if call_wall is not None:
        tick["call_wall"] = round(float(call_wall), 2)
    if put_wall is not None:
        tick["put_wall"] = round(float(put_wall), 2)

    def _mutate(data: dict[str, Any]) -> list[dict[str, Any]]:
        series = data.setdefault("series", {})
        pts: list[dict[str, Any]] = list(series.get(key) or [])
        # Re-check interval under the write lock (another poll may have just written).
        if pts:
            last_t = _parse_ts(pts[-1].get("t"))
            if last_t is not None and (now - last_t).total_seconds() < min_interval_sec:
                return pts
        pts.append(tick)
        if len(pts) > cap:
            pts = pts[-cap:]
        series[key] = pts
        # Prune only this underlying|expiry's prior calendar days — never other names.
        prefix = f"{underlying.upper()}|{expiry}|"
        today = _today_ist().isoformat()
        for k in list(series.keys()):
            if k.startswith(prefix) and not k.endswith(today):
                del series[k]
        return pts

    try:
        return _update_store(_mutate)
    except (json.JSONDecodeError, OSError, ValueError):
        return get_history(underlying, expiry)


def get_history(underlying: str, expiry: str) -> list[dict[str, Any]]:
    with _STORE_LOCK:
        data = _load(strict=False)
        return list(data.get("series", {}).get(_key(underlying, expiry)) or [])


# Measurement basis carried on each day-end HHI row. Rows also carry both basis
# values (``hhi_gross`` / ``hhi_net``) so switching basis does not restart the
# cross-session comparison from zero.
_DAILY_HHI_BASIS_FIELDS = (
    "basis",
    "strike_window",
    "sign_mode",
    "updated_at",
    "hhi_gross",
    "hhi_net",
    "strike_window_assumed",
    "legacy",
)

# Rows written before basis tagging existed. Only two paths ever persisted a
# day-end HHI: the snapshot route (window from the UI) and the background GEX
# recorder, which passes no window and so always used the config default. The
# recorder samples continuously through the session, so the last write of a day —
# the one ``upsert_daily_hhi`` keeps — was almost always the recorder's. Hence a
# defensible default rather than a guess, still flagged ``strike_window_assumed``.
LEGACY_DAILY_HHI_BASIS = "net"
LEGACY_DAILY_HHI_SIGN_MODE = "naive"


def legacy_daily_hhi_window() -> int:
    """Strike window assumed for day-end rows written before basis tagging."""
    try:
        from config import GAMMA_DENSITY_DEFAULTS

        return int(GAMMA_DENSITY_DEFAULTS.get("strike_window") or 20)
    except Exception:
        return 20


def normalize_legacy_daily_hhi_row(row: dict[str, Any]) -> dict[str, Any]:
    """Interpret an untagged day-end row as net-basis at the assumed window.

    Applied on read as well as on write, so a store that has not been migrated
    yet behaves identically to one that has. Already-tagged rows pass through.
    """
    if row.get("basis"):
        return row
    out = dict(row)
    out["basis"] = LEGACY_DAILY_HHI_BASIS
    out["sign_mode"] = LEGACY_DAILY_HHI_SIGN_MODE
    out["strike_window"] = legacy_daily_hhi_window()
    out["strike_window_assumed"] = True
    out["legacy"] = True
    if out.get("hhi") is not None and out.get("hhi_net") is None:
        out["hhi_net"] = out["hhi"]
    return out


def _daily_hhi_row(date_str: str, hhi: float, src: dict[str, Any] | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"date": str(date_str)[:10], "hhi": round(float(hhi), 4)}
    for field in _DAILY_HHI_BASIS_FIELDS:
        val = (src or {}).get(field)
        if val is not None:
            row[field] = val
    return row


def _normalize_daily_hhi_entries(raw: Any) -> list[dict[str, Any]]:
    """Return ``[{date, hhi, ...basis}, ...]`` sorted ascending by date."""
    out: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        # Allow { "YYYY-MM-DD": hhi } map form
        for d, h in raw.items():
            try:
                out.append(_daily_hhi_row(str(d), float(h)))
            except (TypeError, ValueError):
                continue
    elif isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            d = row.get("date")
            h = row.get("hhi")
            if d is None or h is None:
                continue
            try:
                out.append(_daily_hhi_row(str(d), float(h), row))
            except (TypeError, ValueError):
                continue
    out.sort(key=lambda r: r["date"])
    # Deduplicate by date (last write wins)
    by_date: dict[str, dict[str, Any]] = {}
    for row in out:
        by_date[row["date"]] = row
    return [by_date[k] for k in sorted(by_date)]


def get_daily_hhi_series(underlying: str) -> list[dict[str, Any]]:
    """Persisted per-trading-day HHI for ``underlying`` (oldest → newest)."""
    with _STORE_LOCK:
        data = _load(strict=False)
        store = data.get("daily_hhi") or {}
        return _normalize_daily_hhi_entries(store.get(_daily_hhi_key(underlying)))


def _row_hhi_for_basis(row: dict[str, Any], basis: str) -> float | None:
    """This row's HHI on ``basis``, or None when it does not carry that basis.

    Rows written since dual-basis persistence carry both ``hhi_gross`` and
    ``hhi_net``, so a day recorded on one basis can still serve a comparison on
    the other. Legacy rows carry net only.
    """
    if str(row.get("basis") or LEGACY_DAILY_HHI_BASIS) == str(basis):
        raw = row.get("hhi")
    else:
        raw = row.get(f"hhi_{basis}")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def filter_daily_hhi_basis(
    series: list[dict[str, Any]] | None,
    *,
    basis: str | None = None,
    strike_window: int | None = None,
    sign_mode: str | None = None,
) -> list[dict[str, Any]]:
    """Keep day-end rows comparable with the current snapshot, on ``basis``.

    HHI is computed over the ATM-trimmed window, so its floor is 1/N — a day
    recorded at ``strike_window=10`` is not comparable with one at 20, and a gross
    reading is not comparable with a net one. Rows that carry the requested basis
    under a different recorded basis are resolved to it and their ``hhi`` rewritten
    accordingly. Untagged legacy rows are read as net-basis at the assumed window
    (see :func:`normalize_legacy_daily_hhi_row`). Pass all filters as ``None`` to
    keep everything untouched.
    """
    rows = list(series or [])
    if basis is None and strike_window is None and sign_mode is None:
        return rows
    out: list[dict[str, Any]] = []
    for raw_row in rows:
        row = normalize_legacy_daily_hhi_row(raw_row)
        if strike_window is not None:
            row_window = row.get("strike_window")
            if row_window is None or int(row_window) != int(strike_window):
                continue
        if sign_mode is not None:
            row_mode = row.get("sign_mode")
            if row_mode is None or str(row_mode) != str(sign_mode):
                continue
        if basis is None:
            out.append(row)
            continue
        resolved = _row_hhi_for_basis(row, str(basis))
        if resolved is None:
            continue
        if str(row.get("basis")) == str(basis):
            out.append(row)
        else:
            promoted = dict(row)
            promoted["hhi"] = round(resolved, 4)
            promoted["basis"] = str(basis)
            out.append(promoted)
    return out


def count_assumed_window_rows(series: list[dict[str, Any]] | None) -> int:
    """Rows in ``series`` whose strike window was inferred, not recorded."""
    return sum(1 for r in (series or []) if r.get("strike_window_assumed"))


def upsert_daily_hhi(
    underlying: str,
    hhi: float,
    *,
    when: datetime | None = None,
    max_days: int = DAILY_HHI_MAX_DAYS,
    force: bool = False,
    basis: str | None = None,
    strike_window: int | None = None,
    sign_mode: str | None = None,
    hhi_gross: float | None = None,
    hhi_net: float | None = None,
) -> list[dict[str, Any]]:
    """Update today's daily HHI while in session; prune to ``max_days``.

    Last in-session write ≈ session-close value. Returns the pruned series.
    Outside session (unless ``force``), returns the existing series unchanged.
    Only touches ``daily_hhi[UNDERLYING]`` — never clears series/reversals.

    ``basis`` / ``strike_window`` / ``sign_mode`` record how the value was
    measured so later sessions are only compared like-for-like. ``hhi_gross`` /
    ``hhi_net`` persist both measures, so switching basis keeps the cross-session
    comparison instead of restarting it.

    Also migrates any untagged legacy rows in the same locked write — see
    :func:`normalize_legacy_daily_hhi_row`.
    """
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    if not force and not in_session(underlying, now):
        return get_daily_hhi_series(underlying)

    day = now.date().isoformat()
    key = _daily_hhi_key(underlying)
    stamp: dict[str, Any] = {"updated_at": now.isoformat(timespec="seconds")}
    if basis is not None:
        stamp["basis"] = str(basis)
    if strike_window is not None:
        stamp["strike_window"] = int(strike_window)
    if sign_mode is not None:
        stamp["sign_mode"] = str(sign_mode)
    if hhi_gross is not None:
        stamp["hhi_gross"] = round(float(hhi_gross), 4)
    if hhi_net is not None:
        stamp["hhi_net"] = round(float(hhi_net), 4)
    # A freshly measured row is never an inferred one.
    stamp["strike_window_assumed"] = False
    stamp["legacy"] = False

    def _mutate(data: dict[str, Any]) -> list[dict[str, Any]]:
        store = data.setdefault("daily_hhi", {})
        # Migrate untagged rows in the same locked write — idempotent, and it keeps
        # a not-yet-migrated store behaving identically to a migrated one.
        series = [
            normalize_legacy_daily_hhi_row(r)
            for r in _normalize_daily_hhi_entries(store.get(key))
        ]
        updated = False
        for row in series:
            if row["date"] == day:
                row["hhi"] = round(float(hhi), 4)
                row.update(stamp)
                updated = True
                break
        if not updated:
            series.append(_daily_hhi_row(day, hhi, stamp))
            series.sort(key=lambda r: r["date"])
        if max_days > 0 and len(series) > max_days:
            series = series[-max_days:]
        store[key] = series
        return series

    try:
        return _update_store(_mutate)
    except (json.JSONDecodeError, OSError, ValueError):
        return get_daily_hhi_series(underlying)


def _daily_pin_key(underlying: str) -> str:
    return underlying.upper()


def get_daily_pin_series(underlying: str) -> list[dict[str, Any]]:
    """Persisted per-session pin samples for ``underlying`` (oldest → newest)."""
    with _STORE_LOCK:
        data = _load(strict=False)
        raw = (data.get("daily_pin") or {}).get(_daily_pin_key(underlying))
        rows = [r for r in (raw or []) if isinstance(r, dict) and r.get("date")]
        rows.sort(key=lambda r: str(r["date"]))
        return rows


def _pin_sample(when: datetime, **fields: Any) -> dict[str, Any]:
    sample: dict[str, Any] = {"t": when.isoformat(timespec="seconds")}
    for key in PIN_SAMPLE_FIELDS:
        if key == "t":
            continue
        val = fields.get(key)
        if val is None:
            sample[key] = None
        elif key in ("pin_source", "gamma_regime"):
            sample[key] = str(val)
        else:
            try:
                sample[key] = round(float(val), 4)
            except (TypeError, ValueError):
                sample[key] = None
    return sample


def record_pin_sample(
    underlying: str,
    *,
    pin: float | None,
    pin_source: str | None,
    pin_share: float | None,
    spot: float | None,
    total_gex: float | None,
    gamma_regime: str | None,
    hhi: float | None,
    flip_level: float | None,
    sigma1_pts: float | None,
    strike_step: float | None,
    when: datetime | None = None,
    sample_every_min: int = DAILY_PIN_SAMPLE_MIN,
    max_days: int = DAILY_PIN_MAX_DAYS,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Append a bounded per-session pin checkpoint; refresh the session's close.

    A sample is added only when the last one is older than ``sample_every_min``,
    so a fast poll loop cannot bloat the file. ``close_spot`` is refreshed on
    every call — the last in-session write approximates the close, the same
    convention :func:`upsert_daily_hhi` uses.

    Stores inputs only. Whether a pin "held" is a question about a threshold, so
    it is computed at read time (:func:`pin_hold_outcomes`) and a change of
    threshold re-reads history rather than invalidating it.

    Only touches ``daily_pin[UNDERLYING]``; never clears series/reversals/hhi.
    """
    now = when or datetime.now(tz=IST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)

    if not force and not in_session(underlying, now):
        return get_daily_pin_series(underlying)

    day = now.date().isoformat()
    key = _daily_pin_key(underlying)
    sample = _pin_sample(
        now,
        pin=pin,
        pin_source=pin_source,
        pin_share=pin_share,
        spot=spot,
        total_gex=total_gex,
        gamma_regime=gamma_regime,
        hhi=hhi,
        flip_level=flip_level,
        sigma1_pts=sigma1_pts,
    )

    def _mutate(data: dict[str, Any]) -> list[dict[str, Any]]:
        store = data.setdefault("daily_pin", {})
        rows = [r for r in (store.get(key) or []) if isinstance(r, dict) and r.get("date")]
        row = next((r for r in rows if str(r.get("date")) == day), None)
        if row is None:
            row = {"date": day, "samples": []}
            rows.append(row)
        samples: list[dict[str, Any]] = list(row.get("samples") or [])

        last_t = _parse_ts(samples[-1].get("t")) if samples else None
        if last_t is None or (now - last_t).total_seconds() >= sample_every_min * 60:
            samples.append(sample)
        row["samples"] = samples

        # Refreshed every call: last in-session write ≈ session close.
        if spot is not None:
            try:
                row["close_spot"] = round(float(spot), 2)
                row["close_t"] = now.isoformat(timespec="seconds")
            except (TypeError, ValueError):
                pass
        if strike_step is not None:
            try:
                row["strike_step"] = round(float(strike_step), 4)
            except (TypeError, ValueError):
                pass
        row["updated_at"] = now.isoformat(timespec="seconds")

        rows.sort(key=lambda r: str(r["date"]))
        if max_days > 0 and len(rows) > max_days:
            rows = rows[-max_days:]
        store[key] = rows
        return rows

    try:
        return _update_store(_mutate)
    except (json.JSONDecodeError, OSError, ValueError):
        return get_daily_pin_series(underlying)


def pin_hold_outcomes(
    series: list[dict[str, Any]] | None,
    *,
    hold_steps: float = 1.0,
    default_step: float = 50.0,
) -> list[dict[str, Any]]:
    """Flatten pin samples into ``{…sample, close_spot, held}`` rows for calibration.

    ``held`` is |close − pin| ≤ ``hold_steps`` × the session's strike step. Sessions
    with no recorded close, and samples with no pin, are skipped — an unknown
    outcome must not be scored as a failure.
    """
    out: list[dict[str, Any]] = []
    for row in series or []:
        close = row.get("close_spot")
        if close is None:
            continue
        try:
            close_f = float(close)
            step = float(row.get("strike_step") or default_step)
        except (TypeError, ValueError):
            continue
        tol = max(step, 1.0) * float(hold_steps)
        for sample in row.get("samples") or []:
            pin = sample.get("pin")
            if pin is None:
                continue
            try:
                pin_f = float(pin)
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    **sample,
                    "date": row.get("date"),
                    "close_spot": close_f,
                    "distance_at_close": round(close_f - pin_f, 2),
                    "held": abs(close_f - pin_f) <= tol + 1e-9,
                }
            )
    return out


def hhi_percentile_sessions(
    series: list[dict[str, Any]] | None,
    current_hhi: float,
    *,
    n: int = DAILY_HHI_PERCENTILE_N,
) -> tuple[float | None, int]:
    """Percentile rank of ``current_hhi`` among last ``n`` daily HHIs.

    Sample is the last ``n`` persisted trading-day values (including today when
    present). Returns ``(percentile, session_count)`` where percentile is the
    share of days with HHI ≤ current (0–100).
    """
    vals: list[float] = []
    if series:
        for row in series:
            if row.get("hhi") is None:
                continue
            try:
                vals.append(float(row["hhi"]))
            except (TypeError, ValueError):
                continue
    if n > 0:
        vals = vals[-n:]
    if not vals:
        return None, 0
    n_le = sum(1 for v in vals if v <= float(current_hhi) + 1e-12)
    pct = round(100.0 * n_le / len(vals), 1)
    return pct, len(vals)


def get_persisted_reversals(
    underlying: str,
    expiry: str,
    tf: str | None = None,
) -> list[dict[str, Any]]:
    """Return frozen session reversals for underlying|expiry|today|tf (read-only)."""
    with _STORE_LOCK:
        data = _load(strict=False)
        key = _reversals_key(underlying, expiry, tf)
        return [_canonical_reversal(r) for r in (data.get("reversals", {}).get(key) or [])]


# Alias used by Live+sparse waiting path (saved chips only — no new detect).
get_session_reversals = get_persisted_reversals


def _prune_stale_reversal_keys(
    rev_store: dict[str, Any],
    underlying: str,
    expiry: str,
) -> None:
    """Drop prior-day reversal buckets for this underlying|expiry."""
    today = _today_ist().isoformat()
    u = underlying.upper()
    for k in list(rev_store.keys()):
        parts = str(k).split("|")
        if len(parts) < 4:
            continue
        if parts[0] == u and parts[1] == expiry and parts[2] != today:
            del rev_store[k]


def _format_reversal_label(
    *,
    tf_key: str,
    side: str,
    move_pts: float,
    gex_confirm: bool,
    oi_align: bool,
) -> str:
    move = float(move_pts)
    bits = [
        f"{tf_key} {'Bullish' if side == 'bullish' else 'Bearish'}",
        f"(+{move:.0f} pts)" if side == "bullish" else f"(-{move:.0f} pts)",
    ]
    if gex_confirm:
        bits.append("GEX")
    if oi_align:
        bits.append("OI align")
    return " · ".join(bits)


def _hhmm_ist(ts_ms: int | None = None, iso: str | None = None) -> str | None:
    """Format a timestamp as HH:MM in IST (for chip labels)."""
    if ts_ms is not None:
        try:
            return datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=IST).strftime("%H:%M")
        except (TypeError, ValueError, OSError, OverflowError):
            pass
    if iso:
        try:
            raw = str(iso).replace("Z", "+00:00")
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=IST)
            return dt.astimezone(IST).strftime("%H:%M")
        except (TypeError, ValueError):
            pass
    return None


def format_reversal_chip_times(rev: dict[str, Any]) -> str:
    """Compact pivot / confirm time bits for UI chips.

    With confirm: ``08:39 pivot · conf 09:12``
    Without (legacy): ``08:39`` (pivot only).
    """
    pivot = _hhmm_ist(rev.get("ts_ms"), rev.get("t"))
    conf = _hhmm_ist(rev.get("confirmed_ts_ms"), rev.get("confirmed_at"))
    if pivot is None:
        return "—" if conf is None else f"conf {conf}"
    if conf is None:
        return pivot
    return f"{pivot} pivot · conf {conf}"


def _stamp_confirmed_at(rev: dict[str, Any], now_ms: int) -> dict[str, Any]:
    """Set confirmed_at / confirmed_ts_ms to ``now_ms`` (IST ISO)."""
    now_dt = datetime.fromtimestamp(int(now_ms) / 1000.0, tz=IST)
    rev["confirmed_at"] = now_dt.isoformat()
    rev["confirmed_ts_ms"] = int(now_ms)
    return rev


def _apply_confirm_stamp(
    rev: dict[str, Any],
    cand: dict[str, Any],
    now_ms: int,
) -> dict[str, Any]:
    """Prefer detect's confirm-bar stamp; fall back to reconcile wall-clock.

    Batch Live-unlock / GEX-gate clears used to stamp every new accept with the
    same ``now_ms``, so opposite pivots shared one misleading conf time. Detect
    now emits the first TF bar where the move threshold cleared.
    """
    cts = cand.get("confirmed_ts_ms")
    if cts is not None:
        try:
            cts_i = int(cts)
        except (TypeError, ValueError):
            return _stamp_confirmed_at(rev, int(now_ms))
        rev["confirmed_ts_ms"] = cts_i
        cat = cand.get("confirmed_at")
        if cat:
            rev["confirmed_at"] = cat
        else:
            rev["confirmed_at"] = datetime.fromtimestamp(cts_i / 1000.0, tz=IST).isoformat()
        return rev
    return _stamp_confirmed_at(rev, int(now_ms))


def _confirm_window_open(
    rev: dict[str, Any],
    *,
    confirm_bars: int,
    tf_minutes: int,
    now_ms: int,
) -> bool:
    """True while pivot age is still inside confirm_bars × TF minutes."""
    ts = rev.get("ts_ms")
    if ts is None:
        return False
    try:
        age = int(now_ms) - int(ts)
    except (TypeError, ValueError):
        return False
    window_ms = int(confirm_bars) * int(tf_minutes) * 60 * 1000
    return age < window_ms


def _match_frozen_reversal(
    cand: dict[str, Any],
    frozen: list[dict[str, Any]],
    bar_ms: int,
) -> dict[str, Any] | None:
    """Same side + ts within one TF bar (same pivot / resample jitter)."""
    cts = cand.get("ts_ms")
    cside = cand.get("side")
    if cts is None or cside is None:
        return None
    try:
        cts_i = int(cts)
    except (TypeError, ValueError):
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for fr in frozen:
        if fr.get("side") != cside:
            continue
        fts = fr.get("ts_ms")
        if fts is None:
            continue
        try:
            dt = abs(int(fts) - cts_i)
        except (TypeError, ValueError):
            continue
        if dt < bar_ms and (best is None or dt < best[0]):
            best = (dt, fr)
    return best[1] if best is not None else None


def reconcile_session_reversals(
    candidates: list[dict[str, Any]],
    frozen: list[dict[str, Any]],
    *,
    tf: str | None = None,
    confirm_bars: int | None = None,
    lock_ms: int | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Merge fresh detect candidates with session-frozen reversals.

    While the confirm window is still open, refresh ``move_pts`` / label from the
    latest measured move. After the window closes, keep frozen pts forever so
    refresh/rebuild jitter cannot drift the chip. Append only truly new pivots;
    same-side lock is applied against the frozen+new working list.

    Confirm stamp: prefer candidate ``confirmed_ts_ms`` (detect confirm-bar);
    else first-accept wall-clock. Matching candidates may repair a prior batch
    wall-clock stamp when detect now supplies the real confirm bar.
    """
    tf_key = normalize_reversal_tf(tf)
    params = reversal_tf_params(tf_key)
    confirm = int(confirm_bars if confirm_bars is not None else params["confirm_bars"])
    lock = int(lock_ms if lock_ms is not None else params["lock_ms"])
    bar_ms = int(params["minutes"]) * 60 * 1000
    if now_ms is None:
        now_ms = int(datetime.now(tz=IST).timestamp() * 1000)

    working: list[dict[str, Any]] = [_canonical_reversal(r) for r in frozen]

    for cand in sorted(candidates, key=lambda r: r.get("ts_ms") or 0):
        match = _match_frozen_reversal(cand, working, bar_ms)
        if match is not None:
            # Sync confirm-bar time from detect (idempotent; also repairs batch
            # wall-clock stamps from an earlier Live/GEX unlock reconcile).
            if cand.get("confirmed_ts_ms") is not None:
                _apply_confirm_stamp(match, cand, int(now_ms))
            if _confirm_window_open(
                match,
                confirm_bars=confirm,
                tf_minutes=int(params["minutes"]),
                now_ms=int(now_ms),
            ):
                try:
                    move = float(cand.get("move_pts"))
                except (TypeError, ValueError):
                    continue
                match["move_pts"] = round(move, 1)
                if "gex_confirm" in cand:
                    match["gex_confirm"] = bool(cand.get("gex_confirm"))
                if "partial_ungated" in cand:
                    match["partial_ungated"] = bool(cand.get("partial_ungated"))
                if "provisional" in cand:
                    match["provisional"] = bool(cand.get("provisional"))
                if bool(match.get("gex_confirm")):
                    match["provisional"] = False
                if "oi_align" in cand:
                    match["oi_align"] = bool(cand.get("oi_align"))
                if "oi_gate_pass" in cand:
                    match["oi_gate_pass"] = cand.get("oi_gate_pass")
                match["label"] = _format_reversal_label(
                    tf_key=str(match.get("tf") or tf_key),
                    side=str(match.get("side") or cand.get("side") or "bullish"),
                    move_pts=float(match["move_pts"]),
                    gex_confirm=bool(match.get("gex_confirm")),
                    oi_align=bool(match.get("oi_align")),
                )
            else:
                # Confirm window closed — still allow Live provisional → gated promote.
                if "provisional" in cand:
                    match["provisional"] = bool(cand.get("provisional"))
                if "gex_confirm" in cand:
                    match["gex_confirm"] = bool(cand.get("gex_confirm"))
                if bool(match.get("gex_confirm")):
                    match["provisional"] = False
                if "gex_confirm" in cand or "provisional" in cand:
                    match["label"] = _format_reversal_label(
                        tf_key=str(match.get("tf") or tf_key),
                        side=str(match.get("side") or cand.get("side") or "bullish"),
                        move_pts=float(match.get("move_pts") or cand.get("move_pts") or 0),
                        gex_confirm=bool(match.get("gex_confirm")),
                        oi_align=bool(match.get("oi_align")),
                    )
            continue

        # Truly new pivot — accept only if same-side lock allows it.
        trial = _apply_same_side_lock(working + [_canonical_reversal(cand)], lock)
        kept = any(
            r.get("ts_ms") == cand.get("ts_ms") and r.get("side") == cand.get("side")
            for r in trial
        )
        if kept:
            # Prefer detect confirm-bar; else reconcile wall-clock as fallback.
            working.append(_apply_confirm_stamp(_canonical_reversal(cand), cand, int(now_ms)))

    return sorted(working, key=lambda r: r.get("ts_ms") or 0)


def persist_session_reversals(
    underlying: str,
    expiry: str,
    candidates: list[dict[str, Any]],
    *,
    tf: str | None = None,
    confirm_bars: int | None = None,
    lock_ms: int | None = None,
    now_ms: int | None = None,
) -> list[dict[str, Any]]:
    """Reconcile + write session frozen reversals; return the stable list.

    Updates only this underlying|expiry|date|tf bucket (plus prior-day prune for
    the same underlying|expiry). Other underlyings are left untouched.
    """
    tf_key = normalize_reversal_tf(tf)
    key = _reversals_key(underlying, expiry, tf_key)

    def _mutate(data: dict[str, Any]) -> list[dict[str, Any]]:
        rev_store = data.setdefault("reversals", {})
        frozen = list(rev_store.get(key) or [])
        merged = reconcile_session_reversals(
            candidates,
            frozen,
            tf=tf_key,
            confirm_bars=confirm_bars,
            lock_ms=lock_ms,
            now_ms=now_ms,
        )
        rev_store[key] = [_canonical_reversal(r) for r in merged]
        _prune_stale_reversal_keys(rev_store, underlying, expiry)
        return merged

    try:
        return _update_store(_mutate)
    except (json.JSONDecodeError, OSError, ValueError):
        # Corrupt store — return in-memory reconcile against whatever we can read.
        frozen = get_persisted_reversals(underlying, expiry, tf_key)
        return reconcile_session_reversals(
            candidates,
            frozen,
            tf=tf_key,
            confirm_bars=confirm_bars,
            lock_ms=lock_ms,
            now_ms=now_ms,
        )


def _candle_close_and_time(candle: dict[str, Any]) -> tuple[datetime | None, float | None]:
    ts = _parse_ts(candle.get("date") or candle.get("datetime") or candle.get("time"))
    close = candle.get("close")
    if close is None:
        return ts, None
    try:
        return ts, float(close)
    except (TypeError, ValueError):
        return ts, None


def _candle_volume(candle: dict[str, Any]) -> float | None:
    raw = candle.get("volume")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# Chart attach stays tight so GEX line does not invent flat segments across poll gaps.
GEX_CHART_MATCH_SEC = 120
# Gate matching is wider than the chart window — desk polls sparsely, so a 5m/15m
# pivot often has no GEX tick inside the 2-minute chart attach radius.
GEX_GATE_MATCH_SEC_BY_TF: dict[str, int] = {
    "1m": 3 * 60,
    "5m": 25 * 60,
    "15m": 50 * 60,
}
# Minimum usable GEX history ticks before the hard regime gate can apply.
REVERSAL_GEX_MIN_SAMPLES = 5
# Backward-compatible alias (previously 2).
GEX_STARVE_MIN_POINTS = REVERSAL_GEX_MIN_SAMPLES

REVERSAL_GEX_MODES = frozenset({"live", "research"})


def normalize_reversal_gex_mode(mode: str | None) -> str:
    """Return ``live`` (default) or ``research``."""
    m = (mode or "live").strip().lower()
    return m if m in REVERSAL_GEX_MODES else "live"


def _timed_gex_points(
    underlying: str,
    gex_points: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> list[tuple[datetime, dict[str, Any]]]:
    start, end = session_window(underlying)
    day = today or _today_ist()
    gex_timed: list[tuple[datetime, dict[str, Any]]] = []
    for p in gex_points:
        ts = _parse_ts(p.get("t"))
        if ts is None:
            continue
        if ts.date() != day:
            continue
        tt = ts.timetz().replace(tzinfo=None)
        if not (start <= tt <= end):
            continue
        gex_timed.append((ts, p))
    gex_timed.sort(key=lambda x: x[0])
    return gex_timed


def _nearest_gex(
    gex_timed: list[tuple[datetime, dict[str, Any]]],
    ts: datetime,
    match_sec: int,
) -> dict[str, Any] | None:
    best: tuple[float, dict[str, Any]] | None = None
    for gts, gp in gex_timed:
        dt = abs((gts - ts).total_seconds())
        if dt <= match_sec and (best is None or dt < best[0]):
            best = (dt, gp)
    return best[1] if best is not None else None


def build_chart_series(
    underlying: str,
    gex_points: list[dict[str, Any]],
    spot_candles: list[dict[str, Any]] | None,
    *,
    gex_match_sec: int = GEX_CHART_MATCH_SEC,
) -> list[dict[str, Any]]:
    """Merge day minute spot path with sparse GEX ticks for charting.

    - Spot is filled for every in-session minute candle (full day path).
    - GEX / flip / atm_iv are attached only near a stored GEX tick (else null)
      so gaps do not invent forward-looking flat GEX/IV after the desk stopped
      polling. Display forward-fill of GEX/IV is done in the UI.
    """
    start, end = session_window(underlying)
    today = _today_ist()
    gex_timed = _timed_gex_points(underlying, gex_points, today=today)

    rows: list[dict[str, Any]] = []
    if spot_candles:
        for c in spot_candles:
            ts, close = _candle_close_and_time(c)
            if ts is None or close is None or close <= 0:
                continue
            if ts.date() != today:
                continue
            tt = ts.timetz().replace(tzinfo=None)
            if not (start <= tt <= end):
                continue
            gex_val = None
            pos_val = None
            neg_val = None
            flip_val = None
            regime = None
            pin_val = None
            atm_iv_val = None
            best = _nearest_gex(gex_timed, ts, gex_match_sec)
            if best is not None:
                gex_val = best.get("total_gex")
                pos_val = best.get("pos_gex")
                neg_val = best.get("neg_gex")
                flip_val = best.get("flip_level")
                regime = best.get("gamma_regime")
                pin_val = best.get("pin_strike")
                atm_iv_val = best.get("atm_iv")
            vol = _candle_volume(c)
            rows.append(
                {
                    "t": ts.isoformat(timespec="seconds"),
                    "ts_ms": int(ts.timestamp() * 1000),
                    "spot": round(close, 2),
                    "total_gex": gex_val,
                    "pos_gex": pos_val,
                    "neg_gex": neg_val,
                    "volume": vol,
                    "flip_level": flip_val,
                    "gamma_regime": regime,
                    "pin_strike": pin_val,
                    "atm_iv": atm_iv_val,
                    "source": "candle",
                }
            )

    # If no candles, fall back to GEX ticks alone (still session-filtered)
    if not rows:
        for ts, p in gex_timed:
            rows.append(
                {
                    "t": ts.isoformat(timespec="seconds"),
                    "ts_ms": int(ts.timestamp() * 1000),
                    "spot": p.get("spot"),
                    "total_gex": p.get("total_gex"),
                    "pos_gex": p.get("pos_gex"),
                    "neg_gex": p.get("neg_gex"),
                    "volume": None,
                    "flip_level": p.get("flip_level"),
                    "gamma_regime": p.get("gamma_regime"),
                    "pin_strike": p.get("pin_strike"),
                    "atm_iv": p.get("atm_iv"),
                    "source": "gex",
                }
            )

    rows.sort(key=lambda r: r["ts_ms"])
    return rows


_GEX_SESSION_FILL_KEYS = ("total_gex", "pos_gex", "neg_gex")


def fill_session_gex_series(
    series: list[dict[str, Any]],
    *,
    keys: tuple[str, ...] = _GEX_SESSION_FILL_KEYS,
) -> list[dict[str, Any]]:
    """Reverse+forward fill sparse GEX fields for **display** chart series.

    Chart attach in :func:`build_chart_series` stays sparse (honest gaps for
    reversal detection). Call this on the **display** copy so Net / +VE / −VE
    paint across the spot session path:

    - Minutes **before** the first usable sample carry that sample's value
      backward (display continuity when history starts mid-session).
    - From each sample onward, carry forward until the next sample.

    Early values are carried from the first recorded sample, not independently
    measured. Spot, volume, flip, and regime fields are left unchanged.
    """
    if not series:
        return series
    out = [dict(row) for row in series]
    for key in keys:
        last: float | None = None
        for row in out:
            raw = row.get(key)
            usable: float | None = None
            if raw is not None:
                try:
                    usable = float(raw)
                except (TypeError, ValueError):
                    usable = None
            if usable is not None:
                last = usable
                row[key] = usable
            else:
                row[key] = last
        # Reverse-fill minutes before the first real sample.
        next_val: float | None = None
        for row in reversed(out):
            raw = row.get(key)
            if raw is not None:
                try:
                    next_val = float(raw)
                except (TypeError, ValueError):
                    pass
            elif next_val is not None:
                row[key] = next_val
    return out


def _minute_key_ms(ts: datetime) -> int:
    """Floor timestamp to the minute for volume attach matching."""
    floored = ts.replace(second=0, microsecond=0)
    return int(floored.timestamp() * 1000)


def series_has_usable_volume(series: list[dict[str, Any]]) -> bool:
    """True when any chart row already carries a positive volume."""
    for row in series:
        try:
            if float(row.get("volume") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def attach_volume_from_candles(
    series: list[dict[str, Any]],
    volume_candles: list[dict[str, Any]] | None,
    *,
    only_when_missing: bool = True,
) -> list[dict[str, Any]]:
    """Overlay minute volume onto an existing chart series (chart-only; no signal fields).

    Used to backfill front-month futures volume onto cash-index spot paths that
    report volume=0. Matching is by floored minute timestamp.
    """
    if not series or not volume_candles:
        return series
    if only_when_missing and series_has_usable_volume(series):
        return series

    by_min: dict[int, float] = {}
    for c in volume_candles:
        ts, _close = _candle_close_and_time(c)
        vol = _candle_volume(c)
        if ts is None or vol is None or vol <= 0:
            continue
        by_min[_minute_key_ms(ts)] = vol
    if not by_min:
        return series

    out: list[dict[str, Any]] = []
    for row in series:
        copy = dict(row)
        try:
            existing = float(copy.get("volume") or 0)
        except (TypeError, ValueError):
            existing = 0.0
        if only_when_missing and existing > 0:
            out.append(copy)
            continue
        ts = _parse_ts(copy.get("t"))
        if ts is None and copy.get("ts_ms") is not None:
            try:
                ts = datetime.fromtimestamp(int(copy["ts_ms"]) / 1000.0, tz=IST)
            except (TypeError, ValueError, OSError):
                ts = None
        if ts is not None:
            vol = by_min.get(_minute_key_ms(ts))
            if vol is not None:
                copy["volume"] = vol
                copy["volume_source"] = "future"
        out.append(copy)
    return out


def count_usable_gex_points(gex_points: list[dict[str, Any]]) -> int:
    """Session history ticks that carry a usable total_gex value."""
    n = 0
    for p in gex_points:
        if p.get("total_gex") is None:
            continue
        try:
            float(p["total_gex"])
        except (TypeError, ValueError):
            continue
        n += 1
    return n


def gex_history_recording_meta(
    underlying: str,
    history: list[dict[str, Any]],
    *,
    grace_sec: int = 300,
) -> dict[str, Any]:
    """UI honesty fields when GEX recording started mid-session.

    Spot path comes from full-day candles; GEX only exists from the first
    persisted sample. When that first tick is more than ``grace_sec`` after
    session open, the desk should say so rather than looking "broken."
    """
    points = count_usable_gex_points(history)
    if not history or points <= 0:
        return {
            "gex_history_started_at": None,
            "gex_history_partial": True,
            "gex_history_points": points,
        }
    first_t = history[0].get("t")
    first = _parse_ts(first_t)
    if first is None:
        return {
            "gex_history_started_at": first_t,
            "gex_history_partial": True,
            "gex_history_points": points,
        }
    start, _ = session_window(underlying)
    open_dt = datetime.combine(first.date(), start, tzinfo=IST)
    partial = (first - open_dt).total_seconds() > float(grace_sec)
    return {
        "gex_history_started_at": first_t,
        "gex_history_partial": partial,
        "gex_history_points": points,
    }


def resolve_gex_gate(
    want_gate: bool,
    gex_points: list[dict[str, Any]],
    *,
    min_points: int = REVERSAL_GEX_MIN_SAMPLES,
    mode: str | None = "live",
    history_partial: bool = False,
) -> tuple[bool, bool, bool]:
    """Return (effective_gate, reversals_gex_relaxed, reversals_gex_waiting).

    When Require GEX is ON but session history is sparse (< min_points):

    - **live** (default): ``reversals_gex_waiting=True`` while samples are sparse.
      Upstream still runs detect with ``provisional_ungated`` so price pivots can
      appear muted immediately; hard GEX confirm waits for samples.
    - **research**: auto-relax the gate so ungated spot pivots stay visible
      with an amber UI warning (legacy starve behavior); merge/persist as usual.

    When ``history_partial`` (first GEX tick well after session open): never blank
    the day. Sparse → research-style relax; enough samples → ``effective_gate``
    and ``relaxed`` both True so upstream runs the hybrid policy (ungated
    pivots before the first sample; hard gate only where GEX exists).

    When enough samples exist and history is complete, the hard GEX gate is
    enforced in either mode. When Require GEX is OFF, mode is irrelevant.
    """
    if not want_gate:
        return False, False, False
    n = count_usable_gex_points(gex_points)
    if history_partial:
        # Mid-session recording must not wipe the full-day reversal UX.
        if n < min_points:
            return False, True, False
        return True, True, False
    if n < min_points:
        if normalize_reversal_gex_mode(mode) == "research":
            return False, True, False
        return False, False, True
    return True, False, False


def gex_gate_match_sec(tf: str | None) -> int:
    return int(GEX_GATE_MATCH_SEC_BY_TF[normalize_reversal_tf(tf)])


def _series_index_for_reversal(
    series: list[dict[str, Any]],
    rev: dict[str, Any],
) -> int | None:
    """Locate the chart/TF bar for a reversal candidate (exact then nearest)."""
    ts_ms = rev.get("ts_ms")
    t = rev.get("t")
    if ts_ms is not None:
        try:
            want = int(ts_ms)
        except (TypeError, ValueError):
            want = None
        else:
            for i, row in enumerate(series):
                raw = row.get("ts_ms")
                if raw is None:
                    continue
                try:
                    if int(raw) == want:
                        return i
                except (TypeError, ValueError):
                    continue
    if t:
        for i, row in enumerate(series):
            if row.get("t") == t:
                return i
    if ts_ms is None:
        return None
    try:
        want = int(ts_ms)
    except (TypeError, ValueError):
        return None
    best: tuple[int, int] | None = None
    for i, row in enumerate(series):
        raw = row.get("ts_ms")
        if raw is None:
            continue
        try:
            dt = abs(int(raw) - want)
        except (TypeError, ValueError):
            continue
        if best is None or dt < best[0]:
            best = (dt, i)
    return best[1] if best is not None else None


def apply_partial_history_gex_policy(
    series: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    *,
    confirm_bars: int,
    provisional_ungated: bool = False,
) -> list[dict[str, Any]]:
    """Hybrid gate when GEX recording started mid-session.

    - Pivots with **no** GEX sample in the confirm window (pre-recording / gap)
      are kept as ungated (``partial_ungated=True``, ``gex_confirm=False``).
    - Pivots with GEX nearby must pass the hard regime gate; failures are dropped
      unless ``provisional_ungated`` (Live) — then kept as ``provisional=True``.

    Does not invent morning GEX — only decides which spot pivots stay visible.
    """
    out: list[dict[str, Any]] = []
    for rev in candidates:
        side = str(rev.get("side") or "")
        if side not in ("bullish", "bearish"):
            continue
        idx = _series_index_for_reversal(series, rev)
        if idx is None:
            copy = dict(rev)
            copy["gex_confirm"] = False
            copy["partial_ungated"] = True
            copy["provisional"] = False
            copy["label"] = _format_reversal_label(
                tf_key=normalize_reversal_tf(rev.get("tf")),
                side=side,
                move_pts=float(rev.get("move_pts") or 0),
                gex_confirm=False,
                oi_align=bool(rev.get("oi_align")),
            )
            out.append(copy)
            continue

        has_gex = False
        for k in range(idx, min(len(series), idx + int(confirm_bars) + 2)):
            if _gex_sign(series[k]) is not None:
                has_gex = True
                break

        if not has_gex:
            copy = dict(rev)
            copy["gex_confirm"] = False
            copy["partial_ungated"] = True
            copy["provisional"] = False
            copy["label"] = _format_reversal_label(
                tf_key=normalize_reversal_tf(rev.get("tf")),
                side=side,
                move_pts=float(rev.get("move_pts") or 0),
                gex_confirm=False,
                oi_align=bool(rev.get("oi_align")),
            )
            out.append(copy)
            continue

        hard_ok, _soft = _gex_gate_result(series, idx, int(confirm_bars), side)
        if not hard_ok:
            if not provisional_ungated:
                continue
            copy = dict(rev)
            copy["gex_confirm"] = False
            copy["partial_ungated"] = False
            copy["provisional"] = True
            copy["label"] = _format_reversal_label(
                tf_key=normalize_reversal_tf(rev.get("tf")),
                side=side,
                move_pts=float(rev.get("move_pts") or 0),
                gex_confirm=False,
                oi_align=bool(rev.get("oi_align")),
            )
            out.append(copy)
            continue
        copy = dict(rev)
        copy["gex_confirm"] = True
        copy["partial_ungated"] = False
        copy["provisional"] = False
        copy["label"] = _format_reversal_label(
            tf_key=normalize_reversal_tf(rev.get("tf")),
            side=side,
            move_pts=float(rev.get("move_pts") or 0),
            gex_confirm=True,
            oi_align=bool(rev.get("oi_align")),
        )
        out.append(copy)
    return out


def enrich_series_nearest_gex(
    series: list[dict[str, Any]],
    gex_points: list[dict[str, Any]],
    *,
    match_sec: int,
    underlying: str = "NIFTY",
) -> list[dict[str, Any]]:
    """Copy series, attaching nearest GEX within ``match_sec`` (gate lookback).

    Chart series stays on the short attach window; detection uses this wider
    enrichment so 5m/15m pivots are not wiped when the nearest GEX tick is
    outside the 2-minute chart radius.
    """
    if not series or match_sec <= 0:
        return [dict(r) for r in series]
    # Prefer timestamps already on the series; filter history by session if possible.
    gex_timed: list[tuple[datetime, dict[str, Any]]] = []
    for p in gex_points:
        ts = _parse_ts(p.get("t"))
        if ts is None or p.get("total_gex") is None:
            continue
        gex_timed.append((ts, p))
    if not gex_timed:
        # Fall back to session-filtered list (may still be empty).
        gex_timed = _timed_gex_points(underlying, gex_points)
    else:
        gex_timed.sort(key=lambda x: x[0])

    out: list[dict[str, Any]] = []
    for row in series:
        copy = dict(row)
        ts = _parse_ts(row.get("t"))
        if ts is None and row.get("ts_ms") is not None:
            try:
                ts = datetime.fromtimestamp(int(row["ts_ms"]) / 1000.0, tz=IST)
            except (TypeError, ValueError, OSError):
                ts = None
        if ts is not None:
            best = _nearest_gex(gex_timed, ts, match_sec)
            if best is not None:
                copy["total_gex"] = best.get("total_gex")
                if best.get("pos_gex") is not None:
                    copy["pos_gex"] = best.get("pos_gex")
                if best.get("neg_gex") is not None:
                    copy["neg_gex"] = best.get("neg_gex")
                copy["flip_level"] = best.get("flip_level")
                copy["gamma_regime"] = best.get("gamma_regime")
                if best.get("pin_strike") is not None:
                    copy["pin_strike"] = best.get("pin_strike")
        out.append(copy)
    return out


# Reversal TF presets: swing/confirm in TF bars; lock in wall-clock ms.
REVERSAL_TF_PARAMS: dict[str, dict[str, int]] = {
    "1m": {"minutes": 1, "swing_bars": 3, "confirm_bars": 12, "lock_ms": 18 * 60 * 1000},
    "5m": {"minutes": 5, "swing_bars": 3, "confirm_bars": 7, "lock_ms": 55 * 60 * 1000},
    "15m": {"minutes": 15, "swing_bars": 2, "confirm_bars": 4, "lock_ms": 105 * 60 * 1000},
}
DEFAULT_REVERSAL_TF = "5m"


def normalize_reversal_tf(tf: str | None) -> str:
    t = (tf or DEFAULT_REVERSAL_TF).strip().lower()
    return t if t in REVERSAL_TF_PARAMS else DEFAULT_REVERSAL_TF


def reversal_tf_params(tf: str | None) -> dict[str, int]:
    return dict(REVERSAL_TF_PARAMS[normalize_reversal_tf(tf)])


def adaptive_reversal_min_move(spot: float, series: list[dict[str, Any]] | None = None) -> float:
    """Point threshold for reversal confirm — scales with spot, not a blunt 0.15%.

    Live bug: ``spot * 0.0015`` needed ~116 pts on Sensex (~78k), so morning
    swings of 50–100 pts never fired while afternoon 140+ pt spikes did.
    Use ~0.08% with asset-scale floors, floored further by recent bar noise.
    """
    px = abs(float(spot)) if spot else 0.0
    if px < 10_000:
        floor = 8.0
    elif px < 40_000:
        floor = 20.0
    else:
        floor = 40.0
    pct = px * 0.0008
    dyn = pct
    if series:
        prices: list[float] = []
        for row in series:
            s = row.get("spot")
            if s is None:
                continue
            try:
                prices.append(float(s))
            except (TypeError, ValueError):
                continue
        if len(prices) >= 30:
            diffs = [abs(prices[i] - prices[i - 1]) for i in range(1, len(prices))]
            diffs.sort()
            median_bar = diffs[len(diffs) // 2]
            # Need a multi-bar reclaim, not one noisy tick
            dyn = max(pct, median_bar * 6.0)
    return max(floor, round(dyn, 1))


def resample_chart_series(
    series: list[dict[str, Any]],
    tf: str | None,
) -> list[dict[str, Any]]:
    """Aggregate 1m chart rows into TF bars (last close = bar spot; GEX merged if any)."""
    tf_key = normalize_reversal_tf(tf)
    minutes = int(REVERSAL_TF_PARAMS[tf_key]["minutes"])
    if minutes <= 1 or not series:
        return list(series)

    bucket_ms = minutes * 60 * 1000
    buckets: dict[int, list[dict[str, Any]]] = {}
    order: list[int] = []
    for row in series:
        ts_ms = row.get("ts_ms")
        if ts_ms is None:
            continue
        try:
            key = (int(ts_ms) // bucket_ms) * bucket_ms
        except (TypeError, ValueError):
            continue
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(row)

    out: list[dict[str, Any]] = []
    for key in order:
        rows = buckets[key]
        last = rows[-1]
        spot = last.get("spot")
        gex_val = None
        flip_val = None
        regime = None
        pin = None
        for r in rows:
            if r.get("total_gex") is not None:
                gex_val = r.get("total_gex")
            if r.get("flip_level") is not None:
                flip_val = r.get("flip_level")
            if r.get("gamma_regime") is not None:
                regime = r.get("gamma_regime")
            if r.get("pin_strike") is not None:
                pin = r.get("pin_strike")
        out.append(
            {
                "t": last.get("t"),
                "ts_ms": last.get("ts_ms") if last.get("ts_ms") is not None else key + bucket_ms - 60_000,
                "spot": spot,
                "total_gex": gex_val,
                "flip_level": flip_val,
                "gamma_regime": regime,
                "pin_strike": pin,
                "source": last.get("source") or "tf",
                "tf": tf_key,
            }
        )
    return out


def _is_pivot(
    spots: list[tuple[int, float, str | None]],
    j: int,
    swing_bars: int,
    *,
    trough: bool,
) -> bool:
    """Local extremum; plateaus count once (leftmost bar of the flat)."""
    px = spots[j][1]
    lo = j - swing_bars
    hi = j + swing_bars
    window = [spots[k][1] for k in range(lo, hi + 1)]
    extreme = min(window) if trough else max(window)
    if px != extreme:
        return False
    # First occurrence of the extreme in the window — avoids skipping equal-price plateaus
    # (old ``count(px) == 1`` dropped most live minute pivots on rounded prints).
    first = next(k for k in range(lo, hi + 1) if spots[k][1] == extreme)
    return first == j


def _gex_sign(row: dict[str, Any]) -> int | None:
    regime = row.get("gamma_regime")
    if regime == "positive":
        return 1
    if regime == "negative":
        return -1
    gex = row.get("total_gex")
    if gex is None:
        return None
    try:
        return 1 if float(gex) >= 0 else -1
    except (TypeError, ValueError):
        return None


def _gex_gate_result(
    series: list[dict[str, Any]],
    idx: int,
    confirm_bars: int,
    side: str,
) -> tuple[bool, bool]:
    """Return (passes_hard_gate, soft_gex_confirm_label).

    Hard gate: at least one GEX sample near pivot→confirm, and
      bullish: positive regime/sign OR neg→pos cross in window
      bearish: mirror
    Soft confirm label: regime/sign flip in window (legacy chip hint).
    """
    signs: list[int] = []
    for k in range(idx, min(len(series), idx + confirm_bars + 2)):
        s = _gex_sign(series[k])
        if s is not None:
            signs.append(s)
    if not signs:
        return False, False

    crossed_up = any(signs[i - 1] < 0 and signs[i] > 0 for i in range(1, len(signs)))
    crossed_dn = any(signs[i - 1] > 0 and signs[i] < 0 for i in range(1, len(signs)))
    soft = crossed_up or crossed_dn

    first = signs[0]
    if side == "bullish":
        hard = first > 0 or crossed_up
    else:
        hard = first < 0 or crossed_dn
    return hard, soft or hard


def _safe_level(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _oi_step(strike_step: float | None) -> float:
    try:
        step = float(strike_step) if strike_step is not None else 50.0
    except (TypeError, ValueError):
        step = 50.0
    return step if step > 0 else 50.0


def _oi_align(
    side: str,
    pivot_spot: float,
    *,
    pin_strike: float | None,
    call_wall: float | None,
    put_wall: float | None,
    strike_step: float | None,
) -> bool:
    """Soft proximity to pin / walls — never a hard reject."""
    levels: list[float] = []
    if side == "bullish":
        for lvl in (put_wall, pin_strike):
            lv = _safe_level(lvl)
            if lv is not None:
                levels.append(lv)
    else:
        for lvl in (call_wall, pin_strike):
            lv = _safe_level(lvl)
            if lv is not None:
                levels.append(lv)
    if not levels:
        return False
    tol = float(OI_GATE_NEAR_STEPS) * _oi_step(strike_step)
    return any(abs(pivot_spot - lvl) <= tol for lvl in levels)


def _has_oi_structure(
    *,
    pin_strike: float | None,
    call_wall: float | None,
    put_wall: float | None,
    cliff_strike: float | None,
    strikes: list[dict[str, Any]] | None,
) -> bool:
    if any(
        _safe_level(x) is not None
        for x in (pin_strike, call_wall, put_wall, cliff_strike)
    ):
        return True
    if not strikes:
        return False
    for row in strikes:
        if row.get("ce_doi") is not None or row.get("pe_doi") is not None:
            return True
    return False


def _doi_sums_near(
    strikes: list[dict[str, Any]] | None,
    spot: float,
    step: float,
    *,
    put_side: bool,
    near_steps: int = OI_DOI_NEAR_STEPS,
) -> tuple[float, float, bool]:
    """Sum CE/PE ΔOI near ATM on put side (≤spot) or call side (≥spot)."""
    if not strikes or step <= 0:
        return 0.0, 0.0, False
    tol = float(near_steps) * step
    ce_sum = 0.0
    pe_sum = 0.0
    any_doi = False
    for row in strikes:
        k = _safe_level(row.get("strike"))
        if k is None:
            continue
        if abs(k - spot) > tol:
            continue
        if put_side and k > spot + 1e-9:
            continue
        if not put_side and k < spot - 1e-9:
            continue
        ce = _safe_level(row.get("ce_doi"))
        pe = _safe_level(row.get("pe_doi"))
        if ce is not None:
            ce_sum += ce
            any_doi = True
        if pe is not None:
            pe_sum += pe
            any_doi = True
    return ce_sum, pe_sum, any_doi


def _supportive_doi(
    side: str,
    pivot_spot: float,
    *,
    strike_step: float | None,
    strikes: list[dict[str, Any]] | None,
) -> bool:
    """Bullish: put ΔOI rising on put/ATM side. Bearish: call ΔOI on call side."""
    step = _oi_step(strike_step)
    if side == "bullish":
        ce_sum, pe_sum, any_doi = _doi_sums_near(
            strikes, pivot_spot, step, put_side=True
        )
        if not any_doi:
            return False
        return pe_sum > 0 and pe_sum >= ce_sum
    ce_sum, pe_sum, any_doi = _doi_sums_near(
        strikes, pivot_spot, step, put_side=False
    )
    if not any_doi:
        return False
    return ce_sum > 0 and ce_sum >= pe_sum


def _hostile_breakout_doi(
    side: str,
    pivot_spot: float,
    *,
    strike_step: float | None,
    strikes: list[dict[str, Any]] | None,
) -> bool:
    """Strong opposing ΔOI near/beyond the breakout side (if ΔOI present)."""
    step = _oi_step(strike_step)
    if side == "bullish":
        # Call buildup above/at spot
        ce_sum, pe_sum, any_doi = _doi_sums_near(
            strikes, pivot_spot, step, put_side=False
        )
        if not any_doi:
            return False
        return ce_sum > 0 and ce_sum > max(pe_sum, 0.0)
    ce_sum, pe_sum, any_doi = _doi_sums_near(
        strikes, pivot_spot, step, put_side=True
    )
    if not any_doi:
        return False
    return pe_sum > 0 and pe_sum > max(ce_sum, 0.0)


def _through_barrier(
    pivot_spot: float,
    *,
    barrier: float | None,
    cliff: float | None,
    upside: bool,
) -> bool:
    """True when pivot has already broken through wall and/or cliff."""
    levels: list[float] = []
    for lvl in (barrier, cliff):
        lv = _safe_level(lvl)
        if lv is not None:
            levels.append(lv)
    if not levels:
        return False
    if upside:
        return any(pivot_spot > lv + 1e-9 for lv in levels)
    return any(pivot_spot < lv - 1e-9 for lv in levels)


def _near_support_or_resistance(
    side: str,
    pivot_spot: float,
    *,
    pin_strike: float | None,
    call_wall: float | None,
    put_wall: float | None,
    strike_step: float | None,
) -> bool:
    """PASS-A: within N steps of supportive wall/pin on the correct side of spot."""
    step = _oi_step(strike_step)
    tol = float(OI_GATE_NEAR_STEPS) * step
    if side == "bullish":
        # put_wall or pin at/below spot
        for lvl in (put_wall, pin_strike):
            lv = _safe_level(lvl)
            if lv is None:
                continue
            if lv <= pivot_spot + 1e-9 and abs(pivot_spot - lv) <= tol:
                return True
        return False
    for lvl in (call_wall, pin_strike):
        lv = _safe_level(lvl)
        if lv is None:
            continue
        if lv >= pivot_spot - 1e-9 and abs(pivot_spot - lv) <= tol:
            return True
    return False


def _oi_gate_result(
    side: str,
    pivot_spot: float,
    *,
    pin_strike: float | None,
    call_wall: float | None,
    put_wall: float | None,
    cliff_strike: float | None,
    strike_step: float | None,
    strikes: list[dict[str, Any]] | None,
) -> tuple[bool, bool | None]:
    """Hard OI structure gate.

    Returns ``(passes_hard_gate, oi_gate_pass)`` where ``oi_gate_pass`` is:
      True  — supportive structure (near wall/pin or supportive ΔOI)
      False — hostile / unsupported when structure is known
      None  — ``oi_unknown`` (missing walls/pin/ΔOI) → do **not** block
    """
    if not _has_oi_structure(
        pin_strike=pin_strike,
        call_wall=call_wall,
        put_wall=put_wall,
        cliff_strike=cliff_strike,
        strikes=strikes,
    ):
        return True, None

    if side == "bullish":
        through = _through_barrier(
            pivot_spot, barrier=call_wall, cliff=cliff_strike, upside=True
        )
        hostile = through and _hostile_breakout_doi(
            side, pivot_spot, strike_step=strike_step, strikes=strikes
        )
        # Through upside barrier with no ΔOI tape still counts as clear wrong side
        # when a call/cliff barrier is present.
        if through and (
            hostile
            or not any(
                row.get("ce_doi") is not None or row.get("pe_doi") is not None
                for row in (strikes or [])
            )
        ):
            return False, False
        if _near_support_or_resistance(
            side,
            pivot_spot,
            pin_strike=pin_strike,
            call_wall=call_wall,
            put_wall=put_wall,
            strike_step=strike_step,
        ):
            return True, True
        if _supportive_doi(
            side, pivot_spot, strike_step=strike_step, strikes=strikes
        ):
            return True, True
        return False, False

    # Bearish mirror
    through = _through_barrier(
        pivot_spot, barrier=put_wall, cliff=cliff_strike, upside=False
    )
    hostile = through and _hostile_breakout_doi(
        side, pivot_spot, strike_step=strike_step, strikes=strikes
    )
    if through and (
        hostile
        or not any(
            row.get("ce_doi") is not None or row.get("pe_doi") is not None
            for row in (strikes or [])
        )
    ):
        return False, False
    if _near_support_or_resistance(
        side,
        pivot_spot,
        pin_strike=pin_strike,
        call_wall=call_wall,
        put_wall=put_wall,
        strike_step=strike_step,
    ):
        return True, True
    if _supportive_doi(side, pivot_spot, strike_step=strike_step, strikes=strikes):
        return True, True
    return False, False


def _apply_same_side_lock(
    revs: list[dict[str, Any]],
    lock_ms: int,
) -> list[dict[str, Any]]:
    """Drop same-side within lock; opposite always accepted and resets lock."""
    if lock_ms <= 0:
        return revs
    accepted: list[dict[str, Any]] = []
    last_side: str | None = None
    last_ts: int | None = None
    for rev in sorted(revs, key=lambda r: r.get("ts_ms") or 0):
        ts = rev.get("ts_ms")
        side = rev.get("side")
        if (
            last_side is not None
            and side == last_side
            and ts is not None
            and last_ts is not None
            and abs(int(ts) - int(last_ts)) < lock_ms
        ):
            continue
        accepted.append(rev)
        last_side = str(side) if side is not None else None
        last_ts = int(ts) if ts is not None else last_ts
    return accepted


def _first_confirm_bar_stamp(
    spots: list[tuple[int, float, str | None]],
    series: list[dict[str, Any]],
    j: int,
    confirm: int,
    *,
    side: str,
    pivot_px: float,
    min_move_pts: float,
) -> tuple[str | None, int | None]:
    """First TF bar after the pivot where the move threshold is met."""
    end = min(len(spots), j + 1 + confirm)
    for k in range(j + 1, end):
        sk = spots[k][1]
        if side == "bullish":
            ok = sk - pivot_px >= min_move_pts
        else:
            ok = pivot_px - sk >= min_move_pts
        if not ok:
            continue
        row = series[spots[k][0]]
        ts = row.get("ts_ms")
        try:
            ts_i = int(ts) if ts is not None else None
        except (TypeError, ValueError):
            ts_i = None
        t = row.get("t")
        if t is None and ts_i is not None:
            t = datetime.fromtimestamp(ts_i / 1000.0, tz=IST).isoformat()
        return (str(t) if t is not None else None), ts_i
    return None, None


def detect_spot_reversals(
    series: list[dict[str, Any]],
    *,
    swing_bars: int | None = None,
    min_move_pts: float = 40.0,
    confirm_bars: int | None = None,
    lock_ms: int | None = None,
    dedupe_ms: int | None = None,
    gex_gate: bool = True,
    oi_gate: bool = False,
    provisional_ungated: bool = False,
    tf: str | None = DEFAULT_REVERSAL_TF,
    pin_strike: float | None = None,
    call_wall: float | None = None,
    put_wall: float | None = None,
    cliff_strike: float | None = None,
    strike_step: float | None = None,
    strikes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Detect intraday spot swing reversals on a (possibly TF-resampled) series.

    Rules (bullish):
      - Local trough over ``swing_bars`` (plateau → leftmost bar)
      - Within ``confirm_bars`` after trough, spot rises by ≥ ``min_move_pts``
      - If ``gex_gate``: require nearby GEX sample + positive regime or neg→pos
        (unless ``provisional_ungated`` — emit muted ``provisional`` pivot instead)
      - If ``oi_gate``: require supportive OI walls/pin/ΔOI (AND with GEX when both ON);
        missing OI structure → allow (``oi_unknown``); same provisional path
    Bearish is the mirror.

    Timing (root cause of Live lag):
      Old loop required a full ``confirm_bars`` right-side pad before emission
      (``n - confirm``), so a 5m pivot that already cleared ``min_move`` on the
      next bar still waited ~confirm_bars × TF before the chip appeared — while
      chip times stayed backdated to the swing / first-clear bars. Emit as soon
      as the swing window is complete and the move clears on available future
      bars (≤ confirm). TF bar-close labeling remains a floor on lag vs the eye
      (5m last-close stamp can trail an intrabar turn by up to one bucket).

    Same-side lock drops repeats inside ``lock_ms``; opposite side always resets.
    Soft ``oi_align`` tags proximity to pin/walls without rejecting when gate is OFF.
    ``confirmed_at`` / ``confirmed_ts_ms`` are the first confirm-window bar that
    cleared the move threshold (not reconcile wall-clock).
    """
    tf_key = normalize_reversal_tf(tf)
    params = reversal_tf_params(tf_key)
    swing = int(swing_bars if swing_bars is not None else params["swing_bars"])
    confirm = int(confirm_bars if confirm_bars is not None else params["confirm_bars"])
    if lock_ms is not None:
        lock = int(lock_ms)
    elif dedupe_ms is not None:
        lock = int(dedupe_ms)
    else:
        lock = int(params["lock_ms"])

    spots: list[tuple[int, float, str | None]] = []
    for i, row in enumerate(series):
        s = row.get("spot")
        if s is None:
            continue
        try:
            spots.append((i, float(s), row.get("t")))
        except (TypeError, ValueError):
            continue
    # Need left+right swing for extremum plus ≥1 future bar for the move.
    if len(spots) < swing * 2 + 2:
        return []

    out: list[dict[str, Any]] = []
    n = len(spots)
    # Right edge = swing (local extremum proof), not confirm_bars pad.
    for j in range(swing, n - swing):
        idx, px, t = spots[j]
        is_trough = _is_pivot(spots, j, swing, trough=True)
        is_peak = _is_pivot(spots, j, swing, trough=False)
        if not is_trough and not is_peak:
            continue

        future = [spots[k][1] for k in range(j + 1, min(n, j + 1 + confirm))]
        if not future:
            continue

        if is_trough:
            move = max(future) - px
            if move < min_move_pts:
                continue
            side = "bullish"
        else:
            move = px - min(future)
            if move < min_move_pts:
                continue
            side = "bearish"

        row = series[idx]
        hard_ok, soft_confirm = _gex_gate_result(series, idx, confirm, side)
        provisional = False
        if gex_gate and not hard_ok:
            if not provisional_ungated:
                continue
            provisional = True
            gex_confirm = False
        else:
            gex_confirm = hard_ok if gex_gate else soft_confirm

        row_pin = row.get("pin_strike")
        hist_pin = _safe_level(row_pin)
        eff_pin = pin_strike if pin_strike is not None else hist_pin

        oi_ok = _oi_align(
            side,
            px,
            pin_strike=eff_pin,
            call_wall=call_wall,
            put_wall=put_wall,
            strike_step=strike_step,
        )
        oi_hard_ok, oi_gate_pass = _oi_gate_result(
            side,
            px,
            pin_strike=eff_pin,
            call_wall=call_wall,
            put_wall=put_wall,
            cliff_strike=cliff_strike,
            strike_step=strike_step,
            strikes=strikes,
        )
        if oi_gate and not oi_hard_ok:
            if not provisional_ungated:
                continue
            provisional = True

        conf_t, conf_ts = _first_confirm_bar_stamp(
            spots,
            series,
            j,
            confirm,
            side=side,
            pivot_px=px,
            min_move_pts=float(min_move_pts),
        )

        move_r = round(move, 1)
        out.append(
            {
                "t": t,
                "ts_ms": row.get("ts_ms"),
                "confirmed_at": conf_t,
                "confirmed_ts_ms": conf_ts,
                "spot": px,
                "side": side,
                "move_pts": move_r,
                "gex_confirm": gex_confirm,
                "provisional": provisional,
                "oi_align": oi_ok,
                "oi_gate_pass": oi_gate_pass,
                "tf": tf_key,
                "label": _format_reversal_label(
                    tf_key=tf_key,
                    side=side,
                    move_pts=move_r,
                    gex_confirm=bool(gex_confirm),
                    oi_align=bool(oi_ok),
                ),
            }
        )

    if not out:
        return []
    return _apply_same_side_lock(out, lock)


def minutes_since_session_open(underlying: str) -> int:
    """Minutes from session open to now (for candle lookback), capped.

    Cap follows the instrument session (MCX ~09:00–23:30), not a cash-only
    600-minute ceiling that truncated evening CRUDE spot history.
    """
    now = datetime.now(tz=IST)
    start, end = session_window(underlying)
    open_dt = datetime.combine(now.date(), start, tzinfo=IST)
    if now < open_dt:
        return 60
    mins = int((now - open_dt).total_seconds() // 60) + 15
    end_dt = datetime.combine(now.date(), end, tzinfo=IST)
    session_cap = int((end_dt - open_dt).total_seconds() // 60) + 30
    return max(60, min(mins, max(session_cap, 600)))
