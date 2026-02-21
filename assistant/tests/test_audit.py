"""Tests for security/audit: redaction and audit logging."""

import logging

import pytest

from assistant.security.audit import _redact, audit, REDACT_KEYS


def test_redact_dict_redacts_sensitive_keys():
    assert _redact({"token": "x", "user": "u"}) == {"token": "[REDACTED]", "user": "u"}
    assert _redact({"password": "p"}) == {"password": "[REDACTED]"}
    assert _redact({"api_key": "k"}) == {"api_key": "[REDACTED]"}


def test_redact_dict_nested():
    out = _redact({"a": {"token": "secret", "x": 1}})
    assert out == {"a": {"token": "[REDACTED]", "x": 1}}


def test_redact_list():
    assert _redact([{"token": "t"}, "plain"]) == [{"token": "[REDACTED]"}, "plain"]


def test_redact_plain_value():
    assert _redact("hello") == "hello"
    assert _redact(42) == 42


def test_audit_logs_event(caplog):
    caplog.set_level(logging.INFO, logger="assistant.audit")
    audit("test_event", user_id="u1", action="login")
    assert "test_event" in caplog.text
    assert "user_id" in caplog.text or "u1" in caplog.text


def test_audit_redacts_secrets(caplog):
    caplog.set_level(logging.INFO, logger="assistant.audit")
    audit("api_call", token="secret123", path="/api")
    assert "secret123" not in caplog.text
    assert "[REDACTED]" in caplog.text or "token" in caplog.text
