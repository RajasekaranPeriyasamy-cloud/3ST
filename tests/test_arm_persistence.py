"""ARM state persistence across API restarts."""

from __future__ import annotations

from execution import arming


def test_arm_persists_and_reloads(tmp_path, monkeypatch):
    state_file = tmp_path / "arm_state.json"
    monkeypatch.setattr(arming, "ARM_STATE_FILE", state_file)

    arming.disarm()
    arming.set_mode("live")
    arming.arm(confirm=True)

    assert state_file.exists()
    saved = state_file.read_text(encoding="utf-8")
    assert '"armed": true' in saved
    assert '"mode": "live"' in saved

    arming._STATE.armed = False
    arming._STATE.mode = "paper"
    arming.load_persisted_state()

    assert arming.get_arm_state()["armed"] is True
    assert arming.get_arm_state()["mode"] == "live"


def test_disarm_persists(tmp_path, monkeypatch):
    state_file = tmp_path / "arm_state.json"
    monkeypatch.setattr(arming, "ARM_STATE_FILE", state_file)

    arming.set_mode("live")
    arming.arm(confirm=True)
    arming.disarm()

    arming._STATE.armed = True
    arming.load_persisted_state()

    assert arming.get_arm_state()["armed"] is False


def test_paper_mode_clears_armed_on_reload(tmp_path, monkeypatch):
    state_file = tmp_path / "arm_state.json"
    monkeypatch.setattr(arming, "ARM_STATE_FILE", state_file)

    state_file.write_text(
        '{"armed": true, "mode": "paper", "armed_at": "2026-07-14T12:00:00", "note": "stale"}',
        encoding="utf-8",
    )
    arming.load_persisted_state()

    st = arming.get_arm_state()
    assert st["mode"] == "paper"
    assert st["armed"] is False
