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


@pytest.mark.asyncio
async def test_gateway_lm_studio_native_non_stream():
    """Gateway with use_lm_studio_native uses lm_studio.generate_lm_studio."""
    with patch("assistant.models.gateway.lm_studio.generate_lm_studio", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = "Reply from LM Studio"
        gw = ModelGateway(
            openai_base_url="http://localhost:1234/v1",
            model_name="test",
            use_lm_studio_native=True,
        )
        out = await gw.generate("Hi", stream=False)
        assert out == "Reply from LM Studio"
        mock_gen.assert_called_once()
        call_kw = mock_gen.call_args[1]
        assert call_kw["reasoning"] in ("on", "off")
        assert "system" in call_kw or "prompt" in mock_gen.call_args[0]
