"""utils/logging.py — secret redaction in structured JSON logs."""

from __future__ import annotations

import json
import logging

from utils.logging import JsonFormatter, get_logger, log_event


def _format_record(**extra_fields) -> dict:
    logger = get_logger("test_logging")
    record = logger.makeRecord(logger.name, logging.INFO, "(test)", 0, "test message", (), None)
    record.extra_fields = extra_fields
    return json.loads(JsonFormatter().format(record))


def test_sensitive_key_in_extra_fields_is_redacted():
    out = _format_record(access_token="abc123secret", underlying="NIFTY")
    assert out["access_token"] == "***REDACTED***"
    assert out["underlying"] == "NIFTY"  # non-sensitive fields pass through


def test_sensitive_key_variants_are_all_redacted():
    fields = dict(
        api_secret="s1",
        KITE_API_SECRET="s2",
        request_token="s3",
        password="s4",
        totp_secret="s5",
    )
    out = _format_record(**fields)
    for key in fields:
        assert out[key] == "***REDACTED***", f"{key} was not redacted: {out[key]!r}"


def test_inline_secret_in_message_text_is_redacted():
    logger = get_logger("test_logging")
    record = logger.makeRecord(
        logger.name,
        logging.WARNING,
        "(test)",
        0,
        "auth failed: api_secret=sk_live_abcdef123456 for user X",
        (),
        None,
    )
    out = json.loads(JsonFormatter().format(record))
    assert "sk_live_abcdef123456" not in out["msg"]
    assert "***REDACTED***" in out["msg"]


def test_exception_text_is_redacted():
    logger = get_logger("test_logging")
    try:
        raise RuntimeError("Kite rejected order: access_token=eyJhbGciOi123 expired")
    except RuntimeError:
        import sys

        record = logger.makeRecord(
            logger.name, logging.ERROR, "(test)", 0, "order failed", (), sys.exc_info()
        )
    out = json.loads(JsonFormatter().format(record))
    assert "eyJhbGciOi123" not in out["error"]
    assert "***REDACTED***" in out["error"]


def test_nested_dict_values_are_redacted():
    out = _format_record(raw={"api_key": "topsecret", "order_id": "123"})
    assert out["raw"]["api_key"] == "***REDACTED***"
    assert out["raw"]["order_id"] == "123"


def test_log_event_still_works_end_to_end(caplog):
    logger = get_logger("test_logging")
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(logger, logging.INFO, "order_placed", tradingsymbol="NIFTY26JUL24000CE", qty=75)
    assert any("order_placed" in r.message for r in caplog.records)
