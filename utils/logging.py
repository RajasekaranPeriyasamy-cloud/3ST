"""Structured JSON logging for 3ST execution events.

Three things beyond the original stderr-only logger:

1. **Secret redaction** — every record (message, exception text, and any
   ``extra_fields``) is scrubbed for known-sensitive key/value patterns
   before it is serialized. Kite API secrets, access tokens, request
   tokens, and Firstock/staticip credentials all flow through requests and
   exception messages in this codebase; nothing should have to remember to
   redact them by hand at the call site.
2. **Optional file persistence** (``LOG_TO_FILE=1``) — daily-rotated text
   logs under ``log/``, retained for ``LOG_RETENTION`` days (default 14).
   Off by default so behavior is unchanged unless explicitly opted in.
3. **Always-on JSON error log** (``log/errors.jsonl``) — ERROR+ only,
   auto-truncated to the last 1000 lines on startup. Read this first when
   debugging a post-incident report; it survives a process restart, unlike
   stderr.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_CONFIGURED = False
_REDACTED = "***REDACTED***"

# Key names (case-insensitive, `_`/`-` interchangeable) whose values are
# always scrubbed, wherever they show up — a structured field, or embedded
# in free text like "auth failed: api_secret=abcd1234...".
_SENSITIVE_KEY_NAMES = re.compile(
    r"(api[_-]?key|api[_-]?secret|access[_-]?token|request[_-]?token|"
    r"refresh[_-]?token|checksum|password|pepper|totp[_-]?secret|"
    r"totp|client[_-]?secret)",
    re.IGNORECASE,
)

# Matches "key=value" / "key: value" / '"key": "value"' pairs in free text —
# catches secrets embedded in raw exception strings, not just structured
# extra_fields, e.g. an httpx error dumping the failed request URL/body.
_INLINE_KV_PATTERN = re.compile(
    r"(?P<key>" + _SENSITIVE_KEY_NAMES.pattern + r")"
    r"(?P<sep>[\"']?\s*[:=]\s*[\"']?)"
    r"(?P<value>[^\s\"',}]+)",
    re.IGNORECASE,
)


def _redact_text(text: str) -> str:
    return _INLINE_KV_PATTERN.sub(lambda m: f"{m.group('key')}{m.group('sep')}{_REDACTED}", text)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (_REDACTED if _SENSITIVE_KEY_NAMES.search(str(k)) else _redact_value(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v) for v in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact_text(record.getMessage()),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(_redact_value(extra))
        if record.exc_info and record.exc_info[1] is not None:
            payload["error"] = _redact_text(str(record.exc_info[1]))
        return json.dumps(payload, default=str)


def _log_dir() -> Path:
    d = Path(__file__).resolve().parent.parent / "log"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _truncate_errors_file(path: Path, *, keep_lines: int = 1000) -> None:
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(lines) > keep_lines:
        path.write_text("\n".join(lines[-keep_lines:]) + "\n", encoding="utf-8")


def configure_logging(*, level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger("3st")
    root.setLevel(level)
    if not root.handlers:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(JsonFormatter())
        root.addHandler(console)

        if os.getenv("LOG_TO_FILE", "0").lower() in {"1", "true", "yes", "on"}:
            retention = int(os.getenv("LOG_RETENTION", "14") or 14)
            file_handler = logging.handlers.TimedRotatingFileHandler(
                _log_dir() / "3st.log",
                when="midnight",
                backupCount=retention,
                encoding="utf-8",
            )
            file_handler.setFormatter(JsonFormatter())
            root.addHandler(file_handler)

        errors_path = _log_dir() / "errors.jsonl"
        _truncate_errors_file(errors_path)
        error_handler = logging.FileHandler(errors_path, encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(JsonFormatter())
        root.addHandler(error_handler)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(f"3st.{name}")


def log_event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    record = logger.makeRecord(
        logger.name,
        level,
        "(3st)",
        0,
        message,
        (),
        None,
    )
    record.extra_fields = fields  # type: ignore[attr-defined]
    logger.handle(record)
