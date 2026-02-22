"""Tests for Model Gateway (mocked HTTP)."""

from unittest.mock import AsyncMock, MagicMock, patch

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
async def test_local_gateway_empty_choices_returns_empty():
    with patch("assistant.models.local.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=MagicMock(choices=[]))
        gw = LocalModelGateway(base_url="http://test", api_key="x", model_name="test")
        out = await gw.generate("Hi")
        assert out == ""


@pytest.mark.asyncio
async def test_local_gateway_generate_stream():
    """generate_stream yields content deltas from mocked stream."""

    async def fake_stream():
        for c in ["Hel", "lo"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock(delta=MagicMock(content=c))]
            yield chunk

    with patch("assistant.models.local.AsyncOpenAI") as mock_cls:
        mock_client = AsyncMock()
        mock_cls.return_value = mock_client
        mock_client.chat.completions.create = AsyncMock(return_value=fake_stream())
        gw = LocalModelGateway(base_url="http://test", api_key="x", model_name="test")
        stream = gw.generate_stream("Hi", system="You are helpful.")
        out = ""
        async for token in stream:
            out += token
        assert out == "Hello"


@pytest.mark.asyncio
async def test_gateway_lm_studio_native_non_stream():
    """Gateway with use_lm_studio_native uses lm_studio.generate_lm_studio."""
    with patch(
        "assistant.models.gateway.lm_studio.generate_lm_studio", new_callable=AsyncMock
    ) as mock_gen:
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


@pytest.mark.asyncio
async def test_gateway_model_for_reasoning():
    """_model_for_reasoning returns model name with suffix when reasoning=True."""
    gw = ModelGateway(model_name="llama", reasoning_suffix=":reasoning")
    assert gw._model_for_reasoning(False) == "llama"
    assert gw._model_for_reasoning(True) == "llama:reasoning"


@pytest.mark.asyncio
async def test_gateway_cloud_fallback_on_local_failure():
    """When local fails and cloud_fallback_enabled, use cloud."""
    with patch("assistant.models.gateway.LocalModelGateway") as mock_local_cls:
        mock_local = AsyncMock()
        mock_local.generate = AsyncMock(side_effect=RuntimeError("local down"))
        mock_local_cls.return_value = mock_local
        with patch("assistant.models.gateway.CloudModelGateway") as mock_cloud_cls:
            mock_cloud = AsyncMock()
            mock_cloud.generate = AsyncMock(return_value="Cloud reply")
            mock_cloud_cls.return_value = mock_cloud
            gw = ModelGateway(
                openai_base_url="http://localhost:11434/v1",
                model_name="local",
                cloud_fallback_enabled=True,
                openai_api_key="sk-fake",
            )
            out = await gw.generate("Hi", stream=False)
            assert out == "Cloud reply"


@pytest.mark.asyncio
async def test_gateway_lm_studio_native_stream():
    with patch("assistant.models.gateway.lm_studio.stream_lm_studio") as mock_stream:

        async def gen():
            yield "A"
            yield "B"

        mock_stream.return_value = gen()
        gw = ModelGateway(use_lm_studio_native=True, openai_base_url="http://x/v1")
        result = await gw.generate("Hi", stream=True)
        out = ""
        async for t in result:
            out += t
        assert out == "AB"
        mock_stream.assert_called_once()


@pytest.mark.asyncio
async def test_gateway_lm_studio_native_raises_no_cloud():
    with patch(
        "assistant.models.gateway.lm_studio.generate_lm_studio",
        new_callable=AsyncMock,
        side_effect=ConnectionError("LM Studio down"),
    ):
        gw = ModelGateway(use_lm_studio_native=True, openai_base_url="http://x/v1")
        with pytest.raises(ConnectionError, match="LM Studio down"):
            await gw.generate("Hi", stream=False)


@pytest.mark.asyncio
async def test_gateway_cloud_fallback_stream():
    with patch("assistant.models.gateway.LocalModelGateway") as mock_local_cls:
        mock_local = MagicMock()
        mock_local.generate_stream = MagicMock(side_effect=RuntimeError("local down"))
        mock_local_cls.return_value = mock_local
        with patch("assistant.models.gateway.CloudModelGateway") as mock_cloud_cls:
            mock_cloud = MagicMock()

            async def stream():
                yield "C"
                yield "loud"

            mock_cloud.generate_stream = MagicMock(return_value=stream())
            mock_cloud_cls.return_value = mock_cloud
            gw = ModelGateway(
                cloud_fallback_enabled=True,
                openai_api_key="sk-fake",
            )
            result = await gw.generate("Hi", stream=True)
            out = ""
            async for t in result:
                out += t
            assert out == "Cloud"
