"""Job index for the Equity Report desk."""

from __future__ import annotations

import pytest

from analysis.equity_report import store as st


@pytest.fixture
def clean_store(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "INDEX_FILE", tmp_path / "equity_reports.json")
    monkeypatch.setattr(st, "BODY_DIR", tmp_path / "bodies")
    monkeypatch.setattr(st, "_JOBS", [])
    (tmp_path / "bodies").mkdir()
    return st


def _cfg(monkeypatch, cap: float = 0.0, model: str = "claude-opus-5"):
    monkeypatch.setattr(
        st,
        "equity_report_config",
        lambda: {"model": model, "effort": "high", "daily_usd_cap": cap},
    )


def test_job_lifecycle_writes_body_and_costs(clean_store, monkeypatch):
    _cfg(monkeypatch)
    job = st.create_job("RELIANCE", "Reliance Industries")
    assert job["status"] == "queued"
    assert job["ticker"] == "RELIANCE"

    assert st.next_queued()["id"] == job["id"]
    st.mark_running(job["id"])
    assert st.get_job(job["id"])["status"] == "running"

    usage = {"input_tokens": 200_000, "output_tokens": 10_000}
    st.mark_done(job["id"], "# Report\n\nBody.", usage=usage, citations=[{"url": "u"}])

    done = st.get_job(job["id"], with_body=True)
    assert done["status"] == "done"
    assert done["markdown"] == "# Report\n\nBody."
    # 200k in @ $5/MTok + 10k out @ $25/MTok
    assert done["cost_usd"] == pytest.approx(1.25, abs=1e-6)
    assert st.next_queued() is None


def test_ticker_is_uppercased_and_duplicates_rejected(clean_store, monkeypatch):
    _cfg(monkeypatch)
    job = st.create_job("  infy ")
    assert job["ticker"] == "INFY"
    with pytest.raises(RuntimeError, match="already in progress"):
        st.create_job("INFY")
    # Once it finishes, a fresh report is allowed again.
    st.mark_done(job["id"], "body")
    assert st.create_job("INFY")["status"] == "queued"


def test_daily_cap_blocks_new_jobs(clean_store, monkeypatch):
    _cfg(monkeypatch, cap=1.0)
    first = st.create_job("TCS")
    st.mark_done(first["id"], "body", usage={"input_tokens": 400_000})  # $2.00

    assert st.cap_status()["capped"] is True
    with pytest.raises(RuntimeError, match="cap reached"):
        st.create_job("WIPRO")


def test_zero_cap_means_no_cap(clean_store, monkeypatch):
    _cfg(monkeypatch, cap=0.0)
    job = st.create_job("TCS")
    st.mark_done(job["id"], "body", usage={"input_tokens": 10_000_000})
    assert st.cap_status()["capped"] is False
    assert st.create_job("WIPRO")["status"] == "queued"


def test_cache_tokens_priced_below_fresh_input(clean_store, monkeypatch):
    _cfg(monkeypatch)
    fresh = st.estimate_cost_usd({"input_tokens": 1_000_000}, "claude-opus-5")
    cached = st.estimate_cost_usd({"cache_read_input_tokens": 1_000_000}, "claude-opus-5")
    written = st.estimate_cost_usd({"cache_creation_input_tokens": 1_000_000}, "claude-opus-5")
    assert fresh == pytest.approx(5.0)
    assert cached == pytest.approx(0.5)
    assert written == pytest.approx(6.25)


def test_unknown_model_still_prices(clean_store, monkeypatch):
    _cfg(monkeypatch)
    assert st.estimate_cost_usd({"input_tokens": 1_000_000}, "some-future-model") > 0


def test_cancel_is_visible_to_the_runner(clean_store, monkeypatch):
    _cfg(monkeypatch)
    job = st.create_job("HDFCBANK")
    st.mark_running(job["id"])
    assert st.is_cancelled(job["id"]) is False
    st.cancel_job(job["id"])
    assert st.is_cancelled(job["id"]) is True
    # Cancelling a finished job is a no-op, not a status rewrite.
    done = st.create_job("SBIN")
    st.mark_done(done["id"], "body")
    assert st.cancel_job(done["id"])["status"] == "done"


def test_failed_job_records_partial_usage(clean_store, monkeypatch):
    _cfg(monkeypatch)
    job = st.create_job("ITC")
    st.mark_failed(job["id"], "boom", usage={"input_tokens": 100_000})
    failed = st.get_job(job["id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "boom"
    assert failed["cost_usd"] == pytest.approx(0.5)


def test_delete_removes_index_row_and_body(clean_store, monkeypatch):
    _cfg(monkeypatch)
    job = st.create_job("LT")
    st.mark_done(job["id"], "# Body")
    assert st._body_path(job["id"]).exists()

    assert st.delete_job(job["id"]) is True
    assert st.get_job(job["id"]) is None
    assert not st._body_path(job["id"]).exists()
    assert st.delete_job(job["id"]) is False


def test_reload_survives_restart(clean_store, monkeypatch):
    _cfg(monkeypatch)
    job = st.create_job("AXISBANK")
    st.mark_done(job["id"], "# Persisted")

    monkeypatch.setattr(st, "_JOBS", [])
    st.load_persisted_jobs()

    reloaded = st.get_job(job["id"], with_body=True)
    assert reloaded["status"] == "done"
    assert reloaded["markdown"] == "# Persisted"


def test_reload_fails_jobs_left_in_flight(clean_store, monkeypatch):
    """A restart kills the model stream, so a 'running' job can never resume."""
    _cfg(monkeypatch)
    queued = st.create_job("ONGC")
    running = st.create_job("COALINDIA")
    st.mark_running(running["id"])

    monkeypatch.setattr(st, "_JOBS", [])
    st.load_persisted_jobs()

    for job_id in (queued["id"], running["id"]):
        job = st.get_job(job_id)
        assert job["status"] == "failed"
        assert "restart" in job["error"].lower()


def test_corrupt_index_does_not_raise(clean_store, monkeypatch):
    _cfg(monkeypatch)
    st.INDEX_FILE.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(st, "_JOBS", [{"id": "stale"}])
    st.load_persisted_jobs()
    assert st.list_jobs() == []


def test_prune_drops_oldest_jobs_and_bodies(clean_store, monkeypatch):
    _cfg(monkeypatch)
    monkeypatch.setattr(st, "MAX_JOBS", 3)
    ids = []
    for i in range(5):
        job = st.create_job(f"SYM{i}")
        st.mark_done(job["id"], f"body {i}")
        ids.append(job["id"])

    assert len(st.list_jobs(limit=100)) == 3
    # create_job inserts at the head, so the first two are the ones pruned.
    assert st.get_job(ids[0]) is None
    assert not st._body_path(ids[0]).exists()
    assert st.get_job(ids[-1]) is not None
