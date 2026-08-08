"""JSON-backed job index for the Equity Report desk.

Follows the flat-JSON-per-concern convention (see ``execution/arming.py``) with
one deviation: report bodies are markdown files under ``data/equity_reports/``
rather than strings inside the index, so the index stays small enough to rewrite
on every progress update.

Unlike the other stores in this repo, this one is genuinely touched by two
threads — the API request handlers and the report runner — so every mutation
goes through ``_LOCK``.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import date, datetime
from typing import Any, Literal

from settings import data_dir, equity_report_config

INDEX_FILE = data_dir() / "equity_reports.json"
BODY_DIR = data_dir() / "equity_reports"

JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
TERMINAL_STATUSES = {"done", "failed", "cancelled"}

# Keep the newest N jobs; older ones are pruned along with their bodies.
MAX_JOBS = 200

_LOCK = threading.RLock()
_JOBS: list[dict[str, Any]] = []

# USD per million tokens, by model. Cache reads bill at 0.1x input, cache writes
# at 1.25x (5-minute TTL). Unknown models fall back to the Opus 5 rate so a cost
# estimate is never silently zero.
_RATES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
_DEFAULT_RATE = (5.0, 25.0)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _body_path(job_id: str):
    return BODY_DIR / f"{job_id}.md"


def estimate_cost_usd(usage: dict[str, Any] | None, model: str) -> float:
    """Cost of one report from an Anthropic ``usage`` block.

    Gemini reports run on a free-tier key and are recorded as $0.00. That is
    accurate today but would silently under-report on a paid Gemini plan — add a
    rate entry here before moving this desk onto one.
    """
    if not usage:
        return 0.0
    if model.startswith("gemini") or model == "stub":
        return 0.0
    rate_in, rate_out = _RATES.get(model, _DEFAULT_RATE)
    per_token_in = rate_in / 1_000_000
    per_token_out = rate_out / 1_000_000

    def _n(key: str) -> int:
        try:
            return int(usage.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    cost = (
        _n("input_tokens") * per_token_in
        + _n("output_tokens") * per_token_out
        + _n("cache_read_input_tokens") * per_token_in * 0.1
        + _n("cache_creation_input_tokens") * per_token_in * 1.25
    )
    return round(cost, 4)


def _blank_job(ticker: str, company: str, exchange: str) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:12],
        "ticker": ticker,
        "company": company,
        "exchange": exchange,
        "status": "queued",
        "created_at": _now(),
        "started_at": None,
        "finished_at": None,
        "progress": {"iteration": 0, "tool_calls": 0, "note": "Queued"},
        "usage": {},
        "cost_usd": 0.0,
        "citations": [],
        "model": equity_report_config()["model"],
        "error": None,
    }


def _save() -> None:
    INDEX_FILE.write_text(json.dumps({"jobs": _JOBS}, indent=2), encoding="utf-8")


def load_persisted_jobs() -> None:
    """Restore the job index from disk (survives API restart)."""
    global _JOBS
    BODY_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_FILE.exists():
        _JOBS = []
        return
    try:
        raw = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _JOBS = []
        return
    jobs = raw.get("jobs") if isinstance(raw, dict) else None
    _JOBS = [j for j in (jobs or []) if isinstance(j, dict) and j.get("id")]
    # A job left mid-flight by a restart can never resume — the model stream is
    # gone. Mark it failed rather than leaving the UI spinning forever.
    changed = False
    for job in _JOBS:
        if job.get("status") in {"queued", "running"}:
            job["status"] = "failed"
            job["error"] = "API restarted while this report was in flight."
            job["finished_at"] = _now()
            changed = True
    if changed:
        _save()


load_persisted_jobs()


def _prune_locked() -> None:
    if len(_JOBS) <= MAX_JOBS:
        return
    for job in _JOBS[MAX_JOBS:]:
        try:
            _body_path(job["id"]).unlink(missing_ok=True)
        except OSError:
            pass
    del _JOBS[MAX_JOBS:]


def spend_today_usd() -> float:
    today = date.today().isoformat()
    with _LOCK:
        return round(
            sum(
                float(j.get("cost_usd") or 0.0)
                for j in _JOBS
                if str(j.get("created_at") or "").startswith(today)
            ),
            4,
        )


def cap_status() -> dict[str, Any]:
    cap = equity_report_config()["daily_usd_cap"]
    spent = spend_today_usd()
    return {
        "daily_usd_cap": cap,
        "spent_today_usd": spent,
        "remaining_usd": round(max(cap - spent, 0.0), 4) if cap else None,
        "capped": bool(cap) and spent >= cap,
    }


def create_job(ticker: str, company: str = "", exchange: str = "NSE") -> dict[str, Any]:
    """Queue a report. Raises RuntimeError when the daily spend cap is already hit."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        raise ValueError("ticker is required")

    cap = equity_report_config()["daily_usd_cap"]
    if cap:
        spent = spend_today_usd()
        if spent >= cap:
            raise RuntimeError(
                f"Daily report spend cap reached (${spent:.2f} of ${cap:.2f}). "
                "Raise EQUITY_REPORT_DAILY_USD_CAP or wait until tomorrow."
            )

    with _LOCK:
        if any(
            j.get("ticker") == ticker and j.get("status") in {"queued", "running"}
            for j in _JOBS
        ):
            raise RuntimeError(f"A report for {ticker} is already in progress.")
        job = _blank_job(ticker, (company or "").strip(), (exchange or "NSE").strip().upper())
        _JOBS.insert(0, job)
        _prune_locked()
        _save()
        return dict(job)


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with _LOCK:
        return [dict(j) for j in _JOBS[: max(limit, 1)]]


def get_job(job_id: str, with_body: bool = False) -> dict[str, Any] | None:
    with _LOCK:
        job = next((dict(j) for j in _JOBS if j.get("id") == job_id), None)
    if job is None:
        return None
    if with_body:
        job["markdown"] = read_body(job_id)
    return job


def next_queued() -> dict[str, Any] | None:
    """Oldest queued job — the runner's work source."""
    with _LOCK:
        queued = [j for j in _JOBS if j.get("status") == "queued"]
        return dict(queued[-1]) if queued else None


def _patch_locked(job_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    for job in _JOBS:
        if job.get("id") == job_id:
            job.update(patch)
            _save()
            return dict(job)
    return None


def mark_running(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        return _patch_locked(
            job_id,
            {
                "status": "running",
                "started_at": _now(),
                "error": None,
                "progress": {"iteration": 0, "tool_calls": 0, "note": "Gathering data"},
            },
        )


def update_progress(job_id: str, **fields: Any) -> None:
    with _LOCK:
        for job in _JOBS:
            if job.get("id") == job_id:
                progress = dict(job.get("progress") or {})
                progress.update(fields)
                job["progress"] = progress
                _save()
                return


def write_body(job_id: str, markdown: str) -> None:
    BODY_DIR.mkdir(parents=True, exist_ok=True)
    _body_path(job_id).write_text(markdown or "", encoding="utf-8")


def read_body(job_id: str) -> str:
    path = _body_path(job_id)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def mark_done(
    job_id: str,
    markdown: str,
    usage: dict[str, Any] | None = None,
    citations: list[dict[str, Any]] | None = None,
    model: str = "",
) -> dict[str, Any] | None:
    write_body(job_id, markdown)
    with _LOCK:
        job = next((j for j in _JOBS if j.get("id") == job_id), None)
        model = model or (job or {}).get("model") or equity_report_config()["model"]
        return _patch_locked(
            job_id,
            {
                "status": "done",
                "finished_at": _now(),
                "usage": usage or {},
                "citations": citations or [],
                "model": model,
                "cost_usd": estimate_cost_usd(usage, model),
                "progress": {"iteration": 0, "tool_calls": 0, "note": "Complete"},
            },
        )


def mark_failed(
    job_id: str,
    error: str,
    usage: dict[str, Any] | None = None,
    model: str = "",
) -> dict[str, Any] | None:
    """Failures still bill for whatever ran, so record usage when we have it."""
    with _LOCK:
        job = next((j for j in _JOBS if j.get("id") == job_id), None)
        model = model or (job or {}).get("model") or equity_report_config()["model"]
        patch: dict[str, Any] = {
            "status": "failed",
            "finished_at": _now(),
            "error": str(error)[:2000],
            "progress": {"iteration": 0, "tool_calls": 0, "note": "Failed"},
        }
        if usage:
            patch["usage"] = usage
            patch["cost_usd"] = estimate_cost_usd(usage, model)
        return _patch_locked(job_id, patch)


def cancel_job(job_id: str) -> dict[str, Any] | None:
    """Cancel a job. A running job is flagged; the runner stops at its next checkpoint."""
    with _LOCK:
        job = next((j for j in _JOBS if j.get("id") == job_id), None)
        if job is None:
            return None
        if job.get("status") in TERMINAL_STATUSES:
            return dict(job)
        return _patch_locked(
            job_id,
            {
                "status": "cancelled",
                "finished_at": _now(),
                "progress": {"iteration": 0, "tool_calls": 0, "note": "Cancelled"},
            },
        )


def is_cancelled(job_id: str) -> bool:
    with _LOCK:
        job = next((j for j in _JOBS if j.get("id") == job_id), None)
        return bool(job) and job.get("status") == "cancelled"


def delete_job(job_id: str) -> bool:
    with _LOCK:
        before = len(_JOBS)
        _JOBS[:] = [j for j in _JOBS if j.get("id") != job_id]
        if len(_JOBS) == before:
            return False
        _save()
    try:
        _body_path(job_id).unlink(missing_ok=True)
    except OSError:
        pass
    return True
