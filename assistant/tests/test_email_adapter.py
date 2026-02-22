"""Tests for email adapter: config, send_email (SMTP/SendGrid mocks), outgoing handler."""

from unittest.mock import patch

import httpx
from assistant.channels.email_adapter import get_email_config, send_email
from assistant.core.events import ChannelKind, OutgoingReply


def test_get_email_config_exception_returns_disabled():
    with patch(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        side_effect=RuntimeError("redis down"),
    ):
        cfg = get_email_config("redis://localhost/0")
    assert cfg.get("enabled") is False


def test_get_email_config_disabled_when_empty(monkeypatch):
    monkeypatch.setattr(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        lambda url: {},
    )
    cfg = get_email_config("redis://localhost/0")
    assert cfg.get("enabled") is False
    assert cfg.get("provider") == "smtp"


def test_get_email_config_enabled_and_smtp(monkeypatch):
    monkeypatch.setattr(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        lambda url: {
            "EMAIL_ENABLED": "true",
            "EMAIL_FROM": "bot@test.local",
            "EMAIL_PROVIDER": "smtp",
            "EMAIL_SMTP_HOST": "smtp.example.com",
            "EMAIL_SMTP_PORT": "587",
        },
    )
    cfg = get_email_config("redis://localhost/0")
    assert cfg.get("enabled") is True
    assert cfg.get("from") == "bot@test.local"
    assert cfg.get("smtp_host") == "smtp.example.com"


def test_send_email_disabled_returns_false(monkeypatch):
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {"enabled": False},
    )
    assert send_email("user@test.com", "Subj", "Body", "redis://localhost/0") is False


def test_send_email_smtp_no_host_returns_false(monkeypatch):
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {"enabled": True, "provider": "smtp", "smtp_host": "", "from": "x@y"},
    )
    assert send_email("user@test.com", "Subj", "Body", "redis://localhost/0") is False


def test_send_email_sendgrid_no_key_returns_false(monkeypatch):
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {"enabled": True, "provider": "sendgrid", "sendgrid_api_key": "", "from": "x@y"},
    )
    assert send_email("user@test.com", "Subj", "Body", "redis://localhost/0") is False


def test_send_email_invalid_to_returns_false(monkeypatch):
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {"enabled": True, "provider": "smtp", "smtp_host": "smtp.local"},
    )
    assert send_email("", "Subj", "Body", "redis://localhost/0") is False
    assert send_email("not-an-email", "Subj", "Body", "redis://localhost/0") is False


def test_send_email_smtp_success(monkeypatch):
    sent = []

    def fake_smtp(host, port, timeout=None):
        class F:
            def starttls(self):
                pass

            def login(self, user, password):
                pass

            def sendmail(self, f, to, msg):
                sent.append((f, to, msg))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        return F()

    monkeypatch.setattr("smtplib.SMTP", fake_smtp)
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {
            "enabled": True,
            "from": "bot@test.local",
            "provider": "smtp",
            "smtp_host": "smtp.test",
            "smtp_port": "587",
            "smtp_user": "u",
            "smtp_password": "p",
        },
    )
    out = send_email("user@test.com", "Hi", "Hello", "redis://localhost/0")
    assert out is True
    assert len(sent) == 1
    assert sent[0][0] == "bot@test.local"
    assert sent[0][1] == ["user@test.com"]


def test_send_email_smtp_exception_returns_false(monkeypatch):
    import smtplib as _smtplib

    def smtp_raise(host, port, timeout=None):
        raise _smtplib.SMTPException("connection refused")

    monkeypatch.setattr(_smtplib, "SMTP", smtp_raise)
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {
            "enabled": True,
            "from": "x@y",
            "provider": "smtp",
            "smtp_host": "smtp.test",
            "smtp_port": "587",
        },
    )
    assert send_email("user@test.com", "Subj", "Body", "redis://localhost/0") is False


def test_send_email_sendgrid_success(monkeypatch):
    requests = []

    def fake_post(url, json=None, headers=None, timeout=None):
        requests.append((url, json, headers))

        class R:
            status_code = 202
            text = "ok"

        return R()

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {
            "enabled": True,
            "from": "noreply@test.local",
            "provider": "sendgrid",
            "sendgrid_api_key": "SG.xxx",
        },
    )
    out = send_email("user@test.com", "Subj", "Body", "redis://localhost/0")
    assert out is True
    assert len(requests) == 1
    assert "sendgrid.com" in requests[0][0]
    assert requests[0][1]["personalizations"][0]["to"][0]["email"] == "user@test.com"
    assert requests[0][1]["subject"] == "Subj"
    assert "Bearer SG.xxx" in requests[0][2]["Authorization"]


def test_send_email_sendgrid_non_200_returns_false(monkeypatch):
    class R:
        status_code = 500
        text = "error"

    monkeypatch.setattr("httpx.post", lambda *a, **k: R())
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {
            "enabled": True,
            "from": "x@y",
            "provider": "sendgrid",
            "sendgrid_api_key": "SG.xxx",
        },
    )
    assert send_email("user@test.com", "Subj", "Body", "redis://localhost/0") is False


def test_send_email_sendgrid_exception_returns_false(monkeypatch):
    def _post_raise(*a, **k):
        raise httpx.ConnectError("fail")

    monkeypatch.setattr("httpx.post", _post_raise)
    monkeypatch.setattr(
        "assistant.channels.email_adapter.get_email_config",
        lambda url: {
            "enabled": True,
            "from": "x@y",
            "provider": "sendgrid",
            "sendgrid_api_key": "SG.xxx",
        },
    )
    assert send_email("user@test.com", "Subj", "Body", "redis://localhost/0") is False


def test_outgoing_payload_email_uses_chat_id_as_recipient():
    """OutgoingReply with channel=EMAIL uses chat_id as recipient email."""
    payload = OutgoingReply(
        task_id="t1",
        chat_id="user@example.com",
        text="Hi",
        channel=ChannelKind.EMAIL,
    )
    assert payload.channel == ChannelKind.EMAIL
    assert payload.chat_id == "user@example.com"
