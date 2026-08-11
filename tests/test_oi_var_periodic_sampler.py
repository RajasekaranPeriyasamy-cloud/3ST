"""Unit tests for the OI VAR desk's background session-history sampler.

Mirrors tests/test_oi_movers.py::test_maybe_sample_oi_movers_history_periodic
and tests/test_gamma_density.py's maybe_sample_gex_history_periodic tests —
same due/backoff/budget shape, applied to options.oi_var.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import options.oi_var as oi_var
from options.oi_var import (
    default_oi_var_sample_underlyings,
    maybe_sample_oi_var_history_periodic,
)

IST = ZoneInfo("Asia/Kolkata")


class _FixedDateTime(datetime):
    _fixed: datetime

    @classmethod
    def now(cls, tz=None):
        return cls._fixed if tz is None else cls._fixed.astimezone(tz)


def _fixed_datetime(dt: datetime) -> type[_FixedDateTime]:
    return type("FixedDateTime", (_FixedDateTime,), {"_fixed": dt})


def test_default_oi_var_sample_underlyings_includes_majors() -> None:
    names = default_oi_var_sample_underlyings()
    assert "NIFTY" in names
    assert "CRUDEOIL" in names


def test_maybe_sample_oi_var_history_periodic_records_all_due(monkeypatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(
        oi_var,
        "default_oi_var_sample_underlyings",
        lambda: ["NIFTY", "BANKNIFTY"],
    )
    monkeypatch.setattr(
        "options.gamma_density_history.in_session",
        lambda _u, _now=None: True,
    )
    oi_var._oi_var_sample_last_ok.clear()

    def _fake_snap(underlying: str, *a, **k):
        calls.append(underlying)
        return {"ok": True}

    monkeypatch.setattr(oi_var, "build_var_snapshot", _fake_snap)
    # Friday mid-session — weekday path, avoids the weekend short-circuit.
    monkeypatch.setattr(oi_var, "datetime", _fixed_datetime(datetime(2026, 8, 7, 10, 0, tzinfo=IST)))

    assert maybe_sample_oi_var_history_periodic() is True
    assert calls == ["NIFTY", "BANKNIFTY"]
    assert oi_var._oi_var_sample_last_ok["NIFTY"] > 0


def test_maybe_sample_oi_var_history_periodic_logs_failure_short_backoff(monkeypatch) -> None:
    monkeypatch.setattr(
        oi_var,
        "default_oi_var_sample_underlyings",
        lambda: ["CRUDEOIL"],
    )
    monkeypatch.setattr(
        "options.gamma_density_history.in_session",
        lambda _u, _now=None: True,
    )
    oi_var._oi_var_sample_last_ok.clear()

    def _boom(underlying: str, *a, **k):
        raise RuntimeError(f"no chain for {underlying}")

    monkeypatch.setattr(oi_var, "build_var_snapshot", _boom)
    monkeypatch.setattr(oi_var, "datetime", _fixed_datetime(datetime(2026, 8, 7, 20, 0, tzinfo=IST)))

    assert maybe_sample_oi_var_history_periodic() is False
    last = oi_var._oi_var_sample_last_ok.get("CRUDEOIL")
    assert last is not None


def test_maybe_sample_oi_var_history_periodic_weekend_short_circuit(monkeypatch) -> None:
    monkeypatch.setattr(
        oi_var,
        "default_oi_var_sample_underlyings",
        lambda: ["NIFTY"],
    )
    # Saturday.
    monkeypatch.setattr(oi_var, "datetime", _fixed_datetime(datetime(2026, 8, 8, 10, 0, tzinfo=IST)))

    called = False

    def _fake_snap(*a, **k):
        nonlocal called
        called = True
        return {"ok": True}

    monkeypatch.setattr(oi_var, "build_var_snapshot", _fake_snap)

    assert maybe_sample_oi_var_history_periodic() is False
    assert called is False
