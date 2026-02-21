"""Model Gateway: single entrypoint generate(prompt, stream, reasoning). Cloud disabled by default."""

from __future__ import annotations

import logging
from typing import AsyncIterator

from assistant.models.local import LocalModelGateway
from assistant.models.cloud import CloudModelGateway

logger = logging.getLogger(__name__)


class ModelGateway:
    """Unified gateway: local (Ollama) with optional cloud fallback. LLM does not control lifecycle."""

    def __init__(
        self,
        provider: str = "local",
        model_name: str = "llama3.2",
        fallback_name: str | None = None,
        cloud_fallback_enabled: bool = False,
        reasoning_suffix: str = ":reasoning",
        openai_base_url: str | None = None,
        openai_api_key: str = "",
    ) -> None:
        self._provider = provider
        self._model_name = model_name
        self._fallback_name = fallback_name
        self._cloud_fallback_enabled = cloud_fallback_enabled
        self._reasoning_suffix = reasoning_suffix
        self._openai_base_url = openai_base_url or "http://localhost:11434/v1"
        self._openai_api_key = openai_api_key or "ollama"
        self._local = LocalModelGateway(
            base_url=self._openai_base_url,
            api_key=self._openai_api_key,
            model_name=model_name,
            reasoning_suffix=reasoning_suffix,
        )
        self._cloud: CloudModelGateway | None = None
        if cloud_fallback_enabled and openai_api_key and "sk-" in openai_api_key:
            self._cloud = CloudModelGateway(
                api_key=openai_api_key,
                model_name=fallback_name or "gpt-4",
                base_url=None,
            )

    def _model_for_reasoning(self, reasoning: bool) -> str:
        if reasoning and self._reasoning_suffix:
            return self._model_name + self._reasoning_suffix
        return self._model_name

    async def generate(
        self,
        prompt: str,
        *,
        stream: bool = False,
        reasoning: bool = False,
        system: str | None = None,
    ) -> str | AsyncIterator[str]:
        """Generate completion. Returns full text or async iterator of tokens. Cloud only if enabled."""
        model = self._model_for_reasoning(reasoning)
        try:
            if stream:
                return self._local.generate_stream(prompt, model=model, system=system)
            return await self._local.generate(prompt, model=model, system=system)
        except Exception as e:
            logger.warning("local generate failed: %s", e)
            if self._cloud:
                if stream:
                    return self._cloud.generate_stream(prompt, system=system)
                return await self._cloud.generate(prompt, system=system)
            raise
