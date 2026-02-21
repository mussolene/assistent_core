"""Local model: Ollama or OpenAI-compatible (e.g. llama.cpp) via OpenAI client."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class LocalModelGateway:
    """Ollama / OpenAI-compatible local API. Streaming and reasoning model selection."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434/v1",
        api_key: str = "ollama",
        model_name: str = "llama3.2",
        reasoning_suffix: str = ":reasoning",
    ) -> None:
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        self._model_name = model_name
        self._reasoning_suffix = reasoning_suffix

    async def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
    ) -> str:
        model = model or self._model_name
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self._client.chat.completions.create(
            model=model,
            messages=messages,
        )
        if not resp.choices:
            return ""
        return (resp.choices[0].message.content or "").strip()

    def generate_stream(
        self,
        prompt: str,
        *,
        model: str | None = None,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        """Async generator of content deltas."""

        async def _stream() -> AsyncIterator[str]:
            model_name = model or self._model_name
            messages: list[dict[str, str]] = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            stream = await self._client.chat.completions.create(
                model=model_name,
                messages=messages,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and getattr(delta, "content", None):
                    yield delta.content

        return _stream()
