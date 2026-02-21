"""Tests for Telegram channel: sanitize, rate limit."""

import pytest

from assistant.channels.telegram import sanitize_text, RateLimiter


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
