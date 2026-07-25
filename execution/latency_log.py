"""Order-execution latency logging (JSONL, no SQL) for 3ST.

Every order that goes through ``execution.order_executor.place_leg_order`` appends
one line to ``data/latency_log.jsonl`` capturing the timing breakdown:

* ``validation_ms`` — 3ST pre-trade work (risk gate + broker position reads)
* ``rtt_ms``        — the broker ``place_order`` round-trip
* ``overhead_ms``   — all 3ST overhead (total − rtt)
* ``total_ms``      — end-to-end

``get_stats`` computes all-time averages / SLA (% under 100/150/200 ms) and
p50/p90/p95/p99 (numpy), with percentiles bounded to a recent window so the read
cannot grow without limit. Results are cached briefly for dashboard polling.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from settings import data_dir

_LOCK = threading.Lock()
_PERCENTILE_WINDOW_DAYS = 30
_SLA_BUCKETS_MS = (100.0, 150.0, 200.0)
_CACHE_TTL_SEC = 60.0
_stats_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _log_file() -> Path:
    return data_dir() / "latency_log.jsonl"


def log_latency(
    *,
    order_id: str | None,
    symbol: str,
    order_type: str,
    latencies: dict[str, float],
    status: str,
    broker: str = "kite",
    transaction_type: str | None = None,
    error: Any = None,
    path: Path | None = None,
) -> bool:
    """Append one latency record. Never raises — logging must not break trading."""
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "order_id": order_id,
        "symbol": symbol,
        "order_type": order_type,
        "transaction_type": transaction_type,
        "broker": broker,
        "status": status,
        "rtt_ms": round(float(latencies.get("rtt", 0.0)), 2),
        "validation_ms": round(float(latencies.get("validation", 0.0)), 2),
        "overhead_ms": round(float(latencies.get("overhead", 0.0)), 2),
        "total_ms": round(float(latencies.get("total", 0.0)), 2),
        "error": (str(error)[:500] if error is not None else None),
    }
    p = path or _log_file()
    try:
        with _LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
        _stats_cache["data"] = None
        return True
    except Exception:
        return False


def _read_rows(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or _log_file()
    if not p.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows


def read_recent(limit: int = 100, path: Path | None = None) -> list[dict[str, Any]]:
    rows = _read_rows(path)
    return list(reversed(rows[-limit:]))


def _mean(vals: list[float]) -> float:
    return round(sum(vals) / len(vals), 2) if vals else 0.0


def _percentiles(vals: list[float]) -> dict[str, float]:
    if not vals:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
    import numpy as np

    arr = np.asarray(vals, dtype=float)
    return {
        "p50": round(float(np.percentile(arr, 50)), 2),
        "p90": round(float(np.percentile(arr, 90)), 2),
        "p95": round(float(np.percentile(arr, 95)), 2),
        "p99": round(float(np.percentile(arr, 99)), 2),
    }


def _within_window(ts: str | None, cutoff: datetime) -> bool:
    if not ts:
        return False
    try:
        return datetime.fromisoformat(ts) >= cutoff
    except Exception:
        return False


def get_stats(path: Path | None = None, *, use_cache: bool = True) -> dict[str, Any]:
    now = time.monotonic()
    if use_cache and _stats_cache["data"] is not None and (now - _stats_cache["at"]) < _CACHE_TTL_SEC:
        return _stats_cache["data"]

    rows = _read_rows(path)
    total = len(rows)
    failed = sum(1 for r in rows if str(r.get("status", "")).upper() == "FAILED")

    def _col(name: str) -> list[float]:
        out = []
        for r in rows:
            v = r.get(name)
            if isinstance(v, (int, float)):
                out.append(float(v))
        return out

    cutoff = datetime.now(timezone.utc) - timedelta(days=_PERCENTILE_WINDOW_DAYS)
    recent_totals = [
        float(r["total_ms"])
        for r in rows
        if isinstance(r.get("total_ms"), (int, float)) and _within_window(r.get("ts"), cutoff)
    ]

    totals = _col("total_ms")
    sla = {
        f"sla_{int(b)}ms": (round(sum(1 for v in totals if v < b) / total * 100, 2) if total else 0.0)
        for b in _SLA_BUCKETS_MS
    }

    # Per-broker breakdown
    brokers: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        brokers.setdefault(str(r.get("broker") or "unknown"), []).append(r)
    broker_stats: dict[str, Any] = {}
    for bname, brows in brokers.items():
        btot = len(brows)
        b_totals = [float(r["total_ms"]) for r in brows if isinstance(r.get("total_ms"), (int, float))]
        b_recent = [
            float(r["total_ms"])
            for r in brows
            if isinstance(r.get("total_ms"), (int, float)) and _within_window(r.get("ts"), cutoff)
        ]
        bp = _percentiles(b_recent)
        broker_stats[bname] = {
            "total_orders": btot,
            "failed_orders": sum(1 for r in brows if str(r.get("status", "")).upper() == "FAILED"),
            "avg_total": _mean(b_totals),
            "p50_total": bp["p50"],
            "p99_total": bp["p99"],
            "sla_150ms": (round(sum(1 for v in b_totals if v < 150) / btot * 100, 2) if btot else 0.0),
        }

    pct = _percentiles(recent_totals)
    stats = {
        "total_orders": total,
        "failed_orders": failed,
        "success_rate": round((total - failed) / total * 100, 2) if total else 0.0,
        "avg_rtt": _mean(_col("rtt_ms")),
        "avg_validation": _mean(_col("validation_ms")),
        "avg_overhead": _mean(_col("overhead_ms")),
        "avg_total": _mean(totals),
        "p50_total": pct["p50"],
        "p90_total": pct["p90"],
        "p95_total": pct["p95"],
        "p99_total": pct["p99"],
        "percentile_window_days": _PERCENTILE_WINDOW_DAYS,
        "percentile_sample": len(recent_totals),
        **sla,
        "broker_stats": broker_stats,
        "updated_at": datetime.now().astimezone().isoformat(),
    }
    _stats_cache.update(at=now, data=stats)
    return stats


def prune(max_rows: int = 50_000, path: Path | None = None) -> int:
    """Cap the log to the most recent ``max_rows`` lines. Returns rows dropped."""
    p = path or _log_file()
    rows = _read_rows(p)
    if len(rows) <= max_rows:
        return 0
    keep = rows[-max_rows:]
    dropped = len(rows) - len(keep)
    try:
        with _LOCK:
            with open(p, "w", encoding="utf-8") as f:
                for r in keep:
                    f.write(json.dumps(r, default=str) + "\n")
        _stats_cache["data"] = None
    except Exception:
        return 0
    return dropped
