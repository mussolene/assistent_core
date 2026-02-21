"""Tests for LM Studio native API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from assistant.models import lm_studio


def test_native_base_url_strips_v1():
    assert lm_studio._native_base_url("http://localhost:1234/v1") == "http://localhost:1234"
    assert lm_studio._native_base_url("http://host:1234/v1/") == "http://host:1234"


def test_native_base_url_empty():
    assert lm_studio._native_base_url("") == "http://localhost:1234"
    assert lm_studio._native_base_url("http://localhost:1234") == "http://localhost:1234"


@pytest.mark.asyncio
async def test_generate_lm_studio_mock():
    """generate_lm_studio returns message content from output array."""
    fake_response = MagicMock()
    fake_response.json.return_value = {
        "output": [
            {"type": "reasoning", "content": "hidden"},
            {"type": "message", "content": "Связь проверена."},
        ],
    }
    fake_response.raise_for_status = lambda: None
    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.post = AsyncMock(return_value=fake_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        out = await lm_studio.generate_lm_studio(
            "http://localhost:1234/v1", "test-model", "Hi", system="You are helpful"
        )
    assert out == "Связь проверена."


@pytest.mark.asyncio
async def test_generate_lm_studio_empty_output():
    fake_response = MagicMock()
    fake_response.json.return_value = {"output": []}
    fake_response.raise_for_status = lambda: None
    with patch("httpx.AsyncClient") as mock_cls:
        ctx = AsyncMock()
        ctx.post = AsyncMock(return_value=fake_response)
        mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
        mock_cls.return_value.__aexit__ = AsyncMock(return_value=None)
        out = await lm_studio.generate_lm_studio("http://localhost:1234/v1", "m", "Hi")
    assert out == ""


def test_is_lm_studio_native_url():
    assert lm_studio.is_lm_studio_native_url("http://localhost:1234/v1") is True
    assert lm_studio.is_lm_studio_native_url("http://host/api/v1") is True
    assert lm_studio.is_lm_studio_native_url("http://localhost:11434/v1") is False
    assert lm_studio.is_lm_studio_native_url("") is False
