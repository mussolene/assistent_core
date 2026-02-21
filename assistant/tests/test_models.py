"""Tests for Model Gateway (mocked HTTP)."""

from unittest.mock import AsyncMock, patch

import pytest

from assistant.models.gateway import ModelGateway
from assistant.models.local import LocalModelGateway


@pytest.mark.asyncio
async def test_local_gateway_mock():
    """Test local gateway with mocked OpenAI client."""
    class FakeMessage:
        content = "Hello"
    class FakeChoice:
        message = FakeMessage()
    class FakeResponse:
        choices = [FakeChoice()]
    with patch("assistant.models.local.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=FakeResponse())
        gw = LocalModelGateway(base_url="http://test", api_key="x", model_name="test")
        out = await gw.generate("Hi")
        assert out.strip() == "Hello"
