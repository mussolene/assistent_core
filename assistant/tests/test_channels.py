"""Tests for Telegram channel: sanitize, rate limit, strip_think."""

import pytest

from assistant.channels.telegram import sanitize_text, RateLimiter, _strip_think_blocks


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
