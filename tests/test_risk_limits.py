"""Risk limit persistence across API restarts."""

from __future__ import annotations


def test_update_limits_persists_to_disk(tmp_path, monkeypatch):
    from risk import limits as rl

    limits_file = tmp_path / "risk_limits.json"
    monkeypatch.setattr(rl, "LIMITS_FILE", limits_file)
    monkeypatch.setattr(rl, "_LIMITS", rl.RiskLimits())

    rl.update_limits(
        max_open_positions=100,
        max_orders_per_minute=100,
        max_daily_loss=10_000,
        max_qty=500,
    )
    assert limits_file.exists()
    saved = limits_file.read_text(encoding="utf-8")
    assert "100" in saved

    monkeypatch.setattr(rl, "_LIMITS", rl.RiskLimits())
    rl.load_persisted_limits()
    out = rl.get_limits()
    assert out["max_open_positions"] == 100
    assert out["max_orders_per_minute"] == 100
    assert out["max_qty"] == 500.0
