"""Tests for the JSONL latency logger + stats (no SQL, no Kite)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from execution import latency_log as ll


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / "latency_log.jsonl"


def _seed(path, totals, *, broker="kite", status="SUCCESS"):
    for i, t in enumerate(totals):
        ll.log_latency(
            order_id=f"O{i}",
            symbol="NIFTY25000CE",
            order_type="MARKET",
            transaction_type="BUY",
            latencies={"validation": 10.0, "rtt": t - 12.0, "overhead": 12.0, "total": t},
            status=status,
            broker=broker,
            path=path,
        )


def test_log_and_read_recent(log_path):
    _seed(log_path, [100.0, 120.0, 140.0])
    recent = ll.read_recent(10, path=log_path)
    assert len(recent) == 3
    # Most recent first
    assert recent[0]["order_id"] == "O2"
    assert recent[0]["total_ms"] == 140.0


def test_stats_percentiles_and_sla(log_path):
    _seed(log_path, [50.0, 90.0, 120.0, 180.0, 250.0])
    stats = ll.get_stats(path=log_path, use_cache=False)
    assert stats["total_orders"] == 5
    assert stats["failed_orders"] == 0
    assert stats["success_rate"] == 100.0
    # SLA: under 100 → {50,90} = 2/5 = 40%; under 150 → {50,90,120}=3/5=60%
    assert stats["sla_100ms"] == 40.0
    assert stats["sla_150ms"] == 60.0
    assert stats["sla_200ms"] == 80.0
    # Percentiles monotonic
    assert stats["p50_total"] <= stats["p90_total"] <= stats["p99_total"]
    assert stats["avg_total"] == pytest.approx((50 + 90 + 120 + 180 + 250) / 5, abs=0.1)


def test_failed_orders_counted(log_path):
    _seed(log_path, [100.0, 100.0], status="SUCCESS")
    _seed(log_path, [300.0], status="FAILED")
    stats = ll.get_stats(path=log_path, use_cache=False)
    assert stats["total_orders"] == 3
    assert stats["failed_orders"] == 1
    assert stats["success_rate"] == pytest.approx(66.67, abs=0.1)


def test_broker_breakdown(log_path):
    _seed(log_path, [100.0, 120.0], broker="kite")
    _seed(log_path, [40.0], broker="paper")
    stats = ll.get_stats(path=log_path, use_cache=False)
    assert set(stats["broker_stats"].keys()) == {"kite", "paper"}
    assert stats["broker_stats"]["kite"]["total_orders"] == 2
    assert stats["broker_stats"]["paper"]["total_orders"] == 1


def test_percentile_window_excludes_old(log_path, monkeypatch):
    # Old row (100 days ago) should be excluded from percentile sample.
    old_ts = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat(timespec="milliseconds")
    import json

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(json.dumps({"ts": old_ts, "status": "SUCCESS", "total_ms": 9999.0, "broker": "kite"}) + "\n")
    _seed(log_path, [100.0, 120.0])
    stats = ll.get_stats(path=log_path, use_cache=False)
    assert stats["total_orders"] == 3  # all-time count includes old
    assert stats["percentile_sample"] == 2  # percentiles exclude the old row
    assert stats["p99_total"] < 9999.0


def test_prune(log_path):
    _seed(log_path, [float(i) for i in range(1, 21)])
    dropped = ll.prune(max_rows=5, path=log_path)
    assert dropped == 15
    rows = ll._read_rows(log_path)
    assert len(rows) == 5
    assert rows[-1]["total_ms"] == 20.0
