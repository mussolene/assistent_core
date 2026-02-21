"""Tests for core/logging_config: redaction, StructuredFormatter, setup_logging."""

import json
import logging
import sys

import pytest

from assistant.core.logging_config import _redact, StructuredFormatter, setup_logging


def test_redact_string_with_token():
    assert _redact("bearer abc123") == "[REDACTED]"
    assert _redact("token=xyz") == "[REDACTED]"
    assert _redact("hello") == "hello"


def test_redact_dict_recursive():
    assert _redact({"k": "token: x"}) == {"k": "[REDACTED]"}
    assert _redact({"a": "normal"}) == {"a": "normal"}


def test_redact_list():
    assert _redact(["bearer x"]) == ["[REDACTED]"]


def test_structured_formatter_json():
    fmt = StructuredFormatter(use_json=True)
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="", lineno=0,
        msg="hello", args=(), exc_info=None,
    )
    out = fmt.format(record)
    data = json.loads(out)
    assert data["message"] == "hello"
    assert "level" in data
    assert data["level"] == "INFO"


def test_structured_formatter_key_value():
    fmt = StructuredFormatter(use_json=False)
    record = logging.LogRecord(
        name="test", level=logging.WARNING, pathname="", lineno=0,
        msg="warn", args=(), exc_info=None,
    )
    out = fmt.format(record)
    assert "warn" in out
    assert "WARNING" in out


def test_setup_logging():
    setup_logging(level="INFO", use_json=True)
    root = logging.getLogger()
    assert root.level == logging.INFO
    if root.handlers:
        handler = root.handlers[0]
        assert isinstance(handler.formatter, StructuredFormatter)
        assert handler.formatter.use_json is True
