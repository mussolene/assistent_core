"""Cloud model: OpenAI-compatible API. Used only when cloud_fallback_enabled and API key set."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class CloudModelGateway:
    """OpenAI or compatible cloud. No lifecycle control by LLM."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "gpt-4",
        base_url: str | None = None,
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model_name = model_name

    async def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self._client.chat.completions.create(
            model=self._model_name,
            messages=messages,
        )
        if not resp.choices:
            return ""
        return (resp.choices[0].message.content or "").strip()

    def generate_stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
    ) -> AsyncIterator[str]:
        async def _stream() -> AsyncIterator[str]:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            stream = await self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                stream=True,
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content

        return _stream()
