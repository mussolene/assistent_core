"""Tests for Telegram channel: sanitize, rate limit, strip_think, send_typing."""

from unittest.mock import AsyncMock, patch

import pytest

from assistant.channels.telegram import (
    BOT_COMMANDS,
    PARSE_MODE,
    RateLimiter,
    _strip_think_blocks,
    _to_telegram_html,
    sanitize_text,
    send_typing,
)


def test_sanitize_text_empty():
    assert sanitize_text("") == ""
    assert sanitize_text(None) == ""


def test_sanitize_text_truncate():
    long_str = "a" * 5000
    out = sanitize_text(long_str, max_len=100)
    assert len(out) <= 100


def test_sanitize_text_strips_control():
    out = sanitize_text("hello\x00world\n")
    assert "\x00" not in out
    assert "hello" in out


def test_rate_limiter_allows_under_limit():
    limiter = RateLimiter(max_per_minute=2)
    assert limiter.allow("user1") is True
    assert limiter.allow("user1") is True
    assert limiter.allow("user1") is False


def test_rate_limiter_per_user():
    limiter = RateLimiter(max_per_minute=1)
    assert limiter.allow("user1") is True
    assert limiter.allow("user1") is False
    assert limiter.allow("user2") is True


def test_strip_think_blocks_empty():
    assert _strip_think_blocks("") == ""
    assert _strip_think_blocks("  ") == ""


def test_strip_think_blocks_no_think():
    assert _strip_think_blocks("Hello world") == "Hello world"


def test_strip_think_blocks_removes_think():
    text = "<think>\nreasoning here\n</think>\n\nСвязь проверена."
    assert _strip_think_blocks(text) == "Связь проверена."


def test_strip_think_blocks_unclosed_think():
    text = "<think>\nreasoning without close"
    assert _strip_think_blocks(text) == ""


def test_strip_think_blocks_only_think():
    text = "<think>\nok\n</think>"
    assert _strip_think_blocks(text) == ""


def test_bot_commands_include_settings_and_channels():
    commands = {c["command"] for c in BOT_COMMANDS}
    assert "settings" in commands
    assert "channels" in commands


def test_to_telegram_html_uses_html():
    assert PARSE_MODE == "HTML"


def test_to_telegram_html_plain():
    assert _to_telegram_html("Hello") == "Hello"
    assert _to_telegram_html("Принято.") == "Принято."


def test_to_telegram_html_bold():
    assert _to_telegram_html("**bold**") == "<b>bold</b>"
    assert _to_telegram_html("Hello **world**!") == "Hello <b>world</b>!"


def test_to_telegram_html_italic():
    assert _to_telegram_html("*italic*") == "<i>italic</i>"


def test_to_telegram_html_code():
    assert _to_telegram_html("`code`") == "<code>code</code>"


def test_to_telegram_html_escapes():
    assert "&lt;" in _to_telegram_html("<script>")
    assert "&amp;" in _to_telegram_html("a & b")


@pytest.mark.asyncio
async def test_send_typing_calls_telegram_api():
    with patch("assistant.channels.telegram.httpx.AsyncClient") as mock_client:
        mock_post = AsyncMock()
        mock_client.return_value.__aenter__.return_value.post = mock_post
        await send_typing("https://api.telegram.org/bot123", "chat_456")
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "sendChatAction" in call_args[0][0]
        assert call_args[1]["json"] == {"chat_id": "chat_456", "action": "typing"}
