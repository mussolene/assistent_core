"""Tests for SendEmailSkill: allowlist, rate limit, send_email delegation."""

from unittest.mock import MagicMock, patch

import pytest


def __redis_mock(incr_value=1):
    r = MagicMock()
    r.incr.return_value = incr_value
    r.expire.return_value = True
    r.close.return_value = None
    return r

from assistant.skills.send_email_skill import RATE_MAX_PER_WINDOW, SendEmailSkill


@pytest.fixture
def skill():
    return SendEmailSkill(redis_url="redis://localhost:6379/0")


@pytest.mark.asyncio
async def test_send_email_missing_to_returns_error(skill):
    out = await skill.run({"subject": "Hi", "body": "Text"})
    assert out.get("ok") is False
    assert "получателя" in out.get("error", "").lower() or "to" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_send_email_invalid_to_returns_error(skill):
    out = await skill.run({"to": "not-an-email", "subject": "Hi", "body": "Text"})
    assert out.get("ok") is False


@pytest.mark.asyncio
async def test_send_email_allowlist_rejects_when_not_in_list(skill):
    with patch(
        "assistant.skills.send_email_skill._get_allowed_recipients",
        return_value=["allowed@test.com"],
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            out = await skill.run({
                "to": "other@test.com",
                "subject": "Hi",
                "body": "Text",
                "user_id": "u1",
            })
    assert out.get("ok") is False
    assert "allowlist" in out.get("error", "").lower() or "разрешён" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_send_email_allowlist_allows_when_in_list(skill):
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.skills.send_email_skill._get_allowed_recipients",
        return_value=["allowed@test.com"],
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "to": "allowed@test.com",
                    "subject": "Hi",
                    "body": "Text",
                    "user_id": "u1",
                })
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_send_email_rate_limit_exceeded_returns_error(skill):
    redis_mock = __redis_mock(incr_value=RATE_MAX_PER_WINDOW + 1)
    with patch(
        "assistant.skills.send_email_skill._get_allowed_recipients",
        return_value=[],
    ):
        with patch("redis.from_url", return_value=redis_mock):
            out = await skill.run({
                "to": "user@test.com",
                "subject": "Hi",
                "body": "Text",
                "user_id": "u1",
            })
    assert out.get("ok") is False
    assert "лимит" in out.get("error", "").lower() or "limit" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_send_email_success(skill):
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.skills.send_email_skill._get_allowed_recipients",
        return_value=[],
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "to": "user@test.com",
                    "subject": "Test",
                    "body": "Body",
                    "user_id": "u1",
                })
    assert out.get("ok") is True
    assert "отправлено" in out.get("message", "").lower() or "sent" in out.get("message", "").lower()


@pytest.mark.asyncio
async def test_send_email_adapter_failure_returns_error(skill):
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.skills.send_email_skill._get_allowed_recipients",
        return_value=[],
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=False):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "to": "user@test.com",
                    "subject": "Test",
                    "body": "Body",
                    "user_id": "u1",
                })
    assert out.get("ok") is False
    assert "не удалось" in out.get("error", "").lower() or "отправить" in out.get("error", "").lower()


@pytest.mark.asyncio
async def test_send_email_accepts_recipient_and_text_aliases(skill):
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.skills.send_email_skill._get_allowed_recipients",
        return_value=[],
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "recipient": "user@test.com",
                    "subject": "Subj",
                    "text": "Content",
                    "user_id": "u1",
                })
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_send_email_skill_name():
    skill = SendEmailSkill(redis_url="")
    assert skill.name == "send_email"


@pytest.mark.asyncio
async def test_send_email_allowed_recipients_from_config_json(skill):
    """Allowlist from Redis config as JSON string."""
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        return_value={"EMAIL_ALLOWED_RECIPIENTS": '["allowed@test.com"]'},
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "to": "allowed@test.com",
                    "subject": "Hi",
                    "body": "Text",
                    "user_id": "u1",
                })
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_send_email_allowed_recipients_comma_separated(skill):
    """Allowlist from config as comma-separated string."""
    with patch(
        "assistant.skills.send_email_skill._get_allowed_recipients",
        return_value=["one@test.com", "two@test.com"],
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=__redis_mock(incr_value=1)):
                out = await skill.run({
                    "to": "two@test.com",
                    "subject": "Hi",
                    "body": "Text",
                    "user_id": "u1",
                })
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_send_email_rate_limit_redis_exception_continues(skill):
    """When Redis raises during rate limit check, skill still tries to send."""
    with patch(
        "assistant.skills.send_email_skill._get_allowed_recipients",
        return_value=[],
    ):
        with patch("redis.from_url", side_effect=RuntimeError("redis down")):
            with patch("assistant.channels.email_adapter.send_email", return_value=True):
                out = await skill.run({
                    "to": "user@test.com",
                    "subject": "Hi",
                    "body": "Text",
                    "user_id": "u1",
                })
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_send_email_allowed_recipients_from_config_list(skill):
    """Allowlist from Redis config as list (e.g. from dashboard)."""
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        return_value={"EMAIL_ALLOWED_RECIPIENTS": ["list@test.com", "other@test.com"]},
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "to": "list@test.com",
                    "subject": "Hi",
                    "body": "Text",
                    "user_id": "u1",
                })
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_send_email_allowed_recipients_comma_string_from_config(skill):
    """Allowlist from config as comma-separated string (no JSON)."""
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        return_value={"EMAIL_ALLOWED_RECIPIENTS": "a@b.com, b@c.com"},
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "to": "b@c.com",
                    "subject": "Hi",
                    "body": "Text",
                    "user_id": "u1",
                })
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_send_email_allowed_recipients_empty_config_any_recipient_allowed(skill):
    """When EMAIL_ALLOWED_RECIPIENTS is empty, allowlist is empty so any recipient is allowed."""
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        return_value={},
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "to": "anyone@test.com",
                    "subject": "Hi",
                    "body": "Text",
                    "user_id": "u1",
                })
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_send_email_allowed_recipients_config_exception_returns_empty(skill):
    """When config_store raises, allowlist is empty so any recipient is allowed."""
    redis_mock = __redis_mock(incr_value=1)
    with patch(
        "assistant.dashboard.config_store.get_config_from_redis_sync",
        side_effect=RuntimeError("redis unavailable"),
    ):
        with patch("assistant.channels.email_adapter.send_email", return_value=True):
            with patch("redis.from_url", return_value=redis_mock):
                out = await skill.run({
                    "to": "any@test.com",
                    "subject": "Hi",
                    "body": "Text",
                    "user_id": "u1",
                })
    assert out.get("ok") is True
